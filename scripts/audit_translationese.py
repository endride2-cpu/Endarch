#!/usr/bin/env python3
"""
audit_translationese.py — Auditori frasa klise terjemahan mesin di draft novel.

Cara pakai:
  python3 audit_translationese.py <folder_draft> [--threshold N] [--bab N]

Contoh:
  python3 audit_translationese.py work_novel2/translation_pipeline/draf
  python3 audit_translationese.py work_novel2/translation_pipeline/draf --bab 80,100,150
  python3 audit_translationese.py work_novel2/translation_pipeline/draf --threshold 5
"""
import os, re, sys

# Pola frasa klise → skor (berapa banyak "keterlaluan" per kemunculan)
PATTERNS = [
    (r'\btidak\s+bisa\s+disangkal\b', 3, 'Tidak bisa disangkal'),
    (r'\btidak\s+dapat\s+dipungkiri?\b', 3, 'Tidak dapat dipungkiri'),
    (r'\bterombang.?ambing\b', 2, 'Terombang-ambing'),
    (r'\bdapat\s+dilihat\s+bahwa\b', 2, 'Dapat dilihat bahwa'),
    (r'\bhal\s+ini\s+disebabkan\s+oleh\b', 2, 'Hal ini disebabkan oleh'),
    (r'\bdengan\s+demikian\b', 1, 'Dengan demikian'),
    (r'\bdengan\s+kata\s+lain\b', 1, 'Dengan kata lain'),
    (r'\bmerupakan\b', 1, 'Merupakan'),
    (r'\btidak\s+perlu\s+diragukan\s+lagi?\b', 2, 'Tidak perlu diragukan lagi'),
    (r'\btidak\s+ada\s+keraguan\b', 1, 'Tidak ada keraguan'),
    (r'\bdi\s+samping\s+itu\b', 1, 'Di samping itu'),
    (r'\boleh\s+dikatakan\b', 1, 'Boleh dikatakan'),
    (r'\bsecara\s+tidak\s+langsung\b', 1, 'Secara tidak langsung'),
    (r'\btidak\s+jarang\b', 1, 'Tidak jarang'),
    (r'\bkeberadaan\b', 1, 'Keberadaan (berlebihan)'),
    (r'\bkehadiran\b', 1, 'Kehadiran (berlebihan)'),
    (r'\bkeinginan\b', 1, 'Keinginan (vs "ingin")'),
    (r'\bkemampuan\b', 1, 'Kemampuan (vs "bisa/dapat")'),
    (r'\bpengetahuan\b', 1, 'Pengetahuan (vs "tahu")'),
    # "sangat" berlebihan — hitung >1 per 1000 kata
    (r'\bsangat\b', 0.5, 'Sangat (hitung total)'),
    # "yang" berlebihan — hitung >15 per 1000 kata
    (r'\byang\b', 0.1, 'Yang (hitung total)'),
    # "-nya" posesif berlebihan — subjektif, catat saja
    (r'\b[a-z]+nya\b', 0.1, '-nya (hitung total)'),
    # kalimat dengan "Namun," di awal
    (r'^Namun[,\s]', 0.5, 'Namun di awal kalimat'),
    (r'^Selain\s+itu[,\s]', 0.5, 'Selain itu di awal'),
    (r'^Sementara\s+itu[,\s]', 0.5, 'Sementara itu di awal'),
    (r'^Dengan\s+demikian[,\s]', 0.5, 'Dengan demikian di awal'),
    # em dash berlebihan — >5 per bab
    (r'—', 0.1, 'Em dash (hitung total)'),
    # tanda seru berlebihan — >10 per bab
    (r'!', 0.05, 'Tanda seru (hitung total)'),
]

def scan_file(path, threshold=5):
    text = open(path, encoding='utf-8').read()
    # buang header
    body = re.sub(r'^#.*$', '', text, flags=re.M).strip()
    words = len(body.split())
    hits = []
    total_score = 0
    for pattern, weight, label in PATTERNS:
        matches = re.findall(pattern, body, re.I | re.M)
        if matches:
            cnt = len(matches)
            score = cnt * weight
            total_score += score
            if label in ('Sangat (hitung total)', 'Yang (hitung total)', '-nya (hitung total)',
                         'Em dash (hitung total)', 'Tanda seru (hitung total)'):
                continue  # laporan terpisah
            hits.append((label, cnt, round(score, 1)))
    # special: "sangat" >1 per 1000 kata
    sangat = len(re.findall(r'\bsangat\b', body, re.I))
    sangat_threshold = max(2, words / 1000 * 1)
    if sangat > sangat_threshold:
        hits.append((f'Sangat ({sangat}x)', sangat, round(sangat * 0.5, 1)))
    # em dash >5
    emdash = len(re.findall(r'—', body))
    if emdash > 5:
        hits.append((f'Em dash ({emdash}x)', emdash, round(emdash * 0.1, 1)))
    # tanda seru >10
    seru = len(re.findall(r'!', body))
    if seru > 10:
        hits.append((f'Tanda seru ({seru}x)', seru, round(seru * 0.05, 1)))
    # "yang" density
    yang = len(re.findall(r'\byang\b', body, re.I))
    if words > 0 and yang / words * 1000 > 15:
        hits.append((f'Yang padat ({yang}x, {yang/words*1000:.0f}/1000 kata)', yang, round(yang * 0.1, 1)))
    return hits, total_score, words

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('folder', help='Folder draft')
    ap.add_argument('--threshold', type=int, default=4, help='Skor threshold untuk warning (default 4)')
    ap.add_argument('--bab', help='Filter bab (contoh: 80,100,150)')
    args = ap.parse_args()
    
    folder = args.folder
    if not os.path.isdir(folder):
        print(f'❌ Folder tidak ditemukan: {folder}')
        sys.exit(1)
    
    files = sorted(f for f in os.listdir(folder) if f.endswith('_draft.md'))
    if args.bab:
        wanted = set(int(x) for x in args.bab.split(','))
        files = [f for f in files if int(re.search(r'\d+', f).group()) in wanted]
    
    print(f'Scan {len(files)} bab, threshold={args.threshold}...\n')
    flagged = []
    for f in files:
        n = int(re.search(r'\d+', f).group())
        hits, score, words = scan_file(f'{folder}/{f}', args.threshold)
        if score >= args.threshold:
            flagged.append((n, score, words, hits))
        if score >= args.threshold or args.bab:
            flag = ' ⚠️' if score >= args.threshold else ' ✓'
            print(f'[{n:>3}] {words:>5} kata | skor {score:>4.1f}{flag}')
            if hits:
                for h, cnt, s in hits:
                    print(f'       {h} ({cnt}x, skor {s})')
    print(f'\n⚠️ Bab perlu refine: {len(flagged)}/{len(files)}')
    if flagged:
        print(f'   Rata-rata skor: {sum(s for _, s, _, _ in flagged)/len(flagged):.1f}')
        print(f'   Terburuk: {sorted(flagged, key=lambda x: -x[1])[:5]}')

if __name__ == '__main__':
    main()