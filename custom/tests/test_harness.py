"""Behavioral coverage: isolation, real queue integration, safe fallback and budget guards."""
import asyncio
import json
from pathlib import Path
import pytest
from fastapi import HTTPException
from hub_console import chat_harness as h,chat_worker as w
from .test_standalone import client as standalone_client

@pytest.fixture
def client(standalone_client,monkeypatch):
    from hub_console import chat_jobs as jobs
    monkeypatch.setattr(jobs,'model_catalog',lambda request:[{'id':'codex/test','provider':'codex','available':True}])
    monkeypatch.setattr(jobs,'start_worker',lambda *args:None)
    return standalone_client

def create(client,**extra):
    return client.post('/v1/jobs',json=dict(model='codex/test',chat_id='one',message_id='m1',text='Hello',**extra))

def folder(client,ident):
    root=client.app.state.chat_jobs_root;ref=w.load(root/'index'/(ident+'.json'))
    return root/ref['owner']/ref['chat']/ref['folder']


def policy(**kw):return dict(h.DEFAULT,**kw)


def test_policy_is_private_validated_and_chat_scoped(client):
    assert client.put('/v1/harness/settings',json={'enabled':True,'models':['codex/test']}).status_code==200
    assert client.put('/v1/harness/settings',json={'chat_id':'one','mode':'auto'}).json()['mode']=='auto'
    assert client.get('/v1/harness/settings?chat_id=two').json()['mode']=='manual'
    assert client.put('/v1/harness/settings',json={'max_input_tokens':True}).status_code==422
    assert client.put('/v1/harness/settings',json={'budget_usd':-1}).status_code==422
    client.headers['X-WebUI-User']='b'*64
    assert client.get('/v1/harness/settings').json()['enabled'] is False


def test_preflight_respects_quota_context_zero_balance_and_hard_usd_budget():
    rows=[dict(id='codex/a',provider='codex',available=True,connected=True),dict(id='gateway/b',provider='gateway',available=True,connected=True),dict(id='ollama/c',provider='ollama',available=True,connected=True)]
    p=policy(mode='auto',models=[r['id'] for r in rows],budget_usd=5)
    q=[dict(provider='codex',remaining_percent=0)]
    result=h.choose_model(p,'codex/a',rows,q,'hello')
    assert result['model']=='ollama/c'
    assert {v['reason'] for v in result['rejected']}=={'quota_reserve','budget_guard_cost_unknown'}
    with pytest.raises(HTTPException):h.choose_model(policy(models=['codex/a'],max_input_tokens=100),'codex/a',rows,[],'x'*301)
    with pytest.raises(HTTPException):h.choose_model(policy(models=['gateway/b']),'gateway/b',rows,[dict(model_id='gateway/b',currency='USD',balance_amount=0)],'hi')


def test_run_checkpoint_export_owner_isolated_and_secrets_removed(client):
    row=client.post('/v1/harness/runs',json={'chat_id':'one','title':'Investigate','steps':[{'title':'reproduce'}]}).json()
    client.patch('/v1/harness/runs/'+row['id'],json={'checkpoint':'Done sk-testsecretvalue1234567890','memory':'Project note'})
    response=client.get('/v1/harness/runs/'+row['id']+'/export')
    assert response.status_code==200 and 'testsecretvalue' not in response.text and 'Продолжение' in response.text
    assert client.get('/v1/harness/runs/current?chat_id=two').json()['memory']==[]
    client.headers['X-WebUI-User']='b'*64
    assert client.get('/v1/harness/runs/'+row['id']+'/export').status_code==404
    assert client.patch('/v1/harness/runs/'+row['id'],json={'status':'completed'}).status_code==404


def enable(client,monkeypatch):
    monkeypatch.setattr(h,'models',lambda request:[dict(id='codex/test',provider='codex',available=True,connected=True),dict(id='claude/test',provider='claude',available=True,connected=True)])
    from hub_console import chat_quotas
    async def quotas(request):return {'providers':[]}
    monkeypatch.setattr(chat_quotas,'snapshot',quotas)
    assert client.put('/v1/harness/settings',json={'enabled':True,'mode':'auto','models':['codex/test','claude/test'],'auto_extensions':True}).status_code==200


def test_harness_binds_to_real_jobs_and_saves_handoff_and_skill(client,monkeypatch):
    enable(client,monkeypatch)
    ident=create(client,text='исправь ошибку' ) if False else client.post('/v1/jobs',json={'model':'codex/test','chat_id':'one','message_id':'1','text':'исправь ошибку'} )
    assert ident.status_code==200,ident.text
    path=folder(client,ident.json()['id']);desc=w.load(path/'request.json')
    assert desc['harness']['routing']['model']=='codex/test' and desc['extensions']
    def turn(desc,native,events,cancel):
        assert 'Режим AI Hub Harness' in desc['harness_prompt']
        w.atomic(events.folder/'learning.json',{'title':'Debug workflow','content':'Reproduce then fix','triggers':['debug']})
        events.emit({'type':'text','text':'Verified correction'});return {'native_id':'native-id'}
    monkeypatch.setattr(w,'native_turn',turn);w.main(path.parent)
    data=client.get('/v1/harness/runs/current?chat_id=one').json()
    assert data['runs'][0]['status']=='completed'
    assert data['memory'][0]['trusted'] is False
    export=client.get('/v1/harness/runs/'+data['runs'][0]['id']+'/export').text
    assert 'Verified correction' in export
    assert client.post('/v1/harness/catalog',json={'id':'learned/'+data['memory'][0]['id'],'auto_enable':True}).status_code==200


def test_safe_auth_fallback_before_tools_but_no_replay_after_side_effects(client,monkeypatch):
    enable(client,monkeypatch)
    calls=[]
    def turn(desc,native,events,cancel):
        calls.append(desc['model'])
        if desc['model']=='codex/test':raise RuntimeError('quota_exhausted')
        assert native is None
        events.emit({'type':'text','text':'done'});return {}
    monkeypatch.setattr(w,'native_turn',turn)
    ident=create(client).json()['id'];path=folder(client,ident);w.main(path.parent)
    assert calls==['codex/test','claude/test'] and w.load(path/'state.json')['status']=='completed'
    calls.clear()
    def side_effect(desc,native,events,cancel):
        calls.append(desc['model']);events.emit({'type':'status','description':'Использует инструмент'});raise RuntimeError('quota_exhausted')
    monkeypatch.setattr(w,'native_turn',side_effect)
    ident=client.post('/v1/jobs',json={'model':'codex/test','chat_id':'two','message_id':'2','text':'hello'}).json()['id'];path=folder(client,ident);w.main(path.parent)
    assert calls==['codex/test'] and w.load(path/'state.json')['status']=='failed'


def test_builtin_plugin_install_contextual_and_secrets_masked(client):
    data=client.get('/v1/harness/catalog?q=плагин').json()['data'];assert any(r['kind']=='plugin' for r in data)
    result=client.post('/v1/harness/catalog',json={'id':'builtin/development-kit','auto_enable':True});assert result.status_code==200
    ext=client.get('/v1/chat-extensions').json()['items'][0];assert ext['kind']=='plugin' and len(ext['config']['skills'])==2 and not ext['enabled']
    response=client.post('/v1/harness/connect/openai',json={'action':'save','auth_type':'api_key','api_key':'private-harness-test-key','base_url':'https://example.test/v1'})
    assert response.status_code==200 and 'private-harness-test-key' not in response.text
    assert 'private-harness-test-key' not in client.get('/v1/harness/models').text


def test_perowner_native_profile_is_used_and_never_secret_argv(tmp_path):
    w.atomic(tmp_path/'connections.json',{'codex':{'api_key':'private-test-key'}})
    desc={'model':'codex/test','connection_root':str(tmp_path)}
    env=h.native_environment(desc)
    assert env['CODEX_HOME']==str(tmp_path/'codex-api')
    assert w.load(tmp_path/'codex-api/auth.json')['OPENAI_API_KEY']=='private-test-key'
    assert (tmp_path/'codex-api/auth.json').stat().st_mode&0o777==0o600


def test_connections_bind_to_job_when_harness_disabled(client):
    assert client.get('/v1/harness/settings').json()['enabled'] is False
    client.post('/v1/harness/connect/codex',json={'action':'save','auth_type':'api_key','api_key':'private-test-native-key'})
    ident=create(client).json()['id'];desc=w.load(folder(client,ident)/'request.json')
    assert 'harness' not in desc and desc['connection_root']
    env=h.native_environment(desc)
    assert w.load(Path(env['CODEX_HOME'])/'auth.json')['OPENAI_API_KEY']=='private-test-native-key'


def test_manual_plan_and_memory_are_consumed_by_next_job(client,monkeypatch):
    enable(client,monkeypatch)
    run=client.post('/v1/harness/runs',json={'chat_id':'one','title':'My real plan','steps':[{'title':'reproduce'},{'title':'verify'}],'memory':'Prefer small changes','learning':'Reusable diagnostic workflow'}).json()
    assert run['title']=='My real plan'
    ident=create(client).json()['id'];path=folder(client,ident);desc=w.load(path/'request.json')
    assert desc['harness']['run_id']==run['id']
    assert any(r['text']=='Prefer small changes' for r in desc['harness']['memory'])
    def turn(desc,native,events,cancel):
        assert 'reproduce' in desc['harness_prompt']
        w.atomic(events.folder/'plan.json',[{'title':'reproduce','status':'completed'},{'title':'verify','status':'completed'}])
        events.emit({'type':'text','text':'ok'});return {}
    monkeypatch.setattr(w,'native_turn',turn);w.main(path.parent)
    final=client.get('/v1/harness/runs/current?chat_id=one').json()['runs'][0]
    assert all(s['status']=='completed' for s in final['steps'])
