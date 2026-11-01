# Mulai di sini: Novel Translate Android v0.2.5 (masih dikembangkan)

Buka folder ZIP ini sebagai root workspace, bukan file APK. Baca AGENTS.md,
AGENT_START.md, README.md, lalu handoff/AUDIT_SPEC.md dan handoff/SYSTEM_FLOW.md.

## Lokasi kode
- UI Compose: android/app/src/main/java/id/noveltranslate/MainActivity.kt
- Provider API langsung dan Keystore: android/app/src/main/java/id/noveltranslate/RelayClient.kt
  (nama file legacy; kelas aktif bernama ProviderClient).
- Controller, Room dan bridge Python: android/app/src/main/java/id/noveltranslate/Store.kt
- Foreground service/pause: android/app/src/main/java/id/noveltranslate/PipelineService.kt
- State machine delapan tahap, journal/checkpoint: mobile_runtime/engine.py
- Batas JSON Kotlin–Python: mobile_runtime/bridge.py
- Ekstraksi/prompt/build EPUB dan CLI: scripts/pipeline_unified.py
- Validator QA dan pembuat qa_report.md/jsonl: scripts/qa_gate.py
- Kontrak hash/refinement: scripts/publication.py
- Validasi EPUB: scripts/validate_epub.py
- Aturan dan prompt: config/rules.json, prompts/, glossary/global.md
- Tes: tests/ serta android/app/src/androidTest/java/id/noveltranslate/RuntimeSmokeTest.kt

## Keputusan yang berlaku
Android memakai API key dari UI yang dienkripsi Android Keystore, atas perubahan
instruksi pemilik sesudah rancangan awal env-only. CLI dan relay opsional tetap
memakai environment. Jangan memasukkan key ke kode, log, journal atau ZIP.
API utama APK adalah HTTPS OpenAI-compatible; relay/ hanya jalur legacy opsional.

Kontrak draf/, refined/ dan qa_llm.jsonl harus dipertahankan. Jangan membuat tombol
force-pass. False positive nama diselesaikan dengan name_aliases per novel yang
disetujui dengan alasan, bukan menghapus pemeriksaan. Jangan otomatis mengganti
Bang karena bisa onomatope. Pemeriksaan makna dan QA deterministik adalah dua verdict.

## Bukti dan batasnya
handoff/evidence/qa_report.historical.md menyalin seluruh laporan QA yang pernah
diberikan pengguna untuk novel-7928de39d292 (test 2), aplikasi v0.2.0. Ini bukan
laporan terbaru novel bernama test dari video. Contoh glosarium dilabeli jelas.
Snapshot HP terbaru belum ada di filesystem. Ikuti handoff/from_device/README.md;
jangan mengarang log atau menganggap contoh struktur sebagai kejadian nyata.

## Build dan verifikasi
Gunakan JDK 17, Android SDK 35, dan Python build yang cocok dengan Chaquopy (3.11).
cd android && ./gradlew :app:assembleDebug
Untuk emulator x86_64: ./gradlew :app:assembleDebug :app:assembleDebugAndroidTest -PtestAbi=x86_64
Jalankan :app:connectedDebugAndroidTest pada emulator yang cocok.

Tes host: pip install -r requirements.txt -r relay/requirements.txt
python -m unittest discover -s tests -v
Verifikasi terakhir v0.2.5: 77 tes host lulus dan build ARM64 berhasil.
Audit pasca-v0.2.5 (2026-11-01, lihat AUDIT.md): 91 tes host lulus (test_relay tidak dapat
dimuat karena httpx tidak tersedia di lingkungan audit). Kotlin hanya dicek sintaks; APK BELUM
dibangun dan versionCode/versionName belum dinaikkan. Bangun dan uji sebelum distribusi.
Uji instrumen delapan tahap pada emulator lulus sebelumnya (v0.2.2/0.2.3), bukan
bukti bahwa seluruh UI/API nyata v0.2.5 telah diuji. Tidak ada panggilan provider nyata.

## Penandatanganan dan data HP
v0.2.1 lama: sertifikat SHA256 c43fdbf386d4dd31db356f4585ebd74a3cfa078d811747a34836aa2133d10d49
Build v0.2.2 dan berikutnya: de419237c46b805241d058f98bfe31f2665fb88dc7278518f6ca5e8bcf936d71
Kunci lama hilang ketika /tmp direset. APK baru tidak bisa memperbarui instalasi
lama bertanda tangan berbeda. Jangan menyuruh uninstall sebelum menyiapkan dan
memverifikasi cadangan data. Kunci penandatanganan tidak disertakan dalam ZIP.
Keystore Android API key juga tidak boleh diekspor. Rilis produksi belum siap.
