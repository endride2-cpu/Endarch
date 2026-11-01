import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mobile_runtime.engine import Engine
from test_mobile_runtime import FakeModel, fixture


class SlowModel(FakeModel):
    """Records how many provider calls are in flight at once."""

    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.fail_refine_of = None

    def __call__(self, request_id, payload):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(0.02)
            if self.fail_refine_of and payload['prompt'].startswith('# REFINEMENT') and f'ekor BAB {self.fail_refine_of - 1} (sumber)' in payload['prompt']:
                return dict(status='FAILED', error='Provider HTTP 400', retryable=False)
            return super().__call__(request_id, payload)
        finally:
            with self.lock:
                self.active -= 1


class ParallelChapters(unittest.TestCase):
    def run_job(self, parallel, chapters=4, model=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        fixture(root / 'source.epub', chapters)
        engine = Engine.create(root / 'novels', 'Judul', root / 'source.epub', 'fake')
        engine.transport = model or SlowModel()
        engine.command('configure', {'paralel_bab': parallel})
        for _ in range(200):
            state = engine.step()
            if state['status'] == 'WAITING_INPUT' and 'refinement' in (state['error'] or ''):
                engine.command('approve_refine')
            elif state['status'] in ('COMPLETED', 'FAILED', 'BLOCKED_QA', 'WAITING_INPUT'):
                return engine, state
        self.fail('job did not settle')

    def outputs(self, engine):
        files = sorted(engine.base.glob('draf/*.md')) + sorted(engine.base.glob('refined/bab_*'))
        rows = sorted((json.loads(x)['bab'], json.loads(x)['gate']) for x in (engine.base / 'qa_llm.jsonl').read_text().splitlines())
        return {f.name: f.read_text() for f in files}, rows

    def test_chapters_run_concurrently_up_to_limit(self):
        model = SlowModel()
        engine, state = self.run_job(3, model=model)
        self.assertEqual(state['status'], 'COMPLETED')
        self.assertEqual(model.peak, 3)
        self.assertEqual(len((engine.base / 'qa_llm.jsonl').read_text().splitlines()), 4)

    def test_parallel_matches_sequential_results(self):
        sequential, s1 = self.run_job(1)
        parallel, s2 = self.run_job(3)
        self.assertEqual((s1['status'], s2['status']), ('COMPLETED', 'COMPLETED'))
        self.assertEqual(self.outputs(sequential), self.outputs(parallel))
        self.assertEqual(sorted(sequential.state['receipts']), sorted(parallel.state['receipts']))

    def test_failed_chapter_keeps_siblings_receipts(self):
        model = SlowModel()
        model.fail_refine_of = 2
        engine, state = self.run_job(3, model=model)
        self.assertEqual((state['status'], state['stage']), ('FAILED', 'refine'))
        self.assertIn('refine:1', engine.state['receipts'])
        self.assertIn('refine:3', engine.state['receipts'])
        self.assertNotIn('refine:2', engine.state['receipts'])

    def test_parallel_setting_change_keeps_receipts(self):
        engine, state = self.run_job(2)
        self.assertEqual(state['status'], 'COMPLETED')
        receipts = dict(engine.state['receipts'])
        engine.command('configure', {'paralel_bab': 5})
        self.assertEqual(engine.state['status'], 'COMPLETED')
        self.assertEqual(engine.valid_receipts(), set(receipts))
        with self.assertRaises(ValueError):
            engine.command('configure', {'paralel_bab': 0})


if __name__ == '__main__':
    unittest.main()
