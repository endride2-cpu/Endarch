# Audit lanjutan

Tanggal: 2026-10-31. Lingkup: seluruh `scripts/`, `tests/`, config, dokumen. Tes: 51 lulus (46 lama + 5 regresi baru di `tests/test_audit_round2.py`), pyflakes bersih. Tidak ada panggilan API atau upload.

## Diperbaiki
| # | Tingkat | Masalah | Perbaikan |
|---|---|---|---|
| 1 | Kritis | Ekstraktor EPUB membuang teks di luar `<p>`: paragraf `<div>`, judul `<h1>`–`<h6>`, `<blockquote>`; item `<li>` tergabung jadi satu. Novel berbasis `<div>` bisa kehilangan hampir seluruh isi. | Setiap elemen blok menjadi batas paragraf; `<head>/<title>` dilewati; heading pertama jadi `# judul` (sesuai prompt dan cek heading di qa_gate); `<hr>` jadi `* * *`. |
| 2 | Tinggi | Preprocess ulang meninggalkan `teks/bab_*.txt` basi dan diam-diam menimpa sumber di workspace yang sudah diterjemahkan (draf salah pasangan). | File teks lama dibersihkan; jika draf/refined ada dan ekstraksi berubah, proses berhenti dan meminta slug baru. |
| 3 | Tinggi | Slug tidak divalidasi (`../x` dapat menulis config/work di luar folder). | Validasi `^[a-z0-9][a-z0-9-]{0,63}$` di `--init` dan saat load. |
| 4 | Sedang | `doc` relatif dibaca dari CWD, padahal template menyebut relatif ke WORK_ROOT. `doc_raw` hilang diabaikan diam-diam (mode BOTH mati tanpa disadari). | Path relatif dicari di WORK_ROOT → folder paket → CWD; `doc_raw` hilang = error. `--init` menolak EPUB yang tidak ada. |
| 5 | Sedang | Satu bab error API di `refine` menghentikan seluruh tahap tanpa ringkasan. | Kegagalan dikumpulkan seperti `trans`, exit non-zero dengan daftar bab untuk resume. |
| 6 | Sedang | `editor_notes.py` crash bila sebagian bab belum diterjemahkan; impor gagal jika dijalankan dari luar `scripts/`. | Lewati bab tanpa teks final; path impor diperbaiki; variabel mati dihapus. |
| 7 | Rendah | Pola `audit_translationese` "Tidak bisa disangkal" salah ketik (`disingkari`) sehingga tidak pernah cocok. | Regex diperbaiki. |
| 8 | Rendah | Upload: kode mati (local API) dan `TELEGRAM_API_BASE` http polos bisa mengirim token tanpa enkripsi. | Kode mati dihapus; hanya https atau http://localhost. |
| 9 | Rendah | Pesan `--init` menyarankan `all` (bertentangan dengan AGENT_START). | Pesan diarahkan ke `preprocess`. |

## Catatan / belum diubah (keputusan desain)
- Workspace lama yang dibuat dengan parser lama akan ditolak saat preprocess ulang bila hasil ekstraksi berubah; ini disengaja. Gunakan slug baru.
- Pemotongan raw per chunk (`align_chunks`) proporsional per karakter, bisa memotong di tengah kalimat; review makna tetap wajib.
- Cek honorifik `Mas`/`Bang` dapat false-positive; gunakan `izinkan_istilah`.
- Semua gambar dikonversi ke JPEG; SVG/gambar rusak membuat build gagal keras (disengaja).
- `.validated` = validasi struktural internal, bukan EPUBCheck.


## Audit pasca-v0.2.5 (2026-11-01)
Sumber awal identik dengan SOURCE_MANIFEST v0.2.5 (80 file diverifikasi hash). Bukti HP:
screenshot 10:14 menampilkan header bab "Bab 1 · LULUS" — format itu tidak ada di source v0.2.5
(yang menampilkan "Makna: … | QA: …"), jadi APK terpasang lebih lama dari source ini.
Rekaman 09:10 memperlihatkan job baru tetap "Dalam antrean" tanpa progres; QUEUED berarti
siap tetapi belum ada worker, kini diberi label "Siap dijalankan".

| # | Tingkat | Masalah (terbukti dari kode/tes) | Perbaikan |
|---|---|---|---|
| 1 | Tinggi | `configure` menghapus semua receipt; karena receipt memuat seluruh config, mengubah max_tokens atau izinkan_istilah menagih ulang semua trans+refine (terukur: 4 dari 8 panggilan pada fixture 2 bab) dan meminta persetujuan refine lagi. | Receipt valid dicap ulang; hanya tahap terdampak dibatalkan (`CONFIG_SCOPE`). |
| 2 | Tinggi | `ProviderClient.request` mengabaikan request ID; resume atas PENDING/UNKNOWN atau respons hilang = request berbayar baru tanpa persetujuan. | Resume berhenti dengan pesan; `confirm_resend` eksplisit + dialog UI. Respons ditulis ke checkpoint sebelum SUCCEEDED. |
| 3 | Tinggi | Key/endpoint hilang ditangkap sebagai UNKNOWN (tak ada yang terkirim) dan memblokir retry; callTimeout 50 dtk lebih pendek dari generasi bab panjang. | FAILED non-retryable sebelum kirim; 2xx tak terbaca = FAILED; timeout 5/10 menit. |
| 4 | Sedang | Retry menghapus SEMUA checkpoint dan memakai ulang request ID attempt SUCCEEDED. | Hanya checkpoint task yang di-retry; attempt lama ditandai `superseded`. Cek UNKNOWN sebelum mutasi. |
| 5 | Sedang | BERSYARAT tanpa rincian: state job REVIEW tetapi qa_report.jsonl/md LULUS. | qa_gate menjadikannya REVIEW; override di engine dihapus. |
| 6 | Sedang | Verdict makna tetap tampil setelah teks diedit; qa_report.md basi tampil tanpa label. | `review_current`, `qa_current`, `reports_stale` di detail; label UI. |
| 7 | Sedang | Service berhenti di antara step meninggalkan journal RUNNING; `pause` menimpa COMPLETED/BLOCKED_QA/WAITING_INPUT. | Service menjeda job bila loop berhenti; pause hanya untuk status aktif/antre. |
| 8 | Rendah | Angka 5,256,532 vs 5.256.532 ditandai hilang. | Perbandingan berdasarkan nilai EN/ID. |
| 9 | Rendah | Pesan error UI generik ("konfigurasi relay"). | Menampilkan pesan ValueError/Blocked engine (string buatan aplikasi). |

Tes yang diubah karena mengunci perilaku lama: `test_unknown_request_…` (dulu mengharapkan
resume mengirim ulang diam-diam) dan `test_crash_after_success_…` (kini wajib persetujuan).
Bang tetap memblokir; temuan hanya menambah bukti bunyi di sumber dan arahan izinkan_istilah.
