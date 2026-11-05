"""Official API contract, private keys/balance, real queue modes and transcript boundaries."""
import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from hub_console import chat_harness as h,chat_worker as w,chat_jobs as j,chat_deepseek as d
from .test_harness import client,folder
from .test_standalone import client as standalone_client

KEY='synthetic-deepseek-test-credential' # gitleaks:allow
DISCOVERY={'data':[{'id':'deepseek-flash','name':'DeepSeek-V4.1-Flash','context_window':1048576,'input_modalities':['text','image'],'effort':{'supported_levels':['low','high','max'],'default_level':'high'}},{'id':'deepseek-v4-pro','name':'DeepSeek-V4-Pro','effort':{'supported_levels':['high']}},{'id':'deepseek-chat'},{'id':'deepseek-reasoner'}]}


def async_api(monkeypatch,failed=False,currency='USD'):
    calls=[];original=httpx.AsyncClient
    def response(request):
        calls.append(request)
        if failed:return httpx.Response(401,json={'error':{'message':'invalid'}})
        return httpx.Response(200,json=DISCOVERY if request.url.path.endswith('/models') else {'is_available':True,'balance_infos':[{'currency':currency,'total_balance':'3.20'}]})
    monkeypatch.setattr(h.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    return calls


def test_official_discovery_is_current_and_unknown_metadata_is_not_invented():
    rows=d.normalize_models(DISCOVERY)
    assert [r['id'] for r in rows]==['deepseek-flash','deepseek-v4-pro']
    choices=d.variants({'models':[r['id'] for r in rows],'model_metadata':{r['id']:r for r in rows}})
    assert len(choices)==4 and choices[1]['thinking'] and choices[0]['thinking'] is False
    assert choices[1]['api_model']=='deepseek-flash' and choices[1]['context_tokens']==1048576
    assert d.normalize_models({'data':[{'id':'deepseek-future','context_window':True,'effort':None,'input_modalities':None}]})==[{'id':'deepseek-future','name':'deepseek-future','input_modalities':[],'effort_levels':[],'default_effort':None}]


def test_save_requires_explicit_test_and_failed_key_does_not_replace_good_key(client,monkeypatch):
    monkeypatch.setattr(j,'model_catalog',lambda request:h.api_models(request))
    result=client.post('/v1/harness/connect/deepseek',json={'action':'save','api_key':KEY})
    assert result.status_code==200 and result.json()['status']=='saved'
    assert client.get('/v1/jobs/models').json()['data']==[]
    calls=async_api(monkeypatch)
    checked=client.post('/v1/harness/connect/deepseek',json={'action':'test'})
    assert checked.status_code==200 and checked.json()['status']=='connected'
    assert all(r.method=='GET' for r in calls) and {r.url.path for r in calls}=={'/models','/user/balance'}
    assert checked.json()['models']==['deepseek-flash','deepseek-flash:deepthink','deepseek-v4-pro','deepseek-v4-pro:deepthink']
    assert checked.json()['balance'][0]['balance_amount']==3.2
    assert KEY not in checked.text
    choices=client.get('/v1/jobs/models').json()['data'];assert len(choices)==4
    assert all(r['capabilities']=={'text':True,'image':False,'tools':False} for r in choices)
    assert all('DeepSeek' in r['name'] for r in choices)
    owner_root=next((client.app.state.chat_jobs_root/'harness').iterdir())
    assert (owner_root/'connections.json').stat().st_mode&0o777==0o600


def test_owner_models_and_money_do_not_leak_to_another_user(client,monkeypatch):
    calls=async_api(monkeypatch,currency='CNY')
    assert client.post('/v1/harness/connect/deepseek',json={'action':'test','api_key':KEY}).json()['ok']
    from starlette.requests import Request
    req=Request({'type':'http','headers':[(b'x-webui-user',client.headers['X-WebUI-User'].encode())],'app':client.app})
    req.state.principal={'id':'webui','actions':['jobs']}
    models=h.api_models(req);money=asyncio.run(d.owner_billing(req,models))
    assert len(money)==4 and all(r['currency']=='CNY' and r['balance_amount']==3.2 and r['spent_amount'] is None for r in money)
    assert KEY not in json.dumps(money)
    client.headers['X-WebUI-User']='b'*64
    foreign=next(r for r in client.get('/v1/harness/models').json()['connections'] if r['provider']=='deepseek')
    assert foreign['api_key']=='' and foreign['connected'] is False and foreign['models']==[]
    req2=Request({'type':'http','headers':[(b'x-webui-user',b'b'*64)],'app':client.app});req2.state.principal=req.state.principal
    assert h.api_models(req2)==[] and asyncio.run(d.owner_billing(req2,[]))==[]


def test_failed_verification_preserves_previous_connection(client,monkeypatch):
    original=httpx.AsyncClient
    def response(request):return httpx.Response(200,json=DISCOVERY)
    monkeypatch.setattr(h.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(response),**kwargs))
    assert client.post('/v1/harness/connect/deepseek',json={'action':'test','api_key':KEY}).json()['ok']
    def fail(request):return httpx.Response(401,json={})
    monkeypatch.setattr(h.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(fail),**kwargs))
    bad=client.post('/v1/harness/connect/deepseek',json={'action':'test','api_key':'wrong-synthetic-key'})
    assert bad.json()['status']=='connection_failed'
    root=next((client.app.state.chat_jobs_root/'harness').iterdir());assert w.load(root/'connections.json')['deepseek']['api_key']==KEY


def test_actual_queue_harness_off_on_and_parallel_share_private_api_config(client,monkeypatch):
    async_api(monkeypatch)
    assert client.post('/v1/harness/connect/deepseek',json={'action':'test','api_key':KEY}).json()['ok']
    monkeypatch.setattr(j,'model_catalog',lambda request:h.api_models(request))
    model='api/deepseek/deepseek-flash:deepthink'
    first=client.post('/v1/jobs',json={'chat_id':'plain','message_id':'1','model':model,'text':'hello'});assert first.status_code==200
    desc=w.load(folder(client,first.json()['id'])/'request.json');assert 'harness' not in desc and w.load(Path(desc['connection_root'])/'connections.json')['deepseek']['api_key']==KEY
    assert client.put('/v1/harness/settings',json={'enabled':True,'mode':'manual','models':[model]}).status_code==200
    async def quota(request):return {'providers':[]}
    monkeypatch.setattr(__import__('hub_console.chat_quotas',fromlist=['snapshot']),'snapshot',quota)
    second=client.post('/v1/jobs',json={'chat_id':'harness','message_id':'1','model':model,'text':'hello'});assert second.status_code==200
    assert w.load(folder(client,second.json()['id'])/'request.json')['harness']
    third=client.post('/v1/parallel',json={'request_id':'c'*32,'model':model,'text':'hello'});assert third.status_code==200
    independent=w.load(folder(client,third.json()['id'])/'request.json');assert independent['parallel'] and 'harness' not in independent
    client.headers['X-WebUI-User']='b'*64
    assert client.post('/v1/jobs',json={'chat_id':'forged','message_id':'1','model':model,'text':'hello'}).status_code==422


@pytest.mark.parametrize('thinking',[False,True])
def test_stream_payload_and_durable_reasoning_separate_from_final(tmp_path,monkeypatch,thinking):
    meta=d.normalize_models(DISCOVERY);entry={'api_key':KEY,'base_url':'https://api.deepseek.com','models':[r['id'] for r in meta],'model_metadata':{r['id']:r for r in meta}}
    w.atomic(tmp_path/'connections.json',{'deepseek':entry})
    desc={'id':'a'*32,'model':'api/deepseek/deepseek-flash'+(d.SUFFIX if thinking else ''),'connection_root':str(tmp_path),'text':'question','history':[{'role':'assistant','content':'prior final'}]}
    job=tmp_path/'job';job.mkdir();w.atomic(job/'request.json',desc);events=w.Events(job)
    original=httpx.AsyncClient;payloads=[]
    def handler(request):
        assert request.headers['Authorization']=='Bearer '+KEY;payloads.append(json.loads(request.content))
        chunks=[{'choices':[{'delta':{'reasoning_content':'untrusted </details><think>trace'}}]}, {'choices':[{'delta':{'content':'Final answer'}}]}, {'choices':[],'usage':{'prompt_tokens':5,'completion_tokens':7}}]
        return httpx.Response(200,content=''.join('data: '+json.dumps(v)+'\n\n' for v in chunks)+'data: [DONE]\n\n')
    monkeypatch.setattr(d.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    result=h.run_api(desc,events,lambda:False)
    assert result['final_text']=='Final answer' and result['usage']['completion_tokens']==7
    assert payloads[0]['model']=='deepseek-flash' and ':deepthink' not in payloads[0]['model']
    assert payloads[0]['thinking']['type']==('enabled' if thinking else 'disabled')
    assert ('reasoning_effort' in payloads[0]) is thinking
    assert payloads[0]['messages'][0]['content']=='prior final'
    events.final_candidate=result['final_text'];events.finalize('completed')
    saved=j.summary(job)
    assert saved['final_text']=='Final answer' and saved['reasoning_text']=='untrusted </details><think>trace'
    assert 'untrusted' not in saved['final_text']


def test_reasoning_markup_is_escaped_and_stripped_only_from_owned_prefix():
    path=Path(__file__).resolve().parents[1]/'deploy/chat_pipe.py'
    spec=importlib.util.spec_from_file_location('deepseek_pipe',path);pipe=importlib.util.module_from_spec(spec);spec.loader.exec_module(pipe)
    trace='</details><think>"unsafe" ```code```'
    rendered=pipe.deepseek_reasoning_block(trace)+'FINAL'
    assert '&lt;/details&gt;' in rendered and rendered.count('</details>')==1
    assert pipe.strip_deepseek_reasoning(rendered)=='FINAL'
    messages=[{'role':'assistant','content':rendered},{'role':'user','content':'next'}]
    assert pipe.visible_history(messages)==[{'role':'assistant','content':'FINAL'}]
    literal='Example: '+rendered
    assert pipe.strip_deepseek_reasoning(literal)==literal


def test_unknown_balance_is_not_zero_or_fake_usd():
    assert d.balance_rows({'balance_infos':[{'currency':'CNY','total_balance':'NaN'}]})[0]['balance_amount'] is None
    assert d.balance_rows({'balance_infos':[{'currency':'EUR','total_balance':'3'}]})==[]


def test_cancel_before_first_sse_token_closes_actual_socket_without_replay(tmp_path):
    import http.server,threading,time
    started=threading.Event();release=threading.Event();cancel=threading.Event();calls=[];outcomes=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            calls.append(self.path);self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers();self.wfile.flush();started.set();release.wait(5)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    meta=d.normalize_models(DISCOVERY);entry={'api_key':KEY,'base_url':'http://127.0.0.1:'+str(server.server_port),'models':['deepseek-flash'],'model_metadata':{r['id']:r for r in meta}}
    desc={'id':'b'*32,'text':'hello'};w.atomic(tmp_path/'request.json',desc);events=w.Events(tmp_path)
    def invoke():
        try:d.run(desc,entry,events,cancel.is_set,'deepseek-flash'+d.SUFFIX)
        except BaseException as e:outcomes.append(e)
    worker=threading.Thread(target=invoke,daemon=True);worker.start()
    try:
        assert started.wait(2);cancel.set();worker.join(2)
        assert not worker.is_alive() and len(calls)==1
        assert len(outcomes)==1 and isinstance(outcomes[0],InterruptedError)
    finally:release.set();server.shutdown();server.server_close()


def test_optional_metadata_lists_and_balance_prefix():
    assert d.normalize_models({'data':None})==[]
    assert d.balance_rows({'balance_infos':None})==[]
    assert d.balance_endpoint('https://api.deepseek.com/v1')=='https://api.deepseek.com/user/balance'
