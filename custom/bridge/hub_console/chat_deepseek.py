"""Official DeepSeek discovery and explicit local Chat/DeepThink presets.

No provider credentials or model execution are needed to install this adapter.
GET /models and /user/balance validate access without a paid completion.
"""
import asyncio
import contextlib
import json
import math
import re
import time
from pathlib import Path

import httpx
from fastapi import HTTPException

OFFICIAL = 'https://api.deepseek.com'
RETIRED = {'deepseek-chat','deepseek-reasoner','deepseek-v4-flash','deepseek-v4-flash-vision-exp'}
SUFFIX = ':deepthink'


def normalize_models(data):
    out = {}
    supplied=data.get('data') if isinstance(data,dict) else None
    for value in supplied if isinstance(supplied,list) else []:
        if not isinstance(value,dict):continue
        ident=value.get('id','')
        if not isinstance(ident,str) or not re.fullmatch(r'deepseek-[A-Za-z0-9_.-]{1,150}',ident) or ident in RETIRED:continue
        row={'id':ident,'name':str(value.get('name') or ident)[:120]}
        for key in ('context_window','max_output_tokens'):
            number=value.get(key)
            if type(number) is int and 0<number<=10_000_000:row[key]=number
        modalities=value.get('input_modalities')
        row['input_modalities']=[v for v in modalities if v in ('text','image')] if isinstance(modalities,list) else []
        effort=value.get('effort',{})
        supplied=effort.get('supported_levels') if isinstance(effort,dict) else None
        levels=[v for v in supplied if v in ('low','high','max')] if isinstance(supplied,list) else []
        if not levels and ident in {'deepseek-flash','deepseek-v4-pro'}:levels=['low','high','max']
        row['effort_levels']=levels
        row['default_effort']=effort.get('default_level') if isinstance(effort,dict) and effort.get('default_level') in levels else ('high' if 'high' in levels else next(iter(levels),None))
        out[ident]=row
    return list(out.values())[:50]


def variants(entry):
    details=entry.get('model_metadata',{})
    out=[]
    for ident in entry.get('models',[]):
        if ident in RETIRED:continue
        meta=details.get(ident,{'id':ident,'name':ident})
        name=meta.get('name',ident)
        out.append({'id':ident,'name':name+' · Chat','api_model':ident,'thinking':False,'context_tokens':meta.get('context_window')})
        if meta.get('effort_levels'):
            out.append({'id':ident+SUFFIX,'name':name+' · DeepThink','api_model':ident,'thinking':True,'reasoning_effort':meta.get('default_effort') or meta['effort_levels'][0],'context_tokens':meta.get('context_window')})
    return out


def resolve(entry,ident):
    row=next((r for r in variants(entry) if r['id']==ident),None)
    if not row:raise RuntimeError('model_unavailable')
    return row


def balance_rows(data,stamp=None):
    from .chat_billing import amount
    rows=[]
    supplied=data.get('balance_infos') if isinstance(data,dict) else None
    for value in supplied if isinstance(supplied,list) else []:
        if not isinstance(value,dict) or value.get('currency') not in ('USD','CNY'):continue
        rows.append({'currency':value['currency'],'balance_amount':amount(value.get('total_balance')),'spent_amount':None,
            'spend_scope':'DeepSeek API не сообщает общий расход; это фактический остаток аккаунта.',
            'source':'Личный DeepSeek /user/balance','updated_at':stamp or time.time(),'estimated':False,
            'account_available':data.get('is_available') if isinstance(data.get('is_available'),bool) else None})
    return rows


def balance_endpoint(base):
    base=base.rstrip('/')
    return (base[:-3] if base.endswith('/v1') else base)+'/user/balance'


async def owner_billing(request,models):
    from .chat_harness import root
    from .chat_worker import load,atomic
    from .chat_jobs import own
    folder=root(request);entry=load(folder/'connections.json',{}).get('deepseek',{})
    matches=[r for r in models if r.get('id','').startswith('api/deepseek/')]
    if not matches:return []
    cached=load(folder/'deepseek-balance.json',{})
    # Bind cache to the exact saved connection; never reuse another owner's/global key.
    if cached.get('connection_at')!=entry.get('updated_at'):cached={}
    if entry.get('api_key') and time.time()-cached.get('attempted_at',0)>120:
        cached=dict(connection_at=entry.get('updated_at'),attempted_at=time.time(),rows=[])
        try:
            async with httpx.AsyncClient(timeout=8,trust_env=False,follow_redirects=False) as client:
                response=await client.get(balance_endpoint(entry['base_url']),headers={'Authorization':'Bearer '+entry['api_key']})
                response.raise_for_status();cached['rows']=balance_rows(response.json())
        except (httpx.HTTPError,ValueError,KeyError):pass
        atomic(folder/'deepseek-balance.json',cached);own(folder/'deepseek-balance.json',request)
    fallback={'currency':'USD','balance_amount':None,'spent_amount':None,'updated_at':None,'estimated':False,'source':'DeepSeek не сообщил баланс','spend_scope':'Нет данных не означает нулевой баланс.'}
    return [dict(value,provider='api',model_id=row['id'],billing_provider='deepseek',kind='billing',window='billing',description='Личный официальный DeepSeek API. Валюта сохраняется; CNY не конвертируется в USD.') for row in matches for value in cached.get('rows',[]) or [fallback]]


def run(desc,entry,events,cancelled,model):
    # Dedicated worker calls this synchronously. A cancellable async HTTP task
    # frees its socket even before the first headers/token, without replay.
    async def controlled():
        task=asyncio.create_task(_stream(desc,entry,events,cancelled,model))
        try:
            while not task.done():
                if cancelled():raise InterruptedError('cancelled')
                await asyncio.wait({task},timeout=.2)
            return await task
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):await task
    return asyncio.run(controlled())


async def _stream(desc,entry,events,cancelled,model):
    if desc.get('attachments'):raise RuntimeError('attachments_unsupported')
    mode=resolve(entry,model)
    from .chat_artifacts import INSTRUCTIONS
    payload={'model':mode['api_model'],'messages':list(desc.get('history',[]))+[{'role':'user','content':INSTRUCTIONS+desc['text']+desc.get('harness_prompt','')+desc.get('addon_prompt','')}],
        'stream':True,'stream_options':{'include_usage':True},'thinking':{'type':'enabled' if mode['thinking'] else 'disabled'}}
    if mode['thinking']:payload['reasoning_effort']=mode['reasoning_effort']
    answer=[];usage={};reasoning_started=False
    async with httpx.AsyncClient(timeout=httpx.Timeout(660,connect=15),trust_env=False,follow_redirects=False) as client:
        async with client.stream('POST',entry['base_url'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+entry['api_key']},json=payload) as response:
            if response.status_code in (401,403):raise RuntimeError('auth_required')
            if response.status_code in (402,429):raise RuntimeError('quota_exhausted')
            response.raise_for_status()
            async for line in response.aiter_lines():
                if cancelled():raise InterruptedError('cancelled')
                if not line.startswith('data:'):continue
                raw=line[5:].strip()
                if raw=='[DONE]':break
                try:data=json.loads(raw)
                except ValueError:continue
                if data.get('error'):raise RuntimeError('provider_error')
                if isinstance(data.get('usage'),dict):usage=data['usage']
                for choice in data.get('choices',[]):
                    delta=choice.get('delta',{})
                    thinking=delta.get('reasoning_content')
                    if isinstance(thinking,str) and thinking:
                        if not reasoning_started:events.status('running','DeepSeek · формирует рассуждение');reasoning_started=True
                        events.emit({'type':'reasoning','text':thinking,'provider':'deepseek'})
                    content=delta.get('content')
                    if isinstance(content,str) and content:answer.append(content);events.emit({'type':'text','text':content,'channel':'final'})
                    if delta.get('tool_calls'):raise RuntimeError('api_tools_unsupported')
    if not answer:raise RuntimeError('no_text')
    return {'usage':usage,'final_text':''.join(answer)}
