import hashlib
import json
import sqlite3
import time
from unittest.mock import AsyncMock

import pytest
from argon2 import PasswordHasher
from fastapi import HTTPException
from hub_console.webui_password_reset import Recovery


@pytest.fixture
def recovery(tmp_path):
    auth=tmp_path/'webui.db'
    with sqlite3.connect(auth) as db:
        db.executescript('CREATE TABLE user(id TEXT,email TEXT,updated_at INTEGER); CREATE TABLE auth(id TEXT,password TEXT,active INTEGER,mfa TEXT,session_stamp TEXT);')
        db.execute('INSERT INTO user VALUES (?,?,?)',('owner','owner@example.test',0))
        db.execute('INSERT INTO auth VALUES (?,?,?,?,?)',('owner','old',1,json.dumps({'secret':'preserved','login_challenge':{'x':1},'manage_challenge':{},'reset_ticket':{}}),'stamp'))
    return Recovery({'state_db':str(tmp_path/'reset.db'),'webui_db':str(auth),'accounts':{'owner':{'hub_user_id':1}}})


@pytest.mark.asyncio
async def test_unknown_and_known_same_response_and_hashed_token(recovery):
    recovery.deliver=AsyncMock()
    known=await recovery.request('owner@example.test','telegram','a')
    unknown=await recovery.request('absent@example.test','telegram','b')
    assert known==unknown
    code=recovery.deliver.call_args.args[-1]
    with recovery.db() as db:
        digest=db.execute('SELECT digest FROM tickets').fetchone()[0]
    assert digest==hashlib.sha256(code.encode()).hexdigest()
    assert code not in recovery.path.read_bytes().decode('latin1')


@pytest.mark.asyncio
async def test_complete_one_use_session_revoke_preserve_mfa(recovery):
    recovery.deliver=AsyncMock()
    await recovery.request('owner@example.test','telegram','a')
    code=recovery.deliver.call_args.args[-1]
    recovery.complete(code,'LongEnoughPassword!', 'c')
    with recovery.authdb() as db:
        row=db.execute('SELECT * FROM auth').fetchone()
    assert PasswordHasher().verify(row['password'],'LongEnoughPassword!')
    assert row['session_stamp']!='stamp'
    mfa=json.loads(row['mfa']);assert mfa['secret']=='preserved'
    assert mfa['login_challenge'] is None
    with pytest.raises(HTTPException) as exc:recovery.complete(code,'LongEnoughPassword!', 'c')
    assert exc.value.status_code==400


@pytest.mark.asyncio
async def test_bad_password_does_not_consume_and_expired_fails(recovery):
    recovery.deliver=AsyncMock()
    await recovery.request('owner@example.test','telegram','a');code=recovery.deliver.call_args.args[-1]
    with pytest.raises(HTTPException):recovery.complete(code,'short','b')
    with recovery.db() as db:db.execute('UPDATE tickets SET expires=?',(time.time()-1,))
    with pytest.raises(HTTPException):recovery.complete(code,'LongEnoughPassword!','b')


@pytest.mark.asyncio
async def test_delivery_failure_revokes_and_rate_limits(recovery):
    recovery.deliver=AsyncMock(side_effect=ValueError('no delivery'))
    for _ in range(3):await recovery.request('owner@example.test','telegram','a')
    with recovery.db() as db:assert db.execute('SELECT count(*) FROM tickets').fetchone()[0]==0
    with pytest.raises(HTTPException) as exc:await recovery.request('owner@example.test','telegram','a')
    assert exc.value.status_code==429


@pytest.mark.asyncio
async def test_unmapped_account_never_sends(recovery):
    recovery.config['accounts']={};recovery.deliver=AsyncMock()
    await recovery.request('owner@example.test','telegram','a')
    recovery.deliver.assert_not_called()


def test_routes_mock_delivery_synthetic_reset(recovery,monkeypatch,tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from hub_console import webui_password_reset as module
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps(recovery.config))
    monkeypatch.setattr(module,'CONFIG_PATH',cfg)
    codes=[]
    async def capture(self,account,email,channel,code):codes.append(code)
    monkeypatch.setattr(module.Recovery,'deliver',capture)
    app=FastAPI();module.install(app,SimpleNamespace(testing=True))
    with TestClient(app) as client:
        assert client.get('/ai-hub/auth/recovery/options').json()=={'telegram':True,'email':False,'configured':True}
        path='/ai-hub/auth/recovery/request'
        assert client.post(path,json={'email':'owner@example.test'},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.post(path,content='x'*4100).status_code==413
        assert client.post(path,json={'email':'owner@example.test'}).status_code==200
        assert len(codes)==1
        result=client.post('/ai-hub/auth/recovery/complete',json={'code':codes[0],'password':'SyntheticPassword123!'})
        assert result.status_code==200
        assert client.post('/ai-hub/auth/recovery/complete',json={'code':codes[0],'password':'SyntheticPassword123!'}).status_code==400


@pytest.mark.asyncio
async def test_telegram_only_exact_private_binding(recovery,tmp_path,monkeypatch):
    from hub_console import webui_password_reset as module
    hub=tmp_path/'hub.db';token=tmp_path/'bot.token';token.write_text('synthetic-token')
    recovery.config.update(hub_db=str(hub),telegram_token_file=str(token))
    with sqlite3.connect(hub) as db:
        db.executescript('CREATE TABLE users(id INTEGER,disabled INTEGER);CREATE TABLE kv(key TEXT,value TEXT);INSERT INTO users VALUES (1,0);')
        db.execute('INSERT INTO kv VALUES (?,?)',('hub.telegram.links',json.dumps({'99':{'user_id':1,'chat_id':99,'telegram_user_id':99}})))
    calls=[]
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'ok':True}
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,json):calls.append((url,json));return Response()
    monkeypatch.setattr(module.httpx,'AsyncClient',Client)
    await recovery.deliver({'hub_user_id':1},'owner@example.test','telegram','code')
    assert calls[0][1]['chat_id']==99
    assert calls[0][1]['protect_content'] is True
    with sqlite3.connect(hub) as db:
        db.execute('UPDATE kv SET value=?',(json.dumps({'99':{'user_id':1,'chat_id':-123,'telegram_user_id':99}}),))
    with pytest.raises(ValueError):await recovery.deliver({'hub_user_id':1},'owner@example.test','telegram','code')
    assert len(calls)==1
