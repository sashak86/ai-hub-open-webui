"""Native Pipe image route: no text-agent dispatch and private native file output."""
import asyncio
import importlib.util
import types
import sys
from pathlib import Path
import httpx
import pytest

@pytest.fixture
def pipe():
    path=Path(__file__).resolve().parents[1]/'deploy/chat_pipe.py'
    spec=importlib.util.spec_from_file_location('media_pipe',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.mark.parametrize('value',['нарисуй кота','Нарисуйте красивый пейзаж','создай изображение дома','сгенерируй картинку леса','/image a cat','пожалуйста, нарисуй робота'])
def test_explicit_image_intent(pipe,value):assert pipe.image_intent(value)

@pytest.mark.parametrize('value',['не рисуй кота','объясни как нарисовать кота','"нарисуй кота"','```\nнарисуй кота\n```','создай проект','как сгенерировать изображение','что означает слово нарисуй'])
def test_normal_or_quoted_text_is_not_image(pipe,value):assert pipe.image_intent(value) is None

@pytest.mark.parametrize('chosen',[None,'sd15'])
def test_native_pipe_returns_real_image_without_text_model_call(pipe,tmp_path,monkeypatch,chosen):
    records={};uploads=[];calls=[];posts=[];events=[]
    class Files:
        @staticmethod
        async def get_file_by_id_and_user_id(fid,uid):return records.get((uid,fid))
        @staticmethod
        async def insert_new_file(uid,form):records[(uid,form.id)]=form;return form
    class Storage:
        @staticmethod
        def upload_file(handle,name,tags):
            raw=handle.read();p=tmp_path/name;p.write_bytes(raw);uploads.append((name,tags));return raw,str(p)
    modules={'open_webui.models.files':types.SimpleNamespace(Files=Files,FileForm=lambda **kw:types.SimpleNamespace(**kw)),'open_webui.storage.provider':types.SimpleNamespace(Storage=Storage)}
    for key,val in modules.items():monkeypatch.setitem(sys.modules,key,val)
    job={'id':'1'*32,'model':'sdxl-lightning','width':1024,'height':1024,'created_at':1,'started_at':2,'finished_at':3,'message':'Готово','status':'completed'}
    async def handler(req):
        calls.append(req.url.path)
        if req.url.path=='/v1/media' and req.method=='GET':return httpx.Response(200,json={'default_model':'sdxl-lightning','models':[{'id':'sdxl-lightning','name':'SDXL Lightning 8'},{'id':'sd15','name':'SD1.5','available':True}]})
        if req.url.path=='/v1/media' and req.method=='POST':
            import json
            posts.append(json.loads(req.content));return httpx.Response(200,json=job)
        if req.url.path.endswith('/file'):return httpx.Response(200,content=b'\x89PNG\r\n\x1a\nsynthetic-real-file')
        raise AssertionError('Unexpected text-agent/other request '+req.url.path)
    p=pipe.Pipe();monkeypatch.setattr(p,'client',lambda:httpx.AsyncClient(base_url='http://bridge',transport=httpx.MockTransport(handler)))
    async def run():
        async def emit(event):events.append(event)
        body={'model':'ai_hub.codex/gpt-6.1-sol','messages':[{'role':'user','content':'нарисуй a portrait'}]}
        if chosen:body['hub_media_model']=chosen
        args=dict(__user__={'id':'owner'},__metadata__={'chat_id':'chat','message_id':'msg'},__event_emitter__=emit)
        first=''.join([x async for x in p.pipe(body,**args)]);second=''.join([x async for x in p.pipe(body,**args)]);return first,second
    a,b=asyncio.run(run());assert a==b and '![Сгенерированное изображение](/api/v1/files/' in a and '1024×1024' in a
    assert len(records)==1 and len(uploads)==1
    assert posts[0]['model']==(chosen or 'sdxl-lightning')
    assert posts[0]['client_request_id']==posts[1]['client_request_id'] and posts[0]['prompt']=='a portrait'
    assert all(not path.startswith('/v1/jobs') for path in calls)
    assert events[-1]['data']['done'] is True

def test_attachments_are_not_silently_ignored_for_edit(pipe,monkeypatch):
    async def collect(*args):return [{'name':'a.txt','mime':'text/plain','content_base64':'YQ=='}]
    monkeypatch.setattr(pipe,'collect_attachments',collect)
    async def run():return ''.join([x async for x in pipe.image_turn(None,'нарисуй кота',{}, {'id':'a'},[{'id':'image'}],[],None)])
    assert 'Прикрепите один' in asyncio.run(run())


def test_provider_reason_is_visible_for_both_bridge_and_portable_errors(pipe):
    assert pipe.media_error(httpx.Response(409,json={'error':{'message':'Недостаточно RAM'}}))=='Недостаточно RAM'
    assert pipe.media_error(httpx.Response(403,json={'detail':'Аккаунт не разрешён'}))=='Аккаунт не разрешён'


def test_edit_imperative_needs_image(pipe):
    assert pipe.image_intent('исправь цвет рубашки',True)
    assert pipe.image_intent('исправь ошибку') is None
    assert pipe.image_intent('/edit a red flower')=='a red flower'


def test_unconnected_selected_generator_does_not_submit(pipe,monkeypatch):
    async def handler(req):
        assert req.method=='GET'
        return httpx.Response(200,json={'default_model':'sdxl-lightning','models':[{'id':'sdxl-lightning','available':True},{'id':'qwen-image-edit','available':False}]})
    p=pipe.Pipe();monkeypatch.setattr(p,'client',lambda:httpx.AsyncClient(base_url='http://bridge',transport=httpx.MockTransport(handler)))
    async def run():return ''.join([x async for x in p.pipe({'model':'ai_hub.codex/test','hub_media_model':'qwen-image-edit','messages':[{'role':'user','content':'нарисуй a cat'}]},__user__={'id':'owner'},__metadata__={'chat_id':'a','message_id':'b'})])
    assert 'не подключена' in asyncio.run(run())
