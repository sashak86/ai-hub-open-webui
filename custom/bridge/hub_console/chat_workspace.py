"""Owner-scoped project files, verified backups and external WebDAV archives.

Only managed roots and explicitly configured external local roots are addressable.
Remote credentials never round-trip. No arbitrary mounts or shell commands.
"""
from __future__ import annotations
import asyncio
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
import psutil
from pathlib import Path, PurePosixPath
import shutil
import stat
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit, unquote
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from .chat_worker import atomic, load

LIMIT=32*1024*1024
MAX_BACKUP=256*1024*1024
MASK='••••'
EXCLUDED={'.env','credentials','secrets','.ssh','.aws','.npmrc','.pypirc','.netrc','node_modules','.venv','__pycache__','.git'}

def fail(message='Некорректный путь',code=422):raise HTTPException(code,message)
def name(value):
    if not isinstance(value,str) or not value.strip() or len(value)>160 or any(c in value for c in '/\\\x00') or value in {'.','..'}:fail()
    return value.strip()
def relative(value=''):
    if not isinstance(value,str) or '\\' in value or '\x00' in value:fail()
    p=PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts:fail()
    return p

def safe(root,value='',exists=False):
    root=Path(root)
    if root.is_symlink():fail('Символические ссылки запрещены')
    candidate=root.joinpath(*relative(value).parts)
    current=root
    for part in relative(value).parts:
        current=current/part
        if current.is_symlink():fail('Символические ссылки запрещены')
    if not candidate.resolve().is_relative_to(root.resolve()):fail()
    if exists and not candidate.exists():fail('Файл не найден',404)
    return candidate

def folder(request):
    from .chat_jobs import directory,owner_id,mkdir
    owner=getattr(request.state,'workspace_owner',None) or owner_id(request)
    base=directory(request)/'workspace';mkdir(base,request)
    p=base/owner;mkdir(p,request)
    for child in ('files','backups'):mkdir(p/child,request)
    return p

def registry(request):return folder(request)/'registry.json'
def read(request):return load(registry(request),{'projects':[],'storages':[]})
def save(request,data):
    from .chat_jobs import own
    atomic(registry(request),data);own(registry(request),request)
@contextmanager
def locked(request):
    p=folder(request)/'registry.lock'
    with p.open('a') as h:
        os.chmod(p,0o600);own_path(p,request);fcntl.flock(h,fcntl.LOCK_EX)
        data=read(request)
        yield data
        save(request,data)

def lookup(rows,ident):
    row=next((r for r in rows if r['id']==ident),None)
    if row is None:fail('Не найдено',404)
    return row

def storage_root(request,data,ident):
    if ident=='managed':return folder(request)/'files'
    storage=lookup(data['storages'],ident)
    if storage['type']!='local':fail('Удалённое хранилище используется для архивов; рабочие файлы остаются локальными')
    return Path(storage['path'])

def project_path(request,data,project):return safe(storage_root(request,data,project.get('storage_id','managed')),project['folder'],True)
def project_context(request,ident):
    data=read(request);p=lookup(data['projects'],ident)
    return {'id':p['id'],'name':p['name'],'path':str(project_path(request,data,p))}

def own_path(path,request):
    from .chat_jobs import own
    own(path,request)

def busy(request,project,check_process=False):
    from .chat_jobs import directory,owner_id
    root=project_path(request,read(request),project)
    owner=getattr(request.state,'workspace_owner',None) or owner_id(request)
    for path in (directory(request)/owner).glob('*/*/request.json'):
        desc=load(path,{});state=load(path.parent/'state.json',{})
        job_project=desc.get('project_id') or desc.get('harness',{}).get('project_id')
        cwd=desc.get('cwd')
        related=job_project==project['id'] or (isinstance(cwd,str) and Path(cwd).resolve().is_relative_to(root.resolve()))
        if related and state.get('status','queued') in {'queued','running','starting','cancelling'}:fail('В проекте выполняется задача; дождитесь завершения',409)
    if check_process:
        for process in psutil.process_iter(['pid','cwd']):
            cwd=process.info.get('cwd')
            if cwd and Path(cwd).resolve().is_relative_to(root.resolve()):fail('Папка используется работающим процессом; перемещение сейчас недоступно',409)

def public_storage(row):return {k:(MASK if k in {'password','token'} and v else v) for k,v in row.items()}
def audit(request,action,**metadata):
    p=folder(request)/'audit.jsonl'
    with p.open('a') as h:h.write(json.dumps({'at':time.time(),'action':action,**metadata},ensure_ascii=False)+'\n')
    os.chmod(p,0o600);own_path(p,request)

def local_allow(path):
    if not isinstance(path,str):fail('Нужен абсолютный путь')
    roots=[Path(p).resolve() for p in os.environ.get('AI_HUB_WORKSPACE_ROOTS','').split(os.pathsep) if p]
    p=Path(path)
    if not p.is_absolute() or p.is_symlink() or not p.is_dir() or not any(p.resolve().is_relative_to(r) for r in roots):fail('Внешняя локальная папка должна быть разрешена сервером в AI_HUB_WORKSPACE_ROOTS')
    return str(p.resolve())

def webdav_url(storage,suffix=''):
    return storage['url'].rstrip('/')+'/'+ '/'.join(quote(p,safe='') for p in relative(suffix).parts)
async def webdav(storage,method,suffix='',content=None,extra_headers=None,raw=False):
    auth=(storage.get('username',''),storage.get('password','')) if storage.get('username') else None
    headers={'Authorization':'Bearer '+storage['token']} if storage.get('token') else {}
    headers.update(extra_headers or {})
    try:
        async with httpx.AsyncClient(timeout=30,follow_redirects=False,auth=auth) as client:
            async with client.stream(method,webdav_url(storage,suffix),headers=headers,content=content) as response:
                chunks=[];size=0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>LIMIT:fail('Удалённый файл превышает 32 MiB',413)
                    chunks.append(chunk)
                r=httpx.Response(response.status_code,headers=response.headers,content=b''.join(chunks))
            if r.status_code==404:fail('Удалённый файл не найден',404)
            if r.status_code not in {200,201,204,207}:fail('Хранилище отклонило запрос: HTTP '+str(r.status_code),502)
            return r if raw else {'status':r.status_code}
    except httpx.HTTPError:fail('Не удалось подключиться к хранилищу',502)

async def remote_listing(storage,path):
    response=await webdav(storage,'PROPFIND',path,extra_headers={'Depth':'1','Content-Type':'application/xml'},raw=True)
    try:root=ET.fromstring(response.content)
    except ET.ParseError:fail('Некорректный WebDAV ответ',502)
    ns={'d':'DAV:'};items=[]
    base_path=unquote(urlsplit(webdav_url(storage,path)).path).rstrip('/')
    for resource in root.findall('d:response',ns):
        href=unquote(urlsplit(resource.findtext('d:href','',ns)).path).rstrip('/')
        if href==base_path or not href.startswith(base_path+'/'):continue
        item_name=href[len(base_path)+1:]
        if not item_name or '/' in item_name or item_name in {'.','..'}:continue
        prop=resource.find('d:propstat/d:prop',ns)
        if prop is None:continue
        directory=prop.find('d:resourcetype/d:collection',ns) is not None
        size=prop.findtext('d:getcontentlength','0',ns)
        items.append({'name':item_name,'path':str(relative(path)/item_name),'type':'directory' if directory else 'file','size':int(size) if size.isdigit() else 0,'modified':prop.findtext('d:getlastmodified','',ns)})
        if len(items)>=5000:break
    return {'path':path,'items':items}

def excluded(parts):return any(p in EXCLUDED or p.startswith('.env.') or p.endswith(('.pem','.key')) for p in parts)
def make_backup(request,project):
    data=read(request);source=project_path(request,data,project)
    dest=folder(request)/'backups'/project['id'];dest.mkdir(mode=0o700,exist_ok=True);own_path(dest,request)
    ident=uuid.uuid4().hex;target=dest/(ident+'.zip');total=0;count=0
    try:
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for base,dirs,files in os.walk(source,followlinks=False):
                dirs[:]=[d for d in dirs if not excluded((d,)) and not (Path(base)/d).is_symlink()]
                for n in files:
                    p=Path(base)/n;rel=p.relative_to(source)
                    if excluded(rel.parts) or p.is_symlink() or not p.is_file():continue
                    total+=p.stat().st_size;count+=1
                    if total>MAX_BACKUP or count>20000:fail('Архив превышает 256 MiB или 20000 файлов',413)
                    archive.write(p,str(rel))
        os.chmod(target,0o600);own_path(target,request)
        digest=hashlib.sha256(target.read_bytes()).hexdigest()
        meta={'id':ident,'created_at':time.time(),'size':target.stat().st_size,'files':count,'sha256':digest,'excluded':sorted(EXCLUDED)}
        atomic(dest/(ident+'.json'),meta);own_path(dest/(ident+'.json'),request)
        audit(request,'backup',project=project['id'],backup=ident)
        return meta
    except BaseException:
        target.unlink(missing_ok=True);raise

def backup_list(request,ident):
    p=folder(request)/'backups'/ident
    if not p.exists():return []
    return sorted([load(f,{}) for f in p.glob('*.json')],key=lambda r:r.get('created_at',0),reverse=True)

def restore(request,project,ident):
    busy(request,project)
    if not isinstance(ident,str) or len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident):fail()
    dest=folder(request)/'backups'/project['id'];meta=load(dest/(ident+'.json'),{})
    archive_path=dest/(ident+'.zip')
    if not meta or not archive_path.is_file():fail('Архив не найден',404)
    if hashlib.sha256(archive_path.read_bytes()).hexdigest()!=meta.get('sha256'):fail('Контрольная сумма архива не совпадает',409)
    root=project_path(request,read(request),project)
    stage=root.parent/('.restore-'+uuid.uuid4().hex);stage.mkdir(mode=0o700);own_path(stage,request)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            total=0
            for member in archive.infolist():
                rel=relative(member.filename);target=safe(stage,str(rel))
                mode=member.external_attr>>16
                if stat.S_ISLNK(mode) or excluded(rel.parts):fail('Небезопасный архив',409)
                total+=member.file_size
                if total>MAX_BACKUP or len(archive.infolist())>20000:fail('Архив слишком большой',413)
                if member.is_dir():target.mkdir(mode=0o700,parents=True,exist_ok=True)
                else:
                    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                    with archive.open(member) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
                    os.chmod(target,0o700 if mode&0o111 else 0o600);own_path(target,request)
        # Keep an automatic rollback backup before restoring, preserving excluded private files.
        rollback=make_backup(request,project)
        for base,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not excluded((d,)) and not (Path(base)/d).is_symlink()]
            for n in files:
                p=Path(base)/n;rel=p.relative_to(root)
                if not excluded(rel.parts) and not p.is_symlink() and not safe(stage,str(rel)).exists():p.unlink()
        for base,dirs,files in os.walk(stage):
            for d in dirs:safe(root,str((Path(base)/d).relative_to(stage))).mkdir(mode=0o700,parents=True,exist_ok=True)
            for n in files:
                p=Path(base)/n;target=safe(root,str(p.relative_to(stage)));target.parent.mkdir(mode=0o700,parents=True,exist_ok=True);shutil.copyfile(p,target);os.chmod(target,0o700 if p.stat().st_mode&0o111 else 0o600);own_path(target,request)
        for base,dirs,files in os.walk(root):
            for d in dirs:
                path=Path(base)/d
                if not path.is_symlink():own_path(path,request)
        audit(request,'restore',project=project['id'],backup=ident)
        return {'ok':True,'rollback_backup_id':rollback['id']}
    finally:shutil.rmtree(stage,ignore_errors=True)

async def body(request):
    parts=[];size=0
    async for chunk in request.stream():
        size+=len(chunk)
        if size>LIMIT:fail('Файл превышает 32 MiB',413)
        parts.append(chunk)
    return b''.join(parts)

async def json_body(request):
    try:value=json.loads(await body(request))
    except (ValueError,UnicodeError):fail('Ожидается JSON-объект')
    if not isinstance(value,dict):fail('Ожидается JSON-объект')
    return value

async def run_due(request):
    # Called by the process-local scheduler and can be triggered explicitly in tests.
    for project in read(request)['projects']:
        schedule=project.get('schedule',{})
        if schedule.get('enabled') and schedule.get('next_at',0)<=time.time():
            try:
                await asyncio.to_thread(make_backup,request,project)
                keep=schedule.get('keep',7)
                for old in backup_list(request,project['id'])[keep:]:
                    p=folder(request)/'backups'/project['id']
                    for ext in ('.json','.zip'):(p/(old['id']+ext)).unlink(missing_ok=True)
                with locked(request) as data:
                    live=lookup(data['projects'],project['id']);live['schedule']['next_at']=time.time()+schedule['interval_hours']*3600
            except Exception:audit(request,'scheduled_backup_error',project=project['id'])

def install(app,owner_setup):
    contexts={}
    @app.exception_handler(FileNotFoundError)
    async def missing(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Файл или папка не найдены'},status_code=404)
    @app.exception_handler(FileExistsError)
    async def exists(request,exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'Файл или папка уже существуют'},status_code=409)
    def auth(request):
        from .chat_jobs import owner_id
        owner_setup(request);ident=owner_id(request);contexts[ident]=request
    @app.get('/v1/chat-workspace')
    async def listing(request:Request):
        auth(request);data=read(request)
        return {**data,'storages':[{'id':'managed','name':'На сервере','type':'local'},*[public_storage(s) for s in data['storages']]],'capabilities':{'storage_types':['local','webdav'],'remote_files':True,'external_local_requires':'AI_HUB_WORKSPACE_ROOTS','max_file_bytes':LIMIT,'max_backup_bytes':MAX_BACKUP,'excluded_from_backups':sorted(EXCLUDED)}}
    @app.post('/v1/chat-workspace/projects')
    async def create(request:Request):
        auth(request);value=await json_body(request);ident=uuid.uuid4().hex
        with locked(request) as data:
            if len(data['projects'])>=100:fail('Не больше 100 проектов')
            row={'id':ident,'name':name(value.get('name')),'folder':name(value.get('folder',ident)),'storage_id':value.get('storage_id','managed'),'created_at':time.time()}
            root=storage_root(request,data,row['storage_id']);path=safe(root,row['folder'])
            if path.exists():fail('Папка уже существует',409)
            if value.get('import_path'):
                imported=Path(local_allow(value['import_path']))
                sid=uuid.uuid4().hex
                data['storages'].append({'id':sid,'name':row['name']+' (импорт)','type':'local','path':str(imported)})
                row.update(folder='',storage_id=sid,imported=True)
            else:path.mkdir(mode=0o700);own_path(path,request)
            data['projects'].append(row)
        audit(request,'create_project',project=ident);return row
    @app.get('/v1/chat-workspace/projects/{ident}')
    async def detail(request:Request,ident:str):auth(request);return lookup(read(request)['projects'],ident)
    @app.patch('/v1/chat-workspace/projects/{ident}')
    async def update(request:Request,ident:str):
        auth(request);value=await json_body(request)
        with locked(request) as data:
            row=lookup(data['projects'],ident)
            if 'folder' in value or 'storage_id' in value:
                if row.get('imported'):fail('Импортированная папка привязана к внешнему хранилищу; перемещайте файлы внутри неё, имя проекта менять можно',409)
                busy(request,row,check_process=True)
                old=project_path(request,data,row);new_folder=name(value.get('folder',row['folder']));new_storage=value.get('storage_id',row['storage_id']);target=safe(storage_root(request,data,new_storage),new_folder)
                if target!=old:
                    if target.exists():fail('Папка назначения уже существует',409)
                    if target.resolve().is_relative_to(old.resolve()):fail('Нельзя переместить папку в себя')
                    await asyncio.to_thread(shutil.move,str(old),str(target))
                row.update(folder=new_folder,storage_id=new_storage)
            if 'name' in value:row['name']=name(value['name'])
        audit(request,'update_project',project=ident);return row
    @app.get('/v1/chat-workspace/projects/{ident}/files')
    async def files(request:Request,ident:str,path:str=''):
        auth(request);data=read(request);root=project_path(request,data,lookup(data['projects'],ident));p=safe(root,path,True)
        if not p.is_dir():fail('Это не папка')
        items=[]
        for entry in sorted(p.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower())):
            if entry.is_symlink():continue
            st=entry.stat();items.append({'name':entry.name,'path':str(entry.relative_to(root)),'type':'directory' if entry.is_dir() else 'file','size':st.st_size,'modified':st.st_mtime})
            if len(items)>=5000:break
        return {'path':path,'items':items}
    @app.get('/v1/chat-workspace/projects/{ident}/file')
    async def download(request:Request,ident:str,path:str):
        auth(request);data=read(request);p=safe(project_path(request,data,lookup(data['projects'],ident)),path,True)
        if not p.is_file():fail('Это не файл')
        return FileResponse(p,filename=p.name)
    @app.put('/v1/chat-workspace/projects/{ident}/file')
    async def upload(request:Request,ident:str,path:str):
        auth(request);raw=await body(request);data=read(request);root=project_path(request,data,lookup(data['projects'],ident));p=safe(root,path)
        if p==root or not p.parent.is_dir():fail('Папка не найдена')
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(raw)
        own_path(p,request)
        audit(request,'write_file',project=ident,path=path);return {'ok':True,'size':len(raw)}
    @app.post('/v1/chat-workspace/projects/{ident}/files')
    async def operation(request:Request,ident:str):
        auth(request);value=await json_body(request);data=read(request);root=project_path(request,data,lookup(data['projects'],ident));p=safe(root,value.get('path',''))
        if p==root:fail('Корень проекта нельзя удалить или переместить')
        action=value.get('action')
        if action in {'move','delete'}:busy(request,lookup(data['projects'],ident))
        if action=='mkdir':
            if p.exists():fail('Папка уже существует',409)
            if not p.parent.is_dir():fail('Родительская папка не найдена',404)
            p.mkdir(mode=0o700,parents=False,exist_ok=False);own_path(p,request)
        elif action=='move':
            target=safe(root,value.get('to',''))
            if target.exists() or target.resolve().is_relative_to(p.resolve()):fail('Недопустимое назначение',409)
            shutil.move(str(p),str(target))
        elif action=='delete':
            # Move to a private recycle bin, so the UI action is reversible.
            trash=folder(request)/'trash';trash.mkdir(mode=0o700,exist_ok=True);own_path(trash,request)
            shutil.move(str(p),str(trash/(uuid.uuid4().hex+'-'+p.name)))
        else:fail('Неизвестная операция')
        audit(request,action,project=ident,path=value.get('path'));return {'ok':True}
    @app.get('/v1/chat-workspace/projects/{ident}/backups')
    async def backups(request:Request,ident:str):auth(request);lookup(read(request)['projects'],ident);return {'items':backup_list(request,ident)}
    @app.post('/v1/chat-workspace/projects/{ident}/backups')
    async def backup(request:Request,ident:str):
        auth(request);project=lookup(read(request)['projects'],ident);return await asyncio.to_thread(make_backup,request,project)
    @app.get('/v1/chat-workspace/projects/{ident}/backups/{backup_id}')
    async def backup_download(request:Request,ident:str,backup_id:str):
        auth(request);lookup(read(request)['projects'],ident);lookup(backup_list(request,ident),backup_id)
        return FileResponse(folder(request)/'backups'/ident/(backup_id+'.zip'),filename='project-'+backup_id+'.zip')
    @app.post('/v1/chat-workspace/projects/{ident}/restore')
    async def restoring(request:Request,ident:str):
        auth(request);value=await json_body(request);p=lookup(read(request)['projects'],ident);return await asyncio.to_thread(restore,request,p,value.get('backup_id'))
    @app.put('/v1/chat-workspace/projects/{ident}/schedule')
    async def schedule(request:Request,ident:str):
        auth(request);value=await json_body(request);interval=value.get('interval_hours',24);keep=value.get('keep',7)
        if not isinstance(interval,(int,float)) or not 1<=interval<=8760 or not isinstance(keep,int) or not 1<=keep<=100:fail('Интервал 1–8760 часов, хранить 1–100 копий')
        with locked(request) as data:
            p=lookup(data['projects'],ident);p['schedule']={'enabled':bool(value.get('enabled',False)),'interval_hours':interval,'keep':keep,'next_at':time.time()+interval*3600}
        return p['schedule']
    @app.get('/v1/chat-workspace/storages')
    async def storages(request:Request):auth(request);return {'items':[public_storage(s) for s in read(request)['storages']]}
    @app.post('/v1/chat-workspace/storages')
    async def storage_save(request:Request):
        auth(request);value=await json_body(request);kind=value.get('type');ident=value.get('id') or uuid.uuid4().hex
        with locked(request) as data:
            old=lookup(data['storages'],ident) if value.get('id') else {}
            row={'id':ident,'name':name(value.get('name')),'type':kind}
            if kind=='local':row['path']=local_allow(value.get('path',''))
            elif kind=='webdav':
                url=value.get('url','');parts=urlsplit(url)
                if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:fail('WebDAV: HTTPS URL без встроенного пароля')
                row['url']=url
                for k in ('username','password','token'):
                    v=value.get(k,'')
                    if not isinstance(v,str) or len(v)>8000:fail()
                    row[k]=old.get(k,'') if v==MASK else v
            else:fail('Доступны локальное хранилище и WebDAV')
            data['storages']=[r for r in data['storages'] if r['id']!=ident]+[row]
        audit(request,'save_storage',storage=ident);return public_storage(row)
    @app.post('/v1/chat-workspace/storages/{ident}/test')
    async def storage_test(request:Request,ident:str):
        auth(request);s=lookup(read(request)['storages'],ident)
        if s['type']=='webdav':return await webdav(s,'PROPFIND')
        return {'ok':Path(s['path']).is_dir()}
    @app.get('/v1/chat-workspace/storages/{ident}/files')
    async def storage_files(request:Request,ident:str,path:str=''):
        auth(request);s=lookup(read(request)['storages'],ident);relative(path)
        if s['type']=='webdav':return await remote_listing(s,path)
        root=Path(s['path']);p=safe(root,path,True)
        if not p.is_dir():fail('Это не папка')
        items=[]
        for child in sorted(p.iterdir(),key=lambda x:(not x.is_dir(),x.name.lower())):
            if child.is_symlink():continue
            st=child.stat();items.append({'name':child.name,'path':str(child.relative_to(root)),'type':'directory' if child.is_dir() else 'file','size':st.st_size,'modified':st.st_mtime})
            if len(items)>=5000:break
        return {'path':path,'items':items}
    @app.get('/v1/chat-workspace/storages/{ident}/file')
    async def storage_file(request:Request,ident:str,path:str):
        auth(request);s=lookup(read(request)['storages'],ident);relative(path)
        if s['type']=='webdav':
            response=await webdav(s,'GET',path,raw=True)
            return Response(response.content,media_type='application/octet-stream',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(PurePosixPath(path).name)})
        p=safe(Path(s['path']),path,True)
        if not p.is_file():fail('Это не файл')
        return FileResponse(p,filename=p.name)
    @app.put('/v1/chat-workspace/storages/{ident}/file')
    async def storage_write(request:Request,ident:str,path:str):
        auth(request);s=lookup(read(request)['storages'],ident);relative(path);raw=await body(request)
        if not path:fail('Укажите имя файла')
        if s['type']=='webdav':result=await webdav(s,'PUT',path,raw)
        else:
            p=safe(Path(s['path']),path)
            if not p.parent.is_dir():fail('Папка не найдена',404)
            fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as h:h.write(raw)
            own_path(p,request);result={'ok':True,'size':len(raw)}
        audit(request,'storage_write',storage=ident,path=path);return result
    @app.post('/v1/chat-workspace/storages/{ident}/files')
    async def storage_operation(request:Request,ident:str):
        auth(request);s=lookup(read(request)['storages'],ident);value=await json_body(request);path=value.get('path','');relative(path)
        if not path:fail('Корень хранилища защищён')
        action=value.get('action')
        if action not in {'mkdir','move','delete'}:fail('Неизвестная операция')
        if s['type']=='webdav':
            headers={}
            if action=='move':
                to=value.get('to','');relative(to)
                if not to or to==path or to.startswith(path.rstrip('/')+'/'):fail('Недопустимое назначение')
                headers={'Destination':webdav_url(s,to),'Overwrite':'F'}
            result=await webdav(s,{'mkdir':'MKCOL','move':'MOVE','delete':'DELETE'}[action],path,extra_headers=headers)
        else:
            root=Path(s['path']);p=safe(root,path)
            if action=='mkdir':
                if p.exists():fail('Папка уже существует',409)
                p.mkdir(mode=0o700);own_path(p,request)
            elif action=='move':
                target=safe(root,value.get('to',''))
                if target==root or target.exists() or target.resolve().is_relative_to(p.resolve()):fail('Недопустимое назначение',409)
                shutil.move(str(p),str(target))
            else:
                trash=folder(request)/'trash';trash.mkdir(mode=0o700,exist_ok=True);own_path(trash,request)
                shutil.move(str(p),str(trash/(uuid.uuid4().hex+'-'+p.name)))
            result={'ok':True}
        audit(request,'storage_'+action,storage=ident,path=path);return result
    @app.post('/v1/chat-workspace/projects/{ident}/backups/{backup_id}/export')
    async def export(request:Request,ident:str,backup_id:str):
        auth(request);value=await json_body(request);data=read(request);lookup(data['projects'],ident);lookup(backup_list(request,ident),backup_id);s=lookup(data['storages'],value.get('storage_id'))
        p=folder(request)/'backups'/ident/(backup_id+'.zip');filename='project-'+ident+'-'+backup_id+'.zip'
        if s['type']=='webdav':result=await webdav(s,'PUT',filename,p.read_bytes())
        else:
            target=safe(Path(s['path']),filename);await asyncio.to_thread(shutil.copyfile,p,target);result={'ok':True}
        audit(request,'export_backup',project=ident,backup=backup_id,storage=s['id']);return result
    app.state.workspace_installed=True

async def start_scheduler(app):
    async def loop():
        while True:
            await asyncio.sleep(60)
            root=app.state.chat_jobs_root/'workspace'
            if not root.exists():continue
            for owner_dir in root.iterdir():
                if not owner_dir.is_dir() or owner_dir.is_symlink() or len(owner_dir.name)!=64 or any(c not in '0123456789abcdef' for c in owner_dir.name):continue
                request=Request({'type':'http','method':'GET','path':'/','headers':[],'app':app,'state':{'workspace_owner':owner_dir.name}})
                await run_due(request)
    app.state.workspace_scheduler=asyncio.create_task(loop())

async def stop_scheduler(app):
    task=getattr(app.state,'workspace_scheduler',None)
    if task:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
