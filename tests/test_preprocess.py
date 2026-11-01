"""Regresi hasil audit: EPUB sumber nyata (subfolder gambar, <div>, zip-slip, bab pendek) dan gate review."""
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pipeline_unified as pipeline  # noqa: E402
from publication import digest  # noqa: E402

ID = "Dia berjalan pelan menyusuri lorong yang gelap sambil memikirkan semua hal kemarin. "


def png(color):
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(buf, "PNG")
    return buf.getvalue()


def xhtml(body):
    return f'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>{body}</body></html>'


def make_epub(path: Path, chapters: dict, files: dict):
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0"><manifest>'
           + "".join(f'<item id="i{n}" href="Text/{k}" media-type="application/xhtml+xml"/>'
                     for n, k in enumerate(chapters))
           + "</manifest><spine>" + "".join(f'<itemref idref="i{n}"/>' for n in range(len(chapters)))
           + "</spine></package>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        for name, body in chapters.items():
            z.writestr(f"OEBPS/Text/{name}", body)
        for name, data in files.items():
            z.writestr(name, data)


class Workspace:
    def __init__(self, root: Path, epub: Path, **extra):
        self.root = root
        self.conf = dict(pipeline.DEFAULTS, judul="Uji", doc=str(epub), _slug="uji",
                         _base=root / "work_uji" / "uji", **extra)
        self.base = self.conf["_base"]

    def preprocess(self):
        return pipeline.stage_preprocess(self.conf)


class PreprocessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_image_in_subfolder_does_not_crash(self):
        epub = self.tmp / "a.epub"
        make_epub(epub, {"c1.xhtml": xhtml('<p>' + ID * 5 + '</p><p><img src="../Images/x/pic.png"/></p>')},
                  {"OEBPS/Images/x/pic.png": png("green")})
        ws = Workspace(self.tmp, epub)
        self.assertEqual(ws.preprocess(), 1)
        self.assertIn("[IMG:pic.png]", (ws.base / "teks/bab_001.txt").read_text(encoding="utf-8"))
        self.assertTrue((ws.base / "images/pic.png").exists())

    def test_zip_slip_cannot_write_outside_images(self):
        epub = self.tmp / "jahat.epub"
        make_epub(epub, {"c1.xhtml": xhtml('<p>' + ID * 5 + '</p><p><img src="../Images/../../../../DILUAR.txt"/></p>')},
                  {"OEBPS/Images/../../../../DILUAR.txt": b"ditulis EPUB"})
        ws = Workspace(self.tmp / "dalam" / "ws", epub)
        ws.preprocess()
        self.assertEqual(list(self.tmp.rglob("DILUAR.txt")) and
                         [p for p in self.tmp.rglob("DILUAR.txt") if p.parent.name != "images"], [])

    def test_image_inside_div_keeps_position(self):
        epub = self.tmp / "b.epub"
        make_epub(epub, {"c1.xhtml": xhtml('<div class="illus"><img src="../Images/a.png"/></div><p>' + ID * 5 + '</p>')},
                  {"OEBPS/Images/a.png": png("red")})
        ws = Workspace(self.tmp, epub)
        ws.preprocess()
        parts = (ws.base / "teks/bab_001.txt").read_text(encoding="utf-8").split("\n\n")
        self.assertEqual(parts[0], "[IMG:a.png]")

    def test_same_basename_in_two_folders_not_overwritten(self):
        epub = self.tmp / "c.epub"
        make_epub(epub, {"c1.xhtml": xhtml('<p>' + ID * 5 + '</p><p><img src="../Images/x/p.png"/></p>'
                                           '<p><img src="../Images/y/p.png"/></p>')},
                  {"OEBPS/Images/x/p.png": png("green"), "OEBPS/Images/y/p.png": png("blue")})
        ws = Workspace(self.tmp, epub)
        ws.preprocess()
        text = (ws.base / "teks/bab_001.txt").read_text(encoding="utf-8")
        self.assertIn("[IMG:p.png]", text)
        self.assertIn("[IMG:p_2.png]", text)
        colors = {Image.open(ws.base / f"images/{n}").getpixel((1, 1)) for n in ("p.png", "p_2.png")}
        self.assertEqual(len(colors), 2)

    def test_short_section_is_reported_not_silent(self):
        epub = self.tmp / "d.epub"
        make_epub(epub, {"c1.xhtml": xhtml("<p>" + ID * 5 + "</p>"),
                         "c2.xhtml": xhtml("<h1>Prolog</h1><p>Pesan singkat penulis.</p>")}, {})
        ws = Workspace(self.tmp, epub)
        self.assertEqual(ws.preprocess(), 1)
        report = (ws.base / "preprocess_dropped.md").read_text(encoding="utf-8")
        self.assertIn("c2.xhtml", report)
        self.assertEqual(Workspace(self.tmp / "lagi", epub, min_chars=10).preprocess(), 2)


class BuildGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.base = self.tmp / "work_f" / "f"
        for part in ("teks", "refined", "images"):
            (self.base / part).mkdir(parents=True)
        (self.base / ".ch_count").write_text("1")
        Image.new("RGB", (10, 10), "white").save(self.base / "images/a.png")

    def build(self, text, review=True):
        (self.base / "teks/bab_001.txt").write_text(text.split("\n\n", 1)[1] if text.startswith("#") else text,
                                                    encoding="utf-8")
        (self.base / "refined/bab_001.md").write_text(text, encoding="utf-8")
        (self.base / "refined/bab_001.meta.json").write_text(json.dumps({"gate": "LULUS", "notes": ""}), encoding="utf-8")
        if review:
            (self.base / "qa_llm.jsonl").write_text(json.dumps(
                {"bab": "bab_001", "gate": "LULUS", "masalah": [], "hash": digest(self.base / "refined/bab_001.md")}) + "\n",
                encoding="utf-8")
        conf = {"_base": self.base, "_slug": "f", "judul": "F", "adult": False}
        previous, pipeline.WORK_ROOT = pipeline.WORK_ROOT, self.tmp
        try:
            pipeline.stage_build(conf)
        finally:
            pipeline.WORK_ROOT = previous
        with zipfile.ZipFile(self.tmp / "f_terjemahan.epub") as z:
            return z.read("OEBPS/Text/chapter_0001.xhtml").decode()

    def test_build_blocked_without_semantic_review(self):
        with self.assertRaises(SystemExit):
            self.build("# 1. Judul\n\n" + ID * 4, review=False)
        self.assertFalse((self.tmp / "f_terjemahan.epub").exists())

    def test_stale_review_blocks_build(self):
        self.build("# 1. Judul\n\n" + ID * 4)
        with self.assertRaises(SystemExit):
            (self.base / "refined/bab_001.md").write_text("# 1. Judul\n\n" + ID * 4 + "Diubah.", encoding="utf-8")
            conf = {"_base": self.base, "_slug": "f", "judul": "F", "adult": False}
            pipeline.stage_build(conf)

    def test_inline_img_token_is_split_not_printed(self):
        html = self.build("# 1. Judul\n\n" + ID * 3 + "[IMG:a.png]\n\n" + ID)
        self.assertNotIn("[IMG:", html)
        self.assertIn("<img", html)

    def test_no_duplicate_title_without_header(self):
        html = self.build(ID * 4)
        self.assertIn("<h1>Bab 1</h1>", html)
        self.assertNotIn("Bab 1: Bab 1", html)


class AllowTermTests(unittest.TestCase):
    def check(self, draft, terms=None):
        import argparse
        from qa_gate import check_chapter
        args = argparse.Namespace(min_ratio=0.0, max_ratio=99.0, allow_flags=False, allow_terms=terms or [])
        return check_chapter("sumber " * 20, draft, args)

    def test_forbidden_honorific_blocks_by_default(self):
        status, _ = self.check("Bang! Pintu terbanting keras. " + ID * 3)
        self.assertEqual(status, "GAGAL")

    def test_allowed_term_is_exempt_only_when_listed(self):
        self.assertEqual(self.check("Bang! Pintu terbanting. " + ID * 3, ["Bang!"])[0], "LULUS")
        self.assertEqual(self.check("Mas Ayu membuka toko. " + ID * 3, ["Mas Ayu"])[0], "LULUS")

    def test_allowing_one_phrase_does_not_hide_real_violation(self):
        status, issues = self.check("Bang! Pintu terbanting. Kak Min datang. " + ID * 3, ["Bang!"])
        self.assertEqual(status, "GAGAL")
        self.assertTrue(any("Kak" in i for i in issues))

    def test_config_must_be_list_of_strings(self):
        with tempfile.TemporaryDirectory() as d:
            nd = Path(d)
            (nd / "x.json").write_text(json.dumps({"judul": "X", "izinkan_istilah": "Bang!"}), encoding="utf-8")
            previous, pipeline.NOVELS_DIR = pipeline.NOVELS_DIR, nd
            try:
                with self.assertRaises(SystemExit):
                    pipeline.load_conf("x")
            finally:
                pipeline.NOVELS_DIR = previous

    def test_stage_qa_passes_allowed_terms_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "work_q" / "q"
            for part in ("teks", "refined"):
                (base / part).mkdir(parents=True)
            body = "Bang! Pintu terbanting. " + ID * 4
            (base / "teks/bab_001.txt").write_text(body, encoding="utf-8")
            (base / "refined/bab_001.md").write_text("# 1. Judul\n\n" + body, encoding="utf-8")
            (base / "refined/bab_001.meta.json").write_text(json.dumps({"gate": "LULUS", "notes": ""}), encoding="utf-8")
            (base / "qa_llm.jsonl").write_text(json.dumps({"bab": "bab_001", "gate": "LULUS", "masalah": [],
                "hash": digest(base / "refined/bab_001.md")}) + "\n", encoding="utf-8")
            conf = {"_base": base, "sumber": "EN_TRANSLATED", "izinkan_istilah": []}
            with self.assertRaises(SystemExit):
                pipeline.stage_qa(conf)
            conf["izinkan_istilah"] = ["Bang!"]
            pipeline.stage_qa(conf)


if __name__ == "__main__":
    unittest.main()
