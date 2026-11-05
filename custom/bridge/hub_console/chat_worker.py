"""Private, detached structured CLI worker. No tmux input or shell interpolation.

One flock per owner/chat, durable JSONL events, UUID resume. Bridge/browser exit
never cancels a turn. Native child exits on explicit cancel only.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path


def atomic(path, value):
    tmp = path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    with tmp.open('w') as f:
        os.chmod(tmp, 0o600)
        json.dump(value, f, ensure_ascii=False)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def load(path, fallback=None):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return fallback


class Events:
    def __init__(self, folder):
        self.folder=folder
        self.seq=sum(1 for _ in (folder/'events.jsonl').open()) if (folder/'events.jsonl').exists() else 0
        self.state=load(folder/'state.json', {})
        self.text=''
        self.reasoning=''
        self.final_candidate=''
        self.last_message=''
        self.side_effects=False
        self.last_checkpoint=0
    def emit(self, event):
        if event.get('type')=='reasoning':
            self.reasoning=(self.reasoning+event.get('text',''))[:65536]
        if event.get('type')=='text':
            self.text+=event.get('text','')
            event=dict(event,channel=event.get('channel','commentary'))
        if event.get('type')=='status' and any(t in event.get('description','') for t in ('инструмент','команд','файлы')):self.side_effects=True
        self.seq+=1
        event=dict(event, seq=self.seq, at=time.time())
        with (self.folder/'events.jsonl').open('a') as f:
            os.chmod(self.folder/'events.jsonl',0o600)
            f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush()
        if event['type']=='status':
            self.state.update({k:v for k,v in event.items() if k not in ('type','seq')})
            atomic(self.folder/'state.json',self.state)
            if time.monotonic()-self.last_checkpoint>10:
                desc=load(self.folder/'request.json',{});h=desc.get('harness')
                if h:
                    from .chat_harness import checkpoint
                    checkpoint(Path(h['root']),h['run_id'],event.get('status','running'),description=event.get('description',''),handoff=self.text[-16000:],side_effects=self.side_effects)
                    self.last_checkpoint=time.monotonic()
    def finalize(self,status):
        from .chat_artifacts import extract
        fallback={'failed':'Агент не завершил запрос. Выполненные действия и проверки подтверждены только сообщениями о ходе работы; автоматического повтора нет.', 'cancelled':'Запрос остановлен пользователем. Завершение задачи не подтверждено.', 'interrupted':'Исполнитель прерван. Завершение задачи не подтверждено; запрос автоматически не повторялся.'}
        candidate=self.final_candidate or self.last_message
        source='provider' if candidate and status=='completed' else 'status'
        if status!='completed':
            observed=(self.last_message or self.text).strip()[-1500:]
            candidate=fallback.get(status,'Запрос не завершён.')
            if observed:candidate+='\n\nПоследнее сообщение агента (может описывать незавершённую работу):\n'+observed
        candidate=candidate.strip() or 'Агент завершил выполнение без итогового текста. Результат задачи не подтверждён.'
        text,artifacts=extract(candidate)
        if not text:text='Подготовлены интерактивные материалы.'
        desc=load(self.folder/'request.json',{})
        if artifacts:text+='\n\n[Открыть интерактивное окно](/ai-hub/artifacts/'+desc.get('id','')+')'
        self.state.update(final_text=text,artifacts=artifacts,final_source=source)
        if self.reasoning:self.state['reasoning_text']=self.reasoning
        atomic(self.folder/'state.json',self.state)
        self.emit({'type':'final','text':text,'artifacts':artifacts,'status':status})

    def status(self,status,description,**extra):
        if status in ('completed','failed','cancelled','interrupted'):
            extra.setdefault('finished_at',time.time())
            self.finalize(status)
        self.emit(dict(type='status',status=status,description=description,**extra))


def native_command(desc,native_id):
    kind,model=desc['model'].split('/',1)
    if kind=='codex':
        args=['codex','-a','never','-c','sandbox_mode="danger-full-access"','exec','--json','--skip-git-repo-check']
        if native_id: args+=['resume',native_id]
        else: args+=['-C',desc['cwd']]
        if model!='default':args+=['--model',model]
        for f in desc.get('attachments',[]):
            if f['mime'].startswith('image/'):args+=['--image',f['path']]
        return args+['-']
    args=['claude','--print','--verbose','--output-format','stream-json','--include-partial-messages',
          '--permission-mode','acceptEdits','--permission-prompts','none','--model',model]
    if native_id:args+=['--resume',native_id]
    return args


def prompt(desc):
    from .chat_artifacts import INSTRUCTIONS
    text=desc['text']+INSTRUCTIONS
    if desc.get('extension_prompt'):text+=desc['extension_prompt']
    if desc.get('harness_prompt'):text+=desc['harness_prompt']
    if desc.get('addon_prompt'):text+=desc['addon_prompt']
    if desc.get('attachments'):
        text+='\n\nПрикреплённые пользователем файлы (локальные пути, прочитай их для анализа):\n'
        text+='\n'.join(f"- {f['name']}: {f['path']} ({f['mime']})" for f in desc['attachments'])
    return text


def visible_hash(messages):
    normalized=[{'role':r['role'],'content':re.sub(r'\n\n---\n[0-9]{2}\.[0-9]{2}\.[0-9]{4} · [^\n]*\(\+03:00\) · [0-9]+м [0-9]+с[^\n]*$', '', r['content'].strip().replace('\r\n','\n'))} for r in messages]
    return hashlib.sha256(json.dumps(normalized,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def conversation_prompt(desc,native_id):
    text=prompt(desc)
    if not native_id and desc.get('history'):
        text='Предыдущий видимый диалог пользователя (для продолжения контекста):\n'+json.dumps(desc['history'],ensure_ascii=False)+'\n\nТекущий запрос:\n'+text
    return text


def _native_turn(desc,native_id,events,cancelled):
    from .chat_extensions import native_flags
    from .chat_harness import native_environment
    native_env=native_environment(desc)
    desc=dict(desc,native_env=native_env)
    flags,instructions=native_flags(desc,events.folder/'extensions')
    desc=dict(desc,extension_prompt=instructions)
    command=native_command(desc,native_id)
    if desc['model'].startswith('codex/'):
        command[1:1]=flags
    else:command+=flags
    # Child inherits worker process group. Only our child is terminated on cancel.
    stderr=events.folder/'native.stderr'
    usage={}; completed=False; text_seen=False; partial=False; api_error=False;failure_code='provider_failed'
    with stderr.open('ab') as err:
        os.chmod(stderr,0o600)
        proc=subprocess.Popen(command,cwd=desc['cwd'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,start_new_session=True,env={**os.environ,**native_env})
        atomic(events.folder/'native.pid',{'pid':proc.pid})
        proc.stdin.write(conversation_prompt(desc,native_id).encode());proc.stdin.close()
        import selectors
        sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
        buffer=b'';last_status=time.monotonic();cancel_sent=None
        while True:
            if cancelled() and cancel_sent is None:
                os.killpg(proc.pid,signal.SIGTERM);cancel_sent=time.monotonic()
            if cancel_sent and time.monotonic()-cancel_sent>4 and proc.poll() is None:os.killpg(proc.pid,signal.SIGKILL)
            reads=sel.select(.3)
            if reads:
                data=os.read(proc.stdout.fileno(),65536)
                if not data:
                    if proc.poll() is not None:break
                buffer+=data
                if len(buffer)>8_000_000:proc.kill();raise RuntimeError('native_record_limit')
                while b'\n' in buffer:
                    line,buffer=buffer.split(b'\n',1)
                    try:row=json.loads(line)
                    except ValueError:continue
                    kind=desc['model'].split('/')[0]
                    if kind=='codex':
                        if row.get('type')=='thread.started':
                            native_id=row.get('thread_id',native_id)
                            atomic(events.folder.parent/'thread.json',{'native_id':native_id,'provider':kind})
                        if row.get('type') in ('item.started','item.completed','item.updated'):
                            item=row.get('item',{});typ=item.get('type','')
                            if typ=='agent_message' and row['type']=='item.completed':
                                text=item.get('text','')
                                if text:
                                    events.last_message=text
                                    if item.get('phase')=='final_answer' or item.get('channel')=='final':events.final_candidate=text
                                    events.emit({'type':'text','text':text+'\n\n','channel':'final' if item.get('phase')=='final_answer' or item.get('channel')=='final' else 'commentary'});text_seen=True
                            elif typ in ('command_execution','file_change','mcp_tool_call','web_search','image_view'):
                                label={'command_execution':'Выполняет команду','file_change':'Изменяет файлы','mcp_tool_call':'Использует инструмент','web_search':'Ищет информацию','image_view':'Анализирует изображение'}[typ]
                                events.side_effects=True
                                events.status('running',label+(' · готово' if row['type']=='item.completed' else ''))
                        if row.get('type')=='turn.completed':usage=row.get('usage',{});completed=True
                        if row.get('type') in ('turn.failed','error'):
                            detail=str(row).lower()
                            if any(t in detail for t in ('rate_limit','quota','usage limit','credits','429')):failure_code='quota_exhausted'
                            elif any(t in detail for t in ('unauthorized','authentication','401','not logged')):failure_code='auth_required'
                            events.status('running','Провайдер сообщил ошибку; завершаем запрос')
                    else:
                        if row.get('isApiErrorMessage') or row.get('error'):
                            detail=str(row).lower()
                            if any(t in detail for t in ('rate_limit','quota','usage limit','credits','429')):failure_code='quota_exhausted'
                            elif any(t in detail for t in ('unauthorized','authentication','401','not logged')):failure_code='auth_required'
                            api_error=True;continue
                        if row.get('session_id'):
                            native_id=row['session_id'];atomic(events.folder.parent/'thread.json',{'native_id':native_id,'provider':kind})
                        if row.get('type')=='stream_event':
                            event=row.get('event',{});delta=event.get('delta',{})
                            if delta.get('type')=='text_delta':
                                events.emit({'type':'text','text':delta.get('text','')});text_seen=True;partial=True
                            if event.get('type')=='content_block_start' and event.get('content_block',{}).get('type')=='tool_use':
                                events.side_effects=True
                                events.status('running','Использует инструмент '+re.sub('[^A-Za-z0-9_.-]','',event['content_block'].get('name',''))[:80])
                        if row.get('type')=='assistant':
                            events.last_message=''.join(b.get('text','') for b in row.get('message',{}).get('content',[]) if b.get('type')=='text')
                        if row.get('type')=='assistant' and not partial:
                            for block in row.get('message',{}).get('content',[]):
                                if block.get('type')=='text':events.emit({'type':'text','text':block.get('text','')});text_seen=True
                        if row.get('type')=='result':
                            usage=row.get('usage',{});completed=not row.get('is_error',False)
                            if isinstance(row.get('result'),str):events.final_candidate=row['result']
            if time.monotonic()-last_status>3:
                events.status('running',events.state.get('description','Работает'));last_status=time.monotonic()
            if proc.poll() is not None and not reads:
                # Drain once more via select before stopping.
                if not sel.select(0):break
        code=proc.wait()
    if cancelled():raise InterruptedError('cancelled')
    if code or not completed or api_error:
        raise RuntimeError(failure_code)
    if not text_seen:raise RuntimeError('no_text')
    return {'native_id':native_id,'usage':usage}


def native_turn(desc,native_id,events,cancelled):
    try:return _native_turn(desc,native_id,events,cancelled)
    finally:
        if desc.get('extensions') and re.fullmatch('[a-f0-9]{32}',desc.get('id','')):
            from .chat_harness import native_environment
            home=Path(native_environment(desc).get('CODEX_HOME',os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
            (home/('hub-chat-'+desc['id']+'.config.toml')).unlink(missing_ok=True)


def main(chat):
    os.umask(0o077)
    try:
        os.nice(10)
        cpus=sorted(os.sched_getaffinity(0));os.sched_setaffinity(0,set(cpus[:max(1,len(cpus)//2)]))
    except OSError:pass
    lock=(chat/'worker.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    atomic(chat/'worker.pid',{'pid':os.getpid()})
    # At most two native turns across all conversations: other services share VM.
    slots=chat.parent.parent/'slots';slots.mkdir(mode=0o700,exist_ok=True)
    slot=None
    while slot is None:
        for folder in chat.glob('job-*'):
            if (folder/'cancel').exists() and load(folder/'state.json',{}).get('status')=='queued':
                event=Events(folder);event.status('cancelled','Отменено до запуска');event.emit({'type':'done','status':'cancelled'})
        if not any(load(f/'request.json',{}).get('id') and load(f/'state.json',{}).get('status') not in ('completed','failed','cancelled','interrupted') for f in chat.glob('job-*')):
            (chat/'worker.pid').unlink(missing_ok=True);return
        for n in range(2):
            candidate=(slots/str(n)).open('a')
            try:fcntl.flock(candidate,fcntl.LOCK_EX|fcntl.LOCK_NB);slot=candidate;break
            except BlockingIOError:candidate.close()
        if slot is None:time.sleep(.5)
    while True:
        pending=[f for f in chat.glob('job-*') if load(f/'request.json',{}).get('id') and load(f/'state.json',{}).get('status') not in ('completed','failed','cancelled','interrupted')]
        pending.sort(key=lambda f:load(f/'request.json',{}).get('created_at',0))
        if not pending:break
        folder=pending[0]
        if folder.is_symlink() or not folder.is_dir():continue
        desc=load(folder/'request.json',{});state=load(folder/'state.json',{})
        if not desc or state.get('status') in ('completed','failed','cancelled','interrupted'):continue
        events=Events(folder)
        if state.get('status')=='running':
            # Previous worker disappeared. Never blindly replay a submitted turn.
            events.status('interrupted','Исполнитель прерван. Запрос автоматически не повторялся.')
            from .chat_harness import finish
            finish(desc,events,'interrupted','worker_interrupted')
            events.emit({'type':'error','code':'worker_interrupted','message':'Выполнение прервано. Сохранённый вывод доступен.'});events.emit({'type':'done','status':'interrupted'});continue
        cancelled=lambda:(folder/'cancel').exists()
        if cancelled():events.status('cancelled','Отменено до запуска');events.emit({'type':'done','status':'cancelled'});continue
        events.status('running','Запускает агента',started_at=time.time())
        from .chat_harness import turn_prompt,checkpoint,finish
        desc['harness_prompt']=turn_prompt(desc,folder)
        if desc.get('harness'):checkpoint(Path(desc['harness']['root']),desc['harness']['run_id'],'running',job_id=desc['id'])
        provider=desc['model'].split('/')[0]
        thread=load(chat/'thread.json',{})
        native_id=thread.get('native_id') if thread.get('provider')==provider else None
        if thread.get('extensions_hash','')!=desc.get('extensions_hash',''):
            native_id=None
        if desc.get('history_supplied') and thread.get('visible_hash')!=visible_hash(desc.get('history',[])):
            native_id=None  # edited/regenerated branch: bootstrap only selected visible history
        try:
            alternatives=desc.get('harness',{}).get('routing',{}).get('alternatives',[]) if desc.get('harness',{}).get('policy',{}).get('mode')=='auto' else []
            attempts=[desc['model']]+alternatives[:3]
            for attempt_index,model in enumerate(attempts):
                desc['model']=model;provider=model.split('/')[0]
                atomic(folder/'request.json',desc)
                if desc.get('harness'):checkpoint(Path(desc['harness']['root']),desc['harness']['run_id'],'running',model=model,job_id=desc['id'])
                try:
                    if provider in ('codex','claude'):
                        result=native_turn(desc,native_id,events,cancelled)
                    elif provider=='api':
                        from .chat_harness import run_api
                        result=run_api(desc,events,cancelled)
                    else:
                        from .chat_providers import run_provider
                        result=run_provider(model,conversation_prompt(desc,native_id),desc.get('attachments',[]),desc['cwd'],native_id,events.emit,cancelled)
                    break
                except InterruptedError:raise
                except Exception as failure:
                    # Retry only a verified pre-execution auth/quota rejection. Tool calls,
                    # partial output, timeouts and generic failures always stop for handoff.
                    if str(failure) not in ('auth_required','quota_exhausted','provider_quota_exhausted') or events.text or events.side_effects or attempt_index+1==len(attempts):raise
                    native_id=None
                    h=desc['harness'];checkpoint(Path(h['root']),h['run_id'],'running',switch_from=model,switch_to=attempts[attempt_index+1],reason=str(failure))
                    events.status('running','Переключает модель до начала выполнения: '+attempts[attempt_index+1],selected_model=attempts[attempt_index+1])
            if not events.final_candidate and provider not in ('codex','claude'):events.final_candidate=result.get('final_text') or events.text
            finish(desc,events,'completed')
            events.status('completed','Готово',finished_at=time.time(),usage=result.get('usage',{}))
            if result.get('native_id'):
                visible=desc.get('history',[])+[{'role':'user','content':desc['text']},{'role':'assistant','content':events.state.get('final_text',events.text)}]
                atomic(chat/'thread.json',{'native_id':result['native_id'],'provider':provider,'visible_hash':visible_hash(visible),'extensions_hash':desc.get('extensions_hash','')})
            events.emit({'type':'done','status':'completed'})
        except InterruptedError:
            finish(desc,events,'cancelled')
            events.status('cancelled','Остановлено пользователем',finished_at=time.time());events.emit({'type':'done','status':'cancelled'})
        except Exception as failure:
            finish(desc,events,'failed',str(failure) if str(failure) in ('auth_required','quota_exhausted') else 'provider_failed')
            # Raw provider errors may contain prompts/keys. Keep only fixed error codes.
            events.status('failed','Провайдер не завершил запрос. Проверьте авторизацию и лимиты.',finished_at=time.time())
            events.emit({'type':'error','code':'provider_failed','message':'Агент не завершил запрос. Вывод сохранён; автоматического повтора нет.'})
            events.emit({'type':'done','status':'failed'})
    (chat/'worker.pid').unlink(missing_ok=True)


if __name__=='__main__':main(Path(sys.argv[1]))
