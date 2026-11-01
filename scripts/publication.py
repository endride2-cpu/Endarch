"""Shared publication contracts; no network dependencies."""
import hashlib
import json
import re
from pathlib import Path

RULES = json.loads((Path(__file__).resolve().parent.parent / "config/rules.json").read_text(encoding="utf-8"))
FLAGS = re.compile(r"\[(?:" + "|".join(map(re.escape, RULES["flags"])) + r")[^\]]*\]")
EDITORIAL = re.compile(r"Gate Publikasi|Catatan Editor|===(?:TEKS|GATE|CATATAN)===", re.I)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def final_text_path(base, n, source="auto"):
    base = Path(base)
    refined = [base / f"refined/bab_{n:03d}.md", base / f"refined/bab_{n:03d}_draft.md"]
    draft = base / f"draf/bab_{n:03d}_draft.md"
    candidates = refined + [draft] if source == "auto" else refined if source == "refined" else [draft]
    return next((p for p in candidates if p.exists()), candidates[0])


def chapter_numbers(base):
    return sorted({int(m.group(1)) for folder in ("teks", "draf", "refined")
                   for p in (Path(base) / folder).glob("bab_*.*")
                   if (m := re.match(r"bab_(\d+)", p.name))})


def parse_refinement(text):
    match = re.fullmatch(r"\s*===TEKS===\s*\n(.*?)\n===GATE===\s*\n(LULUS|BERSYARAT|GAGAL)\s*\n===CATATAN===\s*\n?(.*?)\s*", text, re.S)
    if not match or not match[1].strip():
        raise ValueError("Invalid refinement block format")
    if EDITORIAL.search(match[1]):
        raise ValueError("Editorial metadata inside chapter text")
    return match[1].strip(), {"gate": match[2], "notes": match[3].strip()}


def assert_prompt(prompt):
    if re.search(r"\[(?:TEMPEL|ISI)\b", prompt):
        raise ValueError("Unresolved prompt placeholder")
    return prompt


def resolve_source(conf, n):
    base = conf["_base"]
    en = (base / f"teks/bab_{n:03d}.txt").read_text(encoding="utf-8")
    raw_path = base / f"teks_raw/bab_{n:03d}.txt"
    raw = raw_path.read_text(encoding="utf-8") if raw_path.exists() else ""
    return ("BOTH" if raw else conf["sumber"], en, raw)


def ensure_uploadable(path):
    path = Path(path)
    if "_PREVIEW" in path.stem.upper():
        raise ValueError("Preview uploads are forbidden")
    if path.suffix.lower() == ".epub":
        marker = path.with_suffix(path.suffix + ".validated")
        if not marker.exists() or marker.read_text().strip() != digest(path):
            raise ValueError("EPUB needs current hash-bound validation")


def _table_rows(glossary, section):
    """Baris tabel (sel-sel) di bagian glosarium yang judulnya memuat `section`."""
    active = False
    for line in glossary.splitlines():
        if re.match(r"^#{1,3}\s", line) or re.match(r"^\*\*\d", line):
            active = section in line.lower()
            continue
        s = line.strip()
        if active and s.startswith("|") and "---" not in s.replace(" ", ""):
            yield [c.strip() for c in s.strip("|").split("|")]


def glossary_names(glossary):
    """Nama resmi karakter (kolom ke-2 tabel KARAKTER); kosong = tabel tidak terbaca."""
    return [c[1] for c in _table_rows(glossary, "karakter")
            if len(c) >= 2 and 3 <= len(c[1]) <= 40 and not c[1].lower().startswith("nama")]


def glossary_onomatope(glossary):
    """Pasangan (bunyi Hangeul, padanan Indonesia) dari tabel ISTILAH & ONOMATOPE."""
    return [(a, b) for cells in _table_rows(glossary, "onomatope") for a, b in zip(cells, cells[1:])
            if re.search(r"[\uac00-\ud7a3]", a) and 1 <= len(b) <= 25 and re.match(r"^[A-Za-z]", b)]
