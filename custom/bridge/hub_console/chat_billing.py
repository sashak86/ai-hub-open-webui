"""Read-only balances from configured provider accounts; no invented USD totals.

DeepSeek: https://api-docs.deepseek.com/api/get-user-balance/
OpenRouter: https://openrouter.ai/docs/api/api-reference/credits/get-credits
No top-up, model call, new credentials or currency conversion occurs here.
"""
from __future__ import annotations
import math
import os
import time
from pathlib import Path
import httpx
from .chat_worker import load


def amount(value):
    if isinstance(value, bool): return None
    try: value=float(value)
    except (TypeError,ValueError): return None
    return value if math.isfinite(value) and value>=0 else None


def credentials(home):
    values={}
    for line in (home/'.config/ai-gateway/keys.env').read_text().splitlines() if (home/'.config/ai-gateway/keys.env').exists() else []:
        if '=' in line and not line.startswith('#'):
            key,value=line.split('=',1);values[key]=value.strip().strip('"').strip("'")
    for key in ('DEEPSEEK_API_KEY','OPENROUTER_API_KEY'):
        if os.environ.get(key):values[key]=os.environ[key]
    auth=load(home/'.local/share/opencode/auth.json',{})
    for provider,key in [('deepseek','DEEPSEEK_API_KEY'),('openrouter','OPENROUTER_API_KEY')]:
        entry=auth.get(provider,{})
        if isinstance(entry,dict) and entry.get('type')=='api' and isinstance(entry.get('key'),str):values.setdefault(key,entry['key'])
    return values


def read_accounts(home):
    keys=credentials(home);out={};stamp=time.time()
    with httpx.Client(timeout=8,trust_env=False,follow_redirects=False) as client:
        for provider,key,url in [('deepseek','DEEPSEEK_API_KEY','https://api.deepseek.com/user/balance'),('openrouter','OPENROUTER_API_KEY','https://openrouter.ai/api/v1/credits')]:
            if not keys.get(key):continue
            try:
                response=client.get(url,headers={'Authorization':'Bearer '+keys[key]})
                response.raise_for_status();data=response.json()
                if provider=='deepseek':
                    balances=[{'currency':v['currency'],'balance_amount':amount(v.get('total_balance')),'spent_amount':None,'spend_scope':'Провайдер не сообщает расход через API баланса'} for v in data.get('balance_infos',[]) if isinstance(v,dict) and v.get('currency') in ('USD','CNY')]
                else:
                    d=data.get('data',{});credits=amount(d.get('total_credits'));spent=amount(d.get('total_usage'))
                    balances=[{'currency':'USD','spent_amount':spent,'balance_amount':round(credits-spent,10) if credits is not None and spent is not None and credits>=spent else None,'spend_scope':'Все списания аккаунта OpenRouter за всё время (включая другие приложения)'}]
                out[provider]=[dict(v,source=provider+' account API',updated_at=stamp,estimated=False) for v in balances]
            except (httpx.HTTPError,ValueError,TypeError,KeyError):pass
    return out


def rows(home,accounts,models):
    configured=load(home/'.config/ai-gateway/models.json',{}).get('models',{})
    result=[]
    for model in models:
        provider=model.get('provider');ident=model.get('id','')
        if provider not in ('gateway','opencode','ollama'):continue
        underlying=configured.get(ident.split('/',1)[-1],{}).get('model','') if provider=='gateway' else ident.removeprefix('opencode/')
        billing_provider='openrouter' if underlying.startswith('openrouter/') else 'deepseek' if underlying.startswith('deepseek/') else underlying.split('/')[0]
        fallback={'currency':'USD','spent_amount':None,'balance_amount':None,'spend_scope':'Расход аккаунта недоступен','source':'Провайдер не сообщил денежные данные','updated_at':None,'estimated':False}
        for account in accounts.get(billing_provider,[]) or [fallback]:
            result.append(dict(account,provider=provider,model_id=ident,kind='local' if provider=='ollama' else 'billing',window='billing',billing_provider=billing_provider,
                description='Локальная модель: баланс токенов провайдера не применяется.' if provider=='ollama' else 'Денежный баланс привязан к аккаунту провайдера. Нет данных не означает нулевой баланс. Бесплатная квота не является долларовым балансом.'))
    return result
