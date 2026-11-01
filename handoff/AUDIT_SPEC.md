# Audit spec: perbaikan minimal dan dapat diverifikasi

Sebelum mengedit: jelaskan flow, expected vs actual, titik divergensi dan bukti.
Bedakan fakta dari dugaan; rekam file/fungsi yang terkait. Hindari mengganti arsitektur.

## Sudah ada di source v0.2.5 — tetap perlu regression audit
- Status lokal hidup, progres tahap dan bab, spinner, label Indonesia.
- Persetujuan ekstraksi/refine hanya saat WAITING_INPUT yang tepat, termasuk backend.
- Banner terbaru; informasi menunggu keputusan berbeda dari FAILED/BLOCKED_QA.
- Header bab menampilkan Makna dan QA; laporan basi diberi label.
- GAGAL/REVIEW membuka Hasil; Draf read-only diberi petunjuk.
- Temuan QA di atas tab. Aksi API di atas teks, dengan dialog konfirmasi.
- Editor hasil dibatasi tinggi agar tombol simpan tidak puluhan layar di bawah.
- Padanan nama SOURCE→TARGET disetujui per novel beserta alasan; QA memeriksa target
  benar-benar ada, temuan lain tetap memblokir; tidak ada blanket override.
- Pratinjau glosarium berbentuk kartu; editor read-only tetap terbaca.
- Form model pemeriksa, max_tokens dan frasa pengecualian; tidak meminta JSON.
- Tarif input/output opsional per sejuta token untuk model tertentu; snapshot tarif
  disimpan pada attempt. Estimasi belum memperhitungkan diskon cache.
- Debug via whitelist, tanpa prosa/glosarium/prompt/key/URL endpoint.

## Kriteria penerimaan kritis
1. Ekstraksi kosong tidak boleh lanjut gloss. Progres UI mencerminkan receipt yang ada.
2. Tombol persetujuan prematur ditolak backend. Konfirmasi biaya tidak mengirim request
   ketika dibatalkan. Edit manual tidak memanggil refine.
3. Makna LULUS + QA GAGAL harus terlihat sebagai dua status. Jangan beri label gabungan
   LULUS bila QA belum ada/basi. Temuan tampil pada bab yang benar.
4. Resume BLOCKED_QA tidak membangun EPUB. Koreksi hasil membatalkan review hash lama.
5. Terjemahan sah Third Imperial Prince → Pangeran Ketiga Kekaisaran dapat memakai
   alias tersetujui. Target hilang/terpotong tetap gagal; flag unresolved tetap gagal.
6. Bang diperiksa dalam sumber dan hasil; jangan menyimpulkan sapaan dari string saja.
   Angka 5,256,532 vs 5.256.532 harus ditelaah dengan nilai yang sama, bukan dihapus.
7. Debug hanya data diagnostik yang diizinkan. Periksa clipboard aktual, bukan label UI.
8. Tarif kosong/usage hilang berarti unknown, bukan nol. Tarif tidak berlaku ke model
   berbeda. Semua biaya harus dilabeli estimasi dengan mata uang.
9. Workspace /data/user/0 dan /data/data harus dinormalkan sebelum relative_to.
10. Job dan data lama tetap terbaca. Jangan migrasi/destruktif tanpa backup terverifikasi.

## Ditambahkan pasca-audit 2026-11-01 (source; belum diuji di emulator/HP)
- Kartu "Temuan QA" menggulir ke bab terkait dan membuka tab Hasil.
- UNKNOWN/PENDING: resume tidak mengirim ulang; kartu peringatan + dialog confirm_resend.
  FAILED dibedakan untuk kegagalan sebelum kirim dan respons 2xx yang tak terbaca.
- Label Makna "(versi lama)" bila hash berubah; laporan QA basi diberi label.
- Konfigurasi membatalkan tahap terdampak saja; retry per bab tidak memakai ulang request ID.
- Temuan Bang menyebut bukti bunyi di sumber/raw, tetap GAGAL sampai frasa ditelaah dan
  didaftarkan di izinkan_istilah. Angka dibandingkan berdasarkan nilai (5,256,532 = 5.256.532).

## Masih terbuka / jangan klaim selesai
- Build APK dan uji UI di emulator/HP untuk perubahan pasca-audit di atas.
- Rekonsiliasi UNKNOWN otomatis tetap tidak mungkin tanpa API provider untuk mencari request;
  pengguna memeriksa dasbor penggunaan secara manual.
- Audit prompt/glosarium EN_TRANSLATED yang malah mendeklarasikan RAW_KOREA dan
  menebak nama Hangul. Jangan menyimpulkan sumber Korea dari tebakan model.
- Migrasi aman data pengguna dari APK lama dengan signing berbeda.
- Tes UI end-to-end di HP Android 16, job panjang, pause/process death, status basi.
- Penyimpanan signing key permanen/aman untuk rilis berikutnya, tanpa memasukkannya
  ke source publik. Backup harus diuji dan dapat dipulihkan.

## Validasi yang harus dicatat agent berikutnya
Laporkan tes yang benar-benar dijalankan, versi APK, ABI, sertifikat signing, serta
apakah API asli atau fake. Jangan memanggil provider berbayar tanpa otorisasi.
Paket ini menyertakan source dan bukti historis; bukan salinan filesystem HP.
