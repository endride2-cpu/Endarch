# Novel Translate Android

Single-user APK (Android 10+, ARM64) with eight checkpointed stages. The app keeps EPUBs,
working files, and the Room index in private app storage. It sends gloss, translation, refinement, and semantic-check requests directly to an
OpenAI-compatible HTTPS endpoint. Configure the endpoint, model, and API key in **API
langsung** settings. The API key is encrypted with Android Keystore and is never written
to Python configs or request logs.

Build with JDK 17 and Android SDK 35:

```bash
cd android
./gradlew :app:assembleDebug :app:lintDebug :app:assembleDebugAndroidTest
```

Install `app/build/outputs/apk/debug/app-debug.apk`. For an x86_64 emulator only, add
`-PtestAbi=x86_64`; the default is ARM64. To run the offline device smoke test, install
both `app-debug.apk` and `app-debug-androidTest.apk` from the corresponding output folders,
then run `adb shell am instrument -w id.noveltranslate.test/androidx.test.runner.AndroidJUnitRunner`.
The smoke test runs all eight stages on synthetic content and checks Pillow, EPUB and notes.
It does not contact the relay. An Android device/emulator test is required before production
use. This repository has not been tested with a real provider or long novels on a phone.

In the APK, tap **API langsung**, enter the endpoint base URL, model ID, and API key,
then save. Enter the provider endpoint ending in `/v1` (the app appends
`/chat/completions`). An optional raw EPUB enables BOTH.
Run or pause the job from the novel screen. A pause waits for the active request/checkpoint.
The app asks for extraction review if necessary and for refinement approval after translation.
Warnings require a written review per current version. GAGAL requires correction and
rechecking. Export uses Android's document picker or Sharesheet per file; nothing is
uploaded automatically.

`job.json` and hash receipts are the recovery authority across Python/Android process
death. Room indexes jobs, tasks, attempts and events for the UI; a recovery worker rebuilds
this index on startup and pauses interrupted jobs. The APK processes one paid job at a time.
`draf/`, `refined/`, `qa_llm.jsonl`, QA reports and final EPUB retain the original pipeline
contracts. The EPUB marker means internal structural validation, not EPUBCheck.

Android background limits may stop a foreground service (especially after force-stop or
on Android 15's data sync budget). The service then pauses the job; reopen the APK and resume.

Provider outcomes: FAILED means the call is known not to have produced a usable result
(HTTP error, missing key/endpoint before anything was sent, or an unreadable 2xx body, which
may still have been billed). UNKNOWN/PENDING means the connection dropped or the process died
while waiting, so the provider may have processed and billed it. The journal keeps one local
request ID per call and sends it as `X-Client-Request-Id`, but OpenAI-compatible providers do
**not** deduplicate on it. Resume therefore never re-sends such a call; the user must check the
provider usage history and confirm "Kirim ulang panggilan" explicitly. Responses are written to
the checkpoint before the attempt is marked SUCCEEDED. Read/call timeouts are 5/10 minutes
because non-streaming completions return nothing until generation ends.

Editorial settings invalidate only what they affect: a new checker model re-runs the meaning
check; allowed phrases re-run deterministic QA only; max output tokens apply to later calls and
keep existing results, QA decisions and the validated EPUB. Extraction settings still require a
new novel once translation exists. An explicit per-chapter retry drops only that chapter's cached
responses and never reuses an earlier request ID.

Build output is a debug-signed APK. Set up release signing and test on representative
ARM64 phones before external distribution. App backup and device transfer are excluded
because both novel content and the paired device credential are private.
