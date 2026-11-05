"""Read both actual subscription windows; never collapse primary/secondary quota.

Uses the same already-authorized local CLI OAuth as ai-failover. No API keys,
account identifiers, credential values or raw provider failures leave this module.
"""
from __future__ import annotations
import asyncio
import datetime as dt
import math
from pathlib import Path
import pwd
import time
import httpx
from .chat_worker import load,atomic


def number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)

def window(provider,name,data,stamp,source):
    pct=data.get('used_percent');reset=data.get('resets_at');seconds=data.get('seconds')
    pct=float(pct) if number(pct) and 0<=pct<=100 else None
    if isinstance(reset,str):
        try:reset=dt.datetime.fromisoformat(reset.replace('Z','+00:00')).timestamp()
        except ValueError:reset=None
    if not number(reset) or reset<=0:reset=None
    return dict(provider=provider,window=name,label=data.get('label',name),available=pct is not None,used_percent=pct,
                remaining_percent=100-pct if pct is not None else None,resets_at=reset,updated_at=stamp,
                window_seconds=seconds if number(seconds) and seconds>0 else None,source=source,
                description=data.get('label',name))

def codex_windows(data,stamp):
    rows=[];limits=data.get('rate_limit') or {}
    for key in ('primary_window','secondary_window'):
        value=limits.get(key)
        if not isinstance(value,dict):continue
        seconds=value.get('limit_window_seconds')
        label='5 часов' if seconds==18000 else '7 дней' if seconds==604800 else str(round(seconds/3600,2))+' ч' if number(seconds) and seconds>0 else 'Основное окно' if key=='primary_window' else 'Дополнительное окно'
        rows.append(window('codex',key,dict(used_percent=value.get('used_percent'),resets_at=value.get('reset_at'),seconds=seconds,label=label),stamp,'Codex subscription usage'))
    return rows

def read_remote(home):
    stamp=time.time();rows=[]
    with httpx.Client(timeout=12,trust_env=False) as client:
        try:
            auth=load(home/'.codex/auth.json',{}).get('tokens',{})
            response=client.get('https://chatgpt.com/backend-api/wham/usage',headers={'Authorization':'Bearer '+auth['access_token'],'ChatGPT-Account-Id':auth.get('account_id',''),'User-Agent':'codex_cli_rs'})
            response.raise_for_status();rows+=codex_windows(response.json(),stamp)
        except (KeyError,TypeError,ValueError,httpx.HTTPError):pass
        try:
            auth=load(home/'.claude/.credentials.json',{}).get('claudeAiOauth',{})
            response=client.get('https://api.anthropic.com/api/oauth/usage',headers={'Authorization':'Bearer '+auth['accessToken'],'anthropic-beta':'oauth-2025-04-20','User-Agent':'claude-code/ai-hub-chat'})
            response.raise_for_status();data=response.json()
            for key,label,seconds in [('five_hour','5 часов',18000),('seven_day','7 дней',604800),('seven_day_opus','7 дней · Opus',604800),('seven_day_sonnet','7 дней · Sonnet',604800)]:
                value=data.get(key)
                if isinstance(value,dict):rows.append(window('claude',key,dict(used_percent=value.get('utilization'),resets_at=value.get('resets_at'),label=label,seconds=seconds),stamp,'Claude subscription usage'))
        except (KeyError,TypeError,ValueError,httpx.HTTPError):pass
    return rows

async def snapshot(request):
    home=Path(pwd.getpwnam(request.app.state.settings.desktop_user).pw_dir)
    path=request.app.state.chat_jobs_root/'subscription-windows.json'
    cached=load(path,{})
    if time.time()-cached.get('attempted_at',0)>120 and not getattr(request.app.state,'windows_refresh',False):
        request.app.state.windows_refresh=True
        async def refresh():
            try:
                rows=await asyncio.to_thread(read_remote,home)
                from .chat_billing import read_accounts
                accounts=await asyncio.to_thread(read_accounts,home)
                previous=load(path,{}).get('providers',[])
                got={r['provider'] for r in rows}
                rows+=[r for r in previous if r.get('provider') not in got]
                path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                atomic(path,{'attempted_at':time.time(),'providers':rows,'accounts':accounts})
                from .chat_jobs import own
                own(path,request)
            finally:request.app.state.windows_refresh=False
        asyncio.create_task(refresh())
    rows=cached.get('providers',[])
    have={r['provider'] for r in rows}
    legacy=load(home/'.local/state/ai-failover/limits.json',{})
    if 'claude' not in have:
        for key,label,seconds in [('five_hour','5 часов',18000),('seven_day','7 дней',604800)]:
            data=legacy.get('claude',{}).get(key,{})
            rows.append(window('claude',key,dict(used_percent=data.get('pct'),resets_at=data.get('resets'),label=label,seconds=seconds),legacy.get('ts'),'ai-failover subscription usage'))
    if 'codex' not in have:
        rows.append(window('codex','subscription',{'label':'Подписка · окна обновляются'},legacy.get('ts'),'Ожидается ответ провайдера'))
    from .chat_billing import rows as billing_rows
    from .chat_jobs import model_catalog
    models=await asyncio.to_thread(model_catalog,request)
    rows+=billing_rows(home,cached.get('accounts',{}),models)
    if getattr(request,'headers',{}).get('x-webui-user'):
        from .chat_deepseek import owner_billing
        rows+=await owner_billing(request,models)
    return {'providers':rows}
