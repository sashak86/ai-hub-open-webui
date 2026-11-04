"""Durable final result, timing, artifact exports and portable route wiring."""
import json
import httpx
from .test_standalone import client


def test_artifact_exports_through_real_authenticated_portable_route(client,monkeypatch):
    from hub_console import chat_jobs as jobs,chat_worker as worker
    monkeypatch.setattr(jobs,'model_catalog',lambda request:[{'id':'codex/offline','provider':'codex','available':True}])
    monkeypatch.setattr(jobs,'start_worker',lambda *a:None)
    original=client.app.state.http
    def handler(request):
        if request.url.path=='/api/v1/auths/':return httpx.Response(200,json={'id':'a','role':'user' if request.headers.get('authorization')=='Bearer ordinary-user' else 'admin'})
        return httpx.Response(404)
    client.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    # UI proxy owner is sha256('a'), separate from fixture's direct API owner.
    headers={'Authorization':'Bearer browser-test'}
    import hashlib
    client.headers['X-WebUI-User']=hashlib.sha256(b'a').hexdigest()
    try:
        response=client.post('/v1/jobs',json={'model':'codex/offline','chat_id':'artifacts','message_id':'one','text':'offline'})
        assert response.status_code==200,response.text
        key=response.json()['id'];ref=worker.load(client.app.state.chat_jobs_root/'index'/(key+'.json'))
        folder=client.app.state.chat_jobs_root/ref['owner']/ref['chat']/ref['folder'];events=worker.Events(folder)
        events.emit({'type':'text','text':'Progress only'})
        events.final_candidate='Verified final.\n```ai-hub-artifact\n'+json.dumps({'type':'table','title':'Result','data':{'columns':['Value'],'rows':[['=SUM(A1)'],[42]]}})+'\n```'
        events.status('running','Working',started_at=100);events.status('completed','Done',finished_at=112)
        base='/ai-hub/api/jobs/'+key
        result=client.get(base,headers=headers)
        assert result.status_code==200,result.text
        assert result.json()['duration_seconds']==12 and result.json()['final_text'].startswith('Verified final.')
        assert 'Progress only' not in result.json()['final_text']
        assert "'=SUM(A1)" in client.get(base+'/artifacts/1/export?format=csv',headers=headers).text
        assert client.get(base+'/artifacts/1/export',headers=headers).json()['data']['rows'][1]==[42]
        ordinary={'Authorization':'Bearer ordinary-user'}
        assert client.get(base,headers=ordinary).status_code==200
        assert client.get(base+'/artifacts/1/export',headers=ordinary).status_code==200
        assert client.get('/ai-hub/api/connectors',headers=ordinary).status_code==403
        assert client.get('/ai-hub/artifacts-ui.js').status_code==200
        assert 'installArtifactsUI' in client.get('/ai-hub/artifacts-ui.js').text
    finally:client.app.state.http=original


def test_exact_generated_footer_keeps_native_context_hash():
    from hub_console.chat_worker import visible_hash
    original=[{'role':'assistant','content':'Final answer'}]
    displayed=[{'role':'assistant','content':'Final answer\n\n---\n04.11.2026 · 15:15:00 (+03:00) · 0м 12с'}]
    assert visible_hash(original)==visible_hash(displayed)
    changed=[{'role':'assistant','content':'Changed answer\n\n---\n04.11.2026 · 15:15:00 (+03:00) · 0м 12с'}]
    assert visible_hash(original)!=visible_hash(changed)
