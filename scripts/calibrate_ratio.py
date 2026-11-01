#!/usr/bin/env python3
"""
calibrate_ratio.py — Kalibrasi ambang rasio panjang (median ± 3×MAD) dari novel terbit.

Input: satu atau lebih folder kerja pipeline_unified (<base>/teks + <base>/draf atau refined).
Output: tabel ambang per jenis sumber, siap disalin ke config novel (min_ratio/max_ratio).

Pemakaian:
  python3 calibrate_ratio.py <base1> [base2 ...] --kind EN_TRANSLATED
  python3 calibrate_ratio.py <base> --json ratios.json
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

from publication import final_text_path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ratios_for(base: Path, source: str) -> list[float]:
    teks = base / "teks"
    out = []
    for src in sorted(teks.glob("bab_*.txt")):
        try:
            n = int(src.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        final = final_text_path(base, n, source)
        if not final.exists():
            continue
        src_len = len("".join(src.read_text(encoding="utf-8").split()))
        dst_len = len("".join(final.read_text(encoding="utf-8").split()))
        if src_len:
            out.append(dst_len / src_len)
    return out


def calibrate(values: list[float]) -> dict:
    if not values:
        raise ValueError("tidak ada pasangan bab untuk dikalibrasi")
    median = statistics.median(values)
    mad = statistics.median([abs(v - median) for v in values]) or 0.05
    return {
        "n": len(values),
        "median": round(median, 4),
        "mad": round(mad, 4),
        "min_ratio": round(max(0.1, median - 3 * mad), 3),
        "max_ratio": round(median + 3 * mad, 3),
    }


def main():
    ap = argparse.ArgumentParser(description="Kalibrasi ambang rasio QA dari novel terbit")
    ap.add_argument("bases", nargs="+", help="folder kerja pipeline (<base>/teks + draf/refined)")
    ap.add_argument("--kind", default="EN_TRANSLATED", help="label jenis sumber untuk laporan")
    ap.add_argument("--source", choices=("auto", "draf", "refined"), default="auto")
    ap.add_argument("--json", default=None, help="tulis hasil kalibrasi ke file JSON")
    args = ap.parse_args()

    values: list[float] = []
    for arg in args.bases:
        base = Path(arg).resolve()
        part = ratios_for(base, args.source)
        print(f"{base.name}: {len(part)} bab")
        values += part
    try:
        result = calibrate(values)
    except ValueError as exc:
        sys.exit(f"❌ {exc}")

    print(f"\nJenis sumber : {args.kind}")
    print(f"Bab          : {result['n']}")
    print(f"Median       : {result['median']}")
    print(f"MAD          : {result['mad']}")
    print(f"min_ratio    : {result['min_ratio']}")
    print(f"max_ratio    : {result['max_ratio']}")
    print(f"\nTambahkan ke config novel:\n  \"min_ratio\": {result['min_ratio']}, "
          f"\"max_ratio\": {result['max_ratio']}")
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"📄 {args.json}")


if __name__ == "__main__":
    main()
