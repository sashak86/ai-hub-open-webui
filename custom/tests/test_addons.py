"""Portable owner isolation and shared addon hooks without installing packages."""
import hashlib
from pathlib import Path
import httpx
from .test_standalone import client


def test_addon_registry_through_authenticated_browser_proxy(client):
    original=client.app.state.http
    async def handler(request):
        if request.url.path=='/api/v1/auths/':
            token=request.headers.get('authorization','')
            if token in {'Bearer owner-a','Bearer owner-b'}:
                return httpx.Response(200,json={'id':token[-1],'role':'admin'})
            return httpx.Response(401)
        return httpx.Response(404)
    client.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        base='/ai-hub/api/harness/addons'
        assert client.get(base,headers={'Authorization':'Bearer invalid'}).status_code==401
        owner={'Authorization':'Bearer owner-a'}
        assert len(client.get(base,headers=owner).json()['data'])==5
        saved=client.post(base+'/omniroute',headers=owner,json={'action':'configure','endpoint':'https://gateway.example','api_key':'synthetic-private-addon-value'})
        assert saved.status_code==200,saved.text
        assert 'synthetic-private-addon-value' not in saved.text
        rows=client.get(base,headers={'Authorization':'Bearer owner-b'}).json()['data']
        gateway=next(row for row in rows if row['id']=='omniroute')
        assert not gateway['api_key'] and not gateway['enabled'] and not gateway['endpoint']
    finally:
        client.app.state.http=original


def test_original_skill_reaches_native_job_with_harness_off(client,tmp_path,monkeypatch):
    from hub_console import chat_jobs as jobs,chat_worker as worker
    owner=hashlib.sha256(('webui:'+('a'*64)).encode()).hexdigest()
    folder=client.app.state.chat_jobs_root/'harness'/owner
    folder.mkdir(parents=True,exist_ok=True)
    bundle=tmp_path/'original-skill';bundle.mkdir()
    (bundle/'SKILL.md').write_text('UPSTREAM_SKILL_WORKFLOW_MARKER')
    worker.atomic(folder/'state.json',{'addons':{'task-observer':{'enabled':True,'installed':True,'bundle':str(bundle)}}})
    monkeypatch.setattr(jobs,'model_catalog',lambda request:[{'id':'codex/offline','provider':'codex','available':True}])
    monkeypatch.setattr(jobs,'start_worker',lambda *args:None)
    result=client.post('/v1/jobs',json={'model':'codex/offline','chat_id':'addon-chat','message_id':'one','text':'Implement a task and observe the workflow'})
    assert result.status_code==200,result.text
    ref=worker.load(client.app.state.chat_jobs_root/'index'/(result.json()['id']+'.json'))
    job=client.app.state.chat_jobs_root/ref['owner']/ref['chat']/ref['folder']
    desc=worker.load(job/'request.json')
    assert 'harness' not in desc
    assert 'UPSTREAM_SKILL_WORKFLOW_MARKER' in worker.prompt(desc)
    assert desc['addons']['active']==['task-observer']


def test_connected_omniroute_creates_owner_only_native_model(client,monkeypatch):
    from hub_console import chat_addons
    original=client.app.state.http;records=[]
    async def handler(request):
        if request.url.path=='/api/v1/auths/':return httpx.Response(200,json={'id':'gateway-owner','role':'admin'})
        if request.url.path=='/api/models':return httpx.Response(200,json={'data':[{'id':'ai_hub.codex/offline'}]})
        if request.url.path=='/api/v1/models/create':
            import json
            records.append(json.loads(request.content));return httpx.Response(200,json={'ok':True})
        return httpx.Response(404)
    async def action(request,ident,body):
        assert ident=='omniroute' and body['action']=='test'
        return {'ok':True,'health':True,'models':['provider/offline-model']}
    monkeypatch.setattr(chat_addons,'action',action)
    client.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response=client.post('/ai-hub/api/harness/addons/omniroute',headers={'Authorization':'Bearer browser-test'},json={'action':'test'})
        assert response.status_code==200,response.text
        assert response.json()['model_records']==[records[0]['id']]
        assert records[0]['params']['custom_params']['hub_api_model']=='api/omniroute/provider/offline-model'
        assert records[0]['access_grants']==[{'principal_type':'user','principal_id':'gateway-owner','permission':'read'}]
    finally:client.app.state.http=original
