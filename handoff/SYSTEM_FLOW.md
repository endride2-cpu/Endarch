# Alur sistem

User → MainActivity/Compose → Bridge/Room → Engine Python → file workspace & ProviderClient → HTTPS provider → validasi respons & checkpoint → snapshot UI → QA → EPUB/notes.

1. Impor: SAF menyalin EPUB ke penyimpanan privat. Judul/model/config masuk job.json;
   API key berada di prefs terenkripsi Keystore, bukan config.
2. Worker foreground menjalankan satu job. Bridge mengunci mutasi Python. Engine
   menyimpan journal secara atomik; Room menjadi indeks job, task, attempt dan event.
3. preprocess: ekstrak bab/gambar. Jumlah nol gagal. Jika perlu telaah ekstraksi,
   job berhenti WAITING_INPUT, baru persetujuan diizinkan.
4. gloss: seed dan blok tambahan → glosarium/master_full.md.
5. trans: style dan chunk per bab → draf/bab_NNN_draft.md. Setelah selesai menunggu
   persetujuan refinement.
6. refine: parse blok TEKS/GATE/CATATAN → refined/bab_NNN.md + .meta.json.
7. check: bandingkan sumber/raw dan hasil; append bab/gate/masalah/hash ke qa_llm.jsonl.
8. qa: validasi deterministik menggabungkan aturan, metadata refine, dan check.
   qa_report.md untuk manusia dan qa_report.jsonl untuk pembacaan terstruktur.
9. build memeriksa QA lagi. validate memeriksa EPUB, marker hash dan editor_notes.md.
10. Ekspor hanya bila versi hasil masih valid. Berbagi per file melalui tindakan pengguna.

## Timing dan sumber status
UI membaca job.json atomik berkala agar tidak menunggu mutex panggilan model.
Detail bab dimuat lewat bridge. Banner memakai snapshot terbaru, bukan pesan lama.
Verdict makna bukan verdict gabungan QA. qa_current mengikat laporan ke release_key;
review_current mengikat verdict makna ke hash teks final. detail() juga mengirim qa_current
dan reports_stale agar qa_report.md lama diberi label basi. BERSYARAT dari pemeriksa makna
(dengan atau tanpa rincian) menjadi REVIEW di qa_report.jsonl/md, sama dengan state job.
Konfigurasi: receipt yang masih valid dicap ulang dengan config baru; hanya tahap yang
terdampak dibatalkan (check_model → check, izinkan_istilah → qa, preferensi_editorial →
refine, max_tokens → tidak ada, raw_map/min_chars → preprocess dan diblokir setelah draf).
Paralel: trans/refine/check mengirim hingga paralel_bab bab bersamaan (bawaan 3, 1–8; potongan dalam
satu bab tetap berurutan). Journal dikunci per mutasi; tiap bab punya receipt sendiri. Setelah satu
batch, error terminal didahulukan atas Waiting. paralel_bab tidak masuk fingerprint, jadi mengubahnya
tidak menagih ulang.
Respons API disimpan ke checkpoint sebelum attempt ditandai SUCCEEDED. Attempt PENDING/UNKNOWN
tidak dikirim ulang oleh resume; perlu confirm_resend eksplisit. Pause hanya mengubah job yang
sedang berjalan/antre; service yang berhenti di tengah loop menjeda job agar tidak tertinggal RUNNING.
Edit teks membatalkan check/build/validate. Review ulang memakai API dan dapat berbiaya.
BLOCKED_QA tidak menerima resume biasa; pengguna memperbaiki bab atau memilih retry
pemeriksaan secara eksplisit. Persetujuan padanan nama mengubah kebijakan QA per novel,
membatalkan rilis lama dan menjalankan QA lagi tanpa meminta terjemahan baru.

## Batas audit
Tidak ada akun SaaS, multiuser, atau cloud queue. Android foreground-service budget
bisa menghentikan pekerjaan panjang. Request ID lokal (X-Client-Request-Id) hanya korelasi;
provider tidak menjamin idempotensi, sehingga kirim ulang UNKNOWN selalu atas persetujuan pengguna.
