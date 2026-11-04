"""Owner-private connectors. No discovery/import of host credentials.

Write approvals are exact-body, single-use, short-lived and only issued to UI.
Adapters use fixed API origins; arbitrary HTTP/MCP endpoints require explicit setup.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode,urlsplit
import httpx
from fastapi import HTTPException,Request
from .chat_harness import root,locked,payload,text,audit


def tool(name,permission,description,write=False):
    return dict(name=name,permission=permission,description=description,write=write)


def entry(ident,name,category,fields,tools,url,notes='',oauth=None):
    return dict(id=ident,name=name,category=category,auth_methods=['oauth','access_token'] if oauth else ['api_key'],fields=[dict(name=f,label=f.replace('_',' '),secret=f in {'token','password','client_secret','refresh_token'},required=f not in {'refresh_token','client_id','client_secret','redirect_uri'} and not ((oauth or ident in {'http','mcp'}) and f=='token') and not (ident=='instagram' and f=='account_id')) for f in fields],permissions=sorted({t['permission'] for t in tools}),tools=tools,setup_url=url,notes=notes,oauth=oauth)

CATALOG=[
 entry('telegram','Telegram Bot','messenger',['token'],[tool('profile','read','Bot identity'),tool('chat','read','Get chat by chat_id'),tool('send','send','Send message to chat_id',True)],'https://core.telegram.org/bots/features#botfather','Bot API, not personal Telegram history. The user must start the bot or add it to the group.'),
 entry('slack','Slack','messenger',['token'],[tool('profile','read','Token identity'),tool('search','read','Search messages (user token search:read)'),tool('send','send','Send channel message (chat:write)',True)],'https://api.slack.com/apps','Use personal app OAuth token. Search requires a user token.'),
 entry('discord','Discord Bot','messenger',['token'],[tool('profile','read','Bot identity'),tool('messages','read','Read channel_id messages'),tool('send','send','Send to channel_id',True)],'https://discord.com/developers/applications','Invite bot with minimum channel permissions; message content may require privileged intent.'),
 entry('instagram','Instagram Professional','social',['token','account_id','client_id','client_secret','redirect_uri'],[tool('profile','read','Professional account profile'),tool('media','read','Recent account media')],'https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login','Business/Creator account required. Instagram Login does not require a Facebook Page. Personal accounts unsupported. Meta app, exact HTTPS redirect and approved permissions/app review for other users required.',dict(authorize='https://www.instagram.com/oauth/authorize',token='https://api.instagram.com/oauth/access_token',scope='instagram_business_basic')),
 entry('facebook','Facebook Pages','social',['token','account_id'],[tool('profile','read','Page profile'),tool('posts','read','Page posts'),tool('publish','publish','Publish page text post',True)],'https://developers.facebook.com/docs/pages-api','Page access token and pages_read_engagement / pages_manage_posts as applicable. User-owned Pages, not arbitrary personal profiles.'),
 entry('gmail','Gmail','mail',['token','refresh_token','client_id','client_secret','redirect_uri'],[tool('profile','read','Mailbox profile'),tool('search','read','Search messages by Gmail query'),tool('message','read','Read message_id'),tool('draft','draft','Create raw RFC822 base64url draft',True)],'https://developers.google.com/workspace/gmail/api/auth/scopes','gmail.readonly for reading; gmail.compose for drafts. Restricted scopes may require verification.',dict(authorize='https://accounts.google.com/o/oauth2/v2/auth',token='https://oauth2.googleapis.com/token',scope='https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.compose')),
 entry('microsoft','Microsoft Outlook','mail',['token','refresh_token','client_id','client_secret','redirect_uri'],[tool('profile','read','Microsoft user'),tool('search','read','Search mailbox'),tool('draft','draft','Create draft message',True)],'https://learn.microsoft.com/en-us/graph/auth-register-app-v2','Register a Web app in Entra ID. Delegated User.Read Mail.Read Mail.ReadWrite offline_access. This adapter creates drafts, never sends mail.',dict(authorize='https://login.microsoftonline.com/common/oauth2/v2.0/authorize',token='https://login.microsoftonline.com/common/oauth2/v2.0/token',scope='offline_access User.Read Mail.Read Mail.ReadWrite')),
 entry('imap','IMAP + SMTP','mail',['host','username','password','smtp_host'],[tool('search','read','Search INBOX by IMAP criterion'),tool('message','read','Read uid from INBOX'),tool('send','send','Send plain text email via SMTP',True)],'https://docs.python.org/3/library/imaplib.html','TLS only: IMAP 993 and SMTP 465. Use app-specific password if required. Search defaults to UNSEEN; no flags are changed.'),
 entry('http','HTTP JSON API','app',['base_url','token'],[tool('get','read','GET relative path'),tool('post','write','POST relative path JSON',True)],'https://developer.mozilla.org/en-US/docs/Web/HTTP','Explicit HTTPS origin. Relative paths only; redirects disabled. Do not point at metadata/admin services.'),
 entry('mcp','MCP HTTP server','app',['base_url','token'],[tool('list','read','List MCP tools'),tool('call','write','Call remote MCP tool',True)],'https://modelcontextprotocol.io/specification/2025-06-18/basic/transports','JSON HTTP transport only. All remote calls need confirmation because remote tool annotations are untrusted; SSE-only/stdio servers unsupported.'),
]
BY_ID={r['id']:r for r in CATALOG}

EXAMPLES={
 'telegram':{'profile':{},'chat':{'chat_id':'123456'},'send':{'chat_id':'123456','text':'Hello'}},
 'slack':{'profile':{},'search':{'query':'project status'},'send':{'channel':'C12345','text':'Hello'}},
 'discord':{'profile':{},'messages':{'channel_id':'123456'},'send':{'channel_id':'123456','text':'Hello'}},
 'instagram':{'profile':{},'media':{}},
 'facebook':{'profile':{},'posts':{},'publish':{'text':'Hello'}},
 'gmail':{'profile':{},'search':{'query':'is:unread'},'message':{'message_id':'MESSAGE_ID'},'draft':{'raw':'BASE64URL_RFC822'}},
 'microsoft':{'profile':{},'search':{'query':'project'},'draft':{'to':'name@example.com','subject':'Draft','text':'Hello'}},
 'imap':{'search':{'query':'UNSEEN'},'message':{'uid':'123'},'send':{'to':'name@example.com','subject':'Subject','text':'Hello'}},
 'http':{'get':{'path':'/items','params':{}},'post':{'path':'/items','body':{'name':'example'}}},
 'mcp':{'list':{},'call':{'name':'TOOL_NAME','arguments':{}}},
}
for provider in CATALOG:
    for spec in provider['tools']:
        example=EXAMPLES[provider['id']][spec['name']]
        spec['example_arguments']=example
        spec['input_schema']={'type':'object','properties':{k:{'type':'object' if isinstance(v,dict) else 'string'} for k,v in example.items()},'additionalProperties':False}



def folder(request):return root(request)


def public(row):
    if row.get('expires_at',float('inf'))<time.time():row=dict(row,status='expired')
    return {k:v for k,v in row.items() if k not in {'credentials','approvals','oauth'}} | {'credential_fields':sorted(row.get('credentials',{})),'credentials':{k:'••••••••' for k in row.get('credentials',{})}}


def get_row(state,ident):
    row=state.get('connectors',{}).get(ident)
    if not row:raise HTTPException(404,'Подключение не найдено')
    return row


def origin(value):
    p=urlsplit(text(value,1000))
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.query or p.fragment:raise HTTPException(422,'Укажите HTTPS URL без логина, query и fragment')
    if p.hostname.lower() in {'169.254.169.254','metadata.google.internal','localhost','127.0.0.1','::1'}:raise HTTPException(422,'Служебный адрес запрещён')
    return value.rstrip('/')


def relative(base,path):
    path=text(path,1000)
    if not path.startswith('/') or path.startswith('//') or '\\' in path or re.search(r'(?i)%2f|%5c|%2e',path) or any(x in {'.','..'} for x in path.split('/')) or '?' in path or '#' in path:raise HTTPException(422,'Нужен относительный API путь без query и переходов')
    return base.rstrip('/')+path


async def http(method,url,**kwargs):
    async with httpx.AsyncClient(timeout=25,follow_redirects=False,trust_env=False) as client:
        async with client.stream(method,url,**kwargs) as response:
            if response.status_code>=300:raise HTTPException(502,'API отклонил запрос (HTTP '+str(response.status_code)+')')
            data=b''
            async for chunk in response.aiter_bytes():
                data+=chunk
                if len(data)>2_000_000:raise HTTPException(502,'Ответ API слишком большой')
    try:result=json.loads(data)
    except ValueError:raise HTTPException(502,'API вернул не JSON') from None
    if isinstance(result,dict) and (result.get('ok') is False or 'error' in result):raise HTTPException(502,'API отклонил запрос; проверьте токен и scopes')
    return result


def arg(args,key,limit=1000):return text(args.get(key,''),limit)


def ident(args,key):
    value=arg(args,key,200)
    if not re.fullmatch(r'[\w@.:+-]+',value):raise HTTPException(422,'Некорректный '+key)
    return value


async def adapter(row,name,args):
    c=row['credentials'];p=row['provider'];token=c.get('token','');headers={'Authorization':'Bearer '+token} if token else {};params={}
    method='GET';body=None
    if p=='imap':
        try:return sanitize(await asyncio.to_thread(mail_adapter,row,name,args),c)
        except HTTPException:raise
        except Exception:raise HTTPException(502,'Mail TLS/auth запрос не выполнен') from None
    if p=='mcp':return sanitize(await mcp_adapter(row,name,args),c)
    if p=='telegram':
        operation={'profile':'getMe','chat':'getChat','send':'sendMessage'}[name]
        url='https://api.telegram.org/bot'+token+'/'+operation;headers={}
        if name!='profile':params={'chat_id':ident(args,'chat_id')}
        if name=='send':method='POST';body={**params,'text':arg(args,'text',4096)};params={}
    elif p=='slack':
        url='https://slack.com/api/'+{'profile':'auth.test','search':'search.messages','send':'chat.postMessage'}[name]
        if name=='search':params={'query':arg(args,'query'),'count':20}
        if name=='send':method='POST';body={'channel':ident(args,'channel'),'text':arg(args,'text',12000)}
    elif p=='discord':
        headers={'Authorization':'Bot '+token};url='https://discord.com/api/v10'+('/users/@me' if name=='profile' else '/channels/'+ident(args,'channel_id')+'/messages')
        if name=='messages':params={'limit':20}
        if name=='send':method='POST';body={'content':arg(args,'text',2000),'allowed_mentions':{'parse':[]}}
    elif p in {'instagram','facebook'}:
        account=c.get('account_id') or ('me' if p=='instagram' else '')
        if not (re.fullmatch(r'\d+',account) or p=='instagram' and account=='me'):raise HTTPException(422,'account_id должен быть числом')
        url=('https://graph.instagram.com/v25.0/' if p=='instagram' else 'https://graph.facebook.com/v25.0/')+account
        if name=='profile':params={'fields':'id,username,account_type' if p=='instagram' else 'id,name'}
        elif name in {'media','posts'}:url+='/'+name;params={'limit':20}
        elif name=='publish':url+='/feed';method='POST';body={'message':arg(args,'text',12000)}
    elif p=='gmail':
        url='https://gmail.googleapis.com/gmail/v1/users/me/'+{'profile':'profile','search':'messages','message':'messages/'+ident(args,'message_id') if name=='message' else '', 'draft':'drafts'}[name]
        if name=='search':params={'q':arg(args,'query'),'maxResults':20}
        if name=='message':params={'format':'full'}
        if name=='draft':
            raw=arg(args,'raw',100000)
            if not re.fullmatch(r'[A-Za-z0-9_=-]+',raw):raise HTTPException(422,'raw: base64url RFC822')
            method='POST';body={'message':{'raw':raw}}
    elif p=='microsoft':
        url='https://graph.microsoft.com/v1.0/me'+('' if name=='profile' else '/messages')
        if name=='search':params={'$search':'"'+arg(args,'query').replace('"','')+'"','$top':20}
        if name=='draft':method='POST';body={'subject':arg(args,'subject',1000),'body':{'contentType':'Text','content':arg(args,'text',50000)},'toRecipients':[{'emailAddress':{'address':arg(args,'to',320)}}]}
    elif p=='http':
        url=relative(c['base_url'],arg(args,'path'));params=args.get('params',{})
        if not isinstance(params,dict):raise HTTPException(422,'params должен быть объектом')
        if name=='post':method='POST';body=args.get('body',{})
    elif p=='mcp':
        url=c['base_url'];method='POST';headers['Accept']='application/json, text/event-stream'
        body={'jsonrpc':'2.0','id':uuid.uuid4().hex,'method':'tools/list' if name=='list' else 'tools/call','params':{} if name=='list' else {'name':arg(args,'name'),'arguments':args.get('arguments',{})}}
    result=await http(method,url,headers=headers,params=params,**({'json':body} if body is not None else {}))
    return sanitize(result,c)


def sanitize(result,credentials):
    secrets_to_remove=[v for k,v in credentials.items() if k in {'token','password','client_secret','refresh_token'} and isinstance(v,str) and v]
    def clean(value):
        if isinstance(value,str):
            for secret in secrets_to_remove:value=value.replace(secret,'[REDACTED]')
            return value
        if isinstance(value,list):return [clean(x) for x in value]
        if isinstance(value,dict):return {clean(str(k)):clean(v) for k,v in value.items()}
        return value
    return clean(result)


def specification(row,name):
    spec=next((t for t in BY_ID[row['provider']]['tools'] if t['name']==name),None)
    if not spec:raise HTTPException(404,'Инструмент не найден')
    if spec['permission'] not in row['permissions']:raise HTTPException(403,'Разрешение инструмента не выдано')
    return spec


async def invoke(request,ident,body,interactive=True):
    name=text(body.get('tool',''),100);args=body.get('arguments',{})
    if not isinstance(args,dict) or len(json.dumps(args))>150000:raise HTTPException(422,'Некорректные arguments')
    digest=hashlib.sha256(json.dumps([name,args],sort_keys=True).encode()).hexdigest()
    with locked(folder(request)) as state:
        row=get_row(state,ident);spec=specification(row,name)
        if spec['write']:
            approvals=row.setdefault('approvals',{});now=time.time()
            row['approvals']=approvals={k:v for k,v in approvals.items() if v['expires']>now}
            proof=body.get('confirmation_token','')
            if not proof:
                if not interactive:raise HTTPException(409,'Требуется подтверждение в интерфейсе подключений')
                proof=secrets.token_urlsafe(32);approvals[hashlib.sha256(proof.encode()).hexdigest()]={'digest':digest,'expires':now+180}
                return dict(confirmation_required=True,confirmation_token=proof,summary={'provider':row['provider'],'tool':name,'arguments':args},expires_in=180)
            grant=approvals.pop(hashlib.sha256(str(proof).encode()).hexdigest(),None)
            if not grant or grant['digest']!=digest or grant['expires']<now:raise HTTPException(403,'Подтверждение истекло, использовано или не совпадает')
        row=json.loads(json.dumps(row))
    audit(folder(request),'connector_tool_started',connector=ident,tool=name,write=spec['write'])
    try:result=await adapter(row,name,args)
    except (httpx.HTTPError,KeyError,ValueError):
        audit(folder(request),'connector_tool_failed',connector=ident,tool=name)
        raise HTTPException(502,'Не удалось выполнить API запрос; проверьте настройку') from None
    audit(folder(request),'connector_tool_completed',connector=ident,tool=name,write=spec['write'])
    return dict(result=result,connector_id=ident,tool=name)


def install(app,owner_setup):
    from .chat_jobs import owner_id
    def auth(request):owner_setup(request);owner_id(request)
    @app.get('/v1/connectors/catalog')
    async def catalog(request:Request):auth(request);return {'data':CATALOG}
    @app.get('/v1/connectors')
    async def listing(request:Request):
        auth(request)
        with locked(folder(request)) as state:return {'data':[public(r) for r in state.get('connectors',{}).values()]}
    @app.post('/v1/connectors')
    async def create(request:Request):
        auth(request);body=await payload(request);provider=body.get('provider');spec=BY_ID.get(provider)
        if not spec:raise HTTPException(422,'Неизвестный провайдер')
        credentials=body.get('credentials',{});permissions=body.get('permissions',['read'])
        if not isinstance(credentials,dict) or not isinstance(permissions,list) or any(p not in spec['permissions'] for p in permissions):raise HTTPException(422,'Некорректные настройки')
        fields={f['name'] for f in spec['fields']};credentials={k:text(v,12000) for k,v in credentials.items() if k in fields and v}
        for f in spec['fields']:
            if f['required'] and not credentials.get(f['name']) and not (f['name']=='token' and spec.get('oauth')):raise HTTPException(422,'Нужно поле '+f['name'])
        if spec.get('oauth') and not credentials.get('token') and not all(credentials.get(k) for k in ('client_id','client_secret','redirect_uri')):raise HTTPException(422,'Нужен token или client_id/client_secret/redirect_uri')
        if provider in {'http','mcp'}:credentials['base_url']=origin(credentials['base_url'])
        if provider=='imap':
            for k in ('host','smtp_host'):
                if not re.fullmatch(r'[A-Za-z0-9.-]+',credentials[k]) or credentials[k].lower() in {'localhost','169.254.169.254'}:raise HTTPException(422,'Некорректный mail host')
        row=dict(id=uuid.uuid4().hex,provider=provider,label=text(body.get('label',spec['name']),100),credentials=credentials,permissions=permissions,status='configured' if credentials.get('token') or provider in {'http','mcp','imap'} else 'setup_required',created_at=time.time())
        with locked(folder(request)) as state:state.setdefault('connectors',{})[row['id']]=row
        audit(folder(request),'connector_created',connector=row['id'],provider=provider)
        return public(row)
    @app.delete('/v1/connectors/{ident}')
    async def remove(request:Request,ident:str):
        auth(request)
        with locked(folder(request)) as state:get_row(state,ident);del state['connectors'][ident]
        audit(folder(request),'connector_removed',connector=ident);return {'ok':True,'remote_revocation_required':True}
    @app.get('/v1/connectors/{ident}/tools')
    async def tools(request:Request,ident:str):
        auth(request)
        with locked(folder(request)) as state:
            row=get_row(state,ident);return {'data':[t for t in BY_ID[row['provider']]['tools'] if t['permission'] in row['permissions']]}
    @app.post('/v1/connectors/{ident}/invoke')
    async def call(request:Request,ident:str):auth(request);return await invoke(request,ident,await payload(request))
    @app.post('/v1/connectors/{ident}/health')
    async def health(request:Request,ident:str):
        auth(request)
        with locked(folder(request)) as state:row=json.loads(json.dumps(get_row(state,ident)))
        name='search' if row['provider']=='imap' else 'get' if row['provider']=='http' else 'list' if row['provider']=='mcp' else 'profile'
        error=None
        try:await adapter(row,name,{'path':'/'})
        except (HTTPException,httpx.HTTPError,KeyError,ValueError):error='Проверка API не прошла: проверьте токен, scopes и доступность'
        with locked(folder(request)) as state:
            current=get_row(state,ident);current.update(status='error' if error else 'connected',last_checked_at=time.time(),error=error);result=public(current)
        audit(folder(request),'connector_health',connector=ident,ok=not error);return result
    @app.post('/v1/connectors/{ident}/oauth/start')
    async def oauth_start(request:Request,ident:str):
        auth(request)
        with locked(folder(request)) as state:
            row=get_row(state,ident);spec=BY_ID[row['provider']].get('oauth');c=row['credentials']
            if not spec:raise HTTPException(422,'OAuth не поддерживается этим адаптером')
            if not all(c.get(k) for k in ('client_id','client_secret','redirect_uri')):raise HTTPException(422,'Зарегистрируйте OAuth app и сохраните client_id, client_secret, redirect_uri')
            origin(c['redirect_uri']);redirect=c['redirect_uri'];nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48);row['oauth']={'state':hashlib.sha256(nonce.encode()).hexdigest(),'expires':time.time()+600,'verifier':verifier}
            scope=spec['scope']
            if row['provider']=='gmail' and 'draft' not in row['permissions']:scope='https://www.googleapis.com/auth/gmail.readonly'
            if row['provider']=='microsoft' and 'draft' not in row['permissions']:scope='offline_access User.Read Mail.Read'
            query=dict(client_id=c['client_id'],redirect_uri=redirect,response_type='code',scope=scope,state=nonce)
            if row['provider']!='instagram':query.update(code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),code_challenge_method='S256')
            if row['provider']=='gmail':query.update(access_type='offline',prompt='consent')
            return {'authorization_url':spec['authorize']+'?'+urlencode(query),'redirect_uri':redirect,'expires_in':600}
    @app.post('/v1/connectors/{ident}/oauth/callback')
    async def oauth_callback(request:Request,ident:str):
        auth(request);body=await payload(request)
        with locked(folder(request)) as state:
            row=get_row(state,ident);flow=row.get('oauth',{});nonce=text(body.get('state',''),200)
            if flow.get('expires',0)<time.time() or not secrets.compare_digest(flow.get('state',''),hashlib.sha256(nonce.encode()).hexdigest()):raise HTTPException(403,'Недействительный OAuth state')
            del row['oauth'];row=json.loads(json.dumps(row))
        c=row['credentials'];spec=BY_ID[row['provider']]['oauth']
        try:tokens=await http('POST',spec['token'],data=dict(client_id=c['client_id'],client_secret=c['client_secret'],redirect_uri=c['redirect_uri'],grant_type='authorization_code',code=text(body.get('code',''),6000),**({'code_verifier':flow['verifier']} if row['provider']!='instagram' else {})))
        except httpx.HTTPError:raise HTTPException(502,'OAuth token endpoint недоступен') from None
        if not isinstance(tokens.get('access_token'),str):raise HTTPException(502,'OAuth не вернул access_token')
        with locked(folder(request)) as state:
            row=get_row(state,ident);row['credentials']['token']=tokens['access_token']
            if tokens.get('refresh_token'):row['credentials']['refresh_token']=tokens['refresh_token']
            if row['provider']=='instagram' and str(tokens.get('user_id','')).isdigit():row['credentials']['account_id']=str(tokens['user_id'])
            row.update(status='configured',expires_at=time.time()+int(tokens.get('expires_in',3600)));result=public(row)
        audit(folder(request),'connector_oauth_completed',connector=ident);return result
    @app.post('/v1/connectors/{ident}/refresh')
    async def refresh(request:Request,ident:str):
        auth(request)
        with locked(folder(request)) as state:row=json.loads(json.dumps(get_row(state,ident)))
        c=row['credentials'];spec=BY_ID[row['provider']].get('oauth')
        if not spec or not c.get('refresh_token'):raise HTTPException(422,'Нет OAuth refresh token; переподключите аккаунт')
        try:tokens=await http('POST',spec['token'],data={k:c[k] for k in ('client_id','client_secret','refresh_token')}|{'grant_type':'refresh_token'})
        except (httpx.HTTPError,KeyError):raise HTTPException(502,'Обновить токен не удалось') from None
        if not isinstance(tokens.get('access_token'),str):raise HTTPException(502,'Не получен access_token')
        with locked(folder(request)) as state:
            row=get_row(state,ident);row['credentials']['token']=tokens['access_token']
            if tokens.get('refresh_token'):row['credentials']['refresh_token']=tokens['refresh_token']
            row.update(status='configured',expires_at=time.time()+int(tokens.get('expires_in',3600)));result=public(row)
        audit(folder(request),'connector_refreshed',connector=ident);return result


def native_extension(request):
    """Only private owner path is handed to CLI; tokens stay in private store."""
    import sys
    with locked(folder(request)) as state:
        if not any(r.get('status')=='connected' for r in state.get('connectors',{}).values()):return []
    return [dict(id='connectors00000000000000000000000',name='Connected services (read only)',kind='mcp',enabled=True,providers=['codex','claude'],config=dict(transport='stdio',command=sys.executable,args=['-m','hub_console.chat_connector_mcp','--root',str(folder(request))],env={'PYTHONPATH':str(Path(__file__).resolve().parents[1])}))]


def mail_adapter(row,name,args):
    import imaplib
    import smtplib
    import ssl
    from email.message import EmailMessage
    c=row['credentials']
    if name=='send':
        message=EmailMessage();message['From']=c['username'];message['To']=arg(args,'to',320);message['Subject']=arg(args,'subject',1000);message.set_content(arg(args,'text',100000))
        with smtplib.SMTP_SSL(c['smtp_host'],465,timeout=25,context=ssl.create_default_context()) as server:
            server.login(c['username'],c['password']);server.send_message(message)
        return {'accepted':True}
    with imaplib.IMAP4_SSL(c['host'],993,ssl_context=ssl.create_default_context(),timeout=25) as server:
        server.login(c['username'],c['password']);server.select('INBOX',readonly=True)
        if name=='search':
            query=arg(args,'query',1000) or 'UNSEEN'
            if '\r' in query or '\n' in query:raise HTTPException(422,'Некорректный IMAP query')
            status,data=server.uid('search',None,query)
            if status!='OK':raise HTTPException(502,'IMAP search отклонён')
            return {'uids':data[0].decode().split()[-50:]}
        uid=ident(args,'uid')
        if not uid.isdigit():raise HTTPException(422,'uid должен быть числом')
        status,data=server.uid('fetch',uid,'(BODY.PEEK[])')
        if status!='OK':raise HTTPException(502,'IMAP fetch отклонён')
        raw=next((x[1] for x in data if isinstance(x,tuple)),b'')
        return {'raw_rfc822':raw[:200000].decode('utf-8','replace'),'truncated':len(raw)>200000}


async def mcp_adapter(row,name,args):
    """Initialize a fresh Streamable HTTP session, then perform one bounded call."""
    headers={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'}
    if row['credentials'].get('token'):headers['Authorization']='Bearer '+row['credentials']['token']
    async with httpx.AsyncClient(timeout=25,follow_redirects=False,trust_env=False) as client:
        async def send(message):
            async with client.stream('POST',row['credentials']['base_url'],headers=headers,json=message) as response:
                if response.status_code>=300:raise HTTPException(502,'MCP HTTP '+str(response.status_code))
                if response.headers.get('mcp-session-id'):headers['Mcp-Session-Id']=response.headers['mcp-session-id']
                data=b''
                async for chunk in response.aiter_bytes():
                    data+=chunk
                    if len(data)>2_000_000:raise HTTPException(502,'MCP ответ слишком большой')
                if not data:return {}
                try:return json.loads(data)
                except ValueError:raise HTTPException(502,'Этот MCP endpoint требует SSE; используйте JSON HTTP endpoint') from None
        initialized=await send({'jsonrpc':'2.0','id':'init','method':'initialize','params':{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'workbench','version':'1'}}})
        if initialized.get('error'):raise HTTPException(502,'MCP initialize отклонён')
        await send({'jsonrpc':'2.0','method':'notifications/initialized'})
        result=await send({'jsonrpc':'2.0','id':'call','method':'tools/list' if name=='list' else 'tools/call','params':{} if name=='list' else {'name':arg(args,'name'),'arguments':args.get('arguments',{})}})
        if result.get('error'):raise HTTPException(502,'MCP tool отклонён')
        return result.get('result',result)
