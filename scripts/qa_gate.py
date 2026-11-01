#!/usr/bin/env python3
"""
qa_gate.py — QA gate deterministik untuk draft terjemahan (tanpa LLM, tanpa biaya).

Memeriksa per bab (layout pipeline: <base>/teks/bab_XXX.txt + <base>/draf/bab_XXX_draft.md):
  1. Rasio panjang sumber vs terjemahan (deteksi pemangkasan / halusinasi tambahan)
  2. Jumlah & urutan token [IMG:...] sumber vs draf
  3. Flag tersisa [AMBIGU] [MISMATCH] [EN≠RAW] [UNCERTAIN] → butuh keputusan manusia
  4. Honorifik terlarang: "Kak", "Bang", "Noona", "Unnie", "Seonbae" (harus hyung/oppa/...)
  5. Markup: **bold** / 「」 / 『』 tidak berpasangan, heading bab hilang
  6. Terjemahan masih bahasa sumber kasar (deteksi kata EN umum berlebihan)

Status per bab: LULUS / REVIEW / GAGAL. Exit code 0 hanya kalau tidak ada GAGAL.

Pemakaian:
  python3 qa_gate.py <base>                     # base = folder dgn teks/ & draf/
  python3 qa_gate.py <base> --min-ratio 0.45 --max-ratio 2.8
  python3 qa_gate.py <base> --report qa_report.md
"""
import argparse
import json
import re
import sys
from pathlib import Path
from publication import (EDITORIAL, FLAGS, RULES, digest, final_text_path,
                         glossary_names, glossary_onomatope)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RATIO_LIMITS = {"EN_TRANSLATED": (0.85, 1.6), "RAW_KOREA": (0.35, 3.0), "BOTH": (0.85, 1.6)}
IMG_TOKEN = re.compile(r"\[IMG:([^\]]+)\]")

# Honorifik/istilah terlarang → padanan baku (daftar dari config/rules.json, satu sumber dgn prompt)
_HINTS = {
    "Kak": "pakai hyung/oppa/nuna/eonni (sesuai glosarium)",
    "Kakak": "pakai hyung/oppa/nuna/eonni (sesuai glosarium)",
    "Mas": "pakai honorifik Korea",
    "Bang": "pakai honorifik Korea",
    "Noona": "nuna",
    "Unnie": "eonni",
    "Seonbae": "sunbae",
    "Oppa-oppa": "varian honorifik ganda",
}
FORBIDDEN = [
    (re.compile(rf"\b{re.escape(term)}\b"), f"{term} → {_HINTS.get(term, 'pakai honorifik Korea')}")
    for term in RULES["forbidden"]
]

# Istilah yang juga lazim sebagai onomatope: ditelaah terhadap sumber, tidak diganti otomatis.
SOUND_LIKE = {"Bang"}
NUMBER = re.compile(r"\b\d[\d.,]*\b")


def number_values(token: str) -> set[str]:
    """Nilai yang mungkin dari token angka, baik gaya EN (1,234.5) maupun ID (1.234,5)."""
    t = token.rstrip(".,")
    values = set()
    for group, decimal in ((",", "."), (".", ",")):
        if re.fullmatch(rf"\d{{1,3}}(?:{re.escape(group)}\d{{3}})+(?:{re.escape(decimal)}\d+)?|\d+(?:{re.escape(decimal)}\d+)?", t):
            whole, _, frac = t.replace(group, "").partition(decimal)
            values.add(str(int(whole)) + ("." + frac.rstrip("0") if frac.rstrip("0") else ""))
    return values or {t}


def sound_evidence(term: str, src_body: str, ctx: dict) -> int:
    """Berapa kali bunyi `term` muncul di sumber EN atau raw (via tabel onomatope glosarium)."""
    count = len(re.findall(rf"\b{re.escape(term)}\b", src_body, re.I))
    raw = ctx.get("raw", "")
    return count + sum(raw.count(snd) for snd, pad in ctx.get("onomatope", []) if pad.lower() == term.lower())


# Indikasi output masih bahasa Inggris (di luar nama/dirujuk glosarium)
EN_WORDS = re.compile(
    r"\b(the|and|with|was|were|his|her|they|then|when|because|which|said)\b", re.I)


def paras(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def strip_md(text: str) -> str:
    return re.sub(r"^#.*$", "", text, flags=re.M).strip()


def check_chapter(src_text: str, draft_text: str, args, ctx: dict | None = None) -> tuple[str, list[str]]:
    ctx = ctx or {}
    issues, warns = [], []
    src_body, dr_body = strip_md(src_text), strip_md(draft_text)

    # 1. Rasio panjang (karakter non-spasi) & jumlah paragraf
    sc = len(re.sub(r"\s", "", src_body))
    dc = len(re.sub(r"\s", "", dr_body))
    ratio = dc / sc if sc else 1.0
    if ratio < args.min_ratio:
        issues.append(f"RASIO {ratio:.2f} < {args.min_ratio} — kemungkinan KONTEN DIPANGKAS")
    elif ratio > args.max_ratio:
        issues.append(f"RASIO {ratio:.2f} > {args.max_ratio} — kemungkinan isi DITAMBAH/halusinasi")
    sp, dp = len(paras(src_body)), len(paras(dr_body))
    if sp and dp / sp < 0.5:
        warns.append(f"paragraf {dp} vs sumber {sp} — banyak paragraf digabung (cek manual)")

    # 2a. Token gambar yang menyatu dengan teks dalam satu paragraf
    if any(IMG_TOKEN.search(p) and not IMG_TOKEN.fullmatch(p) for p in paras(draft_text)):
        warns.append("token [IMG:...] menyatu dengan teks dalam satu paragraf (build akan memecahnya; cek posisi)")

    # 2. Token gambar
    s_imgs = IMG_TOKEN.findall(src_text)
    d_imgs = IMG_TOKEN.findall(draft_text)
    if s_imgs != d_imgs:
        miss = set(s_imgs) - set(d_imgs)
        extra = set(d_imgs) - set(s_imgs)
        for m in miss:
            issues.append(f"[IMG:{m}] HILANG di draf")
        for e in extra:
            issues.append(f"[IMG:{e}] tidak ada di sumber")
        if not miss and not extra:
            issues.append("urutan gambar berubah")

    # 3. Unresolved editorial flags block release.
    n_flags = len(FLAGS.findall(draft_text))
    if n_flags:
        (warns if getattr(args, "allow_flags", False) else issues).append(f"{n_flags} unresolved flags")
    if EDITORIAL.search(draft_text):
        issues.append("Editorial metadata inside chapter text")

    # 4. Honorifik terlarang (frasa di `izinkan_istilah` dikecualikan: onomatope, nama orang, dst.)
    checked = dr_body
    for term in sorted(getattr(args, "allow_terms", None) or [], key=len, reverse=True):
        if term.strip():
            checked = checked.replace(term, " ")
    for (pat, msg), term in zip(FORBIDDEN, RULES["forbidden"]):
        hits = pat.findall(checked)
        if not hits:
            continue
        evidence = sound_evidence(term, src_body, ctx) if term in SOUND_LIKE else 0
        hint = (f" (sumber memuat bunyi '{term}' ×{evidence}: mungkin onomatope — telaah konteks; bila benar, "
                "daftarkan frasanya di izinkan_istilah novel ini, jangan ganti otomatis)"
                if evidence and len(hits) <= evidence else "")
        issues.append(f"honorifik terlarang ×{len(hits)}: {msg}{hint}")

    # 5. Markup balance
    if len(re.findall(r"\*\*", dr_body)) % 2:
        issues.append("jumlah `**` ganjil — bold tidak tertutup")
    for pair in ("「」", "『』"):
        if dr_body.count(pair[0]) != dr_body.count(pair[1]):
            warns.append(f"{pair[0]}{pair[1]} tidak berpasangan ({dr_body.count(pair[0])}/{dr_body.count(pair[1])})")
    if src_text.lstrip().startswith("#") and not draft_text.lstrip().startswith("#"):
        warns.append("heading bab hilang di draf")

    # 6. Output masih bahasa Inggris (heuristik: >8 kata EN umum per 1000 kata)
    words = dr_body.split()
    if words:
        en_hits = len(EN_WORDS.findall(dr_body))
        per_k = en_hits * 1000 / max(len(words), 1)
        if per_k > 8:
            issues.append(f"indikasi output masih bahasa Inggris ({en_hits} kata EN umum / {per_k:.0f} per 1000)")

    # 7. Konsistensi ANGKA: angka di sumber (yang pakai digit arab) tak muncul di draf
    if re.search(r"\d", src_body):
        d_values = {v for num in NUMBER.findall(dr_body) for v in number_values(num)}
        missing = {num for num in NUMBER.findall(src_body) if not number_values(num) & d_values}
        for num in sorted(missing, key=lambda x: -len(x))[:5]:
            if len(num.rstrip(".,")) >= 1:
                warns.append(f"angka '{num}' ada di sumber tapi tidak di draf (cek konversi/penulisan)")

    # 8. NAMA karakter glosarium: muncul di sumber tapi hilang di draf
    for name in ctx.get("names", []):
        if name.lower() in src_body.lower() and name.lower() not in dr_body.lower() and not any(
                re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", dr_body, re.I)
                for alias in ctx.get("name_aliases", {}).get(name, [])):
            issues.append(f"nama '{name}' ada di sumber tapi TIDAK ADA di draf — salah terjemah nama/konten hilang")

    # 9. ONOMATOPE (hanya kalau raw Hangeul tersedia): hitung bunyi vs padanan draf
    for snd, pad in ctx.get("onomatope", []):
        n_raw = ctx["raw"].count(snd)
        n_id = len(re.findall(rf"\b{re.escape(pad)}\b", dr_body, re.I))
        if n_raw >= 3 and n_id == 0:
            warns.append(f"onomatope {snd} ×{n_raw} di raw tapi padanan '{pad}' tak pernah muncul")

    status = "GAGAL" if issues else ("REVIEW" if warns else "LULUS")
    return status, issues + [f"[REVIEW] {w}" for w in warns]


def run(argv=None):
    ap = argparse.ArgumentParser(description="QA gate deterministik draft terjemahan")
    ap.add_argument("base", help="folder novel (berisi teks/ & draf/)")
    ap.add_argument("--min-ratio", type=float, default=None)
    ap.add_argument("--max-ratio", type=float, default=None)
    ap.add_argument("--source", choices=("auto", "draf", "refined"), default="auto")
    ap.add_argument("--kind", choices=("EN_TRANSLATED", "RAW_KOREA", "BOTH"), default=None,
                    help="jenis sumber utk ambang rasio default (pipeline_unified meneruskannya)")
    ap.add_argument("--allow-flags", action="store_true", help="internal preview only")
    ap.add_argument("--allow-term", action="append", default=[], metavar="FRASA",
                    help="frasa yang dikecualikan dari cek honorifik terlarang (boleh diulang), mis. 'Bang!'")
    ap.add_argument("--require-review", action="store_true",
                    help="GAGAL bila review makna (qa_llm.jsonl, hash cocok) belum ada untuk sebuah bab")
    ap.add_argument("--report", default=None, help="tulis laporan markdown (default <base>/qa_report.md)")
    ap.add_argument("--json", default=None, help="tulis hasil JSONL (default <base>/qa_report.jsonl)")
    ap.add_argument("--name-aliases", default="{}", help="Approved source-name to target-alias lists, JSON")
    args = ap.parse_args(argv)
    args.name_aliases = json.loads(args.name_aliases)
    if not isinstance(args.name_aliases, dict) or any(not isinstance(k, str) or not k.strip() or not isinstance(v, list) or not v or any(not isinstance(a, str) or not a.strip() for a in v) for k, v in args.name_aliases.items()):
        ap.error("Invalid name aliases")

    base = Path(args.base).resolve()
    teks = base / "teks"
    if not teks.is_dir():
        sys.exit(f"❌ {base} tidak berisi folder teks/ & draf/ (layout folder kerja pipeline_unified)")

    numbers = sorted(int(p.stem.split("_")[1]) for p in teks.glob("bab_*.txt"))
    drafts = [final_text_path(base, n, args.source) for n in numbers]
    if not drafts:
        sys.exit(f"❌ tidak ada bab_*_draft.md di {base}")

    # konteks tambahan dari folder kerja pipeline
    gl_file = base / "glosarium" / "master_full.md"
    gl = gl_file.read_text(encoding="utf-8") if gl_file.exists() else ""
    names = glossary_names(gl)
    ono = glossary_onomatope(gl)
    glossary_warning = None
    if gl and not names:
        glossary_warning = ("glosarium tidak punya tabel KARAKTER yang terbaca — cek nama karakter "
                            "NONAKTIF untuk novel ini (periksa format tabel di glosarium/master_full.md)")
        print(f"  ⚠ {glossary_warning}")
    # hasil checker LLM (stage `check`) utk digabung
    llm = {}
    llm_f = base / "qa_llm.jsonl"
    if llm_f.exists():
        for line in llm_f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                llm[r["bab"]] = r
            except Exception:
                pass

    rows = []
    n_fail = n_review = 0
    for df in drafts:
        n_m = re.search(r"bab_(\d+)", df.stem)
        bab = f"bab_{int(n_m.group(1)):03d}" if n_m else df.stem.replace("_draft", "")
        sf = teks / f"{bab}.txt"
        rawf = base / "teks_raw" / f"{bab}.txt"
        raw = rawf.read_text(encoding="utf-8") if rawf.exists() else ""
        ctx = {"name_aliases": args.name_aliases, "names": names, "onomatope": ono, "raw": raw}
        if not df.exists() or (not sf.exists() and not raw):
            rows.append((df.stem, "GAGAL", ["missing source or final chapter"]))
            n_fail += 1
            continue
        # rasio dihitung terhadap teks EN kalau ada; RAW_KOREA murni → ambang raw
        kind = "BOTH" if (raw and sf.exists()) else (args.kind or "EN_TRANSLATED")
        if not sf.exists():
            kind = "RAW_KOREA"
        lo, hi = RATIO_LIMITS.get(kind, RATIO_LIMITS["EN_TRANSLATED"])
        chapter_args = argparse.Namespace(**{**vars(args), "allow_terms": args.allow_term,
            "min_ratio": args.min_ratio if args.min_ratio is not None else lo,
            "max_ratio": args.max_ratio if args.max_ratio is not None else hi})
        status, issues = check_chapter(sf.read_text(encoding="utf-8") if sf.exists() else raw,
                                        df.read_text(encoding="utf-8"), chapter_args, ctx)
        if bab in llm and llm[bab].get("hash") != digest(df):
            if llm[bab].get("gate") == "GAGAL":
                # konservatif: GAGAL lama tetap memblokir sampai `check` dijalankan ulang
                issues.append("verdict GAGAL lama (teks sudah berubah) — jalankan ulang tahap check")
            else:
                issues.append("hasil check basi (hash berbeda)")
                if status == "LULUS":
                    status = "REVIEW"
                llm.pop(bab)
        if args.require_review and not args.allow_flags and bab not in llm:
            status = "GAGAL"
            issues = ["review makna belum ada/basi — tulis qa_llm.jsonl untuk teks final ini "
                      "(tahap `check` atau review agent; lihat AGENT_START.md langkah 8)"] + issues
        # gabung verdict checker LLM
        if bab in llm:
            gate, probs = llm[bab].get("gate"), llm[bab].get("masalah", [])
            if gate == "GAGAL":
                status = "GAGAL"
                issues = [f"[LLM-GAGAL] {p}" for p in probs] + issues
            elif gate == "BERSYARAT":
                # BERSYARAT = pemeriksa ragu; tanpa rincian pun tetap perlu telaah manusia.
                if status == "LULUS":
                    status = "REVIEW"
                issues = issues + ([f"[LLM-BERSYARAT] {p}" for p in probs] or
                                   ["[LLM-BERSYARAT] pemeriksa makna ragu tanpa rincian — telaah manual"])
        meta = base / "refined" / f"{bab}.meta.json"
        if meta.exists() and df.parent.name == "refined":
            gate = json.loads(meta.read_text(encoding="utf-8")).get("gate", "GAGAL")
            if gate == "GAGAL":
                status = "GAGAL"
                issues.append("Refinement gate: GAGAL")
            elif gate == "BERSYARAT" and status == "LULUS":
                status = "REVIEW"
                issues.append("Refinement gate: BERSYARAT")
        rows.append((df.stem, status, issues))
        if status == "GAGAL":
            n_fail += 1
        elif status == "REVIEW":
            n_review += 1
        print(f"  {status:7} {df.stem}" + (f" — {issues[0]}" if issues else ""))

    report = base / "qa_report.md" if args.report is None else Path(args.report)
    lines = [f"# QA Report — {base.name}", "",
             f"Total {len(rows)} bab · LULUS {len(rows)-n_fail-n_review} · REVIEW {n_review} · GAGAL {n_fail}", ] + ([f"⚠ {glossary_warning}", ""] if glossary_warning else [])
    for stem, status, issues in rows:
        if status != "LULUS":
            lines.append(f"## {stem} — {status}")
            lines += [f"- {i}" for i in issues]
            lines.append("")
    report.write_text("\n".join(lines), encoding="utf-8")

    jf = base / "qa_report.jsonl" if args.json is None else Path(args.json)
    jf.write_text("\n".join(json.dumps({"bab": s, "status": st, "issues": i}, ensure_ascii=False)
                            for s, st, i in rows), encoding="utf-8")

    print(f"\n📄 {report}")
    print(f"📊 LULUS {len(rows)-n_fail-n_review} · REVIEW {n_review} · GAGAL {n_fail}")
    return 1 if n_fail else 0


def main():
    sys.exit(run())


if __name__ == "__main__":
    main()
