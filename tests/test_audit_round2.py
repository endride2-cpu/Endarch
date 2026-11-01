"""Regresi audit lanjutan: ekstraksi blok non-<p>, slug aman, path relatif, regex audit."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import pipeline_unified as pu  # noqa: E402
import audit_translationese as at  # noqa: E402


def parse(html):
    p = pu.P()
    p.feed(html)
    p.finalize()
    return p.paras


class Round2(unittest.TestCase):
    def test_heading_div_blockquote_li_kept(self):
        html = ("<html><head><title>Judul File</title></head><body><h2>Chapter 1: The Gate</h2>"
                "<div>Div A.</div><div>Div B.</div><blockquote>Kutipan</blockquote>"
                "<ul><li>satu</li><li>dua</li></ul><hr/><p>Akhir.</p></body></html>")
        self.assertEqual(parse(html), ["# Chapter 1: The Gate", "Div A.", "Div B.", "Kutipan",
                                       "satu", "dua", "* * *", "Akhir."])

    def test_only_first_heading_is_header(self):
        self.assertEqual(parse("<h1>Judul</h1><p>a</p><h3>Sub</h3><p>b</p>"), ["# Judul", "a", "Sub", "b"])

    def test_nested_div_p_and_image(self):
        self.assertEqual(parse("<div><p>x</p><div><img src='i/a.png'/></div>y</div>"),
                         ["x", "[IMG:a.png]", "y"])

    def test_unsafe_slug_rejected(self):
        for bad in ("../x", "A", "a/b", "", "a b"):
            with self.assertRaises(SystemExit):
                pu.check_slug(bad)
        self.assertEqual(pu.check_slug("novel-1"), "novel-1")

    def test_translationese_pattern(self):
        pat = next(p for p, _, label in at.PATTERNS if label == "Tidak bisa disangkal")
        self.assertTrue(re.search(pat, "hal itu tidak bisa disangkal lagi", re.I))


if __name__ == "__main__":
    unittest.main()
