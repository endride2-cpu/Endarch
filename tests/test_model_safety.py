"""Pengaman untuk model murah: temperature dari config, peringatan model sama, glosarium tak terbaca."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pipeline_unified as pipeline  # noqa: E402
from publication import glossary_names, glossary_onomatope  # noqa: E402

GOOD_GLOSSARY = """**1. KARAKTER**
| Hangeul | Nama_Resmi | Detail |
|---|---|---|
| 민준 | Min-jun | protagonis |
| 서연 | Seo-yeon | adik |

**3. ISTILAH & ONOMATOPE**
| Hangeul | Nama_Resmi | Detail |
|---|---|---|
| 쾅 | Brak | pintu |
"""
BAD_GLOSSARY = "Karakter utama adalah Min-jun dan Seo-yeon.\n\n- Brak = bunyi pintu\n"


class FakeClient:
    """Menangkap argumen yang benar-benar dikirim ke API."""
    def __init__(self):
        self.calls = []
        outer = self

        class Completions:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(kwargs)
                msg = type("M", (), {"content": "ok"})()
                choice = type("C", (), {"message": msg, "finish_reason": "stop"})()
                return type("R", (), {"choices": [choice], "usage": None})()

        self.chat = type("Chat", (), {"completions": Completions})()


def captured_log(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*args)
    return result, buf.getvalue()


class TemperatureTests(unittest.TestCase):
    def test_default_is_unchanged(self):
        client = FakeClient()
        pipeline.chat(client, "m", "prompt", 100)
        self.assertEqual(client.calls[0]["temperature"], 0.3)

    def test_config_value_reaches_the_api(self):
        client = FakeClient()
        pipeline.chat(client, "m", "prompt", 100, temperature=pipeline.temp({"temperature": 0.0}))
        self.assertEqual(client.calls[0]["temperature"], 0.0)

    def test_temp_default_when_missing(self):
        self.assertEqual(pipeline.temp({}), 0.3)

    def test_invalid_temperature_rejected_at_load(self):
        for bad in ("panas", -1, 5):
            with tempfile.TemporaryDirectory() as d:
                (Path(d) / "x.json").write_text(json.dumps({"judul": "X", "temperature": bad}), encoding="utf-8")
                previous, pipeline.NOVELS_DIR = pipeline.NOVELS_DIR, Path(d)
                try:
                    with self.assertRaises(SystemExit, msg=repr(bad)):
                        pipeline.load_conf("x")
                finally:
                    pipeline.NOVELS_DIR = previous


class SameModelWarningTests(unittest.TestCase):
    def test_warns_when_checker_is_same_as_translator(self):
        warned, out = captured_log(pipeline.warn_same_model, {"model": "murah-1", "check_model": None})
        self.assertTrue(warned)
        self.assertIn("check_model", out)
        self.assertTrue(pipeline.warn_same_model({"model": "murah-1", "check_model": "murah-1"}))

    def test_warns_when_checker_equals_fallback_model(self):
        self.assertTrue(pipeline.warn_same_model({"model": "a", "model_fallback": "b", "check_model": "b"}))

    def test_silent_when_checker_differs(self):
        warned, out = captured_log(pipeline.warn_same_model, {"model": "murah-1", "check_model": "kuat-1"})
        self.assertFalse(warned)
        self.assertEqual(out, "")


class GlossaryCheckTests(unittest.TestCase):
    def test_parses_names_and_onomatope(self):
        self.assertEqual(glossary_names(GOOD_GLOSSARY), ["Min-jun", "Seo-yeon"])
        self.assertEqual(glossary_onomatope(GOOD_GLOSSARY), [("쾅", "Brak")])

    def test_unreadable_glossary_yields_no_names(self):
        self.assertEqual(glossary_names(BAD_GLOSSARY), [])

    def _qa_output(self, glossary):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "work_g" / "g"
            for part in ("teks", "draf", "glosarium"):
                (base / part).mkdir(parents=True)
            text = "Dia berjalan pelan menyusuri lorong yang gelap sambil berpikir. " * 4
            (base / "teks/bab_001.txt").write_text(text, encoding="utf-8")
            (base / "draf/bab_001_draft.md").write_text("# 1. Judul\n\n" + text, encoding="utf-8")
            (base / "glosarium/master_full.md").write_text(glossary, encoding="utf-8")
            r = subprocess.run([sys.executable, str(ROOT / "scripts/qa_gate.py"), str(base)],
                               capture_output=True, text=True, check=False)
            return r.returncode, r.stdout, (base / "qa_report.md").read_text(encoding="utf-8")

    def test_qa_warns_but_does_not_fail_on_unreadable_glossary(self):
        code, out, report = self._qa_output(BAD_GLOSSARY)
        self.assertEqual(code, 0)
        self.assertIn("tabel KARAKTER", out)
        self.assertIn("tabel KARAKTER", report)

    def test_qa_quiet_on_good_glossary(self):
        code, out, report = self._qa_output(GOOD_GLOSSARY)
        self.assertEqual(code, 0)
        self.assertNotIn("tabel KARAKTER", out + report)


if __name__ == "__main__":
    unittest.main()
