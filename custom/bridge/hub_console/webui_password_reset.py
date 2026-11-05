"""Owner-bound recovery for native Open WebUI passwords; no shared admin credential reset."""
import asyncio
import hashlib
import json
import logging
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
import uuid
from email.message import EmailMessage
from pathlib import Path

import httpx
from argon2 import PasswordHasher
from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse

CONFIG_PATH = Path(os.environ.get('SASHA_WORKBENCH_PASSWORD_RESET_CONFIG', str(Path.home()/'.config/sasha-workbench/password-reset.json')))
LOG = logging.getLogger(__name__)
GENERIC = {'message': 'Если для аккаунта настроено восстановление, код отправлен выбранным способом. Код действует 10 минут.'}


class Recovery:
    def __init__(self, config):
        self.config = config
        self.path = Path(config.get('state_db', str(Path(os.environ.get('SASHA_WORKBENCH_DATA', str(Path.home()/'.local/state/sasha-workbench')))/'password-reset.db')))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS tickets (digest TEXT PRIMARY KEY, uid TEXT, expires REAL); CREATE TABLE IF NOT EXISTS limits (bucket TEXT PRIMARY KEY, count INTEGER, expires REAL);')
        self.path.chmod(0o600)

    def db(self):
        return sqlite3.connect(self.path, timeout=10)

    def authdb(self):
        db = sqlite3.connect(self.config['webui_db'], timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def limit(self, bucket, maximum):
        now = time.time()
        digest = hashlib.sha256(bucket.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM limits WHERE expires<?', (now,))
            row = db.execute('SELECT count FROM limits WHERE bucket=?', (digest,)).fetchone()
            if row and row[0] >= maximum:
                raise HTTPException(429, 'Слишком много попыток. Повторите через 15 минут.')
            db.execute('INSERT INTO limits VALUES (?,?,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1', (digest, 1, now+900))

    async def request(self, email, channel, ip, background=None):
        LOG.warning('password_recovery.request')
        self.limit('request-ip:'+ip, 12)
        self.limit('request-email:'+email.casefold(), 3)
        with self.authdb() as db:
            user = db.execute('SELECT u.id,u.email FROM user u JOIN auth a ON a.id=u.id WHERE lower(u.email)=? AND a.active=1', (email.casefold(),)).fetchone()
        if not user:
            return GENERIC
        account = self.config.get('accounts', {}).get(user['id'], {})
        if not account or channel not in {'telegram', 'email'}:
            return GENERIC
        code = secrets.token_urlsafe(24)
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.db() as db:
            db.execute('DELETE FROM tickets WHERE uid=? OR expires<?', (user['id'], time.time()))
            db.execute('INSERT INTO tickets VALUES (?,?,?)', (digest, user['id'], time.time()+600))
        if background is not None:
            background.add_task(self.deliver_safe, account, user['email'], channel, code, digest)
        else:
            await self.deliver_safe(account, user['email'], channel, code, digest)
        return GENERIC

    async def deliver_safe(self, account, email, channel, code, digest):
        try:
            await self.deliver(account, email, channel, code)
            LOG.warning('password_recovery.delivered channel=%s', channel)
        except Exception:
            LOG.warning('password_recovery.delivery_failed channel=%s', channel)
            with self.db() as db:
                db.execute('DELETE FROM tickets WHERE digest=?', (digest,))

    async def deliver(self, account, email, channel, code):
        message = 'Sasha · AI Workbench\nКод восстановления пароля:\n'+code+'\nДействует 10 минут. Если вы не запрашивали сброс, проигнорируйте сообщение.'
        if channel == 'telegram':
            uid = account.get('hub_user_id')
            if type(uid) is not int:
                raise ValueError('No binding')
            db = sqlite3.connect('file:'+self.config['hub_db']+'?mode=ro', uri=True)
            try:
                enabled = db.execute('SELECT id FROM users WHERE id=? AND disabled=0', (uid,)).fetchone()
                row = db.execute("SELECT value FROM kv WHERE key='hub.telegram.links'").fetchone()
                links = json.loads(row[0]) if row else {}
            finally:
                db.close()
            targets = [v['chat_id'] for k,v in links.items() if enabled and isinstance(v,dict) and v.get('user_id') == uid and type(v.get('chat_id')) is int and v.get('telegram_user_id') == v['chat_id'] and str(v['chat_id']) == k]
            if len(targets) != 1:
                raise ValueError('No unique private binding')
            token = Path(self.config['telegram_token_file']).read_text().strip()
            async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
                r = await client.post('https://api.telegram.org/bot'+token+'/sendMessage', json={'chat_id':targets[0], 'text':message, 'disable_web_page_preview':True, 'protect_content':True})
                r.raise_for_status()
                if not r.json().get('ok'):
                    raise ValueError('Delivery failed')
        else:
            if not account.get('email_enabled'):
                raise ValueError('Email disabled')
            await asyncio.to_thread(self.smtp, email, message)

    def smtp(self, email, text):
        cfg = self.config.get('smtp', {})
        msg = EmailMessage()
        msg['From'] = cfg['from']; msg['To'] = email; msg['Subject'] = 'Восстановление пароля Sasha · AI Workbench'
        msg.set_content(text)
        with smtplib.SMTP_SSL(cfg['host'], cfg.get('port',465), timeout=15, context=ssl.create_default_context()) as server:
            if cfg.get('username'):
                server.login(cfg['username'], Path(cfg['password_file']).read_text().strip())
            server.send_message(msg)

    def complete(self, code, password, ip):
        self.limit('complete-ip:'+ip, 20)
        if not 12 <= len(password) <= 128 or len(password.encode()) > 512:
            raise HTTPException(422, 'Пароль должен содержать от 12 до 128 символов.')
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT uid FROM tickets WHERE digest=? AND expires>?', (digest,time.time())).fetchone()
            if not row:
                raise HTTPException(400, 'Код неверен или истёк.')
            db.execute('DELETE FROM tickets WHERE uid=?', (row[0],))
        # Token is consumed before mutation: a crash fails closed, never permits reuse.
        hashed = PasswordHasher().hash(password)
        with self.authdb() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT mfa FROM auth WHERE id=? AND active=1', (row[0],)).fetchone()
            if not current:
                raise HTTPException(400, 'Код неверен или истёк.')
            mfa = json.loads(current['mfa']) if current['mfa'] and current['mfa'] != 'null' else None
            if mfa:
                for key in ('login_challenge','manage_challenge','reset_ticket'):
                    mfa[key] = None
                mfa['revision'] = str(uuid.uuid4())
            db.execute('UPDATE auth SET password=?,session_stamp=?,mfa=? WHERE id=?', (hashed,str(uuid.uuid4()),json.dumps(mfa),row[0]))
            db.execute('UPDATE user SET updated_at=? WHERE id=?', (int(time.time()),row[0]))
        LOG.warning('password_recovery.completed account=%s', hashlib.sha256(row[0].encode()).hexdigest()[:12])
        return {'message':'Пароль изменён. Войдите с новым паролем. Двухфакторная защита сохранена.'}


def install(app, settings):
    from .standalone import valid_origin
    try:
        recovery = Recovery(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        LOG.warning('password_recovery.not_configured')
        recovery = None

    @app.get('/ai-hub/password-reset.js')
    async def asset():
        return FileResponse(Path(__file__).resolve().parents[2]/'deploy/password-reset.js',media_type='text/javascript',headers={'Cache-Control':'no-store'})

    @app.get('/ai-hub/auth/recovery/options')
    async def options():
        return {'telegram': bool(recovery and recovery.config.get('accounts')), 'email': bool(recovery and recovery.config.get('smtp')), 'configured': recovery is not None}

    @app.post('/ai-hub/auth/recovery/{action}')
    async def reset(action: str, request: Request, background: BackgroundTasks):
        if not valid_origin(request.headers.get('origin'), str(request.base_url).rstrip('/')):
            raise HTTPException(403,'Invalid Origin')
        if not recovery:
            raise HTTPException(503,'Восстановление ещё не настроено администратором.')
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw)>4096:
                raise HTTPException(413,'Слишком большой запрос')
        try:
            body=json.loads(raw)
            if not isinstance(body,dict):raise ValueError()
        except (ValueError,TypeError):
            raise HTTPException(422,'Некорректный запрос')
        ip=request.client.host if request.client else 'unknown'
        if action=='request':
            email=body.get('email','');channel=body.get('channel','telegram')
            if not isinstance(email,str) or not 3<=len(email)<=254 or not isinstance(channel,str):raise HTTPException(422,'Укажите email')
            return await recovery.request(email.strip(),channel,ip,background)
        if action=='complete':
            code=body.get('code','');password=body.get('password','')
            if not isinstance(code,str) or not 20<=len(code)<=100 or not isinstance(password,str):raise HTTPException(422,'Проверьте код и пароль')
            return await asyncio.to_thread(recovery.complete,code,password,ip)
        raise HTTPException(404,'Не найдено')
