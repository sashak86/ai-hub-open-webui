"""
title: AI Hub — агенты
version: 1.0.0
description: Локальные Codex и Claude Code: очередь, ход работы и вложения.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import io
import uuid
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

MAX_BYTES = 32 * 1024 * 1024
UPLOAD_ROOT = Path('/app/backend/data/uploads')


def identity(user):
    uid = (user or {}).get('id')
    if not isinstance(uid, str) or not uid:
        raise ValueError('Войдите в свой аккаунт перед отправкой.')
    return hashlib.sha256(uid.encode()).hexdigest()


def text_and_images(messages):
    latest = next((m for m in reversed(messages) if m.get('role') == 'user'), {})
    content = latest.get('content', '')
    if isinstance(content, str):
        return content, []
    text, images = [], []
    for part in content or []:
        if part.get('type') == 'text':
            text.append(part.get('text', ''))
        elif part.get('type') == 'image_url':
            image = part.get('image_url', {})
            images.append(image.get('url', '') if isinstance(image, dict) else image)
    return '\n'.join(text), images


def inline_image(url, index):
    if not isinstance(url, str) or len(url) > MAX_BYTES * 4 // 3 + 256:
        raise ValueError('Изображение слишком большое.')
    match = re.fullmatch(r'data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)', url)
    if not match:
        raise ValueError('Прикрепите изображение через загрузку файла; внешние ссылки не скачиваются.')
    try:
        raw = base64.b64decode(match[2], validate=True)
    except ValueError:
        raise ValueError('Повреждённое изображение.') from None
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError('Изображение пустое или больше 32 МБ.')
    return {'name': f'image-{index}.{match[1].split("/")[1]}', 'mime': match[1],
            'content_base64': base64.b64encode(raw).decode()}, len(raw)


async def collect_attachments(files, images, user):
    # Resolve records by ID AND authenticated owner, never trust client path/name/data.
    from open_webui.models.files import Files
    uid = (user or {}).get('id')
    identity(user)
    attachments, seen, total = [], set(), 0
    for item in files or []:
        fid = item.get('id') or (item.get('file') or {}).get('id')
        if not isinstance(fid, str) or fid in seen:
            continue
        record = await Files.get_file_by_id_and_user_id(fid, uid)
        if record is None:
            raise ValueError('Один из файлов не принадлежит вашему аккаунту или удалён.')
        if not record.path:
            raise ValueError('Файл ещё не загружен. Дождитесь завершения загрузки.')
        path = Path(record.path).resolve()
        if not path.is_relative_to(UPLOAD_ROOT.resolve()) or not path.is_file():
            raise ValueError('Недопустимое расположение вложения.')
        size = path.stat().st_size
        total += size
        if total > MAX_BYTES:
            raise ValueError('Общий размер вложений не должен превышать 32 МБ.')
        raw = await asyncio.to_thread(path.read_bytes)
        if len(raw) != size:
            raise ValueError('Файл изменился при чтении. Загрузите его повторно.')
        mime = (record.meta or {}).get('content_type') or 'application/octet-stream'
        name = Path((record.meta or {}).get('name') or record.filename).name
        attachments.append({'name': name, 'mime': mime, 'content_base64': base64.b64encode(raw).decode()})
        seen.add(fid)
    # Uploaded image records may also appear as inline URLs; avoid duplicates.
    for index, url in enumerate(images):
        if isinstance(url, str) and not url.startswith('data:'):
            parts = urlsplit(url)
            match = re.fullmatch(r'/api/v1/files/([^/]+)/content', parts.path)
            trusted_origins = {'192.168.0.29:8446', '127.0.0.1:8446', 'localhost:8446'}
            if match and (not parts.netloc or (parts.scheme == 'https' and parts.netloc in trusted_origins)):
                if match[1] not in seen:
                    extra = await collect_attachments([{'id': match[1]}], [], user)
                    for obj in extra:
                        total += len(base64.b64decode(obj['content_base64']))
                        attachments.append(obj)
                    seen.add(match[1])
                continue
            raise ValueError('Внешние изображения не скачиваются: загрузите файл.')
        # Same uploaded image is commonly included both as a file record and inline data.
        obj, size = inline_image(url, index)
        digest = hashlib.sha256(base64.b64decode(obj['content_base64'])).digest()
        if any(hashlib.sha256(base64.b64decode(a['content_base64'])).digest() == digest for a in attachments):
            continue
        total += size
        attachments.append(obj)
        if total > MAX_BYTES:
            raise ValueError('Общий размер вложений не должен превышать 32 МБ.')
    if total > MAX_BYTES:
        raise ValueError('Общий размер вложений не должен превышать 32 МБ.')
    return attachments


def deepseek_reasoning_block(value):
    """Untrusted provider output, escaped inside an exact owned wrapper."""
    import html
    if not isinstance(value,str) or not value:return ''
    return '<details data-ai-hub-reasoning="deepseek-v1"><summary>Рассуждение DeepSeek</summary>\n<pre>'+html.escape(value[:65536])+'</pre></details>\n\n'


def strip_deepseek_reasoning(value):
    import re
    return re.sub(r'\A<details data-ai-hub-reasoning="deepseek-v1"><summary>Рассуждение DeepSeek</summary>\n<pre>[^<]*</pre></details>\n\n','',value,count=1)


def visible_history(messages):
    """Only selected visible conversation, never system instructions/private reasoning."""
    last_user = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=-1)
    history, total = [], 0
    for message in messages[:last_user]:
        if message.get('role') not in {'user', 'assistant'}:
            continue
        content = message.get('content', '')
        if isinstance(content, list):
            content = '\n'.join(part.get('text', '') for part in content if part.get('type') == 'text')
        if not isinstance(content, str):
            continue
        if message.get('role')=='assistant':content=strip_deepseek_reasoning(content)
        total += len(content)
        if total > 200000:
            raise ValueError('Для смены агента история слишком длинная. Создайте новый чат с кратким контекстом.')
        if content:
            history.append({'role': message['role'], 'content': content})
    return history


def display_time(value, full=False):
    try:
        stamp = datetime.fromtimestamp(float(value)) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(ZoneInfo(os.environ.get('HUB_CHAT_TIMEZONE', 'Europe/Moscow'))).strftime('%d.%m %H:%M:%S' if full else '%H:%M:%S')
    except (ValueError, TypeError, OverflowError):
        return str(value)


def status_description(event):
    state = event.get('status', 'running')
    label = {'queued': 'В очереди', 'running': 'Работает', 'completed': 'Готово',
             'cancelled': 'Остановлено', 'interrupted': 'Прервано', 'failed': 'Ошибка'}.get(state, 'Работает')
    parts = [label]
    started = event.get('started_at')
    if started:
        parts.append('старт ' + display_time(started))
    elapsed = max(0, int(event.get('elapsed') or 0))
    parts.append(f'{elapsed // 60} мин {elapsed % 60:02d} с')
    description = event.get('description')
    if isinstance(description, str) and description and description != label:
        parts.append(description[:300])
    return ' · '.join(parts)


def merge_status(history, status):
    history = list(history or [])
    if (history and status.get('action') in {'ai_hub_job','ai_hub_media'} and status.get('stage_key')
            and history[-1].get('action') == status.get('action')
            and history[-1].get('job_id') == status.get('job_id')
            and history[-1].get('stage_key') == status['stage_key']):
        history[-1] = status
    else:
        history.append(status)
    return history


async def status_emitter(metadata, user, fallback):
    # Own native function statuses use bounded phase history instead of one DB row
    # entry per clock tick. Native chat content is updated independently as before.
    try:
        from open_webui.models.chats import Chats
        from open_webui.socket.main import get_event_emitter
    except ImportError:
        return fallback  # Isolated adapter tests outside the installed WebUI.
    chat_id, message_id = metadata.get('chat_id'), metadata.get('message_id')
    chat = await Chats.get_chat_by_id(chat_id)
    if not chat or chat.user_id != user['id']:
        raise ValueError('Чат недоступен для текущего аккаунта.')
    live = await get_event_emitter({**metadata, 'user_id': user['id']}, update_db=False)
    async def emit(event):
        if event.get('type') == 'status':
            current = await Chats.get_message_by_id_and_message_id(chat_id, message_id)
            history = merge_status((current or {}).get('statusHistory', []), event.get('data', {}))
            await Chats.upsert_message_to_chat_by_id_and_message_id(chat_id, message_id, {'statusHistory': history}, touch=False)
        if live:
            await live(event)
    return emit


def image_intent(text,has_image=False):
    """Only an explicit current-user imperative/command, never quoted history."""
    match=re.match(r'^\s*(?:/(?:image|edit)(?:\s+|$)|(?:пожалуйста[, ]+)?(?:нарисуй(?:те)?|(?:создай(?:те)?|сгенерируй(?:те)?)\s+(?:изображение|картинку|рисунок|фото))\s*[:,—-]?\s+)([\s\S]+)$',text,re.I)
    if match:return match.group(1).strip()
    if has_image:
        edit=re.match(r'^\s*(?:пожалуйста[, ]+)?(?:измени|исправь|отредактируй|замени|перерисуй|убери|добавь)\s+([\s\S]+)$',text,re.I)
        if edit:return text.strip()
    return None


async def media_file(client,job,headers,user):
    # Use native storage and owner checks, so normal Markdown images survive reload.
    from open_webui.models.files import Files,FileForm
    from open_webui.storage.provider import Storage
    uid=user['id'];fid=str(uuid.uuid5(uuid.NAMESPACE_URL,'sasha-media:'+uid+':'+job['id']))
    existing=await Files.get_file_by_id_and_user_id(fid,uid)
    if not existing:
        response=await client.get('/v1/media/'+job['id']+'/file',headers=headers);response.raise_for_status();raw=response.content
        if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw)>MAX_BYTES:raise ValueError('Генератор вернул недопустимое изображение.')
        name='Sasha-'+job['model']+'-'+job['id'][:8]+'.png'
        _,path=await asyncio.to_thread(Storage.upload_file,io.BytesIO(raw),fid+'.png',{'OpenWebUI-User-Id':uid,'OpenWebUI-File-Id':fid})
        form=FileForm(id=fid,filename=name,path=path,data={},meta={'name':name,'content_type':'image/png','size':len(raw),'file_hash':hashlib.sha256(raw).hexdigest(),'data':{'media_job_id':job['id']}})
        try:existing=await Files.insert_new_file(uid,form)
        except Exception:
            existing=await Files.get_file_by_id_and_user_id(fid,uid)
            if not existing:raise ValueError('Не удалось сохранить результат в чат.') from None
        if not existing:raise ValueError('Не удалось сохранить результат в чат.')
    return '/api/v1/files/'+fid+'/content'


def media_error(response):
    try:
        value=response.json();reason=value.get('error',{}).get('message') or value.get('detail')
        return reason if isinstance(reason,str) else 'Локальная генерация недоступна.'
    except (ValueError,AttributeError):return 'Локальная генерация недоступна.'


async def image_turn(pipe,text,metadata,user,files,images,emitter,prepared=None):
    attachments=prepared if prepared is not None else await collect_attachments(files,images,user) if files or images else []
    sources=[a for a in attachments if a['mime'].startswith('image/') and a['name'].lower()!='mask.png']
    masks=[a for a in attachments if a['name'].lower()=='mask.png']
    if attachments and (len(sources)!=1 or len(masks)>1 or len(sources)+len(masks)!=len(attachments)):
        yield 'Прикрепите один PNG/JPEG/WebP референс. Для правки области добавьте mask.png (белое — изменить, чёрное — сохранить). Маску удобно нарисовать в «Изображения и видео».';return
    owner=identity(user);headers={'X-WebUI-User':owner};prompt=image_intent(text,bool(sources))
    if not prompt:return
    async def status(job):
        if emitter:
            await emitter({'type':'status','data':{'action':'ai_hub_media','job_id':job.get('id'),'stage_key':job.get('status')+':'+job.get('message',''),'description':'Локальное изображение · '+job.get('message',''),'done':job.get('status') in {'completed','failed','interrupted','cancelled'}}})
    async with pipe.client() as client:
        catalog=await client.get('/v1/media',headers=headers)
        if catalog.status_code>=400:
            yield media_error(catalog);return
        options=catalog.json()
        model=metadata.get('hub_media_model') or options.get('default_model','sdxl-lightning')
        if not isinstance(model,str) or not any(r.get('id')==model and r.get('available',True) and r.get('kind','image')=='image' for r in options.get('models',[])):
            yield 'Выбранная модель изображения не подключена. Откройте выбор модели и повторите запрос.';return
        key='chat:'+hashlib.sha256((metadata['chat_id']+':'+metadata['message_id']).encode()).hexdigest()
        result=await client.post('/v1/media',headers=headers,json={'model':model,'prompt':prompt,'chat_id':metadata['chat_id'],'client_request_id':key,**({'mode':'inpaint' if masks else 'reference','source_base64':sources[0]['content_base64'],**({'mask_base64':masks[0]['content_base64']} if masks else {})} if sources else {})});
        if result.status_code>=400:
            yield media_error(result);return
        job=result.json();phase=None
        while True:
            current=(job.get('status'),job.get('message'))
            if phase!=current:await status(job);phase=current
            if job.get('status') in {'completed','failed','interrupted','cancelled'}:break
            await asyncio.sleep(2)
            result=await client.get('/v1/media/'+job['id'],headers=headers);result.raise_for_status();job=result.json()
        if job.get('status')!='completed':
            yield job.get('message') or 'Изображение не создано. Автоматического повтора нет.';return
        url=await media_file(client,job,headers,user)
        name=next((row['name'] for row in options.get('models',[]) if row['id']==job['model']),job['model'])
        start=job.get('started_at',job['created_at']);end=job.get('finished_at',start);elapsed=max(0,end-job['created_at'])
        yield '![Сгенерированное изображение]('+url+')\n\n'+name+' · '+str(job['width'])+'×'+str(job['height'])
        if job.get('translated') and job.get('effective_prompt'):
            # Plain fenced text, bounded and generated locally; never executable markup.
            description=re.sub(r'([\\`*_{}\[\]()#+.!>|-])',r'\\\1',job['effective_prompt'][:2500])
            yield '\n\nОписание для генерации (переведено локально):\n\n> '+description.replace('\n','\n> ')
        zone=ZoneInfo('Europe/Moscow');a=datetime.fromtimestamp(start,zone).strftime('%H:%M:%S');b=datetime.fromtimestamp(end,zone).strftime('%H:%M:%S')
        yield '\n\n---\n'+a+'–'+b+' · '+str(round(elapsed,1))+' с · МСК (с подготовкой)'


class Pipe:
    def __init__(self):
        self.type = 'manifold'
        self.name = ''

    def client(self):
        token = os.environ.get('HUB_CHAT_TOKEN') or os.environ.get('OPENAI_API_KEY', '')
        if not token:
            raise ValueError('Подключение к агентам не настроено администратором.')
        return httpx.AsyncClient(
            base_url=os.environ.get('HUB_CHAT_URL', 'https://192.168.0.29:8445'),
            headers={'Authorization': 'Bearer ' + token}, trust_env=False,
            verify=os.environ.get('SSL_CERT_FILE', True),
            timeout=httpx.Timeout(None, connect=15),
        )

    async def pipes(self):
        async with self.client() as client:
            response = await client.get('/v1/jobs/models')
            response.raise_for_status()
            return [{'id': model['id'], 'name': model['name']} for model in response.json().get('data', [])
                    if model.get('available', True)]

    async def pipe(self, body: dict, __user__: dict = None, __files__: list = None,
                   __metadata__: dict = None, __event_emitter__=None):
        metadata = __metadata__ or {}
        owner = identity(__user__)
        chat_id = metadata.get('chat_id')
        message_id = metadata.get('message_id')
        queued_user_id = metadata.get('user_message_id', '')
        if isinstance(queued_user_id, str) and queued_user_id.startswith('hubq-') and not metadata.get('regeneration_message_id'):
            message_id = 'queue:' + queued_user_id + ':' + (body.get('hub_api_model') or body.get('model', '').split('.', 1)[-1])
        if not isinstance(chat_id, str) or not isinstance(message_id, str):
            raise ValueError('Сохраните чат перед отправкой агенту.')
        text, images = text_and_images(body.get('messages', []))
        attachments = await collect_attachments(__files__, images, __user__) if __files__ or images else []
        if image_intent(text,any(a['mime'].startswith('image/') for a in attachments)) is not None:
            emitter=await status_emitter(metadata,__user__,__event_emitter__)
            async for chunk in image_turn(self,text,{**metadata,'message_id':message_id,'hub_media_model':body.get('hub_media_model')},__user__,__files__,images,emitter,prepared=attachments):yield chunk
            return
        history = visible_history(body.get('messages', []))
        model = (body.get('hub_api_model') or body.get('model', '').split('.', 1)[-1])
        if not text.strip() and not attachments:
            raise ValueError('Добавьте сообщение или файл.')
        headers = {'X-WebUI-User': owner}
        __event_emitter__ = await status_emitter(metadata, __user__, __event_emitter__)
        latest_status = {}
        async def emit(event):
            nonlocal latest_status
            latest_status = {**latest_status, **event}
            event = latest_status
            if event.get('status') in {'completed', 'failed', 'cancelled', 'interrupted'}:
                started = event.get('started_at')
                if started:
                    try:
                        timestamp = float(started) if isinstance(started, (int,float)) else datetime.fromisoformat(started.replace('Z','+00:00')).timestamp()
                        event['elapsed'] = max(float(event.get('elapsed') or 0), float(event.get('finished_at') or timestamp) - timestamp)
                    except (ValueError,TypeError):
                        pass
                event['description'] = {'completed':'Завершено','failed':'Запрос завершился с ошибкой','cancelled':'Остановлено','interrupted':'Прервано'}[event['status']]
            if __event_emitter__:
                await __event_emitter__({'type': 'status', 'data': {
                    'description': status_description(event),
                    'done': event.get('status') in {'completed', 'failed', 'cancelled', 'interrupted'},
                    'action': 'ai_hub_job', 'job_id': event.get('id'),
                    'stage_key': json.dumps([event.get('status'), event.get('description')], ensure_ascii=False),
                    'started_at': event.get('started_at'), 'elapsed': event.get('elapsed'),
                }})
        async with self.client() as client:
            if text.strip() == '/stop':
                result = await client.post('/v1/jobs/cancel-chat', headers=headers, json={'chat_id': chat_id})
                result.raise_for_status()
                yield 'Запрошена остановка заданий этого чата.'
                return
            await emit({'status': 'queued', 'description': 'Принимаем запрос'})
            try:
                limits_response = await client.get('/v1/jobs/limits', headers=headers, timeout=3)
            except httpx.HTTPError:
                limits_response = None
            if limits_response is not None and limits_response.is_success and __event_emitter__:
                for provider in limits_response.json().get('providers', []):
                    # Money belongs in the model-specific composer/modal, not dozens of status rows.
                    if provider.get('kind') in ('billing', 'local'):
                        continue
                    label = str(provider.get('provider', ''))
                    description = str(provider.get('description') or 'Данные о лимитах недоступны')
                    if provider.get('updated_at'):
                        description += ' · данные на ' + display_time(provider['updated_at'])
                    if provider.get('used_percent') is not None and '%' not in description:
                        description += f" · использовано {provider['used_percent']}%"
                    if provider.get('resets_at'):
                        description += ' · обновление ' + display_time(provider['resets_at'], full=True)
                    await __event_emitter__({'type': 'status', 'data': {'action': 'ai_hub_limits', 'description': f'{label}: {description}', 'done': True}})
            response = await client.post('/v1/jobs', headers=headers, json={
                'model': model, 'chat_id': chat_id, 'message_id': message_id,
                'text': text, 'attachments': attachments, 'history': history,
            })
            if response.is_error:
                raise ValueError('Не удалось принять запрос: проверьте модель и вложения.')
            job = response.json()
            job_id = job['id']
            await emit(job)
            seq = 0
            progress=[]
            final_text=''
            while True:
                try:
                    async with client.stream('GET', f'/v1/jobs/{job_id}/events', params={'after': seq}, headers=headers) as stream:
                        stream.raise_for_status()
                        async for line in stream.aiter_lines():
                            if not line.startswith('data:'):
                                continue
                            raw = line[5:].strip()
                            if not raw or raw == '[DONE]':
                                continue
                            event = json.loads(raw)
                            current = int(event.get('seq') or 0)
                            kind = event.get('type')
                            if current and current <= seq and kind not in {'status','done','error'}:
                                continue
                            seq = max(seq, current)
                            if kind == 'reasoning':
                                await emit({'status':'running','description':'DeepSeek · формирует рассуждение','id':job_id})
                            elif kind == 'text':
                                progress.append(event.get('text',''))
                                if event.get('channel')!='final':await emit({'status':'running','description':event.get('text','').strip()[:500] or 'Агент готовит ответ','id':job_id})
                            elif kind == 'final':
                                final_text=event.get('text','')
                            elif kind == 'status':
                                await emit({**event, 'id': job_id})
                            elif kind in {'done', 'error'}:
                                # Error is followed by durable done; let completion own the final.
                                if kind=='error':continue
                                result=await client.get(f'/v1/jobs/{job_id}',headers=headers)
                                result.raise_for_status()
                                saved=result.json()
                                state=saved.get('status') or event.get('status','failed')
                                await emit({**saved,'id':job_id,'status':state})
                                answer=saved.get('final_text') or final_text
                                if not answer:
                                    answer=''.join(progress).strip() if state=='completed' else {'cancelled':'Запрос остановлен пользователем. Завершение задачи не подтверждено.','interrupted':'Исполнитель прерван; запрос автоматически не повторялся.'}.get(state,'Агент не завершил запрос. Вывод сохранён; автоматического повтора нет.')
                                yield deepseek_reasoning_block(saved.get('reasoning_text'))+(answer or 'Агент завершил выполнение без итогового текста. Результат задачи не подтверждён.')
                                from datetime import timezone,timedelta
                                zone=timezone(timedelta(hours=3))
                                def stamp(value):
                                    return datetime.fromtimestamp(value,zone).strftime('%H:%M:%S') if isinstance(value,(int,float)) else 'не запускался'
                                start=saved.get('started_at');end=saved.get('finished_at')
                                duration=max(0,end-start) if isinstance(start,(int,float)) and isinstance(end,(int,float)) else 0
                                queue=max(0,(start or end or saved.get('created_at',0))-saved.get('created_at',0))
                                day=datetime.fromtimestamp(end,zone).strftime('%d.%m.%Y') if isinstance(end,(int,float)) else 'Время завершения отсутствует'
                                waiting=f' · очередь {queue:.1f} с' if queue>=1 else ''
                                duration_label=f'{int(duration)//60}м {int(duration)%60}с' if isinstance(end,(int,float)) else 'длительность неизвестна'
                                end_label=stamp(end) if isinstance(end,(int,float)) else 'неизвестно'
                                yield f"\n\n---\n{day} · {stamp(start)}–{end_label} (+03:00) · {duration_label}{waiting}"
                                return
                except asyncio.CancelledError:
                    # A disconnect must never stop a durable task. /stop is explicit.
                    raise
                except httpx.HTTPStatusError as error:
                    if 400 <= error.response.status_code < 500:
                        await emit({'status':'failed','description':'Доступ к заданию прекращён или оно удалено','id':job_id})
                        yield '\n\nНет доступа к заданию или оно удалено. Запрос повторно не отправлялся.'
                        return
                    await emit({'status': 'running', 'description': 'Восстанавливаем соединение; запрос сохранён', 'id': job_id})
                    await asyncio.sleep(2)
                except httpx.TransportError:
                    await emit({'status': 'running', 'description': 'Восстанавливаем соединение; запрос сохранён', 'id': job_id})
                    await asyncio.sleep(2)
                else:
                    # Server/service restart: resume only reading the same durable job.
                    await asyncio.sleep(1)
