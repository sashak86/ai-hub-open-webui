"""Private, owner-scoped chat extension registry and per-turn native configuration.

Saving never executes a command or contacts a server. Only explicit test/runtime
starts enabled MCP servers. Secrets stay in 0600 files and never round-trip to UI.
"""
from __future__ import annotations
import asyncio
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import time
import uuid
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException, Request
from .chat_worker import atomic, load

MASK='••••'
PROVIDERS=['codex','claude']
NAME=re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
MAX_REGISTRY=512_000

def invalid(message='Некорректное расширение'):
    raise HTTPException(422,message)

def string(value,limit=4000):
    if not isinstance(value,str) or len(value)>limit or '\x00' in value:invalid()
    return value

def secret_map(value,old):
    if not isinstance(value,dict) or len(value)>32:invalid()
    result={}
    for key,val in value.items():
        string(key,120);string(val,8000)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*',key):invalid('Некорректное имя параметра')
        if val==MASK:
            if key not in old:invalid('Введите секрет заново')
            val=old[key]
        result[key]=val
    return result

def mcp_config(value,old=None):
    if not isinstance(value,dict):invalid()
    old=old or {};transport=value.get('transport','http')
    if transport not in {'http','stdio'}:invalid('Поддерживаются Streamable HTTP и stdio')
    result={'transport':transport}
    if transport=='http':
        url=string(value.get('url',''),2000)
        if url==MASK:url=old.get('url','')
        parts=urlsplit(url)
        if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.fragment:invalid('Нужен HTTP(S) URL без встроенного пароля')
        result.update(url=url,headers=secret_map(value.get('headers',{}),old.get('headers',{})))
    else:
        command=string(value.get('command',''),1000)
        if not command or any(c.isspace() for c in command):invalid('Команда и аргументы вводятся отдельно')
        args=value.get('args',[])
        if not isinstance(args,list) or len(args)>64:invalid()
        result.update(command=command,args=[string(x,4000) for x in args],env=secret_map(value.get('env',{}),old.get('env',{})))
    return result

def skill(value):
    if not isinstance(value,dict):invalid()
    content=string(value.get('content',''),64_000)
    if not content.strip():invalid('Добавьте содержимое SKILL.md')
    return {'content':content}

def validate(value,old=None):
    if not isinstance(value,dict):invalid()
    old=old or {};kind=value.get('kind')
    if kind not in {'mcp','skill','plugin'}:invalid()
    ident=value.get('id') or uuid.uuid4().hex
    if not re.fullmatch('[a-f0-9]{32}',str(ident)):invalid()
    name=string(value.get('name',''),100).strip()
    if not name:invalid('Укажите название')
    enabled=value.get('enabled',True)
    if not isinstance(enabled,bool):invalid()
    providers=value.get('providers',PROVIDERS)
    if not isinstance(providers,list) or not providers or any(x not in PROVIDERS for x in providers):invalid('Расширения поддерживают Codex и Claude')
    cfg=value.get('config',{});before=old.get('config',{})
    if kind=='mcp':cfg=mcp_config(cfg,before if old.get('kind')==kind else {})
    elif kind=='skill':cfg=skill(cfg)
    else:
        if not isinstance(cfg,dict):invalid()
        skills=cfg.get('skills',[]);servers=cfg.get('mcp',{})
        if not isinstance(skills,list) or len(skills)>20 or not isinstance(servers,dict) or len(servers)>20:invalid()
        if not skills and not servers:invalid('Плагин должен содержать skills или MCP')
        cleaned=[]
        for entry in skills:
            if not isinstance(entry,dict) or not NAME.fullmatch(str(entry.get('name',''))):invalid('Имя skill: латиница, цифры, _ и -')
            cleaned.append({'name':entry['name'],**skill(entry)})
        if len({x['name'] for x in cleaned})!=len(cleaned):invalid('Имена skills должны различаться')
        mapped={}
        for server_name,entry in servers.items():
            if not NAME.fullmatch(server_name):invalid('Некорректное имя MCP')
            mapped[server_name]=mcp_config(entry,before.get('mcp',{}).get(server_name,{}))
        cfg={'skills':cleaned,'mcp':mapped}
    return dict(id=ident,name=name,kind=kind,enabled=enabled,providers=list(dict.fromkeys(providers)),config=cfg,updated_at=time.time())

def redact(row):
    row=copy.deepcopy(row)
    configs=[row['config']] if row['kind']=='mcp' else list(row['config'].get('mcp',{}).values()) if row['kind']=='plugin' else []
    for cfg in configs:
        if cfg.get('url') and urlsplit(cfg['url']).query:cfg['url']=MASK
        for key in ('headers','env'):
            if key in cfg:cfg[key]={k:MASK for k in cfg[key]}
    return row

def location(request):
    from .chat_jobs import directory,owner_id,mkdir
    folder=directory(request)/'extensions';mkdir(folder,request)
    owner=owner_id(request)
    return folder/(owner+'.json')

def snapshot(request):
    return [r for r in load(location(request),[]) if r.get('enabled')]

def digest(rows):
    return hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def flatten(rows,provider):
    servers={};skills=[]
    for row in rows:
        if provider not in row.get('providers',[]):continue
        key='hub_'+row['id'][:12]
        if row['kind']=='mcp':servers[key]=row['config']
        elif row['kind']=='skill':skills.append((key,row['name'],row['config']['content']))
        else:
            servers.update({key+'_'+k:v for k,v in row['config'].get('mcp',{}).items()})
            skills.extend((key+'_'+s['name'],s['name'],s['content']) for s in row['config'].get('skills',[]))
    return servers,skills

def native_flags(desc,folder):
    provider=desc['model'].split('/')[0];rows=desc.get('extensions',[])
    if not rows or provider not in PROVIDERS:return [],''
    servers,skills=flatten(rows,provider);flags=[]
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    if provider=='codex':
        for name,cfg in servers.items():
            native={k:v for k,v in cfg.items() if k!='transport'}
            if cfg['transport']=='http':native['http_headers']=native.pop('headers',{})
            # TOML inline table encoded without a dependency; JSON strings are TOML compatible.
            def toml(v):
                if isinstance(v,dict):return '{'+','.join(json.dumps(k)+'='+toml(x) for k,x in v.items())+'}'
                if isinstance(v,list):return '['+','.join(toml(x) for x in v)+']'
                if isinstance(v,bool):return str(v).lower()
                return json.dumps(v,ensure_ascii=False)
            flags+=['mcp_servers.'+name+'='+toml(native)]
        if flags:
            # Codex profiles layer a private TOML file without putting secrets in argv.
            home=Path(desc.get('native_env',{}).get('CODEX_HOME',os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
            name='hub-chat-'+desc['id']
            profile=home/(name+'.config.toml')
            fd=os.open(profile,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'w') as handle:handle.write('\n'.join(flags)+'\n')
            flags=['-p',name]
    else:
        native={}
        for name,cfg in servers.items():
            native[name]={k:v for k,v in cfg.items() if k!='transport'}
            native[name]['type']=cfg['transport']
        config=folder/'mcp.json';atomic(config,{'mcpServers':native})
        if native:flags+=['--mcp-config',str(config),'--allowedTools',','.join('mcp__'+name+'__*' for name in native)]
    # Explicit enabled skill instructions are injected only into this conversation.
    # Save SKILL.md too so agents can reference it, with no global installs/hooks.
    prompt=''
    for key,name,content in skills:
        path=folder/key;path.mkdir(mode=0o700,exist_ok=True)
        file=path/'SKILL.md';file.write_text(content);os.chmod(file,0o600)
        prompt+='\n\nПодключённый пользователем навык '+name+' ('+str(file)+'):\n'+content
    return flags,prompt

async def probe(cfg,username):
    """Initialize and list tools only, never tools/call. Bounded, no raw errors."""
    from .system import as_user_kwargs
    async def exchange(send,receive):
        await send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'AI Hub connection test','version':'1'}}})
        init=await receive(1)
        if 'error' in init:raise ValueError()
        await send({'jsonrpc':'2.0','method':'notifications/initialized'})
        await send({'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}})
        response=await receive(2)
        if 'error' in response:raise ValueError()
        tools=response.get('result',{}).get('tools',[])
        if not isinstance(tools,list):raise ValueError()
        return {'ok':True,'tool_count':len(tools),'tools':[{'name':str(t.get('name',''))[:120]} for t in tools[:100] if isinstance(t,dict)]}
    async with asyncio.timeout(20):
        if cfg['transport']=='http':
            async with httpx.AsyncClient(headers=cfg.get('headers',{}),timeout=12,trust_env=False,follow_redirects=False) as client:
                responses={};session=None
                async def send(row):
                    nonlocal session
                    headers={'Accept':'application/json, text/event-stream'}
                    if session:headers.update({'Mcp-Session-Id':session,'MCP-Protocol-Version':'2025-03-26'})
                    async with client.stream('POST',cfg['url'],json=row,headers=headers) as response:
                        response.raise_for_status();session=response.headers.get('mcp-session-id',session)
                        if 'id' not in row:return
                        buffer=b'';total=0
                        sse='text/event-stream' in response.headers.get('content-type','')
                        async for chunk in response.aiter_bytes():
                            total+=len(chunk)
                            if total>2_000_000:raise ValueError()
                            buffer+=chunk
                            if sse:
                                while b'\n' in buffer:
                                    line,buffer=buffer.split(b'\n',1)
                                    if line.startswith(b'data:'):
                                        value=json.loads(line[5:].strip())
                                        if value.get('id')==row['id']:
                                            responses[row['id']]=value;return
                        if sse:raise ValueError()
                        responses[row['id']]=json.loads(buffer)
                async def receive(ident):return responses[ident]
                try:return await exchange(send,receive)
                finally:
                    if session:
                        try:await client.delete(cfg['url'],headers={'Mcp-Session-Id':session})
                        except httpx.HTTPError:pass
        kw=as_user_kwargs(username);kw['env']={**kw.get('env',os.environ),**cfg.get('env',{})}
        proc=await asyncio.create_subprocess_exec(cfg['command'],*cfg.get('args',[]),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,start_new_session=True,limit=2_000_000,**kw)
        async def send(row):proc.stdin.write((json.dumps(row)+'\n').encode());await proc.stdin.drain()
        async def receive(ident):
            for _ in range(100):
                line=await proc.stdout.readline()
                if not line:raise ValueError()
                row=json.loads(line)
                if row.get('id')==ident:return row
            raise ValueError()
        try:return await exchange(send,receive)
        finally:
            try:os.killpg(proc.pid,signal.SIGTERM)
            except ProcessLookupError:pass
            try:await asyncio.wait_for(proc.wait(),2)
            except asyncio.TimeoutError:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await proc.wait()

def install(app,owner_setup):
    from .chat_jobs import owner_id,own
    def auth(request):owner_setup(request);owner_id(request)
    @app.get('/v1/chat-extensions')
    async def listing(request:Request):
        auth(request)
        return {'items':[redact(r) for r in load(location(request),[])], 'capabilities':{'providers':PROVIDERS,'mcp_transports':['http','stdio'],'plugins':'skill_mcp_bundle','skill_format':'SKILL.md'}}
    @app.post('/v1/chat-extensions')
    async def save(request:Request):
        auth(request)
        if int(request.headers.get('content-length','0'))>MAX_REGISTRY:raise HTTPException(413,'Слишком большой пакет')
        raw=await request.body()
        if len(raw)>MAX_REGISTRY:raise HTTPException(413,'Слишком большой пакет')
        try:body=json.loads(raw)
        except ValueError:invalid()
        path=location(request)
        lock=path.with_suffix('.lock')
        with lock.open('a') as handle:
            os.chmod(lock,0o600);own(lock,request);fcntl.flock(handle,fcntl.LOCK_EX)
            rows=load(path,[]);old=next((r for r in rows if isinstance(body,dict) and r['id']==body.get('id')),None)
            if isinstance(body,dict) and body.get('id') and old is None:raise HTTPException(404,'Расширение не найдено')
            row=validate(body,old);rows=[r for r in rows if r['id']!=row['id']]+[row]
            if len(rows)>40 or len(json.dumps(rows))>MAX_REGISTRY:raise HTTPException(413,'Слишком много расширений')
            atomic(path,rows);own(path,request)
        return redact(row)
    @app.delete('/v1/chat-extensions/{ident}')
    async def delete(request:Request,ident:str):
        auth(request);path=location(request)
        if not re.fullmatch('[a-f0-9]{32}',ident):raise HTTPException(404,'Расширение не найдено')
        with path.with_suffix('.lock').open('a') as handle:
            os.chmod(handle.name,0o600);own(handle.name,request);fcntl.flock(handle,fcntl.LOCK_EX)
            rows=load(path,[])
            if not any(r['id']==ident for r in rows):raise HTTPException(404,'Расширение не найдено')
            atomic(path,[r for r in rows if r['id']!=ident]);own(path,request)
        return {'ok':True}
    @app.post('/v1/chat-extensions/{ident}/test')
    async def test(request:Request,ident:str):
        auth(request);row=next((r for r in load(location(request),[]) if r['id']==ident),None)
        if row is None:raise HTTPException(404,'Расширение не найдено')
        if row['kind']=='skill':return {'ok':True,'description':'SKILL.md сохранён и будет передан выбранным агентам в следующем запросе.'}
        configs=[row['config']] if row['kind']=='mcp' else list(row['config'].get('mcp',{}).values())
        try:
            results=[]
            semaphore=asyncio.Semaphore(2)
            async def one(cfg):
                async with semaphore:return await probe(cfg,request.app.state.settings.desktop_user)
            async with asyncio.timeout(25):
                results=await asyncio.gather(*(one(cfg) for cfg in configs))
            return {'ok':True,'connections':results,'tool_count':sum(r['tool_count'] for r in results),'description':'MCP initialize/tools/list выполнены. Инструменты не вызывались.'}
        except (Exception,asyncio.TimeoutError):
            # Never expose command arguments, authorization headers, raw response bodies.
            return {'ok':False,'description':'Подключение не подтверждено. Проверьте адрес, команду и авторизацию; проверка ограничена 20 сек на сервер.'}
