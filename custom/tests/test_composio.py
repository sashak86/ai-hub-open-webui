"""Composio host boundaries; provider effects run against a local mock only."""
import asyncio
import json

import httpx
import pytest
from .test_standalone import client
from hub_console import chat_connectors as c, chat_composio as composio, chat_connector_mcp as m


def create(client, **extra):
    body = {'provider': 'composio', 'credentials': {'token': 'mock-composio-private-key', 'toolkits': 'gmail,github'}, 'permissions': ['read', 'connect', 'write'], **extra}
    result = client.post('/v1/connectors', json=body)
    assert result.status_code == 200, result.text
    assert 'mock-composio-private-key' not in result.text
    assert 'composio' not in result.json().keys()
    return result.json()['id']


@pytest.fixture
def provider(monkeypatch):
    original = httpx.AsyncClient
    calls = []
    def handler(request):
        body = json.loads(request.content) if request.content else {}
        calls.append((request, body))
        assert request.url.host == 'backend.composio.dev'
        assert request.headers['x-api-key'] == 'mock-composio-private-key'
        if request.url.path.endswith('/session'):
            return httpx.Response(201, json={'session_id': 'trs_mockSession'})
        if request.url.path.endswith('/tools'):
            return httpx.Response(200, json={'items': [{'slug': 'GMAIL_FETCH_EMAILS'}], 'next_cursor': None})
        if request.url.path.endswith('/link'):
            return httpx.Response(201, json={'redirect_url': 'https://connect.composio.dev/link/test', 'link_token': 'do-not-forward'})
        return httpx.Response(200, json={'result_type': 'completed', 'error': None, 'data': {'echo': 'mock-composio-private-key'}})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(**kw, transport=httpx.MockTransport(handler)))
    return calls


def test_composio_credential_fields_required_and_masked(client):
    catalog = client.get('/v1/connectors/catalog').json()['data']
    spec = next(x for x in catalog if x['id'] == 'composio')
    assert next(f for f in spec['fields'] if f['name'] == 'token')['secret']
    assert client.post('/v1/connectors', json={'provider':'composio', 'credentials': {'toolkits':'gmail'}}).status_code == 422
    for value in ['', '*', 'gmail,https://evil', 'a '*50]:
        assert client.post('/v1/connectors', json={'provider':'composio','credentials':{'token':'test','toolkits':value}}).status_code == 422
    create(client)
    assert 'mock-composio-private-key' not in client.get('/v1/connectors').text


def test_session_created_on_health_only_owner_pseudonym_and_scope(client,provider):
    key = create(client)
    assert not provider
    route = '/v1/connectors/'+key
    assert client.post(route+'/invoke',json={'tool':'list','arguments':{}}).status_code == 409
    health = client.post(route+'/health').json()
    assert health['status'] == 'connected'
    assert len(provider) == 2
    body = provider[0][1]
    assert body['toolkits'] == {'enable':['github','gmail']}
    assert body['instant'] is False and body['workbench']['enable'] is False and body['proxy_execute']['enable'] is False
    assert body['user_id'].startswith('workbench_') and len(body['user_id']) == 74
    assert 'a'*64 not in body['user_id'] and '@' not in body['user_id']
    assert 'session_id' not in json.dumps(health)
    client.post(route+'/health')
    assert len(provider) == 3 # reuses session; no retry of previous execution
    assert client.post(route+'/refresh').json()['status']=='configured'
    client.post(route+'/health')
    assert provider[-2][1]['user_id']==body['user_id']
    client.headers['X-WebUI-User'] = 'b'*64
    assert client.post(route+'/health').status_code == 404
    assert client.post(route+'/invoke',json={'tool':'list'}).status_code == 404
    other=create(client)
    client.post('/v1/connectors/'+other+'/health')
    assert provider[-2][1]['user_id'] != body['user_id']


def test_native_mcp_executes_only_explicit_reads_and_redacts(client,provider):
    key = create(client)
    client.post('/v1/connectors/'+key+'/health')
    root = next(client.app.state.chat_jobs_root.glob('harness/*'))
    listing = m.dispatch(root,{'method':'tools/list'})['tools']
    assert {t['name'] for t in listing} == {key+'__list',key+'__read'}
    result = m.dispatch(root,{'method':'tools/call','params':{'name':key+'__read','arguments':{'tool_slug':'GMAIL_FETCH_EMAILS','arguments':{}}}})
    assert not result.get('isError')
    assert 'mock-composio-private-key' not in json.dumps(result)
    assert '[REDACTED]' in json.dumps(result)
    assert provider[-1][1] == {'tool_slug':'GMAIL_FETCH_EMAILS','arguments':{}}
    count = len(provider)
    for slug in ['GMAIL_SEND_EMAIL','COMPOSIO_MULTI_EXECUTE_TOOL','SLACK_SEARCH_MESSAGES']:
        result = m.dispatch(root,{'method':'tools/call','params':{'name':key+'__read','arguments':{'tool_slug':slug}}})
        assert result.get('isError') and len(provider) == count
    assert m.dispatch(root,{'method':'tools/call','params':{'name':key+'__call','arguments':{'tool_slug':'GMAIL_SEND_EMAIL'}}})['isError']


def test_general_action_exact_single_use_confirmation(client,provider):
    key = create(client); route='/v1/connectors/'+key
    client.post(route+'/health')
    body={'tool':'call','arguments':{'tool_slug':'GMAIL_SEND_EMAIL','arguments':{'to':'mock@example.test'}}}
    count=len(provider)
    grant=client.post(route+'/invoke',json=body).json()
    assert grant['confirmation_required'] and len(provider)==count
    proof={'confirmation_token':grant['confirmation_token']}
    assert client.post(route+'/invoke',json=body|proof).status_code==200
    assert provider[-1][1]['tool_slug']=='GMAIL_SEND_EMAIL'
    assert client.post(route+'/invoke',json=body|proof).status_code==403
    grant=client.post(route+'/invoke',json=body).json()
    assert client.post(route+'/invoke',json={**body,'arguments':{'tool_slug':'GMAIL_DELETE_MESSAGE'},'confirmation_token':grant['confirmation_token']}).status_code==403


def test_connect_link_fixed_toolkit_and_requires_confirmation(client,provider):
    key=create(client);route='/v1/connectors/'+key
    client.post(route+'/health')
    body={'tool':'connect','arguments':{'toolkit':'gmail'}}
    grant=client.post(route+'/invoke',json=body).json()
    done=client.post(route+'/invoke',json=body|{'confirmation_token':grant['confirmation_token']}).json()
    assert done['result']['authorization_url']=='https://connect.composio.dev/link/test'
    assert 'do-not-forward' not in json.dumps(done)
    body={'tool':'connect','arguments':{'toolkit':'slack'}}
    grant=client.post(route+'/invoke',json=body).json()
    assert client.post(route+'/invoke',json=body|{'confirmation_token':grant['confirmation_token']}).status_code==403


@pytest.mark.parametrize('url',['http://app.composio.dev/x','https://evil.test/x','https://app.composio.dev.evil.test/x','https://user@app.composio.dev/x'])
def test_redirect_and_session_id_reject_untrusted_destinations(monkeypatch,url):
    async def fake(*args,**kw):return {'redirect_url':url}
    monkeypatch.setattr(c,'http',fake)
    row={'provider':'composio','credentials':{'token':'mock','toolkits':'gmail'},'composio':{'session_id':'trs_test'}}
    with pytest.raises(Exception):asyncio.run(composio.adapter(row,'connect',{'toolkit':'gmail'}))
    row['composio']['session_id']='../../other'
    with pytest.raises(Exception):asyncio.run(composio.adapter(row,'list',{}))


def test_health_never_claims_connected_on_auth_failure(client,monkeypatch):
    async def fail(*a,**kw):raise httpx.ConnectError('private-url-must-not-escape')
    monkeypatch.setattr(c,'http',fail)
    key=create(client)
    result=client.post('/v1/connectors/'+key+'/health')
    assert result.status_code==200 and result.json()['status']=='error'
    assert 'private-url-must-not-escape' not in result.text
