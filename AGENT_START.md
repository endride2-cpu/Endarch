# Mulai di AI agent baru

## Keputusan migrasi
- Jalur utama: agent memakai model dan tools yang SUDAH tersedia di platform barunya. Tidak perlu mengambil atau menyalin API key platform tersebut ke script.
- Script lokal menangani ekstraksi, QA deterministik, EPUB dan validasi. Agent mengerjakan glosarium, terjemahan, refinement serta review makna melalui modelnya sendiri.
- Model bawaan agent TIDAK otomatis menjadi endpoint OpenAI untuk Python. Tahap `gloss/trans/refine/check/all` CLI masih membutuhkan API jika dijalankan langsung. Jangan menjalankan `all` dalam mode native tanpa API.
- Jalur alternatif: otomatis penuh memakai API OpenAI-compatible BARU yang dikonfigurasi pengguna secara aman. Tidak membaca ~/.hermes/.env, scripts/.env atau uploader.env.
- Tidak ada kewajiban upload. ID channel lama telah dikosongkan. Jangan kirim file ke tujuan publik tanpa persetujuan pengguna.

## Instruksi untuk agent (jalur utama, tanpa key lama)
1. Baca dokumen ini, lalu `prompts/translation.md`, `prompts/refinement.md`, `prompts/glossary_generator.md` dan `config/rules.json`. Instal `requirements.txt` bila dibutuhkan. Gunakan direktori paket sebagai working directory; jangan mencetak secret.
2. Jika pengguna belum memberikan EPUB dan judul, minta keduanya. Jangan menciptakan sumber novel.
3. Inisialisasi config: `python scripts/pipeline_unified.py --init <slug> '<judul>' <path-absolut.epub>`. Gunakan slug aman huruf kecil/angka/tanda hubung. Sesuaikan doc_raw, sumber, adult dan raw_map bila relevan. Path absolut dianjurkan.
4. Jalankan `python scripts/pipeline_unified.py <slug> preprocess`. Hasil ada di `work/work_<slug>/<slug>/`. Periksa jumlah bab, gambar dan alignment. Jika ekstraksi kehilangan isi atau bab pendek, berhenti dan perbaiki ekstraksi sebelum menerjemahkan. Teks di luar `<p>` (div, heading, blockquote, list) ikut diekstrak; heading pertama menjadi `# judul`, `<hr>` menjadi `* * *`. Jika draf/refined sudah ada dan hasil ekstraksi berubah, preprocess berhenti: gunakan slug baru, jangan menimpa workspace yang sudah diterjemahkan.
5. Buat glosarium spesifik novel melalui model agent berdasarkan sumber; simpan di `glosarium/master_full.md`. Gabungkan dengan `glossary/global.md` (spesifik novel menimpa global). Jangan mengirim seluruh novel dalam satu konteks jika melebihi kapasitas model.
6. Buat panduan gaya singkat dari sumber bab awal di `style.md`. Untuk tiap bab, terjemahkan sesuai prompt, konteks bab sekitar, glosarium dan raw yang telah disejajarkan. Simpan `draf/bab_NNN_draft.md`, dengan header `# N. Judul Indonesia`. Pertahankan semua konten dan token [IMG:nama] persis di posisi asal. Jika perlu chunk, potong di batas paragraf, gunakan konteks, jangan gandakan judul atau isi saat menggabungkan.
7. Refinement menggunakan prompt terkait. Jangan masukkan metadata editorial ke prosa. Simpan teks dari blok ===TEKS=== di `refined/bab_NNN.md` dan metadata `{ "gate": "LULUS|BERSYARAT|GAGAL", "notes": "..." }` di `refined/bab_NNN.meta.json`. Gunakan `publication.parse_refinement` untuk memeriksa output. Jangan menganggap draf sudah refined hanya dengan mengganti nama file.
8. Lakukan review makna per bab memakai raw sebagai otoritas jika ada. Catat di `qa_llm.jsonl`: `{ "bab": "bab_NNN", "gate": "LULUS|BERSYARAT|GAGAL", "masalah": ["..."], "hash": "<SHA256 file final>" }`. Gunakan `publication.digest`; hash harus milik teks final yang benar-benar direview. Review ini WAJIB: `qa` dan `build` memblokir bab yang belum punya catatan review dengan hash yang cocok. Review agent bukan otomatis review independen; laporkan keterbatasan ini. Jangan tulis LULUS tanpa membandingkan sumber. Setelah revisi, review ulang dan append hasil baru.
9. Jalankan `python scripts/pipeline_unified.py <slug> qa`. Jika `preprocess_dropped.md` ada, periksa bagian pendek yang tidak dijadikan bab (atur `min_chars` di config bila perlu). Perbaiki GAGAL dan telaah REVIEW. Aturan honorifik Mas/Bang dapat false-positive (onomatope "Bang!", nama "Mas Ayu"): nilai konteks; jika memang benar, daftarkan frasanya di `izinkan_istilah` pada config novel, jangan ganti semua secara buta dan jangan ubah `config/rules.json`. Jangan menonaktifkan gate hanya agar build berhasil.
10. Jalankan `build`, kemudian `validate` dengan CLI yang sama. Kumpulkan catatan via `python scripts/editor_notes.py <base>`. Serahkan EPUB dan catatan kepada pengguna. `.validated` berarti validasi struktural internal, bukan sertifikasi EPUBCheck atau jaminan mutu terjemahan.

## Alternatif API baru (otomatis penuh)
Konfigurasikan lewat secret manager/environment platform baru, bukan melalui chat atau file yang dibagikan:
- `NOVEL_API_KEY`: key BARU untuk endpoint yang dipilih.
- `NOVEL_API_BASE`: URL API OpenAI-compatible (bukan URL halaman chat).
- `NOVEL_MODEL`: ID model yang benar-benar tersedia.
- Default provider `custom`; provider lama hanya pilihan eksplisit dan membutuhkan key baru miliknya sendiri.

Jika config sudah dibuat saat NOVEL_MODEL belum tersedia, isi field `model` di config secara eksplisit (model bukan secret). Field `api_base` opsional, tanpa token di URL.

Jalankan `python scripts/pipeline_unified.py <slug> all` hanya setelah konfigurasi benar. Default refine="ask" akan berhenti setelah trans. Setelah pengguna menyetujui refinement, jalankan `refine`, `check`, `qa`, `build`, `validate` berurutan. Paket ini belum menyertifikasi kompatibilitas tiap provider.

## Batas audit
Inti pipeline aktif sudah diperbaiki dan tes lokal dijalankan (lihat AUDIT.md untuk audit lanjutan). Tidak ada panggilan API nyata atau upload pada saat migrasi ini.