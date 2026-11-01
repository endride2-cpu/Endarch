#!/usr/bin/env python3
"""book_css.py — CSS EPUB bersama untuk builder EPUB (pipeline_unified.py).

Prinsip kenyamanan baca:
  - Tanpa color/background hardcoded → mengikuti tema reader (dark mode aman)
  - Hyphenasi + justify, efektif karena XHTML memakai xml:lang="id"
  - Paragraf pertama setelah judul/pemisah adegan TIDAK di-indent (konvensi buku)
  - Dialog (paragraf diawali kutip/em-dash) indent lebih kecil → .dlg
  - Ilustrasi besar satu halaman penuh → .illust
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONT_CONFIG = {
    # non-adult → Literata; adult → Source Serif 4
    "literata": {
        "css_name": "Literata", "prefix": "literata",
        "stack": '"Literata", "Source Serif 4", Georgia, "Times New Roman", serif',
    },
    "source-serif": {
        "css_name": "Source Serif 4", "prefix": "sourceserif",
        "stack": '"Source Serif 4", "Literata", Georgia, "Times New Roman", serif',
    },
}
DEFAULT_FONT = "literata"

CSS_BODY = """\
@charset "UTF-8";
html { -webkit-text-size-adjust: 100%; }
body { margin: 0 5%; font-family: {STACK}; line-height: 1.55; }
p { margin: 0 0 0.72em 0; text-indent: 1.25em; text-align: justify;
    -webkit-hyphens: auto; hyphens: auto; -webkit-hyphenate-limit-before: 4; hyphenate-limit-before: 4;
    widows: 2; orphans: 2; }
/* paragraf pertama setelah judul / pemisah adegan: tanpa indent */
h1 + p, .scene + p { text-indent: 0; }
/* dialog: indent lebih kecil supaya halaman tidak berlubang */
p.dlg { text-indent: 0.75em; }
h1 { font-family: "Segoe UI", system-ui, -apple-system, sans-serif; font-size: 1.45em;
     line-height: 1.22; margin: 1.6em 0 1.35em 0; text-align: left; }
img { max-width: 100%; height: auto; }
/* ilustrasi besar: satu halaman sendiri */
.illust { text-align: center; text-indent: 0; margin: 0; padding: 0;
          page-break-before: always; page-break-after: always; }
.illust img { max-height: 96vh; }
.cover-page { margin: 0; padding: 0; text-align: center; }
.cover-page img { max-width: 100%; height: auto; max-height: 100vh; }
.title-page, .credits-page { text-align: center; margin: 9vh 7% 0; }
.title-page h1 { text-align: center; font-size: 1.62em; margin-bottom: .65em; }
.edition { font-family: "Segoe UI", system-ui, sans-serif; opacity: .75; margin: .4em 0; text-indent: 0; }
.stats { font-family: "Segoe UI", system-ui, sans-serif; font-size: .9em; opacity: .8; margin: .35em 0 1.8em; text-indent: 0; }
.logo { width: 40%; max-width: 220px; height: auto; margin: 1.1em auto .9em; display: block; }
.credit, .note { font-family: "Segoe UI", system-ui, sans-serif; text-indent: 0; }
.note { font-size: .86em; opacity: .8; }
.scene { text-align: center; text-indent: 0; opacity: .7; letter-spacing: .18em; margin: 1.2em 0; }
.bracket { font-family: "Segoe UI", system-ui, sans-serif; font-size: .94em; text-indent: 0;
           margin: .8em 2%; padding: .65em .75em; border: 1px solid; border-radius: .25em; }
.listitem { text-indent: 0; margin: .18em 0 .18em 1.15em; }
.endcredit { text-indent: 0; text-align: center; font-family: "Segoe UI", system-ui, sans-serif;
             font-size: .72em; opacity: .75; margin: 2.4em 0 .6em; }
.fnref { font-size: .72em; vertical-align: super; }
.fnref a { text-decoration: none; }
.footnotes { margin: 1.6em 0 0; border-top: 1px solid; padding-top: .8em; }
.footnote { display: block; font-family: "Segoe UI", system-ui, sans-serif; font-size: .82em; margin: .5em 0; }
a { color: inherit; }
"""


def build_css(font_key: str = DEFAULT_FONT) -> str:
    cfg = FONT_CONFIG.get(font_key, FONT_CONFIG[DEFAULT_FONT])
    faces = "\n".join(
        f'@font-face {{ font-family: "{cfg["css_name"]}"; font-style: {style}; '
        f'font-weight: {weight}; src: url("../fonts/{cfg["prefix"]}-{name}.woff2"); }}'
        for name, style, weight in
        (("regular", "normal", 400), ("italic", "italic", 400), ("bold", "normal", 700))
    )
    return faces + "\n" + CSS_BODY.replace("{STACK}", cfg["stack"])


def font_files(font_key: str) -> list[Path]:
    """Path file woff2 yang harus di-copy ke OEBPS/fonts/ untuk font ini."""
    cfg = FONT_CONFIG.get(font_key, FONT_CONFIG[DEFAULT_FONT])
    fdir = ROOT / "fonts"
    return [fdir / f"{cfg['prefix']}-{n}.woff2"
            for n in ("regular", "italic", "bold") if (fdir / f"{cfg['prefix']}-{n}.woff2").exists()]
