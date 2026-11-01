"""Single-writer, checkpointed mobile adapter. No provider credentials live here.

Job journals are the cross-runtime recovery record; Room indexes their snapshots.
All public operations must hold the application-wide bridge lock.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import random
import shutil
import sys
import threading
import time
import uuid
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import pipeline_unified as p
from publication import digest, parse_refinement, glossary_names, assert_prompt
from qa_gate import run as run_qa
from validate_epub import validate
from editor_notes import generate

STAGES = p.STAGES
UNRESOLVED = ('Hasil panggilan API sebelumnya tidak diketahui; provider mungkin sudah memproses dan menagihnya. '
              'Periksa riwayat penggunaan provider, lalu konfirmasi kirim ulang bila perlu')
ACTIVE = {'RUNNING', 'PAUSE_REQUESTED'}
# Earliest stage whose *existing* outputs become invalid when a config key changes.
# None = only future requests use the new value (results already produced stay valid).
# izinkan_istilah only steers the deterministic QA; drafts made before the change are not
# regenerated (use an explicit retry for that), so changing it never re-bills translation.
CONFIG_SCOPE = {'raw_map': 'preprocess', 'min_chars': 'preprocess', 'preferensi_editorial': 'refine',
                'check_model': 'check', 'izinkan_istilah': 'qa', 'max_tokens': None, 'paralel_bab': None}
# Chapters sent to the provider at once in trans/refine/check; chunks inside a chapter stay sequential.
PARALLEL_DEFAULT = 3


def atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('wb') as f:
        f.write(content if isinstance(content, bytes) else content.encode('utf-8'))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def dump(path, obj):
    atomic(path, json.dumps(obj, ensure_ascii=False, indent=2))


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Waiting(Exception):
    pass


class Blocked(Exception):
    pass


class Engine:
    def __init__(self, root, novel_id, transport=None, pause_requested=None):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', novel_id):
            raise ValueError('Invalid novel id')
        self.root = Path(root).resolve()
        self.home = self.root / novel_id
        self.journal = self.home / 'job.json'
        self.state = json.loads(self.journal.read_text())
        self.base = self.home / 'work' / ('work_' + novel_id) / novel_id
        self.transport = transport
        self.pause_requested = pause_requested or (lambda: False)
        # Serializes journal mutation/saves when chapters run in parallel; provider calls run outside it.
        self.lock = threading.RLock()
        self.conf = dict(p.DEFAULTS, **self.state['config'])
        self.conf.update(_slug=novel_id, _base=self.base, _output_root=self.home,
                         require_review=True, allow_flags=False, refine='ask', workers=1)

    @classmethod
    def create(cls, root, title, source, model, source_kind='EN_TRANSLATED', raw=None):
        if not title.strip() or not model.strip():
            raise ValueError('Judul dan model wajib diisi')
        if source_kind not in ('EN_TRANSLATED', 'RAW_KOREA', 'BOTH'):
            raise ValueError('Invalid source kind')
        if source_kind == 'BOTH' and not raw:
            raise ValueError('Mode BOTH memerlukan EPUB raw')
        # Reject oversized archives before any extraction (also limits zip bombs).
        for path in filter(None, [source, raw]):
            with zipfile.ZipFile(path) as z:
                if len(z.infolist()) > 20000 or sum(i.file_size for i in z.infolist()) > 512 * 1024**2:
                    raise ValueError('EPUB exceeds 512 MiB expanded / 20,000 entries')
                if any(i.file_size > 32 * 1024**2 for i in z.infolist()):
                    raise ValueError('EPUB entry exceeds 32 MiB')
        slug = 'novel-' + uuid.uuid4().hex[:12]
        home = Path(root).resolve() / slug  # one canonical form for /data/user/0 vs /data/data
        home.mkdir(parents=True)
        shutil.copyfile(source, home / 'source.epub')
        if raw:
            shutil.copyfile(raw, home / 'raw.epub')
        config = dict(judul=title.strip(), doc=str(home / 'source.epub'),
                      doc_raw=str(home / 'raw.epub') if raw else None,
                      sumber=source_kind, model=model.strip(), check_model=model.strip(),
                      gloss_seed_babs=5, gloss_every=20, min_chars=300,
                      temperature=0.3, max_tokens=20000, izinkan_istilah=[])
        state = dict(id=slug, title=title.strip(), status='QUEUED', stage='preprocess',
                     config=config, tasks={}, attempts=[], events=[], receipts={},
                     decisions={}, approvals={}, created_at=time.time(), error=None)
        dump(home / 'job.json', state)
        return cls(root, slug)

    def save(self):
        with self.lock:
            self.state['updated_at'] = time.time()
            dump(self.journal, self.state)

    def event(self, action, detail=''):
        self.state['events'].append(dict(at=time.time(), action=action, detail=detail))
        self.save()

    def files_hash(self, paths):
        return {str(x.relative_to(self.home)): digest(x) for x in paths if x.is_file()}

    def dependencies(self):
        paths = [self.home / 'source.epub', self.home / 'raw.epub',
                 self.base / 'glosarium/master_full.md', self.base / 'style.md']
        shared = list((ROOT / 'prompts').glob('*.md')) + [ROOT / 'config/rules.json', ROOT / 'glossary/global.md']
        return fingerprint([{k: v for k, v in self.state['config'].items() if k not in ('name_aliases', 'paralel_bab')}, self.files_hash(paths),
                            {str(x.relative_to(ROOT)): digest(x) for x in shared}])

    def release_key(self):
        paths = list((self.base / 'refined').glob('bab_*')) + [self.base / 'qa_llm.jsonl']
        paths += list((self.base / 'teks').glob('*')) + list((self.base / 'teks_raw').glob('*')) + list((self.base / 'images').glob('*'))
        return fingerprint([self.dependencies(), self.files_hash(paths)] + ([self.conf['name_aliases']] if self.conf.get('name_aliases') else []))

    def completed(self, key, inp):
        r = self.state['receipts'].get(key)
        return bool(r and r['input'] == inp and all(
            (self.home / path).is_file() and digest(self.home / path) == sha
            for path, sha in r['outputs'].items()))

    def commit(self, key, inp, paths):
        with self.lock:
            self.state['receipts'][key] = dict(input=inp, outputs=self.files_hash(paths))
            self.state['tasks'][key] = dict(stage=key.split(':')[0], status='SUCCEEDED',
                                           completed_at=time.time())
            self.save()

    def per_chapter(self, key_prefix, inputs, work):
        """Run work(n) for the next batch of chapters whose receipt is stale; True if any ran.

        Each chapter still commits its own receipt, so a failure in one never discards the others.
        After the batch, a terminal error (Blocked/failure) wins over Waiting (pause/retry/unknown).
        """
        todo = [n for n in self.numbers() if not self.completed(f'{key_prefix}:{n}', inputs(n))]
        batch = todo[:max(1, int(self.conf.get('paralel_bab', PARALLEL_DEFAULT)))]
        if len(batch) == 1:
            work(batch[0])
        elif batch:
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                errors = [e for e in [f.exception() for f in [pool.submit(work, n) for n in batch]] if e]
            if errors:
                raise next((e for e in errors if not isinstance(e, Waiting)), errors[0])
        return bool(batch)

    def ask(self, key, prompt, limit=None, model=None):
        payload = dict(model=model or self.conf['model'], prompt=assert_prompt(prompt),
                       max_tokens=limit or self.conf['max_tokens'], temperature=self.conf['temperature'])
        with self.lock:
            attempt = self.prepare_attempt(key, payload)
            if 'content' in attempt:
                return attempt['content']
        try:
            result = self.transport(attempt['id'], payload)
        except Exception:
            with self.lock:
                attempt.update(status='UNKNOWN', error='Transport interrupted; recover same request ID')
                self.save()
            raise Waiting('Status panggilan belum diketahui; lanjutkan untuk memeriksa request yang sama')
        with self.lock:
            return self.finish_attempt(key, attempt, result)

    def prepare_attempt(self, key, payload):
        """Journal the call before it is sent; returns {'content'} when a checkpoint already holds it."""
        if self.pause_requested():
            self.state['status'] = 'PAUSED'
            raise Waiting('Job dijeda pada checkpoint')
        if self.transport is None:
            raise Waiting('Hubungkan relay sebelum menjalankan tahap bahasa')
        cache_key = fingerprint([key, payload])
        cache = self.home / 'checkpoints' / (cache_key + '.json')
        if cache.exists():
            task = self.state['tasks'].setdefault(key, {})
            task.update(status='SUCCEEDED', stage=key.split(':')[0])
            # Crash after the response was saved but before the journal caught up: the content is
            # here, so the call is resolved (and must not keep blocking retries or asking consent).
            for a in self.state['attempts']:
                if a['cache_key'] == cache_key and a['status'] in ('PENDING', 'UNKNOWN'):
                    a.update(status='SUCCEEDED', recovered_from_checkpoint=True, finished_at=time.time())
            self.save()
            return dict(content=json.loads(cache.read_text())['content'])
        task = self.state['tasks'].setdefault(key, {})
        task.update(status='RUNNING', stage=key.split(':')[0])
        # Same local ID across a lost response/process death, new ID only after a known failure.
        # The ID is a local correlation key only: OpenAI-compatible providers do not deduplicate
        # on it, so re-sending an unresolved call may be billed twice and needs explicit consent.
        pending = next((a for a in reversed(self.state['attempts'])
                        if a['cache_key'] == cache_key and (a['status'] in ('PENDING', 'UNKNOWN') or
                                                            (a['status'] == 'SUCCEEDED' and not a.get('superseded')))), None)
        if pending and not pending.get('resend_confirmed'):
            pending['status'] = 'UNKNOWN'
            self.save()
            raise Waiting(UNRESOLVED)
        attempt = pending or dict(id=uuid.uuid4().hex, task=key, cache_key=cache_key,
                                  model=payload['model'], status='PENDING', started_at=time.time(),
                                  cost_status='unknown', cost=None)
        if not pending:
            self.state['attempts'].append(attempt)
        else:
            attempt.update(status='PENDING', resend_confirmed=False, resent_at=time.time())
        self.save()
        return attempt

    def finish_attempt(self, key, attempt, result):
        cache = self.home / 'checkpoints' / (attempt['cache_key'] + '.json')
        cache_key = attempt['cache_key']
        task = self.state['tasks'].setdefault(key, {})
        status = result.get('status')
        content = (result.get('content') or '').strip()
        if status == 'SUCCEEDED' and content and result.get('finish_reason') != 'length':
            # Persist the paid response before the journal says SUCCEEDED, so a crash between the
            # two can never leave a succeeded call without its content.
            dump(cache, dict(content=content))
        attempt.update({k: result.get(k) for k in ('usage', 'cost', 'cost_status', 'price_snapshot')})
        attempt.update(status=status or 'UNKNOWN', finished_at=time.time())
        self.save()
        if status in ('PENDING', 'UNKNOWN'):
            raise Waiting('Panggilan belum pasti selesai; jangan kirim ulang sebagai request baru')
        if status != 'SUCCEEDED':
            attempt['error'] = result.get('error', 'API failure')
            attempt['retryable'] = bool(result.get('retryable'))
            self.save()
            if attempt['retryable']:
                failures = sum(a['cache_key'] == cache_key and a['status'] == 'FAILED'
                               for a in self.state['attempts'])
                if failures < 6:
                    task['next_retry_at'] = time.time() + max(float(result.get('retry_after') or 0), 2 ** failures + random.random())
                    self.state['status'] = 'RETRY_WAIT'
                    self.save()
                    raise Waiting('Retry terjadwal setelah backoff')
            raise ValueError(result.get('error', 'API request failed'))
        if not content or result.get('finish_reason') == 'length':
            raise ValueError('Output kosong atau terpotong; naikkan batas output atau periksa model')
        task.update(status='SUCCEEDED', completed_at=time.time())
        self.save()
        return content

    def numbers(self):
        return sorted(int(x.stem.split('_')[1]) for x in (self.base / 'teks').glob('bab_*.txt'))

    def review_rows(self):
        file = self.base / 'qa_llm.jsonl'
        rows = {}
        for line in file.read_text().splitlines() if file.exists() else []:
            try:
                r = json.loads(line)
                rows[r['bab']] = r
            except (ValueError, KeyError):
                continue
        return rows

    def qa(self):
        # Stronger mobile profile: complete refined pairs and current review receipts.
        if not self.numbers():
            raise Blocked('Tidak ada bab')
        for n in self.numbers():
            key = f'check:{n}'
            if not self.completed(key, self.check_input(n)):
                raise Blocked(f'Bab {n}: review hilang atau basi')
            meta = self.base / f'refined/bab_{n:03d}.meta.json'
            if not meta.exists() or json.loads(meta.read_text()).get('gate') not in ('LULUS', 'BERSYARAT'):
                raise Blocked(f'Bab {n}: refinement belum lulus')
        args = [str(self.base), '--source', 'refined', '--require-review', '--kind', self.conf['sumber']]
        for term in self.conf.get('izinkan_istilah', []):
            args += ['--allow-term', term]
        if self.conf.get('name_aliases'):
            args += ['--name-aliases', json.dumps(self.conf['name_aliases'], ensure_ascii=False)]
        code = run_qa(args)
        rows = [json.loads(x) for x in (self.base / 'qa_report.jsonl').read_text().splitlines()]
        self.state['qa_rows'] = rows
        self.state['qa_report_version'] = self.release_key()
        self.save()
        if code:
            raise Blocked('QA GAGAL; perbaiki bab dan jalankan check ulang')
        release = self.release_key()
        self.state['qa_observed'] = release
        self.save()
        for row in rows:
            if row['status'] == 'REVIEW':
                decision = self.state['decisions'].get(row['bab'])
                if not decision or decision['release'] != release:
                    raise Waiting('Telaah wajib untuk ' + row['bab'])
        return rows

    def check_input(self, n):
        paths = [self.base / f'refined/bab_{n:03d}.md', self.base / f'refined/bab_{n:03d}.meta.json',
                 self.base / f'teks/bab_{n:03d}.txt', self.base / f'teks_raw/bab_{n:03d}.txt']
        return fingerprint([self.dependencies(), self.files_hash(paths)])

    def preprocess_input(self):
        return fingerprint([digest(self.home / 'source.epub'), self.state['config']])

    def refine_draft(self, n):
        override = self.state.get('refine_inputs', {}).get(str(n))
        return self.home / override if override else self.base / f'draf/bab_{n:03d}_draft.md'

    def refine_input(self, n):
        return fingerprint([self.dependencies(), digest(self.refine_draft(n))])

    def receipt_input(self, key):
        """Current expected input of a receipt, or None when it does not depend on config."""
        stage, _, rest = key.partition(':')
        try:
            if stage == 'preprocess':
                return self.preprocess_input()
            if stage == 'trans' and rest.isdigit():
                return self.dependencies()
            if stage == 'refine' and rest.isdigit():
                return self.refine_input(int(rest))
            if stage == 'check' and rest.isdigit():
                return self.check_input(int(rest))
            if stage in ('qa', 'build', 'validate'):
                return self.release_key()
        except FileNotFoundError:
            return 'missing-input'
        return None

    def valid_receipts(self):
        return {k for k in self.state['receipts']
                if (inp := self.receipt_input(k)) is None or self.completed(k, inp)}

    def task_matches(self, task, stage, n):
        parts = task.split(':')
        if parts[0] not in STAGES or STAGES.index(parts[0]) < STAGES.index(stage):
            return False
        return not n or task in ('qa', 'build', 'validate') or parts[1:2] == [str(n)]

    def step(self):
        if self.state['status'] in ('PAUSED', 'COMPLETED', 'BLOCKED_QA', 'FAILED', 'WAITING_INPUT'):
            return self.state
        if self.state['status'] == 'RETRY_WAIT':
            if any(t.get('next_retry_at', 0) > time.time() for t in self.state['tasks'].values()):
                return self.state
        self.state.update(status='RUNNING', error=None)
        self.save()
        try:
            self._step()
        except Waiting as e:
            if self.state['status'] not in ('RETRY_WAIT', 'PAUSED'):
                self.state['status'] = 'WAITING_INPUT'
            self.state['error'] = str(e)
        except Blocked as e:
            self.state.update(status='BLOCKED_QA', error=str(e))
        except (Exception, SystemExit) as e:
            # Never serialize provider messages/URLs, which can contain credentials.
            self.state.update(status='FAILED', error=f'{type(e).__name__}: tahap {self.state["stage"]} gagal; periksa artefak/laporan')
        self.save()
        return self.state

    def advance(self):
        i = STAGES.index(self.state['stage'])
        if i == len(STAGES) - 1:
            self.state['status'] = 'COMPLETED'
        else:
            self.state['stage'] = STAGES[i + 1]

    def _step(self):
        stage = self.state['stage']
        if stage == 'preprocess':
            inp = self.preprocess_input()
            if not self.completed(stage, inp):
                p.stage_preprocess(self.conf)
                if not self.numbers():
                    raise ValueError('Tidak ada bab')
                self.commit(stage, inp, list((self.base / 'teks').glob('*')) + list((self.base / 'teks_raw').glob('*')) + list((self.base / 'images').glob('*')))
            if ((self.base / 'preprocess_dropped.md').exists() or self.conf.get('doc_raw')) and not self.state['approvals'].get('extraction'):
                raise Waiting('Periksa laporan ekstraksi/alignment lalu setujui')
            self.advance()
            return
        if stage == 'gloss':
            files = [self.base / f'teks/bab_{n:03d}.txt' for n in self.numbers()]
            master = self.base / 'glosarium/master_full.md'
            seed = self.conf['gloss_seed_babs']
            blocks = [files[:seed]] + [files[i:i + self.conf['gloss_every']] for i in range(seed, len(files), self.conf['gloss_every'])]
            for i, block in enumerate(blocks):
                key = f'gloss:{i}'
                # Each block receipt points to its immutable contribution, not mutable master.
                part = self.home / 'gloss_parts' / f'{i}.md'
                inp = fingerprint([self.files_hash(block), self.conf['model']])
                if not self.completed(key, inp):
                    existing = '\n\n'.join(x.read_text() for x in sorted((self.home / 'gloss_parts').glob('*.md'))) if i else p.load_global_glossary()
                    prompt = p.PROMPT_GLOSS.replace('[TEMPEL TEKS]', '\n\n'.join(x.read_text() for x in block)).replace('[TEMPEL / KOSONGKAN]', existing or 'Kosong')
                    out = self.ask(key, prompt, 12000)
                    atomic(part, out)
                    self.commit(key, inp, [part])
                    return
            if not master.exists():
                atomic(master, '\n\n'.join((self.home / 'gloss_parts' / f'{i}.md').read_text() for i in range(len(blocks))))
            if not glossary_names(master.read_text()):
                raise Waiting('Perbaiki tabel KARAKTER di editor glosarium')
            self.advance()
            return
        if stage == 'trans':
            style = self.base / 'style.md'
            if not style.exists():
                sample = '\n\n'.join((self.base / f'teks/bab_{n:03d}.txt').read_text() for n in self.numbers()[:3])
                atomic(style, self.ask('trans:style', 'Buat panduan gaya Indonesia maksimum 150 kata dari sumber berikut. Output hanya panduan.\n' + sample, 1000, self.conf.get('check_model')))
                dump(self.base / 'style.meta.json', dict(version=1, sha256=digest(style)))
                return
            gl = p.load_glossary(self.conf)
            inp = self.dependencies()

            def translate(n):
                key = f'trans:{n}'
                out = self.base / f'draf/bab_{n:03d}_draft.md'
                src = (self.base / f'teks/bab_{n:03d}.txt').read_text()
                parts = p.split_long(src, 3500)
                _, _, raw = p.resolve_source(self.conf, n)
                raws = p.align_chunks(raw, parts) if raw else [None] * len(parts)
                outputs = []
                for i, part in enumerate(parts):
                    prompt = p.build_trans_prompt(self.conf, n, part, gl, style.read_text(), prev_id=outputs[-1] if outputs else '', raw_text=raws[i])
                    text = self.ask(key + f':chunk:{i}', prompt)
                    outputs.append(re.sub(r'^#.*\n?', '', text, flags=re.M) if i else text)
                atomic(out, '\n\n'.join(outputs))
                self.commit(key, inp, [out])
            if self.per_chapter('trans', lambda n: inp, translate):
                return
            if not self.state['approvals'].get('refine'):
                raise Waiting('Terjemahan selesai. Setujui refinement untuk melanjutkan')
            self.advance()
            return
        if stage == 'refine':
            gl = p.load_glossary(self.conf)

            def refine(n):
                key = f'refine:{n}'
                draft = self.refine_draft(n)
                inp = self.refine_input(n)
                out = self.base / f'refined/bab_{n:03d}.md'
                meta = out.with_suffix('.meta.json')
                kind, src, raw = p.resolve_source(self.conf, n)
                prompt = (p.PROMPT_REFINE.replace('GLOSARIUM\n[TEMPEL]', 'GLOSARIUM\n' + gl)
                          .replace('TEKS SUMBER\n[TEMPEL]', 'TEKS SUMBER\n' + src + ('\nRAW HANGEUL:\n' + raw if raw else ''))
                          .replace('JENIS SUMBER (RAW_KOREA/EN_TRANSLATED/BOTH)\n[ISI]', 'JENIS SUMBER (RAW_KOREA/EN_TRANSLATED/BOTH)\n' + kind)
                          .replace('DRAF INDONESIA\n[TEMPEL]', 'DRAF INDONESIA\n' + draft.read_text())
                          .replace('KONTEKS (opsional)\n[TEMPEL / KOSONGKAN]', 'KONTEKS (opsional)\n' + p.context_for(self.base, n))
                          .replace('PREFERENSI EDITORIAL (opsional)\n[ISI / KOSONGKAN]', 'PREFERENSI EDITORIAL (opsional)\n' + self.conf.get('preferensi_editorial', '—'))
                          + p.allowed_note(self.conf))
                text, metadata = parse_refinement(self.ask(key, prompt))
                atomic(out, text)
                dump(meta, metadata)
                self.commit(key, inp, [out, meta])
                if metadata['gate'] == 'GAGAL':
                    raise Blocked(f'Refinement bab {n} GAGAL')
            if self.per_chapter('refine', self.refine_input, refine):
                return
            self.advance()
            return
        if stage == 'check':
            def check(n):
                key = f'check:{n}'
                inp = self.check_input(n)
                out = self.base / f'refined/bab_{n:03d}.md'
                _, src, raw = p.resolve_source(self.conf, n)
                # Full source/final avoids proportional chunk alignment introducing false comparisons.
                prompt = p.CHECK_PROMPT.replace('{src}', raw or src).replace('{draft}', out.read_text())
                result = self.ask(key, prompt, 4000, self.conf.get('check_model'))
                match = re.search(r'\{.*\}', result, re.S)
                row = json.loads(match.group() if match else result)
                if row.get('gate') not in ('LULUS', 'BERSYARAT', 'GAGAL') or not isinstance(row.get('masalah'), list) or not all(isinstance(x, str) for x in row['masalah']):
                    raise ValueError('Malformed check result')
                row = dict(bab=f'bab_{n:03d}', gate=row['gate'], masalah=row['masalah'], hash=digest(out))
                log = self.base / 'qa_llm.jsonl'
                receipt = self.home / 'reviews' / f'{n}.json'
                with self.lock:  # append is read-modify-write on a shared file
                    atomic(log, (log.read_text() if log.exists() else '') + json.dumps(row, ensure_ascii=False) + '\n')
                    dump(receipt, row)
                    self.commit(key, inp, [receipt])
                if row['gate'] == 'GAGAL':
                    raise Blocked(f'Review makna bab {n} GAGAL')
            if self.per_chapter('check', self.check_input, check):
                return
            self.advance()
            return
        if stage == 'qa':
            self.qa()
            self.commit('qa', self.release_key(), [self.base / 'qa_report.jsonl'])
            self.advance()
            return
        if stage == 'build':
            self.qa()
            p.stage_build(self.conf)
            out = self.home / f'{self.state["id"]}_terjemahan.epub'
            self.commit('build', self.release_key(), [out])
            self.advance()
            return
        if stage == 'validate':
            self.qa()
            out = self.home / f'{self.state["id"]}_terjemahan.epub'
            if not self.completed('build', self.release_key()):
                raise Blocked('Build basi; jalankan build ulang')
            errors = validate(out)
            marker = out.with_suffix('.epub.validated')
            if errors:
                marker.unlink(missing_ok=True)
                raise ValueError('EPUB tidak valid')
            atomic(marker, digest(out))
            notes = generate(self.base)
            decisions = self.state.get('decisions', {})
            if decisions:
                appendix = '\n\n## Telaah peringatan manusia\n\n' + '\n'.join(
                    f"- **{bab}**: {d['note']} (versi {d['release']})" for bab, d in decisions.items())
                atomic(notes, notes.read_text() + appendix + '\n')
            self.commit('validate', self.release_key(), [out, marker, notes])
            self.advance()

    def command(self, action, data=None):
        data = data or {}
        if self.state['status'] in ACTIVE and action not in ('pause', 'resume'):
            raise ValueError('Pause the job before editing or retrying')
        if action == 'pause':
            # Pausing only applies to work in flight; final/waiting states keep their meaning.
            if self.state['status'] in {'QUEUED', 'RETRY_WAIT'} | ACTIVE:
                self.state['status'] = 'PAUSED'
        elif action == 'resume':
            if self.state['status'] == 'BLOCKED_QA':
                raise ValueError('Perbaiki bab atau jalankan pemeriksaan ulang secara eksplisit sebelum melanjutkan')
            if self.state['status'] == 'COMPLETED':
                return self.state
            self.state['status'] = 'QUEUED'
        elif action in ('approve_refine', 'approve_extraction'):
            expected = 'trans' if action == 'approve_refine' else 'preprocess'
            if self.state['stage'] != expected or self.state['status'] != 'WAITING_INPUT':
                raise ValueError('Persetujuan belum diperlukan pada tahap ini')
            if action == 'approve_extraction' and not self.state.get('receipts', {}).get('preprocess'):
                raise ValueError('Ekstraksi harus selesai sebelum disetujui')
            if action == 'approve_refine' and ('refinement' not in (self.state.get('error') or '').lower() or not self.numbers()):
                raise ValueError('Terjemahan belum siap untuk refinement')
            self.state['approvals']['refine' if action == 'approve_refine' else 'extraction'] = True
            self.state['status'] = 'QUEUED'
        elif action == 'confirm_resend':
            unresolved = [a for a in self.state['attempts'] if a['status'] in ('PENDING', 'UNKNOWN')]
            if not unresolved:
                raise ValueError('Tidak ada panggilan API yang statusnya belum diketahui')
            for a in unresolved:
                a['resend_confirmed'] = True
            self.state['status'] = 'QUEUED'
            self.event(action, ','.join(a['task'] for a in unresolved))
            return self.state
        elif action == 'decide':
            if not data.get('note', '').strip():
                raise ValueError('Catatan telaah wajib diisi')
            if self.state.get('qa_observed') != self.release_key() or not any(
                    r['bab'] == data['chapter'] and r['status'] == 'REVIEW' for r in self.state.get('qa_rows', [])):
                raise ValueError('Jalankan QA dan telaah peringatan versi aktif terlebih dahulu')
            self.state['decisions'][data['chapter']] = dict(note=data['note'].strip(), release=self.release_key())
            self.state.update(stage='qa', status='QUEUED')
        elif action == 'retry':
            stage, n = data['stage'], int(data.get('chapter', 0))
            if stage not in STAGES:
                raise ValueError('Invalid stage')
            # Explicit retry means a new generation, but never for unresolved remote requests.
            if any(a['status'] in ('PENDING', 'UNKNOWN') for a in self.state['attempts']):
                raise ValueError('Recover unresolved request before retrying')
            if stage == 'refine' and n:
                final = self.base / f'refined/bab_{n:03d}.md'
                if final.exists():
                    snapshot = self.home / 'history' / f'{time.time_ns()}-refine-{n}.md'
                    atomic(snapshot, final.read_bytes())
                    self.state.setdefault('refine_inputs', {})[str(n)] = str(snapshot.relative_to(self.home))
            if stage in ('gloss', 'trans', 'preprocess'):
                self.state.pop('refine_inputs', None)
            # Invalidate downstream receipts, retain old files/history for comparison.
            for key in [k for k in self.state['receipts'] if self.task_matches(k, stage, n)]:
                self.state['receipts'].pop(key, None)
            # Only the retried tasks get a fresh generation: drop their cached responses and stop
            # reusing their old request IDs. Other chapters' paid responses stay reusable.
            for a in self.state['attempts']:
                if self.task_matches(a['task'], stage, n):
                    (self.home / 'checkpoints' / (a['cache_key'] + '.json')).unlink(missing_ok=True)
                    if a['status'] == 'SUCCEEDED':
                        a['superseded'] = True
            self.state.update(stage=stage, status='QUEUED')
            self.invalidate_release()
        elif action == 'save_glossary':
            master = self.base / 'glosarium/master_full.md'
            self.edit(master, data)
            self.state['receipts'] = {k: v for k, v in self.state['receipts'].items() if k.split(':')[0] in ('preprocess', 'gloss')}
            self.state.pop('refine_inputs', None)
            self.state['approvals'].pop('refine', None)
            self.state.update(stage='gloss', status='PAUSED')
            self.invalidate_release()
        elif action == 'save_chapter':
            n = int(data['chapter'])
            if n not in self.numbers():
                raise ValueError('Unknown chapter')
            out = self.base / f'refined/bab_{n:03d}.md'
            if p.EDITORIAL.search(data['text']):
                raise ValueError('Metadata editorial tidak boleh masuk prosa')
            self.edit(out, data)
            self.state['receipts'].pop(f'check:{n}', None)
            metadata = out.with_suffix('.meta.json')
            if metadata.exists() and json.loads(metadata.read_text()).get('gate') == 'GAGAL':
                snapshot = self.home / 'history' / f'{time.time_ns()}-refine-{n}.md'
                atomic(snapshot, out.read_bytes())
                self.state.setdefault('refine_inputs', {})[str(n)] = str(snapshot.relative_to(self.home))
                self.state['receipts'].pop(f'refine:{n}', None)
                self.state.update(stage='refine', status='PAUSED')
            else:
                self.state.update(stage='check', status='PAUSED')
            self.invalidate_release()
        elif action == 'approve_name_alias':
            source, target, reason = (data.get(k, '').strip() for k in ('source', 'target', 'reason'))
            if not source or not target or not reason or len(source) > 200 or len(target) > 200:
                raise ValueError('Istilah sumber, padanan dan alasan telaah wajib diisi')
            aliases = self.state['config'].setdefault('name_aliases', {})
            aliases.setdefault(source, [])
            if target not in aliases[source]:
                aliases[source].append(target)
            self.conf['name_aliases'] = aliases
            self.invalidate_release()
            self.state.update(stage='qa', status='QUEUED')
            self.event('name_alias_approved', json.dumps(dict(source=source, target=target, reason=reason), ensure_ascii=False))
        elif action == 'configure':
            allowed = {'raw_map', 'min_chars', 'check_model', 'max_tokens', 'izinkan_istilah', 'preferensi_editorial', 'paralel_bab'}
            if set(data) - allowed:
                raise ValueError('Unsupported configuration')
            if 'paralel_bab' in data and (type(data['paralel_bab']) is not int or not 1 <= data['paralel_bab'] <= 8):
                raise ValueError('Bab paralel harus 1–8')
            if 'izinkan_istilah' in data and (not isinstance(data['izinkan_istilah'], list) or not all(isinstance(x, str) and x.strip() for x in data['izinkan_istilah'])):
                raise ValueError('Invalid allowed terms')
            changed = {k for k, v in data.items() if self.state['config'].get(k) != v}
            if changed & {'min_chars', 'raw_map'} and (self.base / 'draf').exists():
                raise ValueError('Gunakan novel baru untuk mengubah ekstraksi setelah terjemahan')
            self.reconfigure(data, changed)
        else:
            raise ValueError('Unknown action')
        self.event(action)
        return self.state

    def reconfigure(self, data, changed):
        scopes = [CONFIG_SCOPE[k] for k in changed if CONFIG_SCOPE[k]]
        scope = min(scopes, key=STAGES.index) if scopes else None
        if scope == 'preprocess':
            self.state['config'].update(data)
            self.conf.update(data)
            self.state.update(stage='preprocess', status='PAUSED')
            self.state['receipts'] = {}
            self.state.pop('refine_inputs', None)
            self.state['approvals'] = {}
            self.invalidate_release()
            return
        # Receipts are fingerprinted with the whole config, so re-stamp the ones that were valid
        # and are unaffected; otherwise any setting change silently re-bills every language call.
        valid, old_release = self.valid_receipts(), self.release_key()
        self.state['config'].update(data)
        self.conf.update(data)
        if scope:
            for key in [k for k in self.state['receipts'] if self.task_matches(k, scope, 0)]:
                self.state['receipts'].pop(key)
        for key in valid & set(self.state['receipts']):
            inp = self.receipt_input(key)
            if inp is not None:
                self.state['receipts'][key]['input'] = inp
        if scope:
            self.invalidate_release()
            if STAGES.index(scope) < STAGES.index(self.state['stage']) or self.state['status'] == 'COMPLETED':
                self.state['stage'] = scope
            self.state['status'] = 'PAUSED'
        else:
            # Release unchanged in substance: keep QA report, human decisions and validated EPUB.
            new_release = self.release_key()
            for field in ('qa_report_version', 'qa_observed'):
                if self.state.get(field) == old_release:
                    self.state[field] = new_release
            for decision in self.state['decisions'].values():
                if decision.get('release') == old_release:
                    decision['release'] = new_release

    def edit(self, path, data):
        current = digest(path) if path.exists() else None
        if data.get('expected_hash') != current:
            raise ValueError('Konflik versi; muat ulang dokumen')
        if path.exists():
            atomic(self.home / 'history' / (str(time.time_ns()) + '-' + path.name), path.read_bytes())
        atomic(path, data['text'])

    def invalidate_release(self):
        for key in ('qa', 'build', 'validate'):
            self.state['receipts'].pop(key, None)
        self.state['decisions'] = {}
        self.state.pop('qa_report_version', None)
        self.state.pop('qa_rows', None)
        for marker in self.home.glob('*.validated'):
            marker.unlink()

    def qa_report_rows(self):
        report = self.base / 'qa_report.jsonl'
        rows = {}
        for line in report.read_text().splitlines() if report.exists() else []:
            try:
                row = json.loads(line)
                rows[row['bab']] = row
            except (ValueError, KeyError):
                pass
        return rows

    def review_current(self, reviews, n):
        final = self.base / f'refined/bab_{n:03d}.md'
        review = reviews.get(f'bab_{n:03d}')
        return bool(review and final.exists() and review.get('hash') == digest(final))

    def detail(self, include_content=True):
        result = dict(self.state)
        result['chapters'] = []
        reviews = self.review_rows()
        qa_rows = self.qa_report_rows()
        report_current = self.state.get('qa_report_version') == self.release_key()
        result['qa_current'] = report_current
        for n in self.numbers():
            entry = dict(number=n, review=reviews.get(f'bab_{n:03d}'))
            for field, path in [('source', self.base / f'teks/bab_{n:03d}.txt'), ('raw', self.base / f'teks_raw/bab_{n:03d}.txt'),
                                ('draft', self.base / f'draf/bab_{n:03d}_draft.md'), ('refined', self.base / f'refined/bab_{n:03d}.md')]:
                entry[field] = path.read_text() if include_content and path.exists() else ''
                entry[field + '_hash'] = digest(path) if path.exists() else None
            meta = self.base / f'refined/bab_{n:03d}.meta.json'
            entry['meta'] = json.loads(meta.read_text()) if meta.exists() else None
            entry['qa'] = qa_rows.get(f'bab_{n:03d}')
            entry['qa_current'] = report_current
            # Meaning verdict is only current for the exact final text it reviewed.
            entry['review_current'] = self.review_current(reviews, n)
            result['chapters'].append(entry)
        gl = self.base / 'glosarium/master_full.md'
        result['glossary'] = gl.read_text() if gl.exists() else ''
        result['glossary_hash'] = digest(gl) if gl.exists() else None
        result['global_glossary'] = p.load_global_glossary()
        result['glossary_history'] = [x.name for x in sorted((self.home / 'history').glob('*-master_full.md'), reverse=True)]
        result['reports'] = {name: (self.base / name).read_text() for name in ('qa_report.md', 'preprocess_dropped.md', 'preprocess_dropped_full.md', 'align_report.md') if (self.base / name).exists()}
        result['reports_stale'] = ['qa_report.md'] if 'qa_report.md' in result['reports'] and not report_current else []
        return result

    def history(self, name):
        if not re.fullmatch(r'[0-9]+-master_full\.md', name):
            raise ValueError('Unknown revision')
        return {'text': (self.home / 'history' / name).read_text()}

    def chapter(self, n):
        if n not in self.numbers():
            raise ValueError('Unknown chapter')
        result = {}
        for name, path in [('source', self.base / f'teks/bab_{n:03d}.txt'), ('raw', self.base / f'teks_raw/bab_{n:03d}.txt'),
                           ('draft', self.base / f'draf/bab_{n:03d}_draft.md'), ('refined', self.base / f'refined/bab_{n:03d}.md')]:
            result[name] = path.read_text() if path.exists() else ''
        return result

    def debug_report(self):
        """Safe-to-share troubleshooting snapshot: never includes novel prose, prompts, or secrets."""
        attempts = []
        for a in self.state.get('attempts', []):
            attempts.append({k: a.get(k) for k in ('task', 'model', 'status', 'attempt_no', 'usage', 'cost', 'cost_status', 'started_at', 'finished_at', 'retryable', 'error', 'superseded', 'resent_at') if k in a})
        reviews, qa_rows = self.review_rows(), self.qa_report_rows()
        return {
            'app': 'Novel Translate', 'schema_version': 1,
            'novel': {'id': self.state['id'], 'title': self.state.get('title')},
            'job': {k: self.state.get(k) for k in ('status', 'stage', 'error', 'updated_at')},
            'chapters': [{'number': n, 'draft': bool((self.base / f'draf/bab_{n:03d}_draft.md').exists()),
                          'refined': bool((self.base / f'refined/bab_{n:03d}.md').exists()),
                          'review_gate': reviews.get(f'bab_{n:03d}', {}).get('gate'),
                          'review_current': self.review_current(reviews, n),
                          'qa_status': qa_rows.get(f'bab_{n:03d}', {}).get('status'),
                          'qa_issue_count': len(qa_rows.get(f'bab_{n:03d}', {}).get('issues', []))}
                         for n in self.numbers()],
            'qa_current': self.state.get('qa_report_version') == self.release_key(),
            'tasks': {k: {f: v.get(f) for f in ('stage', 'status', 'completed_at', 'next_retry_at') if f in v} for k, v in self.state.get('tasks', {}).items()}, 'attempts': attempts,
            'events': [{'at': e.get('at'), 'action': e.get('action')} for e in self.state.get('events', [])[-100:]],
            'api_configured': bool(self.transport), 'endpoint': 'redacted'
        }

    def export(self, kind):
        if self.state['status'] != 'COMPLETED' or not self.completed('validate', self.release_key()):
            raise Blocked('Hasil final belum valid atau telah berubah')
        self.qa()
        path = self.home / f'{self.state["id"]}_terjemahan.epub' if kind == 'epub' else self.base / 'editor_notes.md'
        if kind not in ('epub', 'notes'):
            raise ValueError('Unknown export kind')
        self.event('export_requested', digest(path))
        return str(path)
