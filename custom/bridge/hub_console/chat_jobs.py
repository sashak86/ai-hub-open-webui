"""Owner-scoped durable WebUI jobs. Private files are conversation content, not audit.

Idempotent message IDs, a serial worker per conversation, replayable event offsets.
Worker processes are detached from HTTP and have no reference to existing tmux panes.
"""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from fastapi import HTTPException,Request
from fastapi.responses import StreamingResponse,JSONResponse,Response
from .chat_worker import atomic,load
from .system import as_user_kwargs,user_env

MAX_ATTACHMENTS=32*1024*1024
TERMINAL={'completed','failed','cancelled','interrupted'}


def owner_id(request):
    if 'jobs' not in request.state.principal.get('actions',[]):raise HTTPException(403,'Chat jobs not granted')
    who=request.headers.get('x-webui-user','')
    if not re.fullmatch('[a-f0-9]{64}',who):raise HTTPException(400,'Authenticated WebUI user required')
    return hashlib.sha256((request.state.principal['id']+':'+who).encode()).hexdigest()


def directory(request):
    root=request.app.state.chat_jobs_root
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    return root


def own(path,request):
    if os.geteuid()==0:
        user=pwd.getpwnam(request.app.state.settings.desktop_user)
        os.chown(path,user.pw_uid,user.pw_gid)


def mkdir(path,request):
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    own(path,request)


def model_catalog(request):
    from .agent_bridge import available_models
    result=[]
    for row in available_models(request):
        if row['id'].startswith('model/'):
            ident=row['id'][6:]
            result.append({'id':ident,'name':('Codex · ' if ident.startswith('codex/') else '')+row['name'],'provider':ident.split('/')[0],'available':True,'capabilities':{'text':True,'image':True,'tools':True}})
    if not any(x['provider']=='codex' for x in result):result.insert(0,{'id':'codex/default','name':'Codex · по умолчанию','provider':'codex','available':True,'capabilities':{'text':True,'image':True,'tools':True}})
    try:
        from .chat_providers import discover
        result+=discover()
    except (ImportError,OSError,ValueError):pass
    from .chat_harness import api_models
    from .chat_addons import api_models as addon_models
    return result+api_models(request)+addon_models(request)


def scoped_job(request,ident):
    owner=owner_id(request)
    if not re.fullmatch('[a-f0-9]{32}',ident):raise HTTPException(404,'Job not found')
    ref=load(directory(request)/'index'/f'{ident}.json',{})
    if ref.get('owner')!=owner:raise HTTPException(404,'Job not found')
    folder=directory(request)/owner/ref['chat']/ref['folder']
    if not folder.is_dir() or folder.is_symlink():raise HTTPException(404,'Job not found')
    return folder


def summary(folder):
    desc=load(folder/'request.json',{});state=load(folder/'state.json',{})
    # Legacy interrupted states may lack an end instant. Never grow terminal
    # duration using the time a browser happens to reload the page.
    end=state.get('finished_at') if state.get('status') in TERMINAL else time.time()
    start=state.get('started_at');created=desc.get('created_at')
    duration=round(max(0,end-start),1) if end is not None and start is not None else (0 if end is not None else None)
    queue_end=start if start is not None else end
    queue=round(max(0,queue_end-created),1) if queue_end is not None and created is not None else None
    total=round(max(0,end-created),1) if end is not None and created is not None else None
    return {**state,'id':desc.get('id'),'model':desc.get('model'),'created_at':created,
            'started_at':start,'queue_seconds':queue,'duration_seconds':duration,
            'total_seconds':total,'time_zone':'UTC','artifacts':state.get('artifacts',[]),'elapsed':duration}


def start_worker(request,chat):
    # Native children survive HTTP cancellation/restart; flock prevents duplicates.
    from . import chat_worker
    env=user_env(request.app.state.settings.desktop_user,{'PYTHONPATH':str(Path(chat_worker.__file__).resolve().parents[1]),'PYTHONDONTWRITEBYTECODE':'1'})
    kw=as_user_kwargs(request.app.state.settings.desktop_user)
    # as_user_kwargs also supplies env; use the same trusted fixed user env.
    kw['env']=env
    proc=subprocess.Popen([sys.executable,'-m','hub_console.chat_worker',str(chat)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,**kw)
    # Reap child without tying its lifetime to request/task cancellation.
    async def reap():
        while proc.poll() is None:await asyncio.sleep(1)
    task=asyncio.create_task(reap());request.app.state.chat_reapers.add(task);task.add_done_callback(request.app.state.chat_reapers.discard)


async def recover(app):
    """Resume authorized queued work; flock protects surviving workers on restart."""
    root=app.state.chat_jobs_root
    if not root.is_dir():return
    request=Request({'type':'http','method':'GET','path':'/','headers':[],'app':app})
    for chat in list(root.glob('*/*'))[:256]:
        if not chat.is_dir() or chat.is_symlink() or not re.fullmatch('[a-f0-9]{64}',chat.name):continue
        if any(load(folder/'state.json',{}).get('status') not in TERMINAL for folder in chat.glob('job-*')):
            start_worker(request,chat)
            await asyncio.sleep(.02)


def install(app,owner_setup,validate_live):
    app.state.chat_reapers=set()
    from .chat_extensions import install as install_extensions
    install_extensions(app,owner_setup)
    from .chat_harness import install as install_harness
    from .chat_workspace import install as install_workspace
    install_harness(app,owner_setup)
    from .chat_addons import install as install_addons
    install_addons(app,owner_setup)
    install_workspace(app,owner_setup)
    from .chat_connectors import install as install_connectors
    install_connectors(app,owner_setup)

    @app.get('/v1/jobs/models')
    async def models(request:Request):
        owner_setup(request)
        if 'jobs' not in request.state.principal.get('actions',[]):raise HTTPException(403,'Chat jobs not granted')
        return {'data':await asyncio.to_thread(model_catalog,request)}

    @app.get('/v1/jobs/limits')
    async def limits(request:Request):
        owner_setup(request)
        if 'jobs' not in request.state.principal.get('actions',[]):raise HTTPException(403,'Chat jobs not granted')
        from .chat_quotas import snapshot as quotas
        return await quotas(request)

    @app.post('/v1/jobs/cancel-chat')
    async def cancel_chat(request:Request):
        owner_setup(request);who=owner_id(request);body=await request.json()
        if not isinstance(body,dict):raise HTTPException(422,'Expected object')
        chatid=body.get('chat_id')
        if not isinstance(chatid,str) or not chatid or len(chatid)>200:raise HTTPException(422,'Invalid chat_id')
        root=directory(request);own(root,request);mkdir(root/who,request)
        chat=root/who/hashlib.sha256(chatid.encode()).hexdigest();mkdir(chat,request)
        atomic(chat/'stop.json',{'at':time.time()});own(chat/'stop.json',request)
        stopped=0;identifiers=[]
        for folder in chat.glob('job-*'):
            ident=load(folder/'request.json',{}).get('id')
            if ident and load(folder/'state.json',{}).get('status') not in TERMINAL:
                (folder/'cancel').touch(mode=0o600);own(folder/'cancel',request);stopped+=1;identifiers.append(ident)
        return {'ok':True,'count':stopped,'jobs':identifiers}

    @app.post('/v1/jobs')
    async def create(request:Request):
        received_at=time.time()
        owner_setup(request);who=owner_id(request);body=await request.json()
        if not isinstance(body,dict):raise HTTPException(422,'Expected object')
        for key in ('chat_id','message_id'):
            if not isinstance(body.get(key),str) or not body[key] or len(body[key])>200:raise HTTPException(422,'Invalid conversation identity')
        text=body.get('text','')
        if not isinstance(text,str) or len(text)>100_000 or '\x00' in text:raise HTTPException(422,'Invalid text')
        if not text.strip() and not body.get('attachments'):raise HTTPException(422,'Empty message')
        model=body.get('model','')
        if not isinstance(model,str) or len(model)>200:raise HTTPException(422,'Invalid model')
        from .chat_harness import settings as harness_settings
        policy=harness_settings(request,body['chat_id'])
        if model not in {row['id'] for row in await asyncio.to_thread(model_catalog,request) if row.get('available') or (policy['enabled'] and policy['mode']=='auto')}:
            raise HTTPException(422,'Model is not available')
        root=directory(request);own(root,request);mkdir(root/'index',request);mkdir(root/who,request)
        chatname=hashlib.sha256(body['chat_id'].encode()).hexdigest();chat=root/who/chatname;mkdir(chat,request)
        # Derive idempotency key from authenticated principal, chat and message id.
        ident=hashlib.sha256((who+'\0'+body['chat_id']+'\0'+body['message_id']).encode()).hexdigest()[:32]
        index=root/'index'/f'{ident}.json'
        recovered=chat/f'job-{ident}'
        if not index.exists() and load(recovered/'request.json',{}).get('id')==ident:
            atomic(index,{'owner':who,'chat':chatname,'folder':recovered.name})
        if index.exists():
            folder=scoped_job(request,ident)
            start_worker(request,chat)
            return summary(folder)
        outstanding=sum(1 for folder in (root/who).glob('*/job-*') if load(folder/'state.json',{}).get('status') not in TERMINAL)
        if outstanding>=20:raise HTTPException(429,'Too many queued jobs')
        cwd=Path(request.state.principal.get('chat_cwd',request.state.principal.get('cwd',str(Path.home())))).resolve()
        roots=[Path(p).resolve() for p in request.state.principal.get('roots',[])]
        if not cwd.is_dir() or not any(cwd.is_relative_to(r) for r in roots):raise HTTPException(403,'Working directory is not granted')
        attachments=body.get('attachments',[])
        if not isinstance(attachments,list) or len(attachments)>10:raise HTTPException(422,'At most ten attachments')
        decoded=[];total=0
        for item in attachments:
            if not isinstance(item,dict):raise HTTPException(422,'Invalid attachment')
            name=item.get('name','file');mime=item.get('mime','application/octet-stream')
            if not isinstance(name,str) or len(name)>200 or not isinstance(mime,str) or not re.fullmatch(r'[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+',mime):raise HTTPException(422,'Invalid attachment metadata')
            try:data=base64.b64decode(item.get('content_base64',''),validate=True)
            except (ValueError,TypeError):raise HTTPException(422,'Invalid attachment encoding') from None
            total+=len(data)
            if total>MAX_ATTACHMENTS:raise HTTPException(413,'Attachments exceed 32MiB')
            decoded.append((name,mime,data))
        history=body.get('history',[])
        if not isinstance(history,list) or len(history)>500:raise HTTPException(422,'Invalid visible history')
        clean=[];history_size=0
        for message in history:
            if not isinstance(message,dict) or message.get('role') not in ('user','assistant') or not isinstance(message.get('content'),str):raise HTTPException(422,'Invalid visible history')
            history_size+=len(message['content'])
            if history_size>200_000:raise HTTPException(413,'Visible history exceeds limit')
            clean.append({'role':message['role'],'content':message['content']})
        folder=chat/f'job-{ident}'
        staging=chat/f'.pending-{ident}-{secrets.token_hex(4)}';mkdir(staging,request);mkdir(staging/'files',request)
        files=[]
        for i,(name,mime,data) in enumerate(decoded):
            safe=re.sub(r'[^A-Za-z0-9._-]','_',Path(name).name)[:100] or 'file'
            path=folder/'files'/f'{i}-{safe}'
            staged_path=staging/'files'/path.name
            with staged_path.open('xb') as f:os.chmod(staged_path,0o600);f.write(data)
            own(staged_path,request);files.append({'name':name,'mime':mime,'path':str(path)})
        desc=dict(id=ident,model=model,text=text,cwd=str(cwd),created_at=time.time(),attachments=files,history=clean,history_supplied='history' in body)
        from .chat_extensions import snapshot,digest
        from .chat_connectors import native_extension
        desc['extensions']=snapshot(request)
        desc['extensions_hash']=digest(desc['extensions'])
        from .chat_harness import prepare,root as harness_root
        desc['connection_root']=str(harness_root(request))
        await prepare(request,body,desc,[])
        from .chat_addons import prepare as prepare_addons
        await prepare_addons(request,body,desc)
        desc['extensions']+=native_extension(request)
        desc['extensions_hash']=digest(desc['extensions'])
        atomic(staging/'request.json',desc);own(staging/'request.json',request)
        stopped_early=load(chat/'stop.json',{}).get('at',0)>=received_at
        atomic(staging/'state.json',{'status':'cancelled' if stopped_early else 'queued','description':'Отменено до запуска' if stopped_early else 'В очереди','created_at':desc['created_at']});own(staging/'state.json',request)
        if stopped_early:
            atomic(staging/'state.json',{'status':'cancelled','description':'Отменено до запуска','created_at':desc['created_at'],'finished_at':time.time(),'final_text':'Запрос отменён до запуска. Агент не выполнял задачу.','artifacts':[]});own(staging/'state.json',request)
        staging.rename(folder)
        atomic(index,{'owner':who,'chat':chatname,'folder':folder.name})
        start_worker(request,chat)
        return summary(folder)

    @app.get('/v1/jobs/{ident}')
    async def read(request:Request,ident:str):
        owner_setup(request);return summary(scoped_job(request,ident))

    @app.get('/v1/jobs/{ident}/artifacts/{artifact_id}/export')
    async def export_artifact(request:Request,ident:str,artifact_id:str,format:str='json'):
        owner_setup(request);folder=scoped_job(request,ident)
        artifact=next((a for a in load(folder/'state.json',{}).get('artifacts',[]) if a.get('id')==artifact_id),None)
        if not artifact:raise HTTPException(404,'Artifact not found')
        from .chat_artifacts import validate,csv_export
        try:validate({k:v for k,v in artifact.items() if k!='id'})
        except (AssertionError,ValueError,TypeError,KeyError):raise HTTPException(422,'Invalid artifact')
        headers={'Cache-Control':'no-store','Content-Disposition':'attachment; filename="artifact-'+artifact_id+'.'+('csv' if format=='csv' else 'json')+'"'}
        if format=='json':return JSONResponse(artifact,headers=headers)
        if format=='csv' and artifact['type']=='table':return Response(csv_export(artifact),media_type='text/csv',headers=headers)
        raise HTTPException(422,'Unsupported export format')

    @app.post('/v1/jobs/{ident}/cancel')
    async def cancel(request:Request,ident:str):
        owner_setup(request);folder=scoped_job(request,ident)
        (folder/'cancel').touch(mode=0o600);own(folder/'cancel',request)
        return {'ok':True}

    @app.get('/v1/jobs/{ident}/events')
    async def events(request:Request,ident:str,after:int=0):
        owner_setup(request);folder=scoped_job(request,ident)
        if after<0:raise HTTPException(422,'Invalid event offset')
        async def stream():
            cursor=after;offset=0;last_kick=0
            while True:
                validate_live(request);owner_id(request)
                path=folder/'events.jsonl'
                if path.exists():
                    with path.open() as f:
                        f.seek(offset)
                        while True:
                            line=f.readline()
                            if not line or not line.endswith('\n'):break
                            offset=f.tell()
                            try:event=json.loads(line)
                            except ValueError:continue
                            if event.get('seq',0)<=cursor:continue
                            cursor=event['seq'];yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
                state=summary(folder)
                if state.get('status') in ('queued','running') and time.monotonic()-last_kick>5:
                    # flock makes this a no-op while the original worker is alive;
                    # if it was OOM-killed, a replacement marks running interrupted,
                    # never resends that turn. Queued following turns can proceed.
                    start_worker(request,folder.parent);last_kick=time.monotonic()
                if state.get('status') in TERMINAL:
                    yield 'data: '+json.dumps({'type':'done','status':state['status'],'seq':cursor})+'\n\n';return
                yield 'data: '+json.dumps(dict(type='status',seq=cursor,**state),ensure_ascii=False)+'\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
