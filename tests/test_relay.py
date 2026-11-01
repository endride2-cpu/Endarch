import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from cryptography.fernet import Fernet
import httpx

from relay.app import create_app


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            'NOVEL_API_KEY': 'test-only-provider-secret', 'NOVEL_API_BASE': 'https://provider.invalid/v1',
            'NOVEL_MODELS': 'fake', 'RELAY_PAIRING_CODE': 'one-use-test-pairing-code',
            'RELAY_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'RELAY_DB': str(Path(self.tmp.name) / 'relay.db'),
            'NOVEL_PRICES_JSON': json.dumps({'fake': {'input_per_million': 1, 'output_per_million': 2, 'currency': 'USD'}})
        })
        self.env.start()
        self.app = create_app()
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="https://relay.test")
        result = await self.client.post('/devices/pair', json={'code': 'one-use-test-pairing-code'})
        self.headers = {'Authorization': 'Bearer ' + result.json()['device_token']}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.env.stop()
        self.tmp.cleanup()

    def body(self):
        return {'request_id': uuid.uuid4().hex, 'model': 'fake', 'prompt': 'private-novel-text', 'max_tokens': 100, 'temperature': .3}

    async def wait(self, request_id):
        for _ in range(50):
            result = (await self.client.get('/requests/' + request_id, headers=self.headers)).json()
            if result['status'] != 'PENDING':
                return result
            await asyncio.sleep(0.01)
        self.fail('Request did not finish')

    async def test_auth_pairing_one_use_and_model_allowlist(self):
        self.assertEqual((await self.client.get('/models')).status_code, 401)
        self.assertEqual((await self.client.post('/devices/pair', json={'code': 'one-use-test-pairing-code'})).status_code, 409)
        body = self.body(); body['model'] = 'forbidden'
        self.assertEqual((await self.client.post('/requests', headers=self.headers, json=body)).status_code, 422)

    async def test_idempotency_encryption_usage_and_conflict(self):
        calls = []
        async def fake_post(client, *args, **kwargs):
            if str(client.base_url).startswith('https://relay.test'):
                return await original_post(client, *args, **kwargs)
            calls.append(kwargs)
            return httpx.Response(200, json={'choices': [{'message': {'content': 'private-result'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20}})
        original_post = httpx.AsyncClient.post
        with patch('httpx.AsyncClient.post', fake_post):
            body = self.body()
            self.assertEqual((await self.client.post('/requests', headers=self.headers, json=body)).status_code, 202)
            result = await self.wait(body['request_id'])
            self.assertEqual(result['status'], 'SUCCEEDED')
            self.assertAlmostEqual(result['cost'], .00005)
            await self.client.post('/requests', headers=self.headers, json=body)
            self.assertEqual(len(calls), 1)
            body['prompt'] = 'different'
            self.assertEqual((await self.client.post('/requests', headers=self.headers, json=body)).status_code, 409)
        with self.app.state.store.db() as db:
            blob = db.execute('SELECT response FROM request').fetchone()[0]
            self.assertNotIn(b'private-result', blob)
        for file in Path(self.tmp.name).glob('*'):
            self.assertNotIn(b'private-novel-text', file.read_bytes())
            self.assertNotIn(b'test-only-provider-secret', file.read_bytes())

    async def test_unknown_is_not_reissued(self):
        calls = []
        async def fake_post(client, *args, **kwargs):
            if str(client.base_url).startswith('https://relay.test'):
                return await original_post(client, *args, **kwargs)
            calls.append(1)
            raise httpx.ReadTimeout('contains-sensitive-provider-data')
        original_post = httpx.AsyncClient.post
        with patch('httpx.AsyncClient.post', fake_post):
            body = self.body()
            await self.client.post('/requests', headers=self.headers, json=body)
            result = await self.wait(body['request_id'])
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertNotIn('sensitive', json.dumps(result))
            await self.client.post('/requests', headers=self.headers, json=body)
            self.assertEqual(len(calls), 1)

    async def test_expired_response_retains_tombstone(self):
        store = self.app.state.store
        identity = self.headers['Authorization'][7:]
        from relay.app import sha
        with store.db() as db:
            db.execute('INSERT INTO request VALUES (?,?,?,?,?,?)', ('a' * 32, sha(identity), 'fp', 'SUCCEEDED', store.cipher.encrypt(b'{"content":"old"}'), 0))
        result = (await self.client.get('/requests/' + 'a' * 32, headers=self.headers)).json()
        self.assertEqual(result['status'], 'UNKNOWN')
        with store.db() as db:
            self.assertIsNotNone(db.execute('SELECT id FROM request').fetchone())
            self.assertIsNone(db.execute('SELECT response FROM request').fetchone()[0])

    async def test_provider_401_is_not_retryable_and_message_is_redacted(self):
        async def fake_post(client, *args, **kwargs):
            if str(client.base_url).startswith('https://relay.test'):
                return await original_post(client, *args, **kwargs)
            return httpx.Response(401, text='test-only-provider-secret')
        original_post = httpx.AsyncClient.post
        with patch('httpx.AsyncClient.post', fake_post):
            body = self.body()
            await self.client.post('/requests', headers=self.headers, json=body)
            result = await self.wait(body['request_id'])
            self.assertEqual(result['status'], 'FAILED')
            self.assertFalse(result['retryable'])
            self.assertNotIn('secret', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
