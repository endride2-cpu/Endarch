#!/usr/bin/env python3
"""
validate_epub.py — Validasi EPUB hasil build SEBELUM upload (tanpa epubcheck).

Cek:
  1. Zip valid & mimetype benar (application/epub+zip, entri pertama, tak terkompres)
  2. Semua file XHTML/OPF/NCX well-formed XML (parse via xml.etree)
  3. Semua href/src di XHTML & manifest ada sebagai file fisik (tidak ada yang hilang)
  4. Semua file di OEBPS terdaftar di manifest (kecuali yang boleh: content.opf, toc.ncx)
  5. Spine tidak kosong & setiap itemref ada di manifest
  6. Bisa dibuka ulang dengan ebooklib (jika terpasang)

Exit 0 = valid, 1 = rusak.

Pemakaian:
  python3 validate_epub.py hasil.epub [hasil2.epub ...]
"""
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from publication import digest

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
OPF_NS = "{http://www.idpf.org/2007/opf}"


def _j(base: Path, ref: str) -> str:
    """Gabung path dengan '/' (format nama entri zip) + normalisasi '../'."""
    import posixpath
    ref = ref.split("#")[0]
    base_str = str(base).replace("\\", "/")
    joined = f"{base_str}/{ref}" if base_str not in (".", "") else ref
    return posixpath.normpath(joined)


def validate(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        return _validate(z, path)


def _validate(z: zipfile.ZipFile, path: Path) -> list[str]:
    errs = []
    names = z.namelist()

    # 1. mimetype
    if not names or names[0] != "mimetype":
        errs.append("entri pertama zip bukan 'mimetype'")
    else:
        info = z.getinfo("mimetype")
        if info.compress_type != zipfile.ZIP_STORED:
            errs.append("mimetype terkompres (harus STORED)")
        if z.read("mimetype").decode("utf-8", "ignore").strip() != "application/epub+zip":
            errs.append("isi mimetype salah")

    # 2. container.xml → OPF
    if "META-INF/container.xml" not in names:
        errs.append("META-INF/container.xml tidak ada")
        return errs
    try:
        cnt = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = cnt.find(f".//{CONTAINER_NS}rootfile").get("full-path")
    except Exception as e:
        return errs + [f"container.xml rusak: {e}"]
    if opf_path not in names:
        return errs + [f"OPF {opf_path} tidak ada di zip"]

    # 3. XML well-formed
    for n in names:
        if n.lower().endswith((".xhtml", ".html", ".htm", ".opf", ".ncx", ".xml")):
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as e:
                errs.append(f"{n}: XML tidak well-formed — {e}")

    # 4. Manifest vs file fisik
    try:
        opf = ET.fromstring(z.read(opf_path))
    except ET.ParseError as e:
        return errs + [f"OPF rusak: {e}"]
    base = Path(opf_path).parent
    items = {}
    for it in opf.findall(f".//{OPF_NS}manifest/{OPF_NS}item"):
        href, iid = it.get("href"), it.get("id")
        items[iid] = href
        full = _j(base, href)
        if full not in names:
            errs.append(f"manifest: {href} (id={iid}) tidak ada di zip")

    # file fisik yang tak terdaftar di manifest
    listed = {_j(base, h) for h in items.values()}
    for n in names:
        if n in ("mimetype", opf_path, "META-INF/container.xml") or n.startswith("META-INF/"):
            continue
        if n not in listed:
            errs.append(f"file tidak terdaftar di manifest: {n}")

    # 5. Spine
    spine = [ir.get("idref") for ir in opf.findall(f".//{OPF_NS}spine/{OPF_NS}itemref")]
    if not spine:
        errs.append("spine kosong")
    for idref in spine:
        if idref not in items:
            errs.append(f"spine: idref '{idref}' tidak ada di manifest")

    # 6. Referensi internal XHTML (img/a/link relatif)
    for n in [x for x in names if x.lower().endswith((".xhtml", ".html"))]:
        try:
            root = ET.fromstring(z.read(n))
        except ET.ParseError:
            continue
        d = Path(n).parent
        for el in root.iter():
            ref = el.get("src") or el.get("href")
            if not ref or ref.startswith(("http", "mailto:", "data:", "#")):
                continue
            target = _j(d, ref)
            if target not in names:
                errs.append(f"{n}: referensi '{ref}' tidak ditemukan")

    # 7. ebooklib round-trip (opsional)
    try:
        from ebooklib import epub  # noqa: F401
        epub.read_epub(str(path))
    except ImportError:
        pass
    except Exception as e:
        errs.append(f"ebooklib gagal membaca ulang: {e!r}")

    return errs


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    bad = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        print(f"🔎 {p}")
        try:
            errs = validate(p)
        except zipfile.BadZipFile:
            errs = ["bukan zip/EPUB valid"]
        if errs:
            p.with_suffix(p.suffix + ".validated").unlink(missing_ok=True)
            bad += 1
            for e in errs:
                print(f"  ❌ {e}")
        else:
            p.with_suffix(p.suffix + ".validated").write_text(digest(p), encoding="ascii")
            print("  ✅ valid")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
