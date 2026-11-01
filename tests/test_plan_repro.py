"""Regression for PLAN.md §5: flags, gate metadata, and typography must not reach the EPUB."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pipeline_unified as pipeline
from publication import digest, ensure_uploadable
from typo import normalize_typo

PLAN_DRAFT = """# 1. Gerbang Pertama

"Kenapa?" tanya Min-jun. "Hyung, tunggu...?"

"Tidak!!" balas pria itu. [AMBIGU: hyung/oppa?]

Langit menggelap... lalu sunyi.

**Gate Publikasi: BERSYARAT**

**Catatan Editor:**
- Flag AMBIGU di paragraf 2 belum terpecahkan; usul onomatope baru: "Brak".
"""

CLEAN_DRAFT = """# 1. Gerbang Pertama

"Kenapa?" tanya Min-jun. "Hyung, tunggu...?"

"Tidak!!" balas pria itu.

Langit menggelap... lalu sunyi.
"""


def make_fixture(root: Path, draft: str) -> Path:
    base = root / "work_fixture" / "fixture"
    for part in ("teks", "draf", "refined", "images"):
        (base / part).mkdir(parents=True, exist_ok=True)
    (base / "teks/bab_001.txt").write_text(draft, encoding="utf-8")
    (base / "draf/bab_001_draft.md").write_text(draft, encoding="utf-8")
    (base / "refined/bab_001.md").write_text(draft, encoding="utf-8")
    (base / "refined/bab_001.meta.json").write_text(
        json.dumps({"gate": "BERSYARAT", "notes": "usul onomatope"}), encoding="utf-8")
    (base / ".ch_count").write_text("1")
    return base


def write_review(base: Path, gate: str = "LULUS") -> None:
    """Review makna untuk teks final bab 1 (hash terikat ke file final)."""
    final = base / "refined/bab_001.md"
    (base / "qa_llm.jsonl").write_text(json.dumps(
        {"bab": "bab_001", "gate": gate, "masalah": [], "hash": digest(final)}) + "\n", encoding="utf-8")


class PlanReproTests(unittest.TestCase):
    def test_typo_keeps_dialogue_and_double_punctuation(self):
        out = normalize_typo(PLAN_DRAFT)
        self.assertNotIn('? "', out)
        self.assertNotIn("! !", out)
        self.assertIn('"Tidak!!"', out)
        self.assertIn("…", out)
        self.assertNotIn("...", out)
        self.assertIn("kata—kata", normalize_typo("kata — kata"))

    def test_flag_and_metadata_block_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = make_fixture(root, PLAN_DRAFT)
            gate = ROOT / "scripts/qa_gate.py"
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, str(gate), str(base)],
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 1, result.stdout.decode(errors="replace"))

            conf = {"_base": base, "_slug": "fixture", "judul": "Gerbang", "adult": False}
            previous = pipeline.WORK_ROOT
            pipeline.WORK_ROOT = root
            try:
                with self.assertRaises(SystemExit):
                    pipeline.stage_build(conf)
            finally:
                pipeline.WORK_ROOT = previous
            self.assertFalse((root / "fixture_terjemahan.epub").exists())

    def test_clean_chapter_builds_publishable_epub(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = make_fixture(root, CLEAN_DRAFT)
            (base / "refined/bab_001.meta.json").write_text(
                json.dumps({"gate": "LULUS", "notes": "—"}), encoding="utf-8")
            write_review(base)
            conf = {"_base": base, "_slug": "fixture", "judul": "Gerbang", "adult": False}
            previous = pipeline.WORK_ROOT
            pipeline.WORK_ROOT = root
            try:
                pipeline.stage_build(conf)
                pipeline.stage_validate(conf)
            finally:
                pipeline.WORK_ROOT = previous
            epub = root / "fixture_terjemahan.epub"
            with zipfile.ZipFile(epub) as archive:
                xhtml = archive.read("OEBPS/Text/chapter_0001.xhtml").decode()
            self.assertNotIn("Gate Publikasi", xhtml)
            self.assertNotIn("Catatan Editor", xhtml)
            self.assertNotIn("AMBIGU", xhtml)
            self.assertIn('&quot;Kenapa?&quot;', xhtml)
            self.assertIn('&quot;Tidak!!&quot;', xhtml)
            ensure_uploadable(epub)


if __name__ == "__main__":
    unittest.main()
