#!/usr/bin/env python3
"""pipeline_unified.py — Pipeline parametrik TERPUSAT (pengganti pipeline_fast* / batch_* / pipeline_rb2).

Satu script untuk semua novel. Konfigurasi per-novel di `novels/<slug>.json`
(lihat novels/template.json). Semua tahap resume-able dan bisa dijalankan parsial.

Tahap:
  preprocess  ekstrak bab + gambar dari EPUB sumber → <work>/<slug>/teks, images
  gloss        glosarium seed + inkremental (tiap gloss_every bab, entri baru saja)
  trans        terjemah per bab (paralel, resume dari draf/ yang sudah ada)
  refine       (opsional, config refine=true) pass refinement per bab
  qa           qa_gate.py deterministik (blokir kalau ada GAGAL)
  build        build EPUB (font sesuai adult, TOC, STORED)
  validate     validate_epub.py — wajib ✅ sebelum upload
  all          semuanya berurutan

Pemakaian:
  python3 pipeline_unified.py <slug> all
  python3 pipeline_unified.py <slug> trans        # ulang/trans saja (resume otomatis)
  python3 pipeline_unified.py <slug> --novels-dir /path/novels build
  python3 pipeline_unified.py --init <slug> <judul> <doc.epub>   # buat config baru

Env:
  WORK_ROOT   (default <folder paket>/work) — root folder kerja
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
import zipfile as zf
import posixpath
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import config as cfg  # noqa: E402
import book_css  # noqa: E402
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from typo import normalize_typo  # noqa: E402
from publication import (EDITORIAL, FLAGS, RULES, assert_prompt, digest, final_text_path,
                         glossary_names, parse_refinement, resolve_source)  # noqa: E402

WORK_ROOT = Path(os.environ.get("WORK_ROOT", str(ROOT / "work")))
NOVELS_DIR = Path(os.environ.get("NOVELS_DIR", ROOT / "novels"))
PROMPTS = ROOT / "prompts"

DEFAULTS = {
    "judul": None, "doc": None, "sumber": "EN_TRANSLATED", "model": os.environ.get("NOVEL_MODEL"),
    "workers": 2, "adult": False, "gloss_seed_babs": 5, "gloss_every": 0,
    "refine": "ask", "max_tokens": 20000, "api": os.environ.get("NOVEL_PROVIDER", "custom"), "preferensi_editorial": "—",
    # kualitas terjemahan:
    "temperature": 0.3,       # suhu sampling semua panggilan model; 0.0–0.2 = lebih stabil antarjalan
    "min_chars": 300,         # bagian sumber lebih pendek dari ini tidak dijadikan bab (dilaporkan)
    "izinkan_istilah": [],    # frasa yang boleh dipakai walau mengandung kata terlarang (mis. "Bang!", "Mas Ayu")
    "require_review": True,   # build diblokir jika review makna per bab (qa_llm.jsonl) belum ada
    "doc_raw": None,          # path EPUB raw Hangeul → mode BOTH otomatis
    "model_fallback": None,   # model eskalasi utk bab yang gagal (mis. nama-model-cadangan)
    "check_model": None,      # model utk checker QA LLM & style sheet (default = model utama)
}


# ---------- util ----------

def log(msg):
    message = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"), flush=True)


SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def check_slug(slug: str) -> str:
    if not SLUG_RE.match(slug or ""):
        sys.exit(f"❌ slug tidak aman: {slug!r} — pakai huruf kecil/angka/tanda hubung, mis. 'novel-saya'")
    return slug


def resolve_doc(path) -> Path:
    """Path absolut apa adanya; relatif dicari di WORK_ROOT dulu (sesuai template), lalu CWD."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    return next((c for c in (WORK_ROOT / p, ROOT / p, Path.cwd() / p) if c.exists()), WORK_ROOT / p)


def load_conf(slug: str) -> dict:
    check_slug(slug)
    f = NOVELS_DIR / f"{slug}.json"
    if not f.exists():
        sys.exit(f"❌ config tidak ada: {f} — buat dengan --init atau salin template.json")
    conf = dict(DEFAULTS)
    conf.update(json.loads(f.read_text(encoding="utf-8")))
    conf["_slug"] = slug
    conf["_base"] = WORK_ROOT / f"work_{slug}" / slug
    if not conf.get("judul"):
        sys.exit("❌ field 'judul' wajib di config")
    try:
        if not 0.0 <= float(conf.get("temperature", 0.3)) <= 2.0:
            raise ValueError
    except (TypeError, ValueError):
        sys.exit("❌ 'temperature' harus angka 0.0–2.0")
    terms = conf.get("izinkan_istilah")
    if not isinstance(terms, list) or not all(isinstance(t, str) and t.strip() for t in terms):
        sys.exit("❌ 'izinkan_istilah' harus berupa daftar string tidak kosong, mis. [\"Bang!\", \"Mas Ayu\"]")
    return conf


class Truncated(RuntimeError):
    """Output budget exhausted; use a different strategy."""


class EmptyResponse(RuntimeError):
    """Model returned no content; transient, worth retrying."""


def chat(client, model, prompt, max_tokens, tag="", log_dir: Path = None, stream=False, temperature=0.3):
    """Panggil LLM dengan retry. Kalau log_dir diset, tiap panggilan dicatat ke api_log.jsonl
    (model, tahap, token prompt/completion, durasi) untuk rekap biaya & throughput."""
    import json as _json
    stream = stream or str(getattr(client, "base_url", "")).rstrip("/") == cfg.api_base("bai").rstrip("/")
    assert_prompt(prompt)
    t0 = time.time()
    for attempt in range(6):
        try:
            result = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": prompt}],
                temperature=temperature, max_tokens=max_tokens, stream=stream)
            if stream:
                chunks = list(result)
                content = "".join((getattr(chunk.choices[0].delta, "content", None) or "") for chunk in chunks if chunk.choices)
                finish = next((chunk.choices[0].finish_reason for chunk in reversed(chunks) if chunk.choices and chunk.choices[0].finish_reason), None)
                r = type("StreamResult", (), {"choices": [type("Choice", (), {"message": type("Message", (), {"content": content})(), "finish_reason": finish})()], "usage": None})()
            else:
                r = result
            txt = (r.choices[0].message.content or "").strip()
            if getattr(r.choices[0], "finish_reason", None) == "length":
                raise Truncated(tag)
            if txt and getattr(r.choices[0], "finish_reason", None) != "length":
                if log_dir is not None:
                    u = getattr(r, "usage", None)
                    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model,
                           "tag": tag, "prompt_tok": getattr(u, "prompt_tokens", None),
                           "completion_tok": getattr(u, "completion_tokens", None),
                           "retries": attempt, "dur_s": round(time.time() - t0, 2)}
                    try:
                        with open(log_dir / "api_log.jsonl", "a", encoding="utf-8") as fh:
                            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")
                    except OSError:
                        pass
                return txt
            raise EmptyResponse(tag)
        except Exception as e:
            if log_dir is not None:
                log_dir.mkdir(parents=True, exist_ok=True)
                with (log_dir / "api_log.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"tag": tag, "error": type(e).__name__, "attempt": attempt}) + "\n")
            if isinstance(e, (Truncated, ValueError)):
                raise
            status = getattr(e, "status_code", None)
            if status is not None and status < 500 and status != 429:
                raise
            if attempt == 5:
                raise
            log(f"  ⚠ {tag} error {e!r} — retry {attempt+1}/5")
            import random
            time.sleep(2 ** attempt + random.random())


def temp(conf) -> float:
    """Temperature dari config (default 0.3). Turunkan (mis. 0.0–0.2) untuk hasil lebih stabil."""
    return float(conf.get("temperature", 0.3))


def warn_same_model(conf):
    """Penerjemah dan pemeriksa yang sama = pemeriksa mudah meloloskan kesalahan yang sama."""
    check = conf.get("check_model") or conf.get("model")
    if check and check in (conf.get("model"), conf.get("model_fallback")):
        log("⚠ check_model sama dengan model penerjemah — review makna jadi kurang independen. "
            "Pakai model yang lebih kuat dan BERBEDA untuk `check_model`.")
        return True
    return False


def api_log_summary(log_file: Path) -> str:
    """Rekap api_log.jsonl → string ringkas (total panggilan, token, retry, durasi)."""
    import json as _json
    if not log_file.exists():
        return "belum ada api_log.jsonl"
    calls = ptok = ctok = retries = dur = 0
    for line in log_file.read_text(encoding="utf-8").splitlines():
        try:
            r = _json.loads(line)
        except ValueError:
            continue
        calls += 1
        ptok += r.get("prompt_tok") or 0
        ctok += r.get("completion_tok") or 0
        retries += r.get("retries") or 0
        dur += r.get("dur_s") or 0
    return (f"{calls} panggilan · {ptok:,} token masuk · {ctok:,} token keluar · "
            f"{retries} retry · total {dur/60:.1f} menit API")


def make_client(conf):
    from openai import OpenAI
    provider = conf.get("api", "custom")
    key = cfg.load_api_key(provider)
    url = conf.get("api_base") or cfg.api_base(provider)
    if not key or not url or not conf.get("model"):
        raise ValueError("API mode needs a NEW provider key, base URL and model. "
                         "For custom: NOVEL_API_KEY, NOVEL_API_BASE, NOVEL_MODEL. "
                         "Without API access, follow AGENT_START.md using the agent model.")
    return OpenAI(base_url=url, api_key=key, timeout=600)


# ---------- preprocess ----------

BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "section",
              "article", "header", "footer", "aside", "pre", "dd", "dt", "tr", "figcaption", "table"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_TAGS = {"script", "style", "head", "title"}


class P(HTMLParser):
    """Ekstrak paragraf dari XHTML bab.

    Setiap elemen blok (p, div, h1-h6, li, blockquote, ...) menjadi batas paragraf sehingga teks di
    luar <p> tidak hilang. Heading pertama ditulis sebagai `# judul` (dipertahankan prompt sebagai
    header bab); <hr> menjadi pemisah adegan `* * *`.
    """

    def __init__(self, resolve=None):
        """resolve(src) -> nama gambar final (aman, unik); default: nama dasar file."""
        super().__init__(convert_charrefs=True)
        self.paras, self.cur, self.skip, self.imgs = [], [], 0, []
        self.resolve = resolve or safe_image_name
        self.heading_done = False
        self.in_heading = False

    def _flush(self):
        t = "".join(self.cur).strip()
        self.cur = []
        if not t:
            return
        if self.in_heading and not self.heading_done:
            self.heading_done = True
            title = re.sub(r"\s+", " ", IMG_SPLIT.sub(" ", t)).strip()
            if title:
                self.paras.append(f"# {title}")
            self.paras.extend(IMG_SPLIT.findall(t))
            return
        self.paras.extend(x.strip() for x in IMG_SPLIT.split(t) if x and x.strip())

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif self.skip:
            return
        elif tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADINGS:
                self.in_heading = True
        elif tag == "br" and self.cur:
            self.cur.append("\n")
        elif tag == "hr":
            self._flush()
            self.paras.append("* * *")
        elif tag == "img":
            name = self.resolve(dict(attrs).get("src", "") or "")
            if name:
                self.imgs.append(name)
                self.cur.append(f"[IMG:{name}]")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            if self.skip:
                self.skip -= 1
        elif not self.skip and tag in BLOCK_TAGS:
            self._flush()
            if tag in HEADINGS:
                self.in_heading = False

    def handle_data(self, d):
        if not self.skip:
            self.cur.append(d)

    def finalize(self):
        self._flush()


IMG_SPLIT = re.compile(r"(\[IMG:[^\]]+\])")


def safe_image_name(src: str) -> str:
    """Nama dasar file saja: tanpa folder, tanpa '..', tanpa karakter kontrol."""
    name = posixpath.basename(src.replace("\\", "/").split("?")[0].split("#")[0])
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    return "" if name in ("", ".", "..") else name


def image_resolver(z, chapter_paths):
    """Petakan tiap <img src> di bab ke (nama final unik, path di zip).

    Nama final selalu nama dasar yang aman; kalau dua gambar berbeda (folder berbeda) memakai
    nama dasar sama, yang kedua diberi akhiran _2, _3, ... supaya tidak saling menimpa.
    Mengembalikan (resolver_per_bab, {nama_final: path_zip}).
    """
    names = set(z.namelist())
    lower = {n.lower(): n for n in names}
    final, taken = {}, {}      # path_zip -> nama final ; nama final -> path_zip

    def locate(chapter, src):
        raw = src.split("?")[0].split("#")[0]
        cand = posixpath.normpath(posixpath.join(posixpath.dirname(chapter), raw))
        if cand in names:
            return cand
        return lower.get(cand.lower())   # toleransi beda huruf besar/kecil (Images vs images)

    def resolver_for(chapter):
        def resolve(src):
            base = safe_image_name(src)
            if not base:
                return ""
            path = locate(chapter, src)
            if path is None:
                return base            # tidak ditemukan: build akan gagal keras ("Missing image")
            if path not in final:
                stem, ext = posixpath.splitext(base)
                name, k = base, 1
                while name in taken:
                    k += 1
                    name = f"{stem}_{k}{ext}"
                final[path], taken[name] = name, path
            return final[path]
        return resolve

    return resolver_for, taken


def spine_chapters(z):
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = next(el.get("full-path") for el in container.iter() if el.tag.endswith("rootfile"))
    opf = ET.fromstring(z.read(opf_path))
    manifest = {el.get("id"): el.get("href") for el in opf.iter() if el.tag.endswith("}item")}
    names = set(z.namelist())
    paths = []
    for el in opf.iter():
        if el.tag.endswith("}itemref") and el.get("idref") in manifest:
            name = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), manifest[el.get("idref")]))
            if name in names and name.lower().endswith((".xhtml", ".html", ".htm")):
                paths.append(name)
    return paths


def stage_preprocess(conf):
    base = conf["_base"]
    if not conf.get("doc"):
        sys.exit("❌ field 'doc' (EPUB sumber) wajib di config untuk preprocess")
    doc = resolve_doc(conf["doc"])
    if not doc.exists():
        sys.exit(f"❌ file sumber tidak ada: {doc}")
    (base / "teks").mkdir(parents=True, exist_ok=True)
    (base / "images").mkdir(parents=True, exist_ok=True)
    z = zipfile.ZipFile(doc)
    texts = spine_chapters(z)
    min_chars = int(conf.get("min_chars", 300))
    resolver_for, taken = image_resolver(z, texts)
    ch, dropped, extracted = 0, [], []
    dropped_full = []
    for t in texts:
        raw = z.read(t).decode("utf-8", "ignore")
        p = P(resolver_for(t))
        p.feed(raw)
        p.finalize()
        clean = [s for s in p.paras
                 if not re.match(r"^(?:#\s*)?(translated\s+chapter|chapter\s+\d+)$", s, re.I)]
        text = "\n\n".join(clean)
        if len(text) < min_chars:
            if text.strip() or p.imgs:
                dropped.append((t, len(text), p.imgs, text.strip()[:60].replace("\n", " ")))
                dropped_full.append(f"## {t}\n\n{text}")
            continue
        ch += 1
        extracted.append((text, p.imgs))
    old = {int(m.group(1)): f.read_text(encoding="utf-8") for f in (base / "teks").glob("bab_*.txt")
           if (m := re.match(r"bab_(\d+)\.txt$", f.name))}
    new = {i: t for i, (t, _) in enumerate(extracted, 1)}
    has_work = any((base / d).is_dir() and any((base / d).glob("bab_*")) for d in ("draf", "refined"))
    if has_work and old and old != new:
        sys.exit("❌ hasil ekstraksi berbeda dari teks/ yang sudah diterjemahkan — sumber/parser berubah. "
                 "Pakai slug baru agar draf lama tidak salah pasangan (lihat AGENT_START.md langkah 4).")
    for f in list((base / "teks").glob("bab_*.txt")) + list((base / "teks").glob("bab_*.imgs")):
        f.unlink()  # cegah bab basi dari run sebelumnya
    for i, (text, imgs) in enumerate(extracted, 1):
        (base / f"teks/bab_{i:03d}.txt").write_text(text, encoding="utf-8")
        if imgs:
            (base / f"teks/bab_{i:03d}.imgs").write_text("\n".join(imgs), encoding="utf-8")
    images_dir = (base / "images").resolve()
    saved_imgs = set()
    for name, zpath in taken.items():
        dest = (images_dir / name).resolve()
        if dest.parent != images_dir:      # pertahanan berlapis terhadap path traversal
            log(f"⚠ gambar dilewati (path tidak aman): {name!r}")
            continue
        dest.write_bytes(z.read(zpath))
        saved_imgs.add(name)
    for report_name in ("preprocess_dropped.md", "preprocess_dropped_full.md"):
        (base / report_name).unlink(missing_ok=True)
    if dropped:
        (base / "preprocess_dropped_full.md").write_text("\n\n".join(dropped_full), encoding="utf-8")
        rows = ["# Bagian sumber yang TIDAK dijadikan bab", "",
                f"Ambang: {min_chars} karakter (ubah dengan `min_chars` di config).", "",
                "| File sumber | Panjang | Gambar | Awal teks |", "|---|---|---|---|"]
        rows += [f"| {t} | {n} | {', '.join(i) or '-'} | {pv} |" for t, n, i, pv in dropped]
        (base / "preprocess_dropped.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        log(f"⚠ {len(dropped)} bagian pendek (<{min_chars} karakter) tidak dijadikan bab "
            f"→ lihat preprocess_dropped.md; turunkan min_chars bila itu bab sungguhan")
    z.close()
    (base / ".ch_count").write_text(str(ch))
    # sumber raw Hangeul (opsional) → teks_raw/ — aktifkan mode BOTH di trans
    if conf.get("doc_raw"):
        raw_doc = resolve_doc(conf["doc_raw"])
        if raw_doc.exists():
            raw_dir = base / "teks_raw"
            raw_dir.mkdir(exist_ok=True)
            for old in raw_dir.glob("bab_*.txt"):  # cegah raw basi dari run sebelumnya
                old.unlink()
            raw_chapters = []
            with zipfile.ZipFile(raw_doc) as rz:
                for t in spine_chapters(rz):
                    p = P()
                    p.feed(rz.read(t).decode("utf-8", "ignore"))
                    p.finalize()
                    text = "\n\n".join(p.paras)
                    if len(text) >= min_chars:
                        raw_chapters.append(text)
            k = len(raw_chapters)
            raw_map = conf.get("raw_map")
            if k != ch and not raw_map:
                raise ValueError(f"raw/EN chapter count mismatch: {k}/{ch}; provide raw_map")
            mapping = resolve_raw_map(raw_map, ch, k)
            for en_n, raw_ns in mapping.items():
                (raw_dir / f"bab_{en_n:03d}.txt").write_text(
                    "\n\n".join(raw_chapters[r - 1] for r in raw_ns), encoding="utf-8")
            report = base / "align_report.md"
            rows = ["# Laporan Penyelarasan Raw ↔ EN", "",
                    "| Bab | Panjang EN | Panjang raw | Rasio |", "|---|---|---|---|"]
            for i in range(1, max(k, ch) + 1):
                en_f = base / f"teks/bab_{i:03d}.txt"
                raw_f = raw_dir / f"bab_{i:03d}.txt"  # sudah per nomor bab EN
                en_len = len(en_f.read_text(encoding="utf-8")) if en_f.exists() else 0
                raw_len = len(raw_f.read_text(encoding="utf-8")) if raw_f.exists() else 0
                ratio = round(raw_len / en_len, 2) if en_len else 0
                rows.append(f"| {i} | {en_len} | {raw_len} | {ratio} |")
            if conf.get("raw_map"):
                rows += ["", "raw_map dipakai: " + json.dumps(conf["raw_map"], ensure_ascii=False)]
            report.write_text("\n".join(rows) + "\n", encoding="utf-8")
            log(f"raw: {k} bab Hangeul → {raw_dir} (mode BOTH aktif otomatis); laporan {report.name}"
                + (f" ⚠ jumlah beda dgn EN ({ch})" if k != ch else ""))
        else:
            sys.exit(f"❌ doc_raw '{raw_doc}' tidak ada — perbaiki path atau set doc_raw: null")
    log(f"preprocess: {ch} bab, {len(saved_imgs)} gambar → {base}")
    return ch


def resolve_raw_map(raw_map, n_en: int, n_raw: int) -> dict[int, list[int]]:
    """Peta bab EN → daftar bab raw (1-based).

    raw_map (opsional) = {"<bab EN>": <bab raw> | [<bab raw>, ...] | null}. Bab EN yang tidak
    disebut memakai nomor yang sama (kalau ada); null = bab EN itu tanpa raw.
    """
    mapping = {n: [n] for n in range(1, min(n_en, n_raw) + 1)}
    for key, val in (raw_map or {}).items():
        en_n = int(key)
        if not 1 <= en_n <= n_en:
            raise ValueError(f"raw_map: bab EN {en_n} di luar 1..{n_en}")
        if val is None:
            mapping.pop(en_n, None)
            continue
        vals = [int(v) for v in (val if isinstance(val, list) else [val])]
        bad = [v for v in vals if not 1 <= v <= n_raw]
        if bad:
            raise ValueError(f"raw_map: bab raw {bad} di luar 1..{n_raw}")
        mapping[en_n] = vals
    return dict(sorted(mapping.items()))


def ch_count(conf) -> int:
    f = conf["_base"] / ".ch_count"
    return int(f.read_text()) if f.exists() else 0


# ---------- gloss (seed + inkremental) ----------

PROMPT_GLOSS = (PROMPTS / "glossary_generator.md").read_text(encoding="utf-8")
PROMPT_TRANS = (PROMPTS / "translation.md").read_text(encoding="utf-8")
PROMPT_REFINE = (PROMPTS / "refinement.md").read_text(encoding="utf-8")


def stage_gloss(conf, cl, model):
    base = conf["_base"]
    gl_dir = base / "glosarium"
    gl_dir.mkdir(parents=True, exist_ok=True)
    files = sorted((base / "teks").glob("bab_*.txt"))
    if not files:
        sys.exit("❌ belum ada teks/ — jalankan preprocess dulu")
    master = gl_dir / "master_full.md"

    if not master.exists():
        seeds = files[:conf["gloss_seed_babs"]]
        srcs = "\n\n===== BAB =====\n\n".join(
            f"### {f.name}\n" + f.read_text(encoding="utf-8") for f in seeds)
        glob = load_global_glossary()
        p = PROMPT_GLOSS.replace("[TEMPEL TEKS]", srcs).replace(
            "[TEMPEL / KOSONGKAN]",
            ("Glosarium GLOBAL lintas-novel yang SUDAH BAKU (JANGAN ulang entri ini di output, "
             "cukup entri spesifik novel ini):\n" + glob) if glob else "Glosarium awal (kosong): belum ada")
        log(f"gloss seed dari {len(seeds)} bab...")
        g = chat(cl, model, p, 12000, "gloss-seed", log_dir=gl_dir, temperature=temp(conf))
        master.write_text(g, encoding="utf-8")
        log(f"gloss seed: {len(g)} char")

    # inkremental: tiap blok gloss_every bab, minta entri BARU saja
    every = int(conf.get("gloss_every") or 0)
    if every > 0:
        done_marker = gl_dir / ".gloss_upto"
        upto = int(done_marker.read_text()) if done_marker.exists() else conf["gloss_seed_babs"]
        gl = combine_glossaries(master.read_text(encoding="utf-8"))
        for start in range(upto, len(files), every):
            block = files[start:start + every]
            srcs = "\n\n===== BAB =====\n\n".join(
                f"### {f.name}\n" + f.read_text(encoding="utf-8") for f in block)
            p = (PROMPT_GLOSS.replace("[TEMPEL TEKS]", srcs)
                 .replace("[TEMPEL / KOSONGKAN]", "Glosarium yang SUDAH ada (jangan diulang, "
                          "cari entri BARU yang belum ada di sana):\n" + gl)
                 + "\n\nOUTPUT: HANYA tabel entri BARU yang belum ada di glosarium. "
                   "Kalau tidak ada entri baru, tulis hanya: TIDAK ADA ENTRI BARU.")
            log(f"gloss inkremental bab {start+1}-{start+len(block)}...")
            g = chat(cl, model, p, 8000, f"gloss+{start}", log_dir=gl_dir, temperature=temp(conf))
            if "TIDAK ADA ENTRI BARU" not in g.upper()[:200]:
                with open(master, "a", encoding="utf-8") as fh:
                    fh.write(f"\n\n<!-- entri tambahan bab {start+1}-{start+len(block)} -->\n\n" + g)
                gl = master.read_text(encoding="utf-8")
            done_marker.write_text(str(start + len(block)))
    log("📊 API: " + api_log_summary(gl_dir / "api_log.jsonl"))
    master_text = master.read_text(encoding="utf-8")
    if not glossary_names(master_text):
        log("⚠ glosarium tidak punya tabel KARAKTER yang terbaca — cek nama karakter di QA akan NONAKTIF. "
            "Perbaiki format tabel di " + str(master))
    return combine_glossaries(master_text)


# ---------- glosarium global + per-novel ----------

GLOBAL_GLOSSARY_FILE = ROOT / "glossary" / "global.md"


def load_global_glossary() -> str:
    if GLOBAL_GLOSSARY_FILE.exists():
        return GLOBAL_GLOSSARY_FILE.read_text(encoding="utf-8")
    return ""


def combine_glossaries(novel_gl: str, global_gl: str | None = None) -> str:
    """Lapisan: GLOSARIUM GLOBAL (dasar) + GLOSARIUM NOVEL (menimpa entri global)."""
    global_gl = load_global_glossary() if global_gl is None else global_gl
    if not global_gl:
        return novel_gl
    return (global_gl.rstrip()
            + "\n\n---\n\n# GLOSARIUM NOVEL INI — menimpa glosarium global jika berbeda\n\n"
            + novel_gl.strip())


def load_glossary(conf) -> str:
    """Glosarium siap-pakai: global + per-novel (dipakai trans/refine)."""
    f = conf["_base"] / "glosarium" / "master_full.md"
    novel_gl = f.read_text(encoding="utf-8") if f.exists() else ""
    return combine_glossaries(novel_gl)


# ---------- trans ----------

def filter_glossary(gl: str, chapter_text: str) -> str:
    """Glosarium relevan utk bab ini: baris tabel entri yang namanya muncul di bab.
    Bagian honorifik/onomatope/aturan selalu utuh. Fallback: glosarium penuh."""
    low = chapter_text.lower()
    sections, cur_title, cur = [], "", []
    for line in gl.splitlines():
        if re.match(r"^#{1,3}\s", line) or re.match(r"^\*\*\d", line):
            sections.append((cur_title, cur))
            cur_title, cur = line, [line]
        else:
            cur.append(line)
    sections.append((cur_title, cur))
    out = []
    for title, lines in sections:
        t = title.lower()
        if any(k in t for k in ("honorifik", "onomatope", "sumber", "nama", "aturan", "gender", "output")):
            out += lines + [""]
            continue
        kept, header_seen = [], 0
        for ln in lines:
            s = ln.strip()
            if s.startswith("|"):
                if "---" in s.replace(" ", ""):
                    kept.append(ln); continue
                header_seen += 1
                if header_seen <= 1:  # baris header tabel
                    kept.append(ln); continue
                cells = [c.strip() for c in s.strip("|").split("|")]
                terms = []
                for c in cells:  # pecah sel majemuk: "gate / 게이트" → keduanya jadi term
                    if 3 <= len(c) <= 60:
                        terms += [p.strip() for p in c.split("/") if 3 <= len(p.strip()) <= 40]
                is_character = "karakter" in t or "character" in t
                if is_character:
                    # nama utuh, potongan nama (>=3 huruf), dan alias di kolom Notes
                    tokens = {tok.lower() for term in terms
                              for tok in re.split(r"[\s/,()]+", term) if len(tok) >= 3}
                    if any(term.lower() in low for term in terms) or tokens & set(re.findall(r"[\w-]+", low)):
                        kept.append(ln)
                elif any(term.lower() in low for term in terms):
                    kept.append(ln)
            else:
                kept.append(ln)
        if "\n".join(kept).strip():
            out += kept + [""]
    filtered = "\n".join(out).strip()
    return filtered if len(filtered) > len(gl) * 0.15 else gl


def context_for(base: Path, n: int, tail=600, head=300) -> str:
    """Ekor bab sumber sebelumnya + awal bab berikutnya (paralel-safe, dari teks sumber)."""
    parts = []
    prev = base / f"teks/bab_{n-1:03d}.txt"
    nxt = base / f"teks/bab_{n+1:03d}.txt"
    if n > 1 and prev.exists():
        t = prev.read_text(encoding="utf-8").strip()
        if t:
            parts.append(f"[— ekor BAB {n-1} (sumber) —]\n...{t[-tail:]}")
    if nxt.exists():
        t = nxt.read_text(encoding="utf-8").strip()
        if t:
            parts.append(f"[— awal BAB {n+1} (sumber) —]\n{t[:head]}...")
    return "\n\n".join(parts) or "—"


def split_long(text: str, max_chars=3500) -> list[str]:
    """Pecah teks di batas paragraf untuk fallback bab panjang."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    parts, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) + 2 > max_chars:
            parts.append(cur)
            cur = p
        else:
            cur = p if not cur else cur + "\n\n" + p
    if cur:
        parts.append(cur)
    return parts or [text]


def align_chunks(reference: str, parts: list[str]) -> list[str]:
    """Split a parallel text proportionally so every chunk keeps its own raw source."""
    if len(parts) == 1:
        return [reference]
    total = sum(len(p) for p in parts) or 1
    out, start = [], 0
    for part in parts[:-1]:
        end = start + int(len(reference) * len(part) / total)
        out.append(reference[start:end])
        start = end
    out.append(reference[start:])
    return out


def load_style_sheet(conf, cl) -> str:
    """Style sheet voice novel — dibuat SEKALI dari bab sumber 1–3 (bukan draf mesin)."""
    base = conf["_base"]
    sf = base / "style.md"
    if sf.exists() and not conf.get("regenerate_style"):
        return sf.read_text(encoding="utf-8")
    first = sorted((base / "teks").glob("bab_*.txt"))[:3]
    if not first:
        raise ValueError("Source chapters required for initial style sheet")
    sample = "\n\n".join(f.read_text(encoding="utf-8") for f in first)
    p = ("Analisis voice penulis dari teks sumber berikut, lalu buat PANDUAN GAYA Indonesia singkat "
         "(maks 150 kata, poin-poin): diksi khas, panjang kalimat dominan, cara render "
         "dialog (tanda kutip, tag bicara), nada narasi, perlakuan istilah asing/honorifik. "
         "Output hanya panduan, tanpa contoh panjang.\n\n" + sample)
    sheet = chat(cl, conf.get("check_model") or conf["model"], p, 1000, "style", log_dir=base,
                 temperature=temp(conf))
    sf.write_text(sheet, encoding="utf-8")
    (base / "style.meta.json").write_text(json.dumps({"version": 1, "sha256": digest(sf),
        "sources": {f.name: digest(f) for f in first}}, indent=2), encoding="utf-8")
    log(f"style sheet dibuat → {sf}")
    return sheet


KONTEKS_OLD = ("Konteks (ekor bab sebelumnya + awal bab berikutnya — HANYA untuk kontinuitas alur,\n"
               "relasi karakter, dan resolusi subjek implisit. JANGAN diterjemahkan, JANGAN diulang\n"
               "atau diringkas di output, JANGAN dipakai sebagai tambahan isi bab ini):\n[TEMPEL / KOSONGKAN]")
STYLE_OLD = ("Panduan Gaya (voice novel ini — pertahankan konsisten, jangan ditulis ulang di output):\n"
             "[TEMPEL / KOSONGKAN]")
RAW_OLD = ("Teks Sumber Rujukan (Hangeul — hanya untuk mode BOTH, rujukan makna utama):\n"
           "[TEMPEL / KOSONGKAN]")


def allowed_note(conf) -> str:
    """Pengecualian honorifik per novel untuk prompt (kosong bila tidak ada)."""
    terms = conf.get("izinkan_istilah") or []
    return ("\nPengecualian untuk novel ini (BOLEH dipakai apa adanya, bukan honorifik): "
            + ", ".join(f"\"{t}\"" for t in terms)) if terms else ""


def build_trans_prompt(conf, n, text, gl, style, prev_id="", raw_text=None) -> str:
    """Rakit prompt terjemah utk 1 bab/bagian dengan SEMUA slot terisi."""
    base = conf["_base"]
    sumber, _, raw = resolve_source(conf, n)
    if raw_text is not None:
        raw = raw_text
    gl_ch = filter_glossary(gl, text)
    if prev_id:  # bagian lanjutan dari bab yang sama
        konteks = "—"
        style_slot = style or "—"
    else:
        konteks = context_for(base, n)
        style_slot = style or "—"
    return (PROMPT_TRANS
            .replace("Glosarium:\n[TEMPEL]", "Glosarium:\n" + gl_ch)
            .replace("Jenis Sumber (RAW_KOREA/EN_TRANSLATED/BOTH):\n[ISI]",
                     f"Jenis Sumber (RAW_KOREA/EN_TRANSLATED/BOTH):\n{sumber}")
            .replace(RAW_OLD, RAW_OLD.replace("[TEMPEL / KOSONGKAN]", raw or "—"))
            .replace(KONTEKS_OLD, KONTEKS_OLD.replace("[TEMPEL / KOSONGKAN]", konteks))
            .replace(STYLE_OLD, STYLE_OLD.replace("[TEMPEL / KOSONGKAN]", style_slot))
            .replace("Teks:\n[TEMPEL]",
                     ("[Terjemahan bagian sebelumnya dari bab yang sama — hanya konteks, "
                      "jangan diulang:]\n" + prev_id[-600:] + "\n\nTeks:\n" + text) if prev_id
                     else "Teks:\n" + text)
            + ("\nJangan tulis judul pada bagian lanjutan." if prev_id else
               f"\n\nHeader judul bab: tulis `# {n}. <judul Indonesia>`.")
            + "\nHonorifik terlarang: " + ", ".join(RULES["forbidden"]) + allowed_note(conf))


def translate_chapter_text(cl, conf, model, n, src, gl, style) -> str:
    """Terjemah 1 bab: full → eskalasi model → pecah chunk. Selalu maju, tak pernah stuck."""
    base = conf["_base"]
    draf = base / "draf"
    try:
        return chat(cl, model, build_trans_prompt(conf, n, src, gl, style),
                    conf["max_tokens"], f"bab{n}", log_dir=draf, temperature=temp(conf))
    except Exception as e:
        log(f"  ⚠ bab {n} gagal utuh ({e!r}) — strategi fallback")
    fb = conf.get("model_fallback")
    if fb:
        try:
            log(f"  ↗ bab {n}: eskalasi ke model {fb}")
            return chat(cl, fb, build_trans_prompt(conf, n, src, gl, style),
                        conf["max_tokens"], f"bab{n}-fb", log_dir=draf, temperature=temp(conf))
        except Exception as e:
            log(f"  ⚠ bab {n} gagal juga di {fb} ({e!r}) — lanjut chunk")
    parts = split_long(src)
    _, _, raw_all = resolve_source(conf, n)
    raw_limit = int(conf.get("raw_chunk_limit", 20000))
    size = 3500
    while raw_all and len(parts) < 60 and size > 300:
        raw_parts = align_chunks(raw_all, parts)
        if max(len(r) for r in raw_parts) <= raw_limit:
            break
        size = int(size * 0.6)
        parts = split_long(src, size)
    raw_parts = align_chunks(raw_all, parts) if raw_all else [None] * len(parts)
    if raw_all and max(len(r) for r in raw_parts) > raw_limit:
        note = (f"bab {n}: raw chunk {max(len(r) for r in raw_parts)} char > {raw_limit} "
                f"— diproses tanpa pemotongan, cek manual")
        log(f"  ⚠ {note}")
        with (base / "qa_report.md").open("a", encoding="utf-8") as fh:
            fh.write(f"\n- {note}\n")
    outs, prev = [], ""
    for i, part in enumerate(parts):
        p = build_trans_prompt(conf, n, part, gl, style, prev_id=prev if i else "",
                               raw_text=raw_parts[i])
        out = chat(cl, fb or model, p, conf["max_tokens"], f"bab{n}c{i+1}", log_dir=draf,
                   temperature=temp(conf))
        outs.append(re.sub(r"^#.*\n?", "", out, flags=re.M) if i else out)
        prev = out
    return "\n\n".join(outs)


def stage_trans(conf, cl, model, gl):
    base = conf["_base"]
    draf = base / "draf"
    draf.mkdir(exist_ok=True)
    total = ch_count(conf)
    todo = [n for n in range(1, total + 1)
            if not (draf / f"bab_{n:03d}_draft.md").exists()]
    if not todo:
        log("trans: semua bab sudah ada (resume OK)")
        return
    log(f"trans: {len(todo)}/{total} bab tersisa, {conf['workers']} worker")
    style = load_style_sheet(conf, cl)
    t0, lock, stats = time.time(), threading.Lock(), {"n": 0}

    def work(n):
        src = (base / f"teks/bab_{n:03d}.txt").read_text(encoding="utf-8")
        r = translate_chapter_text(cl, conf, model, n, src, gl, style)
        (draf / f"bab_{n:03d}_draft.md").write_text(r, encoding="utf-8")
        with lock:
            stats["n"] += 1
            if stats["n"] % 20 == 0:
                el = (time.time() - t0) / 60
                eta = (len(todo) - stats["n"]) / max(stats["n"] / el, 0.01)
                log(f"  ✅ [{stats['n']}/{len(todo)}] ±{eta:.0f} menit sisa")

    failed = []
    with ThreadPoolExecutor(max_workers=conf["workers"]) as ex:
        futs = {ex.submit(work, n): n for n in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                failed.append(futs[f])
                log(f"  ❌ bab {futs[f]} gagal total: {e!r}")
    log(f"trans selesai ({stats['n']} bab)")
    log("📊 API: " + api_log_summary(draf / "api_log.jsonl"))
    if failed:
        sys.exit(f"❌ {len(failed)} bab gagal diterjemahkan: {sorted(failed)} — jalankan `trans` lagi (resume)")


# ---------- refine ----------

def stage_refine(conf, cl, model, gl):
    if conf.get("refine") in (False, "false"):
        log("refine: dilewati (refine=false)")
        return
    base = conf["_base"]
    out_dir = base / "refined"
    out_dir.mkdir(exist_ok=True)
    total = ch_count(conf)
    todo = [n for n in range(1, total + 1)
            if (base / f"draf/bab_{n:03d}_draft.md").exists()
            and not (out_dir / f"bab_{n:03d}.md").exists()]
    log(f"refine: {len(todo)} bab")

    def work(n):
        kind, src, raw = resolve_source(conf, n)
        draft = (base / f"draf/bab_{n:03d}_draft.md").read_text(encoding="utf-8")
        p = (PROMPT_REFINE
             .replace("GLOSARIUM\n[TEMPEL]", "GLOSARIUM\n" + gl)
              .replace("TEKS SUMBER\n[TEMPEL]", "TEKS SUMBER\n" + src +
                       ("\n\nTEKS RAW HANGEUL (utuh):\n" + raw if raw else ""))
             .replace("JENIS SUMBER (RAW_KOREA/EN_TRANSLATED/BOTH)\n[ISI]",
                       f"JENIS SUMBER (RAW_KOREA/EN_TRANSLATED/BOTH)\n{kind}")
             .replace("DRAF INDONESIA\n[TEMPEL]", "DRAF INDONESIA\n" + draft)
             .replace("KONTEKS (opsional)\n[TEMPEL / KOSONGKAN]",
                       "KONTEKS (opsional)\n" + context_for(base, n))
              .replace("PREFERENSI EDITORIAL (opsional)\n[ISI / KOSONGKAN]",
                       "PREFERENSI EDITORIAL (opsional)\n" + conf.get("preferensi_editorial", "—"))
             + "\n\nHonorifik terlarang (wajib diganti ke padanan Korea): " + ", ".join(RULES["forbidden"])
             + allowed_note(conf))
        try:
            r = chat(cl, model, assert_prompt(p), conf["max_tokens"], f"refine{n}", log_dir=out_dir,
                     temperature=temp(conf))
            text, meta = parse_refinement(r)
            (out_dir / f"bab_{n:03d}.md").write_text(text, encoding="utf-8")
            (out_dir / f"bab_{n:03d}.meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except (ValueError, Truncated) as exc:
            with (base / "qa_llm.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"bab": f"bab_{n:03d}", "gate": "GAGAL",
                                     "masalah": [f"refinement parse/error: {type(exc).__name__}"],
                                     "hash": digest(base / f"draf/bab_{n:03d}_draft.md")}) + "\n")

    failed = []
    with ThreadPoolExecutor(max_workers=conf["workers"]) as ex:
        futs = {ex.submit(work, n): n for n in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as e:
                failed.append(futs[f])
                log(f"  ❌ refine bab {futs[f]} gagal: {e!r}")
    log("refine selesai")
    log("📊 API: " + api_log_summary(out_dir / "api_log.jsonl"))
    if failed:
        sys.exit(f"❌ {len(failed)} bab gagal di-refine: {sorted(failed)} — jalankan `refine` lagi (resume)")


# ---------- check (QA LLM per bab) ----------

CHECK_PROMPT = """Anda auditor QA terjemahan novel. Bandingkan SUMBER dan TERJEMAHAN di bawah.
Periksa HANYA: (1) konten hilang/ditambah (paragraf, dialog, detail fakta), (2) salah makna
material, (3) atribusi pelaku/salahan subjek. Abaikan gaya bahasa — itu urusan editor.

Balas HANYA satu baris JSON valid (tanpa markdown):
{"gate": "LULUS|BERSYARAT|GAGAL", "masalah": ["<deskripsi singkat>", ...]}
GAGAL = ada kesalahan material. BERSYARAT = ragu/ada potensi masalah kecil. LULUS = setara.

=== SUMBER ===
{src}

=== TERJEMAHAN ===
{draft}
"""


def stage_check(conf):
    """QA LLM per bab → qa_llm.jsonl (digabung qa_gate ke laporan akhir)."""
    base = conf["_base"]
    drafts = [final_text_path(base, n) for n in range(1, ch_count(conf) + 1)]
    if not drafts or any(not p.exists() for p in drafts):
        sys.exit("❌ belum ada draf — jalankan trans dulu")
    cl = make_client(conf)
    model = conf.get("check_model") or conf["model"]
    warn_same_model(conf)
    out_f = base / "qa_llm.jsonl"
    done = set()
    if out_f.exists():  # resume
        import json as _json
        for line in out_f.read_text(encoding="utf-8").splitlines():
            try:
                rec = _json.loads(line)
                df = final_text_path(base, int(rec["bab"].split("_")[-1]))
                if df.exists() and rec.get("hash") == digest(df):
                    done.add(rec["bab"])
            except Exception:
                pass
    log(f"check: {len([d for d in drafts if d.stem[:7] not in done])}/{len(drafts)} bab (model {model})")

    import json as _json

    def work(df):
        n = int(re.search(r"bab_(\d+)", df.stem).group(1))
        if f"bab_{n:03d}" in done:
            return
        src_f = base / f"teks/bab_{n:03d}.txt"
        src = src_f.read_text(encoding="utf-8") if src_f.exists() else ""
        raw_f = base / f"teks_raw/bab_{n:03d}.txt"
        if raw_f.exists():  # raw lebih otoritatif utk cek makna
            src = raw_f.read_text(encoding="utf-8")
        draft_text = df.read_text(encoding="utf-8")
        source_chunks = split_long(src, 3500)
        draft_chunks = split_long(draft_text, max(3500, len(draft_text) // len(source_chunks) + 1))
        if len(source_chunks) != len(draft_chunks):
            source_chunks = [src[i:i + 3500] for i in range(0, len(src), 3500)]
            draft_chunks = [draft_text[i * len(draft_text) // len(source_chunks):
                                      (i + 1) * len(draft_text) // len(source_chunks)]
                            for i in range(len(source_chunks))]
        verdicts, problems = [], []
        for j, (ss, dd) in enumerate(zip(source_chunks, draft_chunks)):
            try:
                out = chat(cl, model, CHECK_PROMPT.replace("{src}", ss).replace("{draft}", dd),
                           1500, f"check{n}-{j}", log_dir=base, temperature=temp(conf))
                data = _json.loads(re.search(r"\{.*\}", out, re.S).group())
                if data.get("gate") not in ("LULUS", "BERSYARAT", "GAGAL"):
                    raise ValueError("invalid gate")
                verdicts.append(data["gate"])
                problems += data.get("masalah", [])
            except Exception as exc:
                verdicts.append("GAGAL")
                problems.append(f"checker-error segment {j + 1}: {type(exc).__name__}")
        rec = {"bab": f"bab_{n:03d}", "gate": next(g for g in ("GAGAL", "BERSYARAT", "LULUS") if g in verdicts),
               "masalah": problems, "hash": digest(df)}
        with open(out_f, "a", encoding="utf-8") as fh:
            fh.write(_json.dumps(rec, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(max_workers=conf["workers"]) as ex:
        futs = [ex.submit(work, df) for df in drafts]
        for f in as_completed(futs):
            f.result()
    n_ok = n_syarat = n_gagal = 0
    for line in out_f.read_text(encoding="utf-8").splitlines():
        try:
            g = _json.loads(line)["gate"]
        except Exception:
            continue
        n_ok += g == "LULUS"; n_syarat += g == "BERSYARAT"; n_gagal += g == "GAGAL"
    log(f"check selesai — LULUS {n_ok} · BERSYARAT {n_syarat} · GAGAL {n_gagal} (lihat qa_llm.jsonl)")


# ---------- qa ----------

def stage_qa(conf):
    cmd = [sys.executable, str(HERE / "qa_gate.py"), str(conf["_base"]), "--kind", conf.get("sumber", "EN_TRANSLATED")]
    if conf.get("allow_flags"):
        cmd.append("--allow-flags")
    if conf.get("require_review", True):
        cmd.append("--require-review")
    if conf.get("name_aliases"):
        cmd += ["--name-aliases", json.dumps(conf["name_aliases"], ensure_ascii=False)]
    for term in conf.get("izinkan_istilah") or []:
        cmd += ["--allow-term", term]
    if conf.get("min_ratio") is not None:
        cmd += ["--min-ratio", str(conf["min_ratio"])]
    if conf.get("max_ratio") is not None:
        cmd += ["--max-ratio", str(conf["max_ratio"])]
    from qa_gate import run as run_qa
    if run_qa(cmd[2:]) != 0:
        sys.exit("❌ qa_gate GAGAL — perbaiki bab bersangkutan sebelum build (lihat qa_report.md)")


# ---------- build ----------

def norm_img(raw, max_dim=900, q=78):
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw))
        img.load()
        if max(img.width, img.height) > max_dim:
            s = max_dim / max(img.width, img.height)
            img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A") if img.mode == "RGBA" else img.getchannel("L"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=q, optimize=True)
        return out.getvalue()
    except Exception:
        return None


def is_dialog(p: str) -> bool:
    """Paragraf dialog → indent lebih kecil (class .dlg)."""
    return bool(re.match(r'^[「"\'\u201C\u2018]', p.strip()))


def stage_build(conf):
    import html as htmlmod
    import shutil
    base = conf["_base"]
    stage_qa(conf)
    BUILD = base.parent / f"{conf['_slug']}_build"
    suffix = "_PREVIEW" if conf.get("allow_flags") else ""
    OUT = Path(conf.get("_output_root", WORK_ROOT)) / f"{conf['_slug']}_terjemahan{suffix}.epub"
    if BUILD.exists():
        shutil.rmtree(BUILD)
    for d in ("META-INF", "OEBPS/Text", "OEBPS/Images", "OEBPS/Styles", "OEBPS/fonts"):
        (BUILD / d).mkdir(parents=True, exist_ok=True)
    # CSS bersama + embed font (Literata non-adult / Source Serif 4 adult)
    font_key = "source-serif" if conf.get("adult") else "literata"
    (BUILD / "OEBPS/Styles/book.css").write_text(book_css.build_css(font_key), encoding="utf-8")
    font_items = []
    for fp in book_css.font_files(font_key):
        (BUILD / "OEBPS/fonts" / fp.name).write_bytes(fp.read_bytes())
        font_items.append(fp.name)

    img_map = {}
    for f in sorted((base / "images").glob("*")):
        norm = norm_img(f.read_bytes())
        if norm:
            jpg = re.sub(r"\.(webp|png|jpe?g|gif|bmp|tiff?)$", "", f.name, flags=re.I) + ".jpg"
            if any(j == jpg for j, _ in img_map.values()):  # a.png + a.webp → tabrakan nama
                jpg = re.sub(r"\.", "_", f.name) + ".jpg"
            img_map[f.name] = (jpg, norm)
    for jpg, data in img_map.values():
        (BUILD / "OEBPS/Images" / jpg).write_bytes(data)

    chapters = []
    for i in range(1, ch_count(conf) + 1):
        pth = final_text_path(base, i)
        if not pth.exists():
            raise ValueError(f"Missing chapter {i}")
        text = pth.read_text(encoding="utf-8")
        if EDITORIAL.search(text) or (FLAGS.search(text) and not conf.get("allow_flags")):
            raise ValueError(f"Unpublishable chapter {i}")
        meta = base / "refined" / f"bab_{i:03d}.meta.json"
        if pth.parent.name == "refined" and meta.exists() and json.loads(meta.read_text(encoding="utf-8")).get("gate") == "GAGAL":
            raise ValueError(f"Refinement gate failed: {i}")
        header = ""
        for line in text.split("\n")[:5]:
            if line.startswith("#"):
                header = re.sub(r"^(Bab\s+\d+|\d+)[\s:.–—]*", "", line.lstrip("#").strip(),
                                flags=re.I).strip()
                break
        paras = []
        for p in re.split(r"\n\s*\n", text):
            p = p.strip()
            if not p or p.startswith("#"):
                continue
            # token gambar yang menyatu dengan teks → pecah supaya gambar dirender, bukan dicetak mentah
            paras.extend(x.strip() for x in re.split(r"(\[IMG:[^\]]+\])", p) if x.strip())
        imgf = base / f"teks/bab_{i:03d}.imgs"
        if imgf.exists() and "[IMG:" not in text:
            log(f"WARNING: legacy image list for chapter {i}; appending images, original positions unavailable")
            img_names = [l.strip() for l in imgf.read_text(encoding="utf-8").splitlines() if l.strip()]
            paras.extend(f"[IMG:{name}]" for name in img_names)
        chapters.append((i, header, "\n\n".join(paras)))

    toc, total_w = [], 0
    have_imgs = {j for j, _ in img_map.values()}
    for idx, (i, h, text) in enumerate(chapters):
        body = []
        for p in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
            m = re.match(r"^\[IMG:([^\]]+)\]$", p)
            if m:
                image = img_map.get(m.group(1), (m.group(1), None))[0]
                if image in have_imgs:
                    body.append(f'<p class="illust"><img src="../Images/'
                                f'{htmlmod.escape(image)}" alt=""/></p>')
                else:
                    raise ValueError(f"Missing image: {m.group(1)}")
                continue
            elif re.match(r"^[\s*＊–\-~～·•・.…✦✧◆◇☆★✨]+$", p):
                body.append('<p class="scene">* * *</p>')
            elif p.startswith("[") and p.endswith("]"):
                body.append(f'<p class="bracket">{htmlmod.escape(p[1:-1])}</p>')
            else:
                t = htmlmod.escape(normalize_typo(p))
                t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
                t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", t)
                cls = ' class="dlg"' if is_dialog(p) else ""
                body.append(f"<p{cls}>{t}</p>")
        body_html = "\n".join(body)
        label = f"Bab {i}: {h}" if h else f"Bab {i}"
        total_w += len(body_html.split())
        fname = f"chapter_{idx+1:04d}.xhtml"
        (BUILD / "OEBPS/Text" / fname).write_text(
            f'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
            f'xml:lang="id" lang="id">'
            f'<head><title>{htmlmod.escape(h or f"Bab {i}")}</title>\n'
            f'<link rel="stylesheet" type="text/css" href="../Styles/book.css"/></head><body>\n'
            f'<h1>{htmlmod.escape(label)}</h1>\n{body_html}\n</body></html>',
            encoding="utf-8")
        toc.append((fname, label))

    judul = conf["judul"]
    (BUILD / "OEBPS/Text/title.xhtml").write_text(
        f'<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml" '
        f'xml:lang="id" lang="id">'
        f'<head><title>Halaman Judul</title><link rel="stylesheet" type="text/css" '
        f'href="../Styles/book.css"/></head><body>\n<div style="text-align:center;margin:9vh 7% 0;">'
        f'<h1>{htmlmod.escape(judul)}</h1><p>Edisi Lengkap</p>'
        f'<p>Bab: {len(chapters)} · Kata: {total_w:,}</p></div>\n</body></html>', encoding="utf-8")
    ncx = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">',
           f'  <head><meta name="dtb:uid" content="{htmlmod.escape(conf["_slug"])}-id"/><meta name="dtb:depth" content="1"/></head>',
           f'  <docTitle><text>{htmlmod.escape(judul)}</text></docTitle>', "  <navMap>"]
    for i, (fname, h) in enumerate(toc):
        ncx.append(f'    <navPoint id="nav{i+1}" playOrder="{i+1}"><navLabel><text>'
                   f'{htmlmod.escape(h)}</text></navLabel><content src="Text/{fname}"/></navPoint>')
    ncx += ["  </navMap>", "</ncx>"]
    (BUILD / "OEBPS/toc.ncx").write_text("\n".join(ncx), encoding="utf-8")
    manifest = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="css" href="Styles/book.css" media-type="text/css"/>',
                '<item id="title-x" href="Text/title.xhtml" media-type="application/xhtml+xml"/>']
    spine = ["title-x"]
    for i, (fname, h) in enumerate(toc):
        manifest.append(f'<item id="ch-{i+1:04d}" href="Text/{fname}" media-type="application/xhtml+xml"/>')
        spine.append(f"ch-{i+1:04d}")
    for k, fname in enumerate(img_map):
        j = img_map[fname][0]
        manifest.append(f'<item id="img-{k:04d}" href="Images/{htmlmod.escape(j)}" '
                        f'media-type="image/jpeg"/>')
    for k, fname in enumerate(font_items):  # font tersemat
        manifest.append(f'<item id="font-{k}" href="fonts/{fname}" '
                        f'media-type="font/woff2"/>')
    opf = (f'<?xml version="1.0" encoding="UTF-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" '
           f'version="2.0" unique-identifier="uid" xmlns:dc="http://purl.org/dc/elements/1.1/" '
           f'xmlns:opf="http://www.idpf.org/2007/opf">\n  <metadata><dc:identifier id="uid" '
           f'opf:scheme="URI">{htmlmod.escape(conf["_slug"])}-id</dc:identifier><dc:title>{htmlmod.escape(judul)}'
           f'</dc:title><dc:language>id</dc:language></metadata>\n  <manifest>'
           + "\n".join("    " + m for m in manifest) + "</manifest>\n  <spine toc=\"ncx\">"
           + "\n".join(f'    <itemref idref="{s}"/>' for s in spine) + "</spine>\n</package>")
    (BUILD / "OEBPS/content.opf").write_text(opf, encoding="utf-8")
    (BUILD / "META-INF/container.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n  <rootfiles><rootfile '
        'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
        "\n</container>\n", encoding="utf-8")
    (BUILD / "mimetype").write_text("application/epub+zip", encoding="utf-8")
    with zf.ZipFile(OUT, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zf.ZIP_STORED)
        for f in sorted(BUILD.rglob("*")):
            if f.is_file() and f.name != "mimetype":
                z.writestr(str(f.relative_to(BUILD)).replace("\\", "/"), f.read_bytes())
    log(f"build: {len(chapters)} bab → {OUT}")


def stage_validate(conf):
    suffix = "_PREVIEW" if conf.get("allow_flags") else ""
    out = WORK_ROOT / f"{conf['_slug']}_terjemahan{suffix}.epub"
    r = subprocess.run([sys.executable, str(HERE / "validate_epub.py"), str(out)])
    if r.returncode != 0:
        sys.exit(f"❌ EPUB tidak valid — JANGAN upload. Perbaiki dulu: {out}")


# ---------- main ----------

STAGES = ["preprocess", "gloss", "trans", "refine", "check", "qa", "build", "validate"]


def run_stage(name, conf):
    if name == "preprocess":
        stage_preprocess(conf)
    elif name == "gloss":
        cl, m = make_client(conf), conf["model"]
        conf["_gloss"] = stage_gloss(conf, cl, m)
    elif name == "trans":
        gl = conf.get("_gloss") or load_glossary(conf)
        stage_trans(conf, make_client(conf), conf["model"], gl)
    elif name == "refine":
        if conf.get("refine") in (False, "false"):
            log("refine: dilewati (refine=false)")
            return
        gl = conf.get("_gloss") or load_glossary(conf)
        stage_refine(conf, make_client(conf), conf["model"], gl)
    elif name == "check":
        stage_check(conf)
    elif name == "qa":
        stage_qa(conf)
    elif name == "build":
        stage_build(conf)
    elif name == "validate":
        stage_validate(conf)


def init_config(slug, judul, doc):
    check_slug(slug)
    if not Path(doc).expanduser().exists():
        sys.exit(f"❌ file sumber tidak ada: {doc}")
    NOVELS_DIR.mkdir(parents=True, exist_ok=True)
    f = NOVELS_DIR / f"{slug}.json"
    if f.exists():
        sys.exit(f"❌ sudah ada: {f}")
    conf = dict(DEFAULTS)
    conf.update({"judul": judul, "doc": str(Path(doc).expanduser().resolve())})
    f.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ config dibuat: {f} — sesuaikan sumber/doc_raw/adult, lalu jalankan "
          f"`pipeline_unified.py {slug} preprocess` (lihat AGENT_START.md; `all` hanya dengan API baru)")


def main():
    import argparse
    global NOVELS_DIR
    parser = argparse.ArgumentParser(description="Unified novel translation pipeline")
    parser.add_argument("--init", nargs=3, metavar=("SLUG", "TITLE", "EPUB"))
    parser.add_argument("--novels-dir", type=Path, default=NOVELS_DIR)
    parser.add_argument("--allow-flags", action="store_true", help="create internal PREVIEW EPUB only")
    parser.add_argument("--regenerate-style", action="store_true")
    parser.add_argument("slug", nargs="?")
    parser.add_argument("stage", nargs="?", choices=STAGES + ["all"])
    args = parser.parse_args()
    NOVELS_DIR = args.novels_dir
    if args.init:
        init_config(*args.init)
        return
    if not args.slug or not args.stage:
        parser.error("slug and stage are required unless --init is used")
    conf = load_conf(args.slug)
    conf["allow_flags"] = args.allow_flags
    conf["regenerate_style"] = args.regenerate_style or bool(conf.get("regenerate_style"))
    if args.stage == "all":
        warn_same_model(conf)
        for s in STAGES:
            if s == "refine" and conf.get("refine") == "ask":
                log("Refinement requires approval; stopping after translation")
                return
            log(f"▶ {s}")
            run_stage(s, conf)
    else:
        run_stage(args.stage, conf)


if __name__ == "__main__":
    main()
