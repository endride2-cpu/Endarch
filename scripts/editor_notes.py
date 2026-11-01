#!/usr/bin/env python3
"""
editor_notes.py — Kumpulkan Catatan Editor & flag dari draf terjemahan ke satu laporan.

Refinement diminta mengeluarkan flag [AMBIGU] [MISMATCH] [EN≠RAW] dan Catatan Editor
(usul onomatope baru, usul revisi [UNCERTAIN]) — tapi itu tersebar di file draf.
Script ini mengumpulkannya jadi editor_notes.md agar bisa ditindaklanjuti manusia,
dan usulan onomatope/revisi glosarium mudah di-merge.

Pemakaian:
  python3 editor_notes.py <folder_draf>            # → <folder_draf>/../editor_notes.md
  python3 editor_notes.py <base> --all             # pakai base: scan base/draf
"""
import re
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from publication import FLAGS, final_text_path  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def generate(target):
    args = [str(target)]
    target = Path(args[0] if args and args[0] != "--all" else ".").resolve()
    draf_dir = target if target.name == "draf" else (target / "draf")
    if not draf_dir.is_dir():
        draf_dir = target  # coba langsung: folder berisi bab_*_draft.md
    base = draf_dir.parent
    files = [final_text_path(base, int(p.stem.split("_")[1]))
             for p in sorted((base / "teks").glob("bab_*.txt"))
             if final_text_path(base, int(p.stem.split("_")[1])).exists()] if (base / "teks").exists() else sorted(draf_dir.glob("bab_*_draft.md"))
    if not files:
        sys.exit(f"❌ tidak ada draf di {draf_dir}")

    flag_lines, note_lines = [], []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for m in FLAGS.finditer(text):
            flag_lines.append(f"- **{f.stem}**: {m.group(0)}")
        # blok Catatan Editor (sampai akhir file / heading berikutnya)
        meta = base / "refined" / f"bab_{int(f.stem.split('_')[1]):03d}.meta.json"
        if meta.exists():
            note_lines.extend(f"- **{f.stem}**: {line}" for line in json.loads(meta.read_text(encoding="utf-8")).get("notes", "").splitlines() if line.strip() and line.strip() != "—")
        cm = re.search(r"(?im)^#{0,3}\s*catatan editor\s*:?\s*\n([\s\S]*)$", text) if not meta.exists() else None
        if cm:
            for line in cm.group(1).strip().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    note_lines.append(f"- **{f.stem}**: {line}")

    out = draf_dir.parent / "editor_notes.md"
    lines = [f"# Editor Notes — {draf_dir.parent.name}", "",
             f"Flag belum terpecahkan: {len(flag_lines)} · Catatan Editor: {len(note_lines)}", ""]
    if flag_lines:
        lines += ["## Flag belum terpecahkan (keputusan manusia)", *flag_lines, ""]
    if note_lines:
        lines += ["## Catatan Editor (usul onomatope / revisi glosarium)", *note_lines, ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main():
    print(generate(sys.argv[1] if len(sys.argv) > 1 else "."))


if __name__ == "__main__":
    main()
