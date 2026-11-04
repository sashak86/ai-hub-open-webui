"""Portable native service tools and authenticated browser connector boundary."""
import httpx
from .test_standalone import client
from hub_console import chat_connectors as c


def test_browser_owner_credentials_and_explicit_writes(client,monkeypatch):
    original=client.app.state.http
    def handler(request):
        if request.url.path=='/api/v1/auths/':
            token=request.headers.get('authorization','')
            if token in {'Bearer owner-a','Bearer owner-b'}:return httpx.Response(200,json={'id':token[-1],'role':'admin'})
            return httpx.Response(401)
        return httpx.Response(404)
    client.app.state.http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    calls=[]
    async def http(method,url,**kw):calls.append((method,url,kw));return {'ok':True,'result':{'id':7}}
    monkeypatch.setattr(c,'http',http)
    try:
        base='/ai-hub/api/connectors';owner={'Authorization':'Bearer owner-a'}
        assert client.get(base,headers={'Authorization':'Bearer invalid'}).status_code==401
        catalog=client.get(base+'/catalog',headers=owner).json()['data']
        assert len(catalog)==10
        result=client.post(base,headers=owner,json={'provider':'telegram','credentials':{'token':'synthetic-private-value'},'permissions':['read','send']})
        assert result.status_code==200,result.text
        assert 'synthetic-private-value' not in result.text
        key=result.json()['id']
        assert client.get(base,headers={'Authorization':'Bearer owner-b'}).json()['data']==[]
        assert client.post(base+'/'+key+'/health',headers={'Authorization':'Bearer owner-b'},json={}).status_code==404
        assert client.post(base+'/'+key+'/health',headers=owner,json={}).json()['status']=='connected'
        assert calls[0][1].endswith('/getMe')
        calls.clear();body={'tool':'send','arguments':{'chat_id':'7','text':'mock local transport'}}
        approval=client.post(base+'/'+key+'/invoke',headers=owner,json=body).json()
        assert approval['confirmation_required'] and not calls
        assert client.post(base+'/'+key+'/invoke',headers=owner,json=body|{'confirmation_token':approval['confirmation_token']}).status_code==200
        assert calls[0][0]=='POST'
        assert client.post(base+'/'+key+'/invoke',headers=owner,json=body|{'confirmation_token':approval['confirmation_token']}).status_code==403
        assert client.post(base,headers=owner|{'Origin':'https://evil.example'},json={}).status_code==403
    finally:client.app.state.http=original
