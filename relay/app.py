"""Run behind HTTPS: uvicorn relay.app:create_app --factory --host 127.0.0.1.

Provider keys and encryption key are read only from environment. Prompt content
is never persisted. Responses have a 24-hour TTL and are encrypted at rest.
"""
import asyncio
from contextlib import asynccontextmanager
import hashlib
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import httpx
from pydantic import BaseModel, Field


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Pair(BaseModel):
    code: str = Field(min_length=16, max_length=256)


class Request(BaseModel):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    model: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=1_000_000)
    max_tokens: int = Field(ge=1, le=32768)
    temperature: float = Field(ge=0, le=2)


class Store:
    def __init__(self, path, encryption_key, recover=True):
        self.path = str(path)
        self.cipher = Fernet(encryption_key)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS device(token_hash TEXT PRIMARY KEY, created REAL);
              CREATE TABLE IF NOT EXISTS pairing(code_hash TEXT PRIMARY KEY, used REAL);
              CREATE TABLE IF NOT EXISTS request(
                id TEXT PRIMARY KEY, device TEXT NOT NULL, fingerprint TEXT NOT NULL,
                status TEXT NOT NULL, response BLOB, created REAL NOT NULL);
            ''')
            if recover:
                db.execute("UPDATE request SET status='UNKNOWN' WHERE status='PENDING'")
        os.chmod(path, 0o600)

    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def get(self, request_id, device):
        with self.db() as db:
            # Retain tombstones: an expired request ID must never issue a duplicate call.
            db.execute('UPDATE request SET response=NULL WHERE created < ?', (time.time() - 86400,))
            row = db.execute('SELECT * FROM request WHERE id=? AND device=?', (request_id, device)).fetchone()
        if row is None:
            raise HTTPException(404, 'Unknown request')
        if row['response']:
            return json.loads(self.cipher.decrypt(row['response']))
        return {'status': row['status'] if row['status'] in ('PENDING', 'UNKNOWN') else 'UNKNOWN',
                'error': 'Result unavailable or expired', 'cost_status': 'unknown', 'cost': None}

    def finish(self, request_id, result):
        with self.db() as db:
            db.execute('UPDATE request SET status=?, response=? WHERE id=?',
                       (result['status'], self.cipher.encrypt(json.dumps(result).encode()), request_id))


def create_app():
    key = os.environ.get('NOVEL_API_KEY', '')
    base = os.environ.get('NOVEL_API_BASE', '').rstrip('/')
    models = [x.strip() for x in os.environ.get('NOVEL_MODELS', os.environ.get('NOVEL_MODEL', '')).split(',') if x.strip()]
    pairing_code = os.environ.get('RELAY_PAIRING_CODE', '')
    encryption = os.environ.get('RELAY_ENCRYPTION_KEY', '')
    parsed = urlsplit(base)
    if not key or not models or len(pairing_code) < 16 or not encryption:
        raise RuntimeError('Relay environment incomplete; see relay/README.md')
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError('NOVEL_API_BASE must be HTTPS without credentials/query/fragment')
    store = Store(os.environ.get('RELAY_DB', './relay-data/relay.sqlite3'), encryption)
    prices = json.loads(os.environ.get('NOVEL_PRICES_JSON', '{}'))
    tasks = set()
    semaphore = asyncio.Semaphore(1)

    async def cleanup():
        while True:
            with store.db() as db:
                db.execute('UPDATE request SET response=NULL WHERE created < ?', (time.time() - 86400,))
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        janitor = asyncio.create_task(cleanup())
        yield
        janitor.cancel()
        for task in tasks:
            task.cancel()
        await asyncio.gather(janitor, *tasks, return_exceptions=True)

    app = FastAPI(title='Novel Translate private relay', lifespan=lifespan)
    app.state.store = store
    security = HTTPBearer(auto_error=False)

    def device(auth: HTTPAuthorizationCredentials | None = Depends(security)):
        if not auth:
            raise HTTPException(401, 'Device authentication required')
        token_hash = sha(auth.credentials)
        with store.db() as db:
            row = db.execute('SELECT 1 FROM device WHERE token_hash=?', (token_hash,)).fetchone()
        if not row:
            raise HTTPException(401, 'Invalid device credential')
        return token_hash

    @app.post('/devices/pair')
    def pair(body: Pair):
        if not secrets.compare_digest(body.code, pairing_code):
            raise HTTPException(401, 'Invalid pairing code')
        token = secrets.token_urlsafe(48)
        try:
            with store.db() as db:
                db.execute('INSERT INTO pairing VALUES (?,?)', (sha(pairing_code), time.time()))
                db.execute('INSERT INTO device VALUES (?,?)', (sha(token), time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Pairing code already used; rotate it on the relay')
        return {'device_token': token}

    @app.get('/health')
    def health():
        return {'status': 'ready'}

    @app.get('/models')
    def list_models(identity=Depends(device)):
        return {'models': models, 'prices': prices}

    async def execute(body):
        result = {'status': 'UNKNOWN', 'cost_status': 'unknown', 'cost': None}
        try:
            async with semaphore:
                async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=20), follow_redirects=False) as client:
                    response = await client.post(base + '/chat/completions', headers={'Authorization': 'Bearer ' + key},
                                                 json={'model': body.model, 'messages': [{'role': 'user', 'content': body.prompt}],
                                                       'max_tokens': body.max_tokens, 'temperature': body.temperature, 'stream': False})
                if response.status_code != 200:
                    retry_after = response.headers.get('retry-after', '')
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        try:
                            delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            delay = 0
                    result.update(status='FAILED', error='Provider HTTP ' + str(response.status_code),
                                  retryable=response.status_code == 429 or response.status_code >= 500,
                                  retry_after=min(max(delay, 0), 3600))
                else:
                    data = response.json()
                    choice = data['choices'][0]
                    usage = data.get('usage')
                    price = prices.get(body.model)
                    cost = None
                    if usage and price and all(isinstance(usage.get(k), int) for k in ('prompt_tokens', 'completion_tokens')):
                        cost = (usage['prompt_tokens'] * price['input_per_million'] + usage['completion_tokens'] * price['output_per_million']) / 1_000_000
                    result.update(status='SUCCEEDED', content=choice['message'].get('content') or '',
                                  finish_reason=choice.get('finish_reason'), usage=usage,
                                  cost=cost, cost_status='estimated' if cost is not None else 'unknown', price_snapshot=price)
        except (httpx.TimeoutException, httpx.TransportError, asyncio.CancelledError):
            # Provider might have billed/completed; replaying the ID only retrieves this status.
            result['error'] = 'Provider outcome unknown; reconcile before a new request'
        except Exception:
            result['error'] = 'Unrecognized provider response; outcome unknown'
        finally:
            store.finish(body.request_id, result)

    @app.post('/requests', status_code=202)
    async def submit(body: Request, identity=Depends(device)):
        if body.model not in models:
            raise HTTPException(422, 'Model not allowed')
        body_hash = sha(json.dumps(body.model_dump(), sort_keys=True))
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM request WHERE id=?', (body.request_id,)).fetchone()
            if row:
                if row['device'] != identity or row['fingerprint'] != body_hash:
                    raise HTTPException(409, 'Request ID conflict')
            else:
                pending = db.execute("SELECT count(*) FROM request WHERE status='PENDING'").fetchone()[0]
                if pending >= 2:
                    raise HTTPException(429, 'Relay busy', headers={'Retry-After': '5'})
                db.execute('INSERT INTO request VALUES (?,?,?,?,?,?)',
                           (body.request_id, identity, body_hash, 'PENDING', None, time.time()))
        if not row:
            task = asyncio.create_task(execute(body))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        return store.get(body.request_id, identity)

    @app.get('/requests/{request_id}')
    def get_request(request_id: str, identity=Depends(device)):
        return store.get(request_id, identity)

    return app
