"""Owner-authenticated OpenWebUI Action for the explicit Stop button.

The native /api/chat/actions route verifies JWT and chat ownership before invoking
this code. The internal bearer remains server-side; browser disconnect never calls it.
"""
import hashlib
import asyncio
import time
import os
import httpx


class Action:
    async def action(self, body:dict, __user__:dict=None, __event_emitter__=None):
        user=__user__ or {};uid=user.get('id');chat=body.get('chat_id')
        if not isinstance(uid,str) or not uid or not isinstance(chat,str) or not chat or len(chat)>200:
            raise ValueError('Не удалось определить владельца чата.')
        token=os.environ.get('HUB_CHAT_TOKEN') or os.environ.get('OPENAI_API_KEY','')
        if not token:raise ValueError('Подключение к агентам не настроено.')
        async with httpx.AsyncClient(base_url=os.environ.get('HUB_CHAT_URL','http://127.0.0.1:8088'),
                headers={'Authorization':'Bearer '+token,'X-WebUI-User':hashlib.sha256(uid.encode()).hexdigest()},
                verify=os.environ.get('SSL_CERT_FILE',True),trust_env=False,timeout=15) as client:
            response=await client.post('/v1/jobs/cancel-chat',json={'chat_id':chat})
            if response.is_error:raise ValueError('Не удалось остановить задание. Подключение к агенту недоступно.')
            count=response.json().get('count',0)
            jobs=response.json().get('jobs',[])
            job=jobs[0] if jobs else None
            try:
                from open_webui.models.chats import Chats
                owned=await Chats.get_chat_by_id(body.get('chat_id'))
                if owned and owned.user_id==uid:
                    message=await Chats.get_message_by_id_and_message_id(chat,body.get('id'))
                    job=next((r.get('job_id') for r in reversed((message or {}).get('statusHistory',[])) if r.get('job_id')),job)
            except ImportError:pass
            state='cancelled' if not count else 'running'
            if job:
                for _ in range(32):
                    result=await client.get('/v1/jobs/'+job)
                    if result.is_error:break
                    state=result.json().get('status','running')
                    if state in {'cancelled','completed','failed','interrupted'}:break
                    await asyncio.sleep(.25)
            if __event_emitter__:
                terminal=state in {'cancelled','completed','failed','interrupted'}
                label='Остановлено' if state=='cancelled' else 'Готово' if state=='completed' else 'Прервано' if state in {'failed','interrupted'} else 'Остановка запрошена'
                await __event_emitter__({'type':'status','data':{'action':'ai_hub_job','job_id':job,'stage_key':state+'|'+label,'description':label,'done':terminal}})
            return {'ok':True,'cancelled_jobs':count,'status':state}
