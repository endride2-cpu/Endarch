import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mobile_runtime.engine import Engine, Blocked, digest

TEXT = '# 1. Bulan\n\n' + '\n\n'.join(['Bulan naik di atas halaman dan semua orang memperhatikan dengan tenang.'] * 15)
GLOSS = '# KARAKTER\n| Hangeul | Nama_Resmi |\n|---|---|\n| — | Bulan |\n'


def fixture(path, count=2):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        manifest = ''.join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(count))
        spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(count))
        z.writestr('book.opf', f'<package xmlns="http://www.idpf.org/2007/opf"><manifest>{manifest}</manifest><spine>{spine}</spine></package>')
        for i in range(count):
            z.writestr(f'c{i}.xhtml', '<html><body><h1>Bulan</h1>' + ''.join('<p>' + x + '</p>' for x in TEXT.split('\n\n')[1:]) + '</body></html>')


class FakeModel:
    def __init__(self):
        self.calls = []
        self.gate = 'LULUS'
        self.refine = 'LULUS'
        self.fail = None

    def __call__(self, request_id, payload):
        self.calls.append(request_id)
        if self.fail:
            return self.fail
        prompt = payload['prompt']
        if prompt.startswith('# GLOSSARY'):
            content = GLOSS
        elif prompt.startswith('Buat panduan'):
            content = 'Gunakan bahasa Indonesia alami.'
        elif prompt.startswith('# REFINEMENT'):
            content = f'===TEKS===\n{TEXT}\n===GATE===\n{self.refine}\n===CATATAN===\nCatatan editor terpisah.'
        elif prompt.startswith('Anda auditor'):
            content = json.dumps({'gate': self.gate, 'masalah': [] if self.gate == 'LULUS' else ['Tinjau konteks.']})
        else:
            content = TEXT
        return dict(status='SUCCEEDED', content=content, finish_reason='stop', usage={'prompt_tokens': 10, 'completion_tokens': 20}, cost=0.001, cost_status='estimated')


class MobileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source.epub'
        fixture(self.source)
        self.model = FakeModel()
        self.engine = Engine.create(self.root / 'novels', 'Judul', self.source, 'fake')
        self.engine.transport = self.model

    def tearDown(self):
        self.tmp.cleanup()

    def drive(self, until='COMPLETED'):
        for _ in range(100):
            state = self.engine.step()
            if state['status'] == until or state['stage'] == until:
                return state
            if state['status'] == 'WAITING_INPUT' and 'refinement' in (state['error'] or ''):
                self.engine.command('approve_refine')
            elif state['status'] in ('FAILED', 'BLOCKED_QA', 'WAITING_INPUT'):
                return state
        self.fail('Worker failed to reach checkpoint')

    def test_full_pipeline_and_export_preserve_contract(self):
        self.assertEqual(self.drive()['status'], 'COMPLETED', self.engine.state)
        base = self.engine.base
        self.assertTrue((base / 'draf/bab_001_draft.md').exists())
        self.assertTrue((base / 'refined/bab_001.meta.json').exists())
        row = json.loads((base / 'qa_llm.jsonl').read_text().splitlines()[0])
        self.assertEqual(set(row), {'bab', 'gate', 'masalah', 'hash'})
        self.assertEqual(row['hash'], digest(base / 'refined/bab_001.md'))
        self.assertTrue(Path(self.engine.export('epub')).exists())
        self.assertIn('Catatan editor terpisah', Path(self.engine.export('notes')).read_text())

    def test_resume_does_not_rebill_committed_calls(self):
        self.drive('refine')
        self.engine.command('pause')
        count = len(self.model.calls)
        novel_id = self.engine.state['id']
        self.engine = Engine(self.root / 'novels', novel_id, self.model)
        self.engine.command('resume')
        self.assertEqual(self.drive()['status'], 'COMPLETED')
        self.assertEqual(len(self.model.calls) - count, 4)  # two refine + two check

    def test_edit_invalidates_release_and_stale_hash_is_rejected(self):
        self.drive()
        path = self.engine.base / 'refined/bab_001.md'
        before = digest(path)
        self.engine.command('save_chapter', {'chapter': 1, 'text': TEXT + '\n\nBulan bersinar.', 'expected_hash': before})
        with self.assertRaises(Blocked):
            self.engine.export('epub')
        with self.assertRaises(ValueError):
            self.engine.command('save_chapter', {'chapter': 1, 'text': TEXT, 'expected_hash': before})
        self.assertFalse(list(self.engine.home.glob('*.validated')))

    def test_glossary_edit_invalidates_language_receipts(self):
        self.drive()
        gl = self.engine.base / 'glosarium/master_full.md'
        self.engine.command('save_glossary', {'text': GLOSS + '\nCatatan baru.', 'expected_hash': digest(gl)})
        self.assertFalse(any(k.startswith('trans:') for k in self.engine.state['receipts']))
        with self.assertRaises(Blocked):
            self.engine.export('epub')

    def test_review_requires_hash_bound_human_decision(self):
        self.model.gate = 'BERSYARAT'
        state = self.drive()
        self.assertEqual(state['status'], 'WAITING_INPUT')
        self.assertEqual(state['stage'], 'qa')
        for n in self.engine.numbers():
            self.engine.command('decide', {'chapter': f'bab_{n:03d}', 'note': 'Sudah dibandingkan dengan sumber; makna sesuai.'})
        self.assertEqual(self.drive()['status'], 'COMPLETED')

    def test_gagal_cannot_be_overridden_by_decision_or_direct_build(self):
        self.model.gate = 'GAGAL'
        self.assertEqual(self.drive()['status'], 'BLOCKED_QA')
        with self.assertRaises(ValueError):
            self.engine.command('decide', {'chapter': 'bab_001', 'note': 'Tidak boleh meloloskan'})
        self.engine.state.update(stage='build', status='QUEUED')
        self.assertEqual(self.engine.step()['status'], 'BLOCKED_QA')

    def test_unknown_request_needs_consent_before_resend_and_blocks_new_generation(self):
        self.engine.step()  # preprocess
        self.model.fail = {'status': 'UNKNOWN', 'cost_status': 'unknown'}
        self.assertEqual(self.engine.step()['status'], 'WAITING_INPUT')
        self.model.fail = None
        self.engine.command('resume')
        self.engine.step()
        self.assertEqual(len(self.model.calls), 1)  # plain resume never re-sends a possibly billed call
        self.assertEqual(self.engine.state['status'], 'WAITING_INPUT')
        with self.assertRaises(ValueError):
            self.engine.command('retry', {'stage': 'gloss'})
        self.engine.command('confirm_resend')
        self.engine.step()
        self.assertEqual(self.model.calls[-2], self.model.calls[-1])  # same local correlation ID
        self.assertEqual(len(self.engine.state['attempts']), 1)
        self.assertEqual(self.engine.state['attempts'][0]['status'], 'SUCCEEDED')
        with self.assertRaises(ValueError):
            self.engine.command('confirm_resend')

    def test_partial_refined_pair_is_not_a_checkpoint(self):
        self.drive('refine')
        self.engine.step()
        meta = self.engine.base / 'refined/bab_001.meta.json'
        meta.unlink()
        count = len(self.model.calls)
        self.engine.step()
        self.assertTrue(meta.exists())
        self.assertEqual(len(self.model.calls), count)  # recover saved response; no extra charge

    def test_retry_auth_error_does_not_cascade(self):
        self.engine.step()
        self.model.fail = {'status': 'FAILED', 'error': 'Provider HTTP 401', 'retryable': False}
        self.assertEqual(self.engine.step()['status'], 'FAILED')
        self.assertEqual(len(self.model.calls), 1)

    def test_pause_before_call_leaves_no_pending_attempt(self):
        self.engine.step()
        self.engine.pause_requested = lambda: True
        self.assertEqual(self.engine.step()['status'], 'PAUSED')
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.engine.state['attempts'], [])

    def test_no_ui_gate_override_fields(self):
        for field in ('allow_flags', 'require_review', 'api_key', 'refine'):
            with self.assertRaises(ValueError):
                self.engine.command('configure', {field: False})

    def test_crash_after_success_before_cache_does_not_make_new_request(self):
        self.engine.step()
        self.engine.step()
        request_id = self.model.calls[-1]
        for cache in (self.engine.home / 'checkpoints').glob('*.json'):
            cache.unlink()
        (self.engine.home / 'gloss_parts/0.md').unlink()
        self.assertEqual(self.engine.step()['status'], 'WAITING_INPUT')
        self.assertEqual(len(self.model.calls), 1)  # lost response is not silently re-bought
        self.assertEqual(len(self.engine.state['attempts']), 1)
        self.engine.command('confirm_resend')
        self.engine.step()
        self.assertEqual(self.model.calls[-1], request_id)

    def test_response_is_saved_before_attempt_is_marked_succeeded(self):
        self.engine.step()
        journal = []
        original = self.engine.save

        def spy():
            attempts = self.engine.state['attempts']
            if attempts and attempts[-1]['status'] == 'SUCCEEDED':
                journal.append(len(list((self.engine.home / 'checkpoints').glob('*.json'))))
            original()
        self.engine.save = spy
        self.engine.step()
        self.assertTrue(journal and journal[0] == 1, journal)

    def test_saved_response_resolves_attempt_left_pending_by_crash(self):
        self.engine.step()
        self.engine.step()
        attempt = self.engine.state['attempts'][-1]
        attempt['status'] = 'PENDING'  # crash after checkpoint write, before journal update
        self.engine.state['receipts'].pop('gloss:0')
        self.engine.state.update(stage='gloss', status='QUEUED')
        calls = len(self.model.calls)
        self.engine.step()
        self.assertEqual(len(self.model.calls), calls)
        self.assertEqual(attempt['status'], 'SUCCEEDED')
        self.engine.state['status'] = 'PAUSED'
        self.engine.command('retry', {'stage': 'gloss'})  # no longer blocked by a phantom unknown

    def test_pause_does_not_rewrite_final_or_waiting_states(self):
        for status in ('COMPLETED', 'BLOCKED_QA', 'FAILED', 'WAITING_INPUT'):
            self.engine.state['status'] = status
            self.assertEqual(self.engine.command('pause')['status'], status)

    def test_backoff_is_bounded_and_not_a_busy_rebill_loop(self):
        self.engine.step()
        self.model.fail = {'status': 'FAILED', 'error': '429', 'retryable': True}
        for attempt in range(6):
            self.engine.step()
            if attempt < 5:
                self.assertEqual(self.engine.state['status'], 'RETRY_WAIT')
                count = len(self.model.calls)
                self.engine.step()
                self.assertEqual(len(self.model.calls), count)
                for task in self.engine.state['tasks'].values():
                    task['next_retry_at'] = 0
        self.assertEqual(self.engine.state['status'], 'FAILED')
        self.assertEqual(len(self.model.calls), 6)

    def test_failed_refinement_edit_is_rechecked_not_self_approved(self):
        self.model.refine = 'GAGAL'
        self.assertEqual(self.drive()['status'], 'BLOCKED_QA')
        out = self.engine.base / 'refined/bab_001.md'
        edited = TEXT + '\n\nKoreksi manusia.'
        self.engine.command('save_chapter', {'chapter': 1, 'text': edited, 'expected_hash': digest(out)})
        self.assertEqual(self.engine.state['stage'], 'refine')
        snapshot = self.engine.home / self.engine.state['refine_inputs']['1']
        self.assertEqual(snapshot.read_text(), edited)
        self.assertEqual(json.loads(out.with_suffix('.meta.json').read_text())['gate'], 'GAGAL')

    def test_approvals_cannot_skip_pending_stages(self):
        for action in ('approve_extraction', 'approve_refine'):
            with self.assertRaises(ValueError):
                self.engine.command(action)
        self.assertEqual(self.engine.state['approvals'], {})
        self.engine.step()
        with self.assertRaises(ValueError):
            self.engine.command('approve_refine')

    def test_extraction_approval_only_after_inspection_requested(self):
        from mobile_runtime.engine import atomic
        # Preprocess may stop for a real short section; approval cannot precede it.
        self.engine.state['status'] = 'WAITING_INPUT'
        with self.assertRaises(ValueError):
            self.engine.command('approve_extraction')
        self.engine.state['status'] = 'QUEUED'
        self.engine.step()
        self.engine.state.update(stage='preprocess', status='WAITING_INPUT')
        self.engine.command('approve_extraction')
        self.assertTrue(self.engine.state['approvals']['extraction'])

    def test_blocked_job_cannot_resume_without_explicit_repair(self):
        self.engine.state.update(status='BLOCKED_QA', stage='qa')
        with self.assertRaises(ValueError):
            self.engine.command('resume')
        self.assertEqual(self.engine.state['status'], 'BLOCKED_QA')
        self.assertEqual(self.model.calls, [])

    def test_chapter_status_uses_qa_and_marks_report_stale_after_edit(self):
        self.assertEqual(self.drive()['status'], 'COMPLETED')
        chapter = self.engine.detail(False)['chapters'][0]
        self.assertEqual(chapter['qa']['status'], 'LULUS')
        self.assertTrue(chapter['qa_current'])
        out = self.engine.base / 'refined/bab_001.md'
        self.engine.command('save_chapter', {'chapter': 1, 'text': TEXT + '\n\nKoreksi.', 'expected_hash': digest(out)})
        self.assertFalse(self.engine.detail(False)['chapters'][0]['qa_current'])

    def test_alias_approval_rechecks_qa_without_rebilling_language_steps(self):
        self.assertEqual(self.drive()['status'], 'COMPLETED')
        calls = len(self.model.calls)
        self.engine.command('approve_name_alias', {'source': 'Bulan', 'target': 'Rembulan', 'reason': 'Padanan novel telah ditelaah'})
        self.assertEqual(self.drive()['status'], 'COMPLETED')
        self.assertEqual(len(self.model.calls), calls)

    def test_running_job_rejects_edits(self):
        self.engine.step()
        with self.assertRaises(ValueError):
            self.engine.command('save_glossary', {'text': GLOSS, 'expected_hash': None})


if __name__ == '__main__':
    unittest.main()
