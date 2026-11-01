import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from publication import ensure_uploadable, final_text_path, parse_refinement
from qa_gate import check_chapter
from pipeline_unified import P, Truncated, chat
import pipeline_unified as pipeline


class PublicationTests(unittest.TestCase):
    def test_refine_contract(self):
        text, meta = parse_refinement("===TEKS===\n# 1. Judul\n\nCerita.\n===GATE===\nLULUS\n===CATATAN===\nUsul baru")
        self.assertEqual(meta, {"gate": "LULUS", "notes": "Usul baru"})
        self.assertNotIn("CATATAN", text)
        for output in ("Cerita\nGate Publikasi: LULUS", "===TEKS===\nCatatan Editor\n===GATE===\nLULUS\n===CATATAN===\n—"):
            with self.assertRaises(ValueError):
                parse_refinement(output)

    def test_final_text_and_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for folder in ("teks", "draf", "refined"):
                (base / folder).mkdir()
            source = "\n\n".join(["A normal sentence of considerable length."] * 20)
            (base / "teks/bab_001.txt").write_text(source, encoding="utf-8")
            (base / "draf/bab_001_draft.md").write_text(source, encoding="utf-8")
            (base / "refined/bab_001.md").write_text(source + " [AMBIGU: term]", encoding="utf-8")
            self.assertEqual(final_text_path(base, 1).parent.name, "refined")
            status, _ = check_chapter(source, (base / "refined/bab_001.md").read_text(encoding="utf-8"),
                                      argparse.Namespace(min_ratio=0.5, max_ratio=2.0, allow_flags=False))
            self.assertEqual(status, "GAGAL")
            gate = Path(__file__).resolve().parent.parent / "scripts/qa_gate.py"
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, str(gate), str(base)], capture_output=True, env=env)
            self.assertEqual(result.returncode, 1, result.stdout.decode(errors="replace"))
            (base / "refined/bab_001.md").write_text(source, encoding="utf-8")
            result = subprocess.run([sys.executable, str(gate), str(base)], capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors="replace"))

    def test_image_position(self):
        parser = P()
        parser.feed("<p>One.</p><p>Two<img src='../Images/sample.png'/>Three</p><p>Four.</p>")
        parser.finalize()
        self.assertEqual(parser.paras, ["One.", "Two", "[IMG:sample.png]", "Three", "Four."])

    def test_truncated_single_call(self):
        class Choice:
            finish_reason = "length"
            message = type("Message", (), {"content": "partial"})()

        class Client:
            count = 0

            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        client.count += 1
                        return type("Result", (), {"choices": [Choice()]})()

        client = Client()
        with self.assertRaises(Truncated):
            chat(client, "model", "prompt", 100)
        self.assertEqual(client.count, 1)

    def test_preview_upload_guard(self):
        with self.assertRaises(ValueError):
            ensure_uploadable(Path("book_PREVIEW.epub"))

    def test_build_validate_and_editorial_isolation(self):
        from PIL import Image
        import zipfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "work_fixture" / "fixture"
            for part in ("teks", "draf", "refined", "images"):
                (base / part).mkdir(parents=True)
            paragraph = "Bulan naik di atas halaman dan semua orang memperhatikan dengan tenang."
            source = "\n\n".join([paragraph] * 15 + ["[IMG:scene.png]"] + [paragraph] * 15)
            draft = "# 1. Moon\n\n" + source
            (base / "teks/bab_001.txt").write_text(source, encoding="utf-8")
            (base / "draf/bab_001_draft.md").write_text(draft, encoding="utf-8")
            (base / "refined/bab_001.md").write_text(draft, encoding="utf-8")
            (base / "refined/bab_001.meta.json").write_text(json.dumps({"gate": "LULUS", "notes": "Editorial note"}), encoding="utf-8")
            (base / ".ch_count").write_text("1")
            Image.new("RGB", (10, 10), "white").save(base / "images/scene.png")
            (base / "qa_llm.jsonl").write_text(json.dumps(
                {"bab": "bab_001", "gate": "LULUS", "masalah": [],
                 "hash": hashlib.sha256((base / "refined/bab_001.md").read_bytes()).hexdigest()}) + "\n", encoding="utf-8")
            conf = {"_base": base, "_slug": "fixture", "judul": "Moon", "adult": False}
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
            self.assertIn('src="../Images/scene.jpg"', xhtml)
            self.assertNotIn("Editorial note", xhtml)
            self.assertNotIn("Catatan Editor", xhtml)
            self.assertTrue(epub.with_suffix(".epub.validated").exists())
            ensure_uploadable(epub)


if __name__ == "__main__":
    unittest.main()
