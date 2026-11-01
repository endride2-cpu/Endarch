# Data yang masih perlu diambil dari HP

Tidak ada koneksi ke HP pengguna dalam workspace ini. File berikut sengaja belum
ada agar agent tidak mengira data sintetis/historis sebagai log terbaru:

1. debug_snapshot.latest.json
   Saat job Perlu perbaikan QA: Debug & laporkan masalah → Salin laporan debug.
   Tempel output mentah ke file ini. Sertakan app_version. Periksa lagi tidak ada
   kredensial. Versi lama pernah salah menyalin detail lengkap/glosarium.
2. qa_report.latest.md
   Salin seluruh qa_report.md dari job yang sama, bukan hanya headline error.
3. glossary.latest.md
   Salin glosarium novel yang dipakai pada job itu. Pertahankan baris deklarasi
   sumber aslinya, meskipun salah; jangan ubah RAW_KOREA menjadi EN_TRANSLATED untuk
   menutupi bug. Tuliskan sumber yang dipilih pengguna di laporan terpisah.
4. reproduction.md
   Langkah reproduksi, versi APK/Android, nomor bab, harapan dan perilaku aktual.

Catatan 2026-11-01: screenshot di C:\pro menampilkan format header bab yang lebih lama dari
source v0.2.5, jadi sertakan app_version dari laporan debug agar bukti dapat dicocokkan.

Source terbaru memperkecil laporan debug sehingga tidak berisi glosarium/prosa.
Jangan mengirim API key atau EPUB penuh. Jika perlu contoh kalimat, pilih potongan
minimum yang cukup untuk menguji aturan dan beri label sumber vs hasil.
