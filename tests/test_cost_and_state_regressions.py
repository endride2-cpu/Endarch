"""Regressions for audit v0.2.6: avoid paid re-runs, honest retry, consistent QA state."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mobile_runtime.engine import Engine, digest
from qa_gate import check_chapter
import test_mobile_runtime as base_suite
from test_mobile_runtime import TEXT


class Harness(unittest.TestCase):
    setUp = base_suite.MobileTests.setUp
    tearDown = base_suite.MobileTests.tearDown
    drive = base_suite.MobileTests.drive


class CostRegressions(Harness):
    def complete(self):
        self.assertEqual(self.drive()['status'], 'COMPLETED', self.engine.state)
        return len(self.model.calls)

    def reopen(self):
        # The APK builds a fresh Engine for every bridge call; tests must not rely on in-memory conf.
        self.engine = Engine(self.root / 'novels', self.engine.state['id'], self.model)

    def configure(self, data):
        self.engine.command('configure', data)
        self.reopen()

    def rerun(self):
        self.engine.command('resume')
        self.reopen()
        return self.drive()

    def test_max_tokens_change_keeps_results_and_release(self):
        calls = self.complete()
        self.configure({'max_tokens': 30000})
        self.assertEqual(self.engine.state['status'], 'COMPLETED')
        self.assertTrue(self.engine.completed('validate', self.engine.release_key()))
        self.assertTrue(Path(self.engine.export('epub')).exists())
        self.engine.state.update(stage='trans', status='QUEUED')  # even a forced re-walk must hit receipts
        self.assertEqual(self.drive()['status'], 'COMPLETED')
        self.assertEqual(len(self.model.calls), calls)

    def test_check_model_change_only_rechecks(self):
        calls = self.complete()
        self.configure({'check_model': 'checker-2'})
        self.assertEqual(self.engine.state['stage'], 'check')
        self.assertEqual(self.rerun()['status'], 'COMPLETED')
        self.assertEqual(len(self.model.calls) - calls, 2)  # one check per chapter

    def test_allowed_terms_change_only_reruns_deterministic_qa(self):
        calls = self.complete()
        self.configure({'izinkan_istilah': ['Bang!']})
        self.assertEqual(self.engine.state['stage'], 'qa')
        self.assertFalse(list(self.engine.home.glob('*.validated')))
        self.assertEqual(self.rerun()['status'], 'COMPLETED')
        self.assertEqual(len(self.model.calls), calls)

    def test_editorial_preference_reruns_refine_and_check_only(self):
        calls = self.complete()
        self.configure({'preferensi_editorial': 'Dialog lebih santai.'})
        self.assertEqual(self.engine.state['stage'], 'refine')
        self.assertTrue(self.engine.state['approvals'].get('refine'))
        self.assertEqual(self.rerun()['status'], 'COMPLETED')
        # Two new refinements; the fake refiner returns identical text, so the check prompt is
        # byte-identical and its saved response is reused instead of being billed again.
        self.assertEqual(len(self.model.calls) - calls, 2)

    def test_extraction_change_still_blocked_after_translation(self):
        self.complete()
        with self.assertRaises(ValueError):
            self.engine.command('configure', {'min_chars': 10})

    def test_stale_receipt_is_not_revalidated_by_config_change(self):
        self.complete()
        out = self.engine.base / 'refined/bab_001.md'
        out.write_text(TEXT + '\n\nDiubah di luar editor.')  # edit outside the app
        self.engine.command('configure', {'max_tokens': 30000})
        self.assertFalse(self.engine.completed('check:1', self.engine.check_input(1)))

    def test_retry_check_sends_new_request_and_keeps_other_checkpoints(self):
        self.complete()
        before_ids = set(self.model.calls)
        caches = len(list((self.engine.home / 'checkpoints').glob('*.json')))
        self.engine.command('retry', {'stage': 'check', 'chapter': 1})
        self.assertEqual(len(list((self.engine.home / 'checkpoints').glob('*.json'))), caches - 1)
        self.assertEqual(self.rerun()['status'], 'COMPLETED')
        self.assertNotIn(self.model.calls[-1], before_ids)

    def test_retry_refused_with_unknown_call_leaves_receipts(self):
        self.complete()
        receipts = dict(self.engine.state['receipts'])
        self.engine.state['attempts'][0]['status'] = 'UNKNOWN'
        with self.assertRaises(ValueError):
            self.engine.command('retry', {'stage': 'check'})
        self.assertEqual(self.engine.state['receipts'], receipts)

    def test_meaning_verdict_marked_stale_after_edit(self):
        self.complete()
        chapter = self.engine.detail(False)['chapters'][0]
        self.assertTrue(chapter['review_current'])
        out = self.engine.base / 'refined/bab_001.md'
        self.engine.command('save_chapter', {'chapter': 1, 'text': TEXT + '\n\nKoreksi.', 'expected_hash': digest(out)})
        detail = self.engine.detail(False)
        self.assertFalse(detail['chapters'][0]['review_current'])
        self.assertFalse(detail['qa_current'])

    def test_conditional_review_without_details_is_in_report_file(self):
        self.model.gate = 'BERSYARAT'
        original = self.model.__call__

        def call(request_id, payload):
            result = original(request_id, payload)
            if payload['prompt'].startswith('Anda auditor'):
                result['content'] = json.dumps({'gate': 'BERSYARAT', 'masalah': []})
            return result
        self.engine.transport = call
        state = self.drive()
        self.assertEqual(state['status'], 'WAITING_INPUT')
        rows = [json.loads(x) for x in (self.engine.base / 'qa_report.jsonl').read_text().splitlines()]
        self.assertTrue(all(r['status'] == 'REVIEW' for r in rows), rows)
        self.assertIn('REVIEW', (self.engine.base / 'qa_report.md').read_text())


class PathNormalization(unittest.TestCase):
    def test_symlinked_root_keeps_receipts_valid(self):
        import tempfile
        from test_mobile_runtime import fixture, FakeModel
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / 'data' / 'data' / 'app'
            real.mkdir(parents=True)
            alias = Path(tmp) / 'user0'
            os.symlink(real, alias)
            fixture(Path(tmp) / 's.epub')
            engine = Engine.create(alias / 'novels', 'J', Path(tmp) / 's.epub', 'fake')
            engine.transport = FakeModel()
            engine.step()
            again = Engine(real / 'novels', engine.state['id'])
            self.assertTrue(again.completed('preprocess', again.preprocess_input()))
            self.assertTrue(all(not Path(k).is_absolute() for r in again.state['receipts'].values() for k in r['outputs']))


class QaRules(unittest.TestCase):
    args = SimpleNamespace(min_ratio=.1, max_ratio=5, allow_terms=[], allow_flags=False)

    def test_number_compared_by_value_across_separators(self):
        status, issues = check_chapter('He had 5,256,532 points.', 'Dia punya 5.256.532 poin.', self.args, {})
        self.assertEqual(status, 'LULUS', issues)
        status, issues = check_chapter('He had 5,256,532 points.', 'Dia punya 5.256.533 poin.', self.args, {})
        self.assertEqual(status, 'REVIEW')
        self.assertIn('5,256,532', issues[0])

    def test_bang_still_blocks_but_reports_source_sound_evidence(self):
        status, issues = check_chapter('Bang! The door broke.', 'Bang! Pintu itu patah.', self.args, {})
        self.assertEqual(status, 'GAGAL')
        self.assertIn('onomatope', issues[0])
        self.assertIn('izinkan_istilah', issues[0])

    def test_bang_without_source_evidence_has_no_onomatopoeia_hint(self):
        status, issues = check_chapter('Hyung, the door broke.', 'Bang, pintu itu patah.', self.args, {})
        self.assertEqual(status, 'GAGAL')
        self.assertNotIn('onomatope', issues[0])
        _, issues = check_chapter('Bang! Hyung, wait.', 'Bang! Bang, tunggu.', self.args, {})
        self.assertNotIn('onomatope', issues[0])  # more Bang in result than sounds in source

    def test_raw_onomatopoeia_counts_as_source_evidence(self):
        ctx = {'onomatope': [('빵', 'Bang')], 'raw': '빵! 문이 부서졌다.'}
        status, issues = check_chapter('The door broke.', 'Bang! Pintu itu patah.', self.args, ctx)
        self.assertEqual(status, 'GAGAL')
        self.assertIn('onomatope', issues[0])

    def test_allowed_term_resolves_reviewed_onomatopoeia(self):
        args = SimpleNamespace(**dict(vars(self.args), allow_terms=['Bang!']))
        self.assertEqual(check_chapter('Bang! The door broke.', 'Bang! Pintu itu patah.', args, {})[0], 'LULUS')


if __name__ == '__main__':
    unittest.main()
