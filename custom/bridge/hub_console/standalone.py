"""Standalone private bridge + authenticated same-origin Open WebUI frontend proxy.

Bind only to loopback; put your existing TLS reverse proxy in front for LAN access.
Run as the account that owns Codex/Claude auth, not as root.
"""
from __future__ import annotations
import asyncio,contextlib,hashlib,hmac,json,os,re
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI,Request,HTTPException,WebSocket
from fastapi.responses import StreamingResponse,Response
from starlette.background import BackgroundTask
from starlette.staticfiles import StaticFiles
from websockets.asyncio.client import connect
from .config import load_settings
from . import chat_jobs,chat_workspace

HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade','host','x-webui-user'}
def valid_origin(origin,expected):
    if not origin:return True
    if origin.rstrip('/')==expected.rstrip('/'):return True
    parsed=urlsplit(origin)
    if parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.path not in {'','/'}:return False
    allowed={v.strip().rstrip('/') for v in os.environ.get('HUB_PUBLIC_ORIGINS','').split(',') if v.strip()}
    return origin.rstrip('/') in allowed or parsed.netloc in allowed

class BodyTooLarge(Exception):pass
class RequestBodyLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        path=scope.get('path','');limit=46*1024*1024 if path=='/v1/jobs' else 33*1024*1024 if path.startswith(('/v1/chat-workspace','/ai-hub/api/workspace')) else 512*1024
        if not path.startswith(('/v1/','/ai-hub/api/')):return await self.app(scope,receive,send)
        size=0
        async def limited():
            nonlocal size
            message=await receive();size+=len(message.get('body',b''))
            if size>limit:raise BodyTooLarge()
            return message
        try:await self.app(scope,limited,send)
        except BodyTooLarge:
            response=Response('Request body too large',status_code=413)
            await response(scope,receive,send)


async def register_owner_models(client,authorization,user,uid_hash,provider,model_ids,upstream):
    """Native presets inherit Pipe transport, with owner-only read grants.

    The real model id is an extra Pipe param and is independently owner-checked
    by chat_jobs. Shared manifold enumeration never aggregates private accounts.
    """
    headers={'Authorization':authorization}
    available=await client.get(upstream+'/api/models',headers=headers,timeout=20)
    available.raise_for_status()
    base=next((r['id'] for r in available.json().get('data',[]) if r.get('id','').startswith('ai_hub.')),None)
    if not base:raise ValueError('pipe_missing')
    records=[]
    for name in model_ids[:50]:
        if not isinstance(name,str) or len(name)>180:continue
        ident='ai_hub.owner-'+uid_hash[:16]+'-'+hashlib.sha256((provider+'/'+name).encode()).hexdigest()[:16]
        current=await client.get(upstream+'/api/v1/models/model',params={'id':ident},headers=headers,timeout=15)
        exists=current.status_code==200
        payload={'id':ident,'name':name+' · '+provider,'base_model_id':base,
            'meta':{'description':'Подключённая API-модель: текстовый чат без shell и файловых инструментов.',
                'capabilities':{'vision':False,'file_upload':False,'file_context':False,'builtin_tools':False,'web_search':False,'code_interpreter':False}},
            'params':{'custom_params':{'hub_api_model':'api/'+provider+'/'+name}},
            'access_grants':[{'principal_type':'user','principal_id':user['id'],'permission':'read'}]}
        route=upstream+'/api/v1/models/model/update' if exists else upstream+'/api/v1/models/create'
        response=await client.post(route,headers=headers,params={'id':ident} if exists else None,json=payload,timeout=15)
        response.raise_for_status();records.append(ident)
    await client.get(upstream+'/api/models',params={'refresh':'true'},headers=headers,timeout=20)
    return records


def create_app():
    settings=load_settings();root=settings.data_dir;root.mkdir(mode=0o700,parents=True,exist_ok=True)
    token=os.environ.get('HUB_CHAT_TOKEN','')
    if len(token)<32:raise RuntimeError('Set a private HUB_CHAT_TOKEN of at least 32 characters')
    workspace=Path(os.environ.get('SASHA_WORKBENCH_PROJECTS',str(root/'workspace-default'))).resolve();workspace.mkdir(mode=0o700,parents=True,exist_ok=True)
    upstream=os.environ.get('WEBUI_INTERNAL_URL','http://127.0.0.1:3080').rstrip('/')
    @contextlib.asynccontextmanager
    async def lifespan(app):
        app.state.http=httpx.AsyncClient(timeout=httpx.Timeout(None,connect=15),trust_env=False)
        await chat_workspace.start_scheduler(app)
        recovery=asyncio.create_task(chat_jobs.recover(app))
        from .chat_addons import monitor as monitor_addons
        addon_monitor=asyncio.create_task(monitor_addons(app))
        try:yield
        finally:
            addon_monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):await addon_monitor
            recovery.cancel()
            with contextlib.suppress(asyncio.CancelledError):await recovery
            await chat_workspace.stop_scheduler(app);await app.state.http.aclose()
    app=FastAPI(title='Sasha AI Workbench · Open WebUI',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(RequestBodyLimit)
    app.state.settings=settings;app.state.chat_jobs_root=root/'chat-jobs'
    config=root/'bridge-config.json'
    config.write_text(json.dumps({'user':settings.desktop_user,'chat_jobs_dir':str(app.state.chat_jobs_root),'tokens':[{'id':'webui','actions':['jobs'],'roots':[str(workspace)],'cwd':str(workspace)}]}));config.chmod(0o600)
    app.state.config_file=config
    def auth(request):
        expected='Bearer '+token
        if not hmac.compare_digest(request.headers.get('authorization',''),expected):raise HTTPException(401,'Bridge authentication required')
        request.state.principal={'id':'webui','actions':['jobs'],'roots':[str(workspace)],'cwd':str(workspace)}
    chat_jobs.install(app,auth,lambda request:auth(request))
    @app.get('/ai-hub/artifacts-ui.js')
    async def artifact_asset():
        from fastapi.responses import FileResponse
        return FileResponse(Path(__file__).resolve().parents[2]/'deploy/artifacts-ui.js',media_type='text/javascript',headers={'Cache-Control':'no-cache'})
    @app.get('/ai-hub/health')
    async def health():return {'ok':True,'name':'Sasha AI Workbench','upstream':'Open WebUI'}
    @app.api_route('/ai-hub/api/{path:path}',methods=['GET','POST','PUT','PATCH','DELETE'])
    async def settings_proxy(request:Request,path:str):
        origin=request.headers.get('origin')
        if not valid_origin(origin,str(request.base_url)):raise HTTPException(403,'Invalid Origin')
        browser_token=request.headers.get('authorization','')
        if not browser_token.startswith('Bearer '):raise HTTPException(401,'Sign in to Open WebUI')
        user_response=await app.state.http.get(upstream+'/api/v1/auths/',headers={'Authorization':browser_token})
        if user_response.status_code!=200:raise HTTPException(401,'Open WebUI session expired')
        user=user_response.json()
        allowed={v for v in os.environ.get('SASHA_WORKBENCH_ALLOWED_USERS','').split(',') if v}
        own_job_read=bool(re.fullmatch(r'jobs/[a-f0-9]{32}(?:/artifacts/[0-9]{1,2}/export)?',path)) and request.method=='GET'
        if user.get('role') not in {'admin','user'}:raise HTTPException(403,'Account access denied')
        if not own_job_read and user.get('role')!='admin' and user.get('id') not in allowed:raise HTTPException(403,'Workbench is limited to the administrator and explicitly allowed user IDs')
        if path=='limits':target='/v1/jobs/limits'
        elif re.fullmatch(r'jobs/[a-f0-9]{32}(?:/artifacts/[0-9]{1,2}/export)?',path) and request.method=='GET':target='/v1/'+path
        elif path=='extensions' or path.startswith('extensions/'):target='/v1/chat-extensions'+path[len('extensions'):]
        elif path=='connectors' or path.startswith('connectors/'):target='/v1/'+path
        elif path=='workspace' or path.startswith('workspace/'):target='/v1/chat-workspace'+path[len('workspace'):]
        elif path=='harness' or path.startswith('harness/'):target='/v1/harness'+path[len('harness'):]
        else:raise HTTPException(404,'Unknown workbench route')
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>33*1024*1024:raise HTTPException(413,'Request too large')
            chunks.append(chunk)
        headers={'Authorization':'Bearer '+token,'X-WebUI-User':hashlib.sha256(user['id'].encode()).hexdigest(),'Content-Type':request.headers.get('content-type','application/json')}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://bridge',timeout=None) as bridge:
            result=await bridge.request(request.method,target+('?' + request.url.query if request.url.query else ''),headers=headers,content=b''.join(chunks))
        if (path.startswith('harness/connect/') or path=='harness/addons/omniroute') and request.method=='POST' and result.status_code==200:
            data=result.json()
            if data.get('ok') and (data.get('status')=='connected' or (path=='harness/addons/omniroute' and data.get('health'))) and data.get('models'):
                provider=path.rsplit('/',1)[-1]
                if provider not in {'codex','claude'}:
                    try:
                        model_authorization=browser_token
                        if user.get('role')!='admin':
                            # The caller already passed the explicit user allowlist.
                            # The service admin only creates an owner-read-only preset.
                            login=await app.state.http.post(upstream+'/api/v1/auths/signin',json={'email':os.environ.get('WEBUI_ADMIN_EMAIL',''),'password':os.environ.get('WEBUI_ADMIN_PASSWORD','')},timeout=15)
                            login.raise_for_status();model_authorization='Bearer '+login.json()['token']
                        data['model_records']=await register_owner_models(app.state.http,model_authorization,user,hashlib.sha256(user['id'].encode()).hexdigest(),provider,data['models'],upstream)
                    except (httpx.HTTPError,ValueError,KeyError):data['model_registration_warning']='Connection verified, but native private model-list registration failed. Harness routing remains available; check the server Open WebUI administrator connection.'
            return Response(json.dumps(data),media_type='application/json',headers={'Cache-Control':'no-store'})
        response_headers={k:v for k,v in result.headers.items() if k.lower() in {'content-type','content-disposition'}}
        return Response(result.content,status_code=result.status_code,headers={**response_headers,'Cache-Control':'no-store'})
    assets=Path(os.environ.get('SASHA_WORKBENCH_ASSETS',str(Path(__file__).resolve().parents[2]/'runtime/overrides/ai-hub')))
    if assets.is_dir():app.mount('/ai-hub',StaticFiles(directory=assets),name='workbench-assets')
    @app.api_route('/{path:path}',methods=['GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS'])
    async def proxy(request:Request,path:str):
        if path.startswith('v1/'):raise HTTPException(404,'Unknown bridge route')
        headers=[(k,v) for k,v in request.headers.raw if k.decode().lower() not in HOP]
        outgoing=app.state.http.build_request(request.method,upstream+'/'+path,params=request.query_params,headers=headers,content=request.stream())
        response=await app.state.http.send(outgoing,stream=True)
        result=StreamingResponse(response.aiter_raw(),status_code=response.status_code,background=BackgroundTask(response.aclose))
        result.raw_headers=[(k,v) for k,v in response.headers.raw if k.decode().lower() not in HOP];return result
    @app.websocket('/{path:path}')
    async def websocket(ws:WebSocket,path:str):
        origin=ws.headers.get('origin')
        expected=str(ws.base_url).replace('ws://','http://').replace('wss://','https://').rstrip('/')
        if not valid_origin(origin,expected):return await ws.close(code=1008)
        headers={k:v for k,v in ws.headers.items() if k.lower() in {'cookie','authorization','user-agent'}}
        url=upstream.replace('https://','wss://').replace('http://','ws://')+'/'+path+'?'+ws.url.query
        async with connect(url,additional_headers=headers,max_size=8_000_000) as backend:
            await ws.accept()
            async def send():
                async for message in backend:
                    if isinstance(message,str):await ws.send_text(message)
                    else:await ws.send_bytes(message)
            async def receive():
                while True:
                    message=await ws.receive()
                    if message['type']=='websocket.disconnect':return
                    await backend.send(message.get('text') if message.get('text') is not None else message.get('bytes',b''))
            tasks=[asyncio.create_task(send()),asyncio.create_task(receive())]
            done,pending=await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
            for task in pending:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    return app
