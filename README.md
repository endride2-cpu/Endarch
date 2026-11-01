# Novel translation — paket untuk agent baru

Mulai dengan [AGENT_START.md](AGENT_START.md). Jalur utama menggunakan model bawaan agent untuk tugas bahasa, tanpa mengambil API key agent lama. Script ekstraksi, QA deterministik, build dan validasi berjalan lokal.

**Jangan langsung menjalankan `all` jika tidak punya konfigurasi API baru.** Jalur tanpa API script memerlukan agent mengerjakan tahap bahasa dan menyimpan hasil sesuai kontrak file di AGENT_START.md.

## Instalasi dan tes

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Isi paket
- `prompts/`: prompt glosarium, terjemahan dan refinement.
- `scripts/pipeline_unified.py`: pipeline utama; tahap bahasa CLI membutuhkan endpoint API baru.
- `scripts/qa_gate.py`: QA lokal, tanpa panggilan model.
- `scripts/validate_epub.py`: validasi struktural internal EPUB (build EPUB ada di `pipeline_unified.py`, gaya di `book_css.py`).
- `config/rules.json`: aturan editorial; tinjau konteks untuk kemungkinan false-positive.
- `config/channels.json`: kosong; tidak ada tujuan upload default.
- `novels/template.json`: contoh config tanpa model/provider lama sebagai default.
- `glossary/global.md`, `fonts/`: sumber bersama untuk bahasa dan layout.
- `AUDIT.md`: temuan audit dan perbaikan terakhir.
- `LICENSE`: lisensi MIT (hak cipta Reinhardt). Font di `fonts/` berlisensi SIL OFL 1.1 (`fonts/OFL-*.txt`).

API key hanya dari environment yang diinjeksi secara aman, tidak dari file agent lama. Upload Telegram opsional, membutuhkan token baru, tujuan baru dan persetujuan pengguna. Jangan membagikan key melalui chat atau mengemasnya dalam ZIP.

Gunakan jalur utama di AGENT_START.md. Validasi EPUB internal tidak menggantikan EPUBCheck atau review kualitas bahasa.
## Aplikasi Android

Folder [`android/`](android/) berisi APK Kotlin/Compose dan runtime Python yang
membungkus delapan tahap sebagai job persisten. Folder [`mobile_runtime/`](mobile_runtime/)
menyimpan adapter/checkpoint dan melakukan panggilan langsung ke endpoint API
OpenAI-compatible. API key dimasukkan lewat UI aplikasi lalu dienkripsi dengan Android
Keystore. Lihat [panduan Android](android/README.md). Jalur CLI tetap tersedia.
