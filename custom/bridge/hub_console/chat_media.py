"""Owner-private local media jobs; fixed workflows, no arbitrary graph execution.

ComfyUI is trusted loopback infrastructure, never exposed to a browser. Submission
is recorded before I/O and is never automatically replayed after an interruption.
"""
from __future__ import annotations
import asyncio
import contextlib
import json
import re
import shutil
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from .chat_worker import atomic, load
from .chat_harness import payload, text
from .chat_jobs import owner_id, directory

TERMINAL={'completed','failed','cancelled','interrupted'}
MODELS={
 'sd15':dict(name='Stable Diffusion 1.5 · локально',kind='image',width=512,height=512,checkpoint='v1-5-pruned-emaonly.safetensors',notes='Базовая модель для первого запуска. Хуже современных моделей понимает сложные запросы и текст на картинке.'),
 'krea2':dict(name='Krea 2 Turbo Q3 · локально',kind='image',width=768,height=768,notes='Требует больше оперативной памяти для текстового энкодера.'),
 'wan13b':dict(name='Wan2.1 1.3B · видео',kind='video',available=False,notes='Не настроен: веса и проверка памяти/совместимости ещё не выполнены. V100 пока не установлена.'),
}

def config(request):
    return json.loads(request.app.state.config_file.read_text()).get('media_generation',{})

def base(request):
    p=directory(request)/'media';p.mkdir(mode=0o700,exist_ok=True);return p

def scoped(request,ident):
    if not re.fullmatch('[a-f0-9]{32}',ident):raise HTTPException(404,'Материал не найден')
    p=base(request)/owner_id(request)/ident
    if not (p/'job.json').is_file():raise HTTPException(404,'Материал не найден')
    return p

def listing(request):
    who=owner_id(request);p=base(request)/who
    return sorted((load(x,{}) for x in p.glob('*/job.json')),key=lambda j:j.get('created_at',0),reverse=True)[:100]

def summary(j):
    return {k:v for k,v in j.items() if k not in {'prompt_id'}}

def update(p,**values):
    j=load(p/'job.json',{});j.update(values);atomic(p/'job.json',j);return j

def workflow(model,prompt,seed):
    if model=='krea2':
        from .modules.local_ai import _workflow
        result=_workflow(prompt,seed);result['9']['inputs']['filename_prefix']='workbench';return result
    return {
      '1':{'class_type':'CheckpointLoaderSimple','inputs':{'ckpt_name':MODELS['sd15']['checkpoint']}},
      '2':{'class_type':'CLIPTextEncode','inputs':{'clip':['1',1],'text':prompt}},
      '3':{'class_type':'CLIPTextEncode','inputs':{'clip':['1',1],'text':'blurry, distorted, watermark'}},
      '4':{'class_type':'EmptyLatentImage','inputs':{'width':512,'height':512,'batch_size':1}},
      '5':{'class_type':'KSampler','inputs':{'model':['1',0],'positive':['2',0],'negative':['3',0],'latent_image':['4',0],'seed':seed,'steps':20,'cfg':7.0,'sampler_name':'euler','scheduler':'normal','denoise':1.0}},
      '6':{'class_type':'VAEDecode','inputs':{'samples':['5',0],'vae':['1',2]}},
      '9':{'class_type':'SaveImage','inputs':{'images':['6',0],'filename_prefix':'workbench'}}}

async def ready(request,cfg):
    """Optional ai-hub dispatcher; never evict a model to make space."""
    if not cfg.get('hub_dispatcher'):return
    from .modules import local_ai as ai
    from .config import Settings
    settings=Settings(data_dir=Path('/var/lib/hub-console'),desktop_user=request.app.state.settings.desktop_user)
    state=SimpleNamespace(settings=settings)
    local=SimpleNamespace(app=SimpleNamespace(state=state))
    try:
        available=int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:')))//1024
        active=await ai._active(local,ai.SERVICES['comfyui'])=='active'
        warm=False
        if active and request.state.media_model=='sd15':
            try:
                h=(await ai._http(local).get('http://127.0.0.1:8188/history',params={'max_items':1})).json()
                last=next(iter(h.values()),{})
                graph=last.get('prompt',[None,None,{}])[2]
                warm=last.get('status',{}).get('status_str')=='success' and graph.get('1',{}).get('inputs',{}).get('ckpt_name')==MODELS['sd15']['checkpoint']
            except (httpx.HTTPError,ValueError,IndexError,AttributeError):pass
        minimum=6000 if request.state.media_model=='krea2' else 1000 if warm else 3000
        if available<minimum:raise HTTPException(409,f'Недостаточно свободной RAM: {available} МБ, нужно минимум {minimum} МБ. Службы не выгружались.')
        if active:
            ai._touch(local);return
        gpu=ai._target(local,await ai.gpu_info(local))
        if not gpu or ai._free(gpu)<4500:raise HTTPException(409,'Недостаточно свободной видеопамяти RTX 4060: нужно 4500 МБ. NVR и голос не выгружались.')
        st=ai._load(local)
        if st['holder'] and st['holder']!='comfyui':raise HTTPException(409,'GPU занят другой службой')
        st.update(holder='comfyui',since=time.time(),last_use=time.time());ai._save(local,st)
        r=await ai._sc(local,ai.SERVICES['comfyui'],'start','hub-comfyui.service',timeout=60)
        if not r.ok:
            st.update(holder=None,since=0,last_use=0);ai._save(local,st)
            raise HTTPException(503,'ComfyUI не запустился')
    finally:
        if getattr(state,'localai_http',None):await state.localai_http.aclose()

async def animate(p,source):
    proc=None
    try:
        update(p,status='running',started_at=time.time(),message='Создаём плавный наезд на картинку (без ИИ-движения)')
        proc=await asyncio.create_subprocess_exec('nice','-n','10','ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','2','-loop','1','-i',str(source),'-vf',"zoompan=z='min(zoom+0.001,1.12)':d=120:s=512x512:fps=24",'-t','5','-an','-c:v','libx264','-threads','2','-filter_threads','1','-pix_fmt','yuv420p','-movflags','+faststart','-f','mp4',str(p/'video.part'),stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(proc.wait(),60)
        if proc.returncode!=0:raise ValueError('ffmpeg failed')
        output=p/'video.part'
        if not 1000<output.stat().st_size<32*1024*1024:raise ValueError('invalid size')
        output.chmod(0o600);os.replace(output,p/'video.mp4')
        update(p,status='completed',finished_at=time.time(),mime='video/mp4',message='Анимация готова · 5 секунд, без звука и ИИ-движения')
    except asyncio.CancelledError:
        update(p,status='interrupted',finished_at=time.time(),message='Анимация прервана. Автоматического повтора нет.');raise
    except Exception:
        update(p,status='failed',finished_at=time.time(),message='Не удалось создать анимацию. Исходное изображение сохранено.')
    finally:
        if proc and proc.returncode is None:
            proc.kill();await proc.wait()
        (p/'video.part').unlink(missing_ok=True)

async def submit(request,p):
    cfg=config(request);j=load(p/'job.json',{})
    try:
        request.state.media_model=j['model'];await ready(request,cfg)
        async with httpx.AsyncClient(base_url=cfg.get('comfy_url','http://127.0.0.1:8188'),trust_env=False,timeout=15) as c:
            for _ in range(45):
                if load(p/'job.json',{}).get('status')=='cancelled':return
                try:
                    r=await c.get('/system_stats');r.raise_for_status();break
                except httpx.HTTPError:await asyncio.sleep(1)
            else:raise HTTPException(503,'ComfyUI не ответил после запуска')
            q=(await c.get('/queue')).json()
            if q.get('queue_running') or q.get('queue_pending'):raise HTTPException(409,'ComfyUI занят. Задание не отправлялось; повторите после завершения текущей генерации.')
            update(p,status='submitting',started_at=time.time(),message='Передаём задание в ComfyUI')
            # From this point a network failure is ambiguous: never resend.
            r=await c.post('/prompt',json={'prompt':workflow(j['model'],j['prompt'],j['seed'])});r.raise_for_status()
            ident=r.json().get('prompt_id')
            if not ident:raise HTTPException(502,'ComfyUI отклонил workflow')
            update(p,status='running',prompt_id=ident,message='Генерация выполняется')
    except asyncio.CancelledError:
        update(p,status='interrupted',finished_at=time.time(),message='Мост остановлен при передаче задания. Автоматического повтора нет.');raise
    except Exception as e:
        previous=load(p/'job.json',{})
        update(p,status='interrupted' if previous.get('status')=='submitting' else 'failed',finished_at=time.time(),message=e.detail if isinstance(e,HTTPException) else 'Ошибка связи с ComfyUI. Проверьте состояние генератора; автоматического повтора нет.')

async def refresh(request,p):
    async with request.app.state.media_refresh_lock:
        return await _refresh(request,p)

async def _refresh(request,p):
    j=load(p/'job.json',{})
    if j.get('status') not in TERMINAL and j.get('prompt_id'):
        try:
            async with httpx.AsyncClient(base_url=config(request).get('comfy_url','http://127.0.0.1:8188'),trust_env=False,timeout=15) as c:
                r=await c.get('/history/'+j['prompt_id']);r.raise_for_status();h=r.json().get(j['prompt_id'])
                if not h and time.time()-j.get('started_at',j.get('created_at',time.time()))>30:
                    q=await c.get('/queue');q.raise_for_status();queue=q.json()
                    present=any(len(row)>1 and row[1]==j['prompt_id'] for row in queue.get('queue_running',[])+queue.get('queue_pending',[]))
                    if not present:return update(p,status='interrupted',finished_at=time.time(),message='ComfyUI больше не хранит это задание. Выполнение не подтверждено; автоматического повтора нет.')
                if h:
                    if h.get('status',{}).get('status_str')!='success':return update(p,status='failed',finished_at=time.time(),message='Генерация не завершена: проверьте память и журнал ComfyUI.')
                    item=h.get('outputs',{}).get('9',{}).get('images',[{}])[0]
                    if not item.get('filename'):raise ValueError('No output')
                    image=await c.get('/view',params={k:item[k] for k in ('filename','subfolder','type') if k in item});image.raise_for_status()
                    if not image.content.startswith(b'\x89PNG\r\n\x1a\n') or len(image.content)>32*1024*1024:raise ValueError('Invalid PNG')
                    dest=p/'image.png';dest.write_bytes(image.content);dest.chmod(0o600)
                    ended=next((data.get('timestamp',0)/1000 for event,data in reversed(h.get('status',{}).get('messages',[])) if event=='execution_success' and data.get('timestamp')),time.time())
                    return update(p,status='completed',finished_at=ended,message='Изображение готово',mime='image/png')
        except (httpx.HTTPError,ValueError,KeyError,IndexError):pass
    return j

def install(app,owner_setup):
    # Interrupted preparation is never submitted a second time on restart.
    for path in (Path(app.state.chat_jobs_root)/'media').glob('*/*/job.json'):
        j=load(path,{})
        if j.get('status') in {'queued','submitting'} or (j.get('status')=='running' and j.get('model')=='image-motion'):
            update(path.parent,status='interrupted',finished_at=time.time(),message='Подготовка прервана перезапуском. Автоматического повтора нет.')
    app.state.media_tasks=set();app.state.media_by_id={};app.state.media_refresh_lock=asyncio.Lock();app.state.media_submit_lock=asyncio.Lock()
    @app.get('/v1/media')
    async def catalog(request:Request):
        owner_setup(request);owner_id(request);cfg=config(request)
        enabled=cfg.get('enabled',False)
        models=[dict(v,id=k,available=enabled and k in cfg.get('models',[])) for k,v in MODELS.items()]
        return {'enabled':enabled,'animation_available':enabled and bool(shutil.which('ffmpeg')),'models':models,'jobs':[summary(j) for j in listing(request)],'notes':'Локальная генерация. Запросы и результаты хранятся у владельца; облачная оплата не используется.'}
    @app.post('/v1/media')
    async def create(request:Request):
        owner_setup(request);who=owner_id(request);cfg=config(request);body=await payload(request)
        model=body.get('model','sd15');prompt=text(body.get('prompt',''),1000).strip()
        if not prompt:raise HTTPException(422,'Введите описание изображения')
        if not cfg.get('enabled') or model not in cfg.get('models',[]) or model not in MODELS or MODELS[model]['kind']!='image':raise HTTPException(409,'Выбранный генератор не подключён')
        seed=body.get('seed',int(time.time())%2**32)
        if isinstance(seed,bool) or not isinstance(seed,int) or not 0<=seed<2**32:raise HTTPException(422,'Некорректный seed')
        async with app.state.media_submit_lock:
            for existing in base(request).glob('*/*/job.json'):
                j=load(existing,{})
                if j.get('status') not in TERMINAL:
                    if j.get('prompt_id'):await refresh(request,existing.parent);j=load(existing,{})
                    if j.get('status') not in TERMINAL:raise HTTPException(409,'Уже выполняется локальная генерация. Дождитесь завершения.')
            ident=uuid.uuid4().hex;p=base(request)/who/ident;p.mkdir(mode=0o700,parents=True)
            j=dict(id=ident,model=model,prompt=prompt,seed=seed,chat_id=text(body.get('chat_id',''),200),status='queued',message='Подготовка генератора',created_at=time.time(),width=MODELS[model]['width'],height=MODELS[model]['height']);atomic(p/'job.json',j)
            task=asyncio.create_task(submit(request,p));app.state.media_tasks.add(task);app.state.media_by_id[ident]=task;task.add_done_callback(app.state.media_tasks.discard);task.add_done_callback(lambda t:app.state.media_by_id.pop(ident,None))
        return summary(j)
    @app.post('/v1/media/{ident}/animate')
    async def create_animation(ident:str,request:Request):
        owner_setup(request);source=scoped(request,ident);original=await refresh(request,source)
        if not config(request).get('enabled') or not shutil.which('ffmpeg'):raise HTTPException(409,'Анимация не подключена')
        if original.get('status')!='completed' or not (source/'image.png').is_file():raise HTTPException(409,'Сначала создайте изображение')
        async with app.state.media_submit_lock:
            for existing in base(request).glob('*/*/job.json'):
                if load(existing,{}).get('status') not in TERMINAL:raise HTTPException(409,'Дождитесь завершения текущей генерации')
            new=uuid.uuid4().hex;p=base(request)/owner_id(request)/new;p.mkdir(mode=0o700,parents=True)
            j=dict(id=new,model='image-motion',kind='animation',source_id=ident,prompt=original.get('prompt',''),chat_id=original.get('chat_id',''),width=512,height=512,status='queued',created_at=time.time(),message='Анимация картинки · плавный наезд (без ИИ-движения)')
            atomic(p/'job.json',j);task=asyncio.create_task(animate(p,source/'image.png'));app.state.media_tasks.add(task);app.state.media_by_id[new]=task;task.add_done_callback(app.state.media_tasks.discard);task.add_done_callback(lambda t:app.state.media_by_id.pop(new,None))
        return summary(j)

    @app.get('/v1/media/{ident}')
    async def status(ident:str,request:Request):
        owner_setup(request);p=scoped(request,ident);return summary(await refresh(request,p))
    @app.get('/v1/media/{ident}/file')
    async def file(ident:str,request:Request):
        owner_setup(request);p=scoped(request,ident);j=await refresh(request,p)
        ext='mp4' if j.get('mime')=='video/mp4' else 'png'
        asset=p/('video.mp4' if ext=='mp4' else 'image.png')
        if j.get('status')!='completed' or not asset.is_file():raise HTTPException(409,'Материал ещё не готов')
        return FileResponse(asset,media_type=j.get('mime','image/png'),filename='Sasha-'+ident[:8]+'.'+ext,headers={'Cache-Control':'no-store'})
    @app.post('/v1/media/{ident}/cancel')
    async def cancel(ident:str,request:Request):
        owner_setup(request);p=scoped(request,ident);j=load(p/'job.json',{})
        if j.get('status') in TERMINAL:return summary(j)
        if j.get('status')=='submitting':raise HTTPException(409,'Дождитесь завершения передачи задания')
        if not j.get('prompt_id'):
            task=app.state.media_by_id.get(ident)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):await task
        if j.get('prompt_id'):
            async with httpx.AsyncClient(base_url=config(request).get('comfy_url','http://127.0.0.1:8188'),trust_env=False,timeout=15) as c:
                # Only pending job deletion: global interrupt can race another owner's work.
                q=(await c.get('/queue')).json()
                if any(row[1]==j['prompt_id'] for row in q.get('queue_running',[])):raise HTTPException(409,'Генерация уже выполняется. Общий interrupt отключён, чтобы не прервать чужой запрос.')
                r=await c.post('/queue',json={'delete':[j['prompt_id']]});r.raise_for_status()
        return summary(update(p,status='cancelled',finished_at=time.time(),message='Задание отменено'))
