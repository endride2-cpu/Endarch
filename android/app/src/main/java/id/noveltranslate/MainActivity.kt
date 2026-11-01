package id.noveltranslate

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import kotlinx.coroutines.*
import org.json.JSONObject
import java.io.File
import java.util.UUID

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme(colorScheme = lightColorScheme()) { Surface { NovelScreen(application as NovelApp) } } }
    }
    private fun startJob(id: String) {
        ContextCompat.startForegroundService(this, Intent(this, PipelineService::class.java).putExtra("id", id))
    }
    private fun pauseJob() { startService(Intent(this, PipelineService::class.java).setAction("pause")) }
    private suspend fun importFile(uri: Uri): String = withContext(Dispatchers.IO) {
        val file = File(cacheDir, "import-${UUID.randomUUID()}.epub")
        try {
            contentResolver.openInputStream(uri)!!.use { input -> file.outputStream().use { out ->
                val buffer = ByteArray(8192); var total = 0L
                while (true) {
                    val count = input.read(buffer); if (count < 0) break
                    total += count; require(total <= 128L * 1024 * 1024) { "EPUB melebihi 128 MiB" }
                    out.write(buffer, 0, count)
                }
            } }
            file.absolutePath
        } catch (e: Exception) { file.delete(); throw e }
    }

    @Composable
    private fun NovelScreen(app: NovelApp) {
        val scope = rememberCoroutineScope()
        val jobs by app.db.jobs().observe().collectAsState(initial = emptyList())
        var selected by rememberSaveable { mutableStateOf<String?>(null) }
        var detail by remember { mutableStateOf<JSONObject?>(null) }
        var live by remember { mutableStateOf<JSONObject?>(null) }
        var message by remember { mutableStateOf("") }
        var working by remember { mutableStateOf(false) }
        var showSetup by rememberSaveable { mutableStateOf(false) }
        var title by rememberSaveable { mutableStateOf("") }
        var model by rememberSaveable { mutableStateOf(getPreferences(MODE_PRIVATE).getString("model", "") ?: "") }
        var source by rememberSaveable { mutableStateOf<String?>(null) }
        var raw by rememberSaveable { mutableStateOf<String?>(null) }
        var kind by rememberSaveable { mutableStateOf("EN_TRANSLATED") }
        var exportPath by rememberSaveable { mutableStateOf<String?>(null) }
        // Unsaved chapter/glossary edits, owned here so they survive LazyColumn scroll-off.
        // shortcut: kept out of the saved-state Bundle (texts can exceed its size limit), so process death loses them; persist drafts to disk if that matters.
        val edits = remember(selected) { mutableStateMapOf<String, String>() }
        var isRunning by remember { mutableStateOf(PipelineService.running) }
        val listState = rememberLazyListState()
        var focus by remember { mutableStateOf<Pair<Int, Long>?>(null) }  // chapter + click token
        var confirmResend by remember { mutableStateOf(false) }
        val notifyPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
        val epubPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) scope.launch {
                try { source = importFile(uri); message = "EPUB sumber siap" }
                catch (_: Exception) { message = "Impor gagal: periksa format/ukuran file" }
            }
        }
        val rawPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
            if (uri != null) scope.launch {
                try { raw = importFile(uri); kind = "BOTH"; message = "EPUB raw siap" }
                catch (_: Exception) { message = "Impor raw gagal" }
            }
        }
        val exportPicker = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
            val path = exportPath
            if (uri != null && path != null) scope.launch(Dispatchers.IO) {
                try {
                    contentResolver.openOutputStream(uri, "w")!!.use { out -> File(path).inputStream().use { it.copyTo(out) } }
                    withContext(Dispatchers.Main) { message = "File disimpan" }
                } catch (_: Exception) { withContext(Dispatchers.Main) { message = "Ekspor gagal" } }
            }
        }
        fun perform(block: suspend () -> Unit) {
            scope.launch {
                working = true
                try { block() }
                catch (e: Exception) { message = actionError(e) }
                finally { working = false }
            }
        }
        fun command(action: String, data: JSONObject = JSONObject()) {
            val id = selected ?: return
            if (PipelineService.running) { message = "Jeda job sebelum mengubah dokumen"; return }
            perform {
                app.bridge.call(action, id, data)
                detail = app.bridge.call("detail", id)
                message = "Tersimpan; lanjutkan job bila sudah siap"
            }
        }
        LaunchedEffect(Unit) {
            if (Build.VERSION.SDK_INT >= 33) notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
            while (isActive) { isRunning = PipelineService.running; delay(500) }
        }
        LaunchedEffect(selected, jobs.firstOrNull { it.id == selected }?.updatedAt) {
            val id = selected
            if (id != null) try { detail = app.bridge.call("detail", id) }
                catch (e: CancellationException) { throw e }
                catch (e: Exception) { message = actionError(e) }
        }
        BackHandler(enabled = selected != null) { selected = null; detail = null }
        LaunchedEffect(selected) {
            val id = selected
            live = null
            focus = null
            message = ""
            while (id != null && isActive) {
                live = withContext(Dispatchers.IO) {
                    runCatching { JSONObject(File(filesDir, "novels/$id/job.json").readText()) }.getOrNull()
                }
                delay(1000)
            }
        }
        if (confirmResend) AlertDialog(onDismissRequest = { confirmResend = false },
            title = { Text("Kirim ulang panggilan API?") },
            text = { Text("Jika provider sudah memproses panggilan sebelumnya, biaya bisa tertagih dua kali. Lanjutkan hanya bila riwayat penggunaan provider tidak menunjukkan panggilan itu selesai.") },
            confirmButton = { TextButton(onClick = { confirmResend = false; command("confirm_resend") }) { Text("Ya, izinkan kirim ulang") } },
            dismissButton = { TextButton(onClick = { confirmResend = false }) { Text("Batal") } })
        LazyColumn(state = listState, modifier = Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item {
                Spacer(Modifier.height(8.dp))
                Text("Novel Translate", style = MaterialTheme.typography.headlineMedium)
                Text("Pustaka pribadi · delapan tahap · review wajib", style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = { showSetup = !showSetup }, enabled = !isRunning) { Text("API langsung") }
            }
            if (showSetup) item { ProviderSetup { message = it; showSetup = false } }
            if (message.isNotBlank()) item { Text("$message  ✕", color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.fillMaxWidth().clickable(onClickLabel = "Tutup pesan") { message = "" }) }
            if (working) item { LinearProgressIndicator(Modifier.fillMaxWidth()) }
            if (selected == null) {
                item {
                    Card {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("Novel baru", style = MaterialTheme.typography.titleLarge)
                            OutlinedTextField(title, { title = it }, label = { Text("Judul Indonesia") }, modifier = Modifier.fillMaxWidth())
                            OutlinedTextField(model, { model = it }, label = { Text("ID model") }, modifier = Modifier.fillMaxWidth())
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                OutlinedButton(onClick = { epubPicker.launch(arrayOf("application/epub+zip", "application/octet-stream", "application/zip")) }) { Text(if (source == null) "Pilih EPUB" else "Ganti EPUB") }
                                OutlinedButton(onClick = { rawPicker.launch(arrayOf("application/epub+zip", "application/octet-stream", "application/zip")) }) { Text(if (raw == null) "Raw opsional" else "Raw siap") }
                            }
                            Row {
                                FilterChip(selected = kind == "EN_TRANSLATED", onClick = { kind = "EN_TRANSLATED"; raw = null }, label = { Text("Sumber EN") })
                                Spacer(Modifier.width(8.dp))
                                FilterChip(selected = kind == "RAW_KOREA", onClick = { kind = "RAW_KOREA"; raw = null }, label = { Text("Raw Korea") })
                            }
                            Text("Teks bab dikirim langsung ke endpoint pilihanmu. API key disimpan terenkripsi di ponsel.", style = MaterialTheme.typography.bodySmall)
                            Button(enabled = source != null && title.isNotBlank() && model.isNotBlank() && !working && !isRunning, onClick = {
                                perform {
                                    getPreferences(MODE_PRIVATE).edit().putString("model", model).apply()
                                    val data = JSONObject().put("title", title).put("model", model).put("source", source).put("source_kind", kind)
                                    raw?.let { data.put("raw", it) }
                                    val created = app.bridge.call("create", data = data)
                                    selected = created.getString("id")
                                    source?.let { File(it).delete() }; raw?.let { File(it).delete() }
                                    source = null; raw = null; title = ""
                                    detail = app.bridge.call("detail", selected!!)
                                }
                            }) { Text("Buat novel") }
                        }
                    }
                }
                items(jobs, key = { it.id }) { job ->
                    OutlinedCard(onClick = { selected = job.id; detail = null }, modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(job.title, style = MaterialTheme.typography.titleMedium)
                            Text("${stageLabel(job.stage)} · ${statusLabel(job.status)}")
                        }
                    }
                }
            } else {
                val job = jobs.firstOrNull { it.id == selected }
                item {
                    TextButton(onClick = { selected = null; detail = null }) { Text("← Pustaka") }
                    Text(job?.title ?: "Memuat…", style = MaterialTheme.typography.headlineSmall)
                    val shownStatus = live?.optString("status") ?: job?.status.orEmpty()
                    // QUEUED only means "ready"; nothing runs until the worker is started.
                    Text("${stageLabel(live?.optString("stage") ?: job?.stage.orEmpty())} · ${statusLabel(if (shownStatus == "QUEUED" && !isRunning) "READY" else shownStatus)}")
                    if (isRunning && PipelineService.currentId == selected) {
                        LinearProgressIndicator(Modifier.fillMaxWidth())
                        val attempts = live?.optJSONArray("attempts")
                        val activeTask = if (attempts != null && attempts.length() > 0) attempts.optJSONObject(attempts.length() - 1) else null
                        val unit = activeTask?.takeIf { it.optString("status") == "PENDING" }?.optString("task")
                        Text(if (unit != null) "${taskLabel(unit)}; panggilan model dapat memerlukan beberapa menit." else "Sedang memproses; harap tunggu.")
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(enabled = !working && !isRunning && job?.status !in listOf("COMPLETED", "BLOCKED_QA"), onClick = { startJob(selected!!) }) { Text("Jalankan / lanjut") }
                        // pauseJob() pauses whatever the service runs, so only offer it on that job's page.
                        OutlinedButton(enabled = isRunning && PipelineService.currentId == selected, onClick = { pauseJob(); message = "Jeda diminta; menunggu panggilan aktif/checkpoint" }) { Text("Jeda") }
                    }
                    if (isRunning && PipelineService.currentId != selected)
                        Text("Novel lain sedang berjalan. Jeda dulu dari halaman novel itu.", style = MaterialTheme.typography.bodySmall)
                }
                val state = detail
                if (state != null) {
                    val visibleState = live ?: state
                    val error = visibleState.optString("error", "")
                    if (error.isNotBlank() && error != "null") item { Text(error, color = if (visibleState.optString("status") in listOf("FAILED", "BLOCKED_QA")) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary) }
                    item {
                        val tasks = live?.optJSONObject("receipts") ?: state.optJSONObject("receipts") ?: JSONObject()
                        val extraction = tasks.optJSONObject("preprocess")?.optJSONObject("outputs")
                        val extracted = extraction?.keys()?.asSequence()?.count { it.matches(Regex(".*/teks/bab_[0-9]+\\.txt")) } ?: 0
                        val chapterCount = maxOf(extracted, state.getJSONArray("chapters").length())
                        Text("Progres $chapterCount bab", style = MaterialTheme.typography.titleMedium)
                        for (stage in listOf("preprocess", "gloss", "trans", "refine", "check", "qa", "build", "validate")) {
                            val count = tasks.keys().asSequence().count { it == stage || it.matches(Regex("$stage:[0-9]+")) }
                            val active = (live?.optString("stage") ?: job?.stage) == stage
                            val icon = if (active && isRunning) "⏳" else if (count > 0) "✓" else "○"
                            Text("$icon ${stageLabel(stage)} · $count selesai")
                        }
                    }
                    if (!isRunning && state.optString("status") == "WAITING_INPUT") item {
                        when (state.optString("stage")) {
                            "preprocess" -> OutlinedButton(onClick = { command("approve_extraction") }) { Text("Saya sudah memeriksa hasil ekstraksi") }
                            "trans" -> if (state.optString("error").contains("refinement", ignoreCase = true))
                                OutlinedButton(onClick = { command("approve_refine") }) { Text("Lanjutkan penyuntingan") }
                        }
                    }
                    val attemptsNow = (live ?: state).optJSONArray("attempts")
                    val unresolved = attemptsNow != null && (0 until attemptsNow.length()).any {
                        attemptsNow.getJSONObject(it).optString("status") in listOf("UNKNOWN", "PENDING") }
                    if (!isRunning && unresolved) item {
                        Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) {
                            Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                Text("Status panggilan API belum diketahui", style = MaterialTheme.typography.titleSmall)
                                Text("Koneksi terputus atau aplikasi berhenti saat menunggu provider. Provider mungkin sudah memproses dan menagih panggilan ini; aplikasi tidak mengirim ulang tanpa persetujuan. Periksa riwayat penggunaan di dasbor provider terlebih dahulu.")
                                OutlinedButton(enabled = !working, onClick = { confirmResend = true }) { Text("Kirim ulang panggilan…") }
                            }
                        }
                    }
                    val reports = state.optJSONObject("reports") ?: JSONObject()
                    val staleReports = state.optJSONArray("reports_stale")?.let { a -> (0 until a.length()).map { a.getString(it) } }.orEmpty()
                    items(reports.keys().asSequence().toList()) { name ->
                        ExpandableText(if (name in staleReports) "$name (basi — bukan hasil QA aktif)" else name, reports.getString(name))
                    }
                    val chapterList = state.getJSONArray("chapters").let { a -> (0 until a.length()).map { a.getJSONObject(it) } }
                    val flagged = chapterList.filter { it.optJSONObject("qa")?.optString("status") in listOf("GAGAL", "REVIEW") }
                    if (flagged.isNotEmpty()) item {
                        Card { Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                            Text(if (state.optBoolean("qa_current")) "Temuan QA — ketuk untuk membuka Hasil bab"
                                 else "Temuan QA dari laporan terakhir (basi; jalankan ulang setelah perbaikan)", style = MaterialTheme.typography.titleSmall)
                            flagged.forEach { chapter ->
                                val n = chapter.getInt("number")
                                val qa = chapter.getJSONObject("qa")
                                val first = qa.optJSONArray("issues")?.optString(0).orEmpty()
                                TextButton(onClick = {
                                    focus = n to System.nanoTime()
                                    // Chapters are followed by exactly TAIL_ITEMS items (see below).
                                    val index = listState.layoutInfo.totalItemsCount - TAIL_ITEMS - chapterList.size + chapterList.indexOf(chapter)
                                    scope.launch { listState.animateScrollToItem(index.coerceAtLeast(0)) }
                                }) { Text("Bab $n · QA ${qa.optString("status")}: ${first.take(100)}") }
                            }
                        } }
                    }
                    item { GlossaryEditor(state, !isRunning && !working, edits, { name ->
                        app.bridge.call("history", selected!!, JSONObject().put("name", name)).getString("text")
                    }) { command("save_glossary", it) } }
                    items(chapterList, key = { it.getInt("number") }) { chapter ->
                        ChapterCard(chapter, !isRunning && !working, focus?.takeIf { it.first == chapter.getInt("number") }?.second, edits, {
                            app.bridge.call("chapter", selected!!, JSONObject().put("chapter", chapter.getInt("number")))
                        }, { action, data -> command(action, data) })
                    }
                    // Keep everything after the chapter list in ONE item (TAIL_ITEMS relies on it).
                    item { Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    UsageCard(state)
                    DebugCard(JSONObject(state.toString()).apply { live?.let { put("status", it.optString("status")); put("error", it.opt("error")) } }, !working && !isRunning) { _ ->
                        perform {
                            val safe = app.bridge.call("debug", selected!!).put("android_version", Build.VERSION.RELEASE)
                                .put("android_sdk", Build.VERSION.SDK_INT)
                                .put("device_model", Build.MANUFACTURER + " " + Build.MODEL)
                                .put("app_version", packageManager.getPackageInfo(packageName, 0).versionName)
                            val clipboard = getSystemService(android.content.ClipboardManager::class.java)
                            clipboard.setPrimaryClip(android.content.ClipData.newPlainText("Diagnostic report", safe.toString(2)))
                            message = "Laporan debug disalin. Tinjau lalu kirim tanpa API key, prompt, atau isi novel."
                        }
                    }

                    ConfigEditor(state, !isRunning && !working) { command("configure", it) }
                    if (job?.status == "COMPLETED") Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Validasi struktural internal lulus", style = MaterialTheme.typography.titleMedium)
                        Text("Bukan sertifikasi EPUBCheck. Pilih satu file untuk setiap ekspor atau berbagi.")
                        for (kindName in listOf("epub", "notes")) {
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(onClick = { perform {
                                    val path = app.bridge.call("export", selected!!, JSONObject().put("kind", kindName)).getString("path")
                                    exportPath = path
                                    exportPicker.launch(File(path).name)
                                } }) { Text("Simpan $kindName") }
                                OutlinedButton(onClick = { perform {
                                    val path = app.bridge.call("export", selected!!, JSONObject().put("kind", kindName)).getString("path")
                                    val directory = File(cacheDir, "exports").apply { mkdirs() }
                                    val copy = File(directory, File(path).name)
                                    withContext(Dispatchers.IO) { File(path).copyTo(copy, overwrite = true) }
                                    val uri = FileProvider.getUriForFile(this@MainActivity, "$packageName.files", copy)
                                    startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType(if (kindName == "epub") "application/epub+zip" else "text/markdown")
                                        .putExtra(Intent.EXTRA_STREAM, uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION), "Bagikan file ini"))
                                } }) { Text("Bagikan $kindName…") }
                            }
                        }
                    }
                    } }
                }
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }

    @Composable
    private fun ProviderSetup(done: (String) -> Unit) {
        val provider = remember { ProviderClient(this) }
        var url by remember { mutableStateOf(provider.endpoint()) }
        var model by remember { mutableStateOf(provider.model()) }
        var secret by remember { mutableStateOf("") }
        val oldPrice = remember { provider.pricing().takeIf { it.isNotBlank() }?.let { JSONObject(it) } }
        var inputPrice by remember { mutableStateOf(oldPrice?.optString("input_per_million") ?: "") }
        var outputPrice by remember { mutableStateOf(oldPrice?.optString("output_per_million") ?: "") }
        var currency by remember { mutableStateOf(oldPrice?.optString("currency") ?: "USD") }
        Card { Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Endpoint API OpenAI-compatible", style = MaterialTheme.typography.titleMedium)
            Text("Masukkan base URL HTTPS, ID model, dan API key. Key disimpan terenkripsi dengan Android Keystore.")
            OutlinedTextField(url, { url = it }, label = { Text("https://api.example.com/v1") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(model, { model = it }, label = { Text("ID model") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(secret, { secret = it }, label = { Text("API key") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            Text("Tarif opsional per 1 juta token. Kosongkan keduanya bila belum tahu. Estimasi hanya berlaku untuk model ini dan belum menghitung diskon cache.")
            OutlinedTextField(inputPrice, { inputPrice = it }, label = { Text("Tarif input") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(outputPrice, { outputPrice = it }, label = { Text("Tarif output") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(currency, { currency = it.uppercase() }, label = { Text("Mata uang, contoh USD atau IDR") }, modifier = Modifier.fillMaxWidth())
            Button(enabled = url.isNotBlank() && model.isNotBlank() && secret.isNotBlank(), onClick = {
                try {
                    provider.savePricing(model, inputPrice, outputPrice, currency)
                    provider.save(url, model, secret)
                    getPreferences(MODE_PRIVATE).edit().putString("model", model).apply()
                    secret = ""
                    done("Endpoint tersimpan. Key dienkripsi di perangkat.")
                } catch (_: Exception) { done("Simpan gagal. Pastikan URL HTTPS dan field terisi.") }
            }) { Text("Simpan endpoint dan key") }
        } }
    }
}

@Composable
private fun ExpandableText(title: String, text: String) {
    var open by rememberSaveable(title) { mutableStateOf(false) }
    Column { TextButton(onClick = { open = !open }) { Text(title + if (open) " −" else " +") }
        if (open && title.startsWith("Pratinjau glosarium") || open && title.startsWith("Glosarium global")) {
            var headers = emptyList<String>()
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                text.lineSequence().forEach { line ->
                    val clean = line.trim()
                    if (clean.startsWith("|") && clean.endsWith("|")) {
                        val cells = clean.trim('|').split('|').map { it.trim() }
                        if (cells.all { it.matches(Regex(":?-+:?")) }) return@forEach
                        if (headers.isEmpty()) headers = cells
                        else Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(10.dp)) {
                            cells.forEachIndexed { i, value -> Text("${headers.getOrElse(i) { "" }}: $value", style = MaterialTheme.typography.bodySmall) }
                        } }
                    } else {
                        if (clean.startsWith("#")) { headers = emptyList(); Text(clean.trimStart('#').trim(), style = MaterialTheme.typography.titleSmall) }
                        else if (clean.isNotEmpty()) Text(clean, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        } else if (open) SelectionContainer { Text(text, style = MaterialTheme.typography.bodySmall) }
    }
}

@Composable
private fun GlossaryEditor(state: JSONObject, enabled: Boolean, edits: MutableMap<String, String>, loadHistory: suspend (String) -> String, save: (JSONObject) -> Unit) {
    var open by rememberSaveable { mutableStateOf(false) }
    val hash = state.optString("glossary_hash", "null")
    val editKey = "gloss:$hash"
    val text = edits[editKey] ?: state.optString("glossary")
    var pendingRevision by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    fun loadRevision(name: String) { scope.launch { runCatching { edits[editKey] = loadHistory(name) } } }
    if (pendingRevision != null) AlertDialog(onDismissRequest = { pendingRevision = null },
        title = { Text("Buang perubahan yang belum disimpan?") },
        text = { Text("Memuat revisi lama akan menggantikan teks glosarium yang sedang kamu sunting.") },
        confirmButton = { TextButton(onClick = { loadRevision(pendingRevision!!); pendingRevision = null }) { Text("Muat revisi") } },
        dismissButton = { TextButton(onClick = { pendingRevision = null }) { Text("Batal") } })
    Card { Column(Modifier.padding(12.dp)) {
        TextButton(onClick = { open = !open }) { Text("Glosarium novel · editor Markdown") }
        if (open) {
            ExpandableText("Glosarium global (hanya baca)", state.optString("global_glossary"))
            val history = state.optJSONArray("glossary_history")
            if (history != null) for (i in 0 until history.length()) {
                TextButton(enabled = enabled, onClick = {
                    val name = history.getString(i)
                    if (editKey in edits) pendingRevision = name else loadRevision(name)
                }) {
                    Text("Muat revisi ${history.getString(i).substringBefore('-')}")
                }
            }
            ExpandableText("Pratinjau glosarium novel", text)
            Text("Versi novel menimpa glosarium global. Perubahan membatalkan hasil bahasa dan review terkait.")
            OutlinedTextField(text, { edits[editKey] = it }, readOnly = !enabled, modifier = Modifier.fillMaxWidth().heightIn(min = 180.dp, max = 400.dp))
            Button(enabled = enabled, onClick = { save(JSONObject().put("text", text).put("expected_hash", if (hash == "null") JSONObject.NULL else hash)) }) { Text("Simpan revisi glosarium") }
        }
    } }
}

@Composable
private fun ChapterCard(chapter: JSONObject, enabled: Boolean, focusKey: Long?, edits: MutableMap<String, String>, load: suspend () -> JSONObject, command: (String, JSONObject) -> Unit) {
    val n = chapter.getInt("number")
    var open by rememberSaveable(n) { mutableStateOf(false) }
    var tab by rememberSaveable(n) { mutableStateOf(if (chapter.optJSONObject("qa")?.optString("status") in listOf("GAGAL", "REVIEW")) 2 else 0) }
    val hash = chapter.optString("refined_hash", "null")
    var content by remember(n, hash) { mutableStateOf<JSONObject?>(null) }
    // An unsaved edit wins over a reload; a saved revision changes the hash and starts clean.
    val editKey = "bab:$n:$hash"
    val text = edits[editKey] ?: content?.optString("refined").orEmpty()
    LaunchedEffect(open, hash) {
        if (open) {
            try { content = load() }
            catch (_: Exception) { content = null }
        }
    }
    LaunchedEffect(focusKey) { if (focusKey != null) { open = true; tab = 2 } }
    var note by rememberSaveable(n) { mutableStateOf("") }
    var retryStage by remember(n) { mutableStateOf<String?>(null) }
    var aliasSource by rememberSaveable(n) { mutableStateOf("") }
    var aliasTarget by rememberSaveable(n) { mutableStateOf("") }
    var aliasReason by rememberSaveable(n) { mutableStateOf("") }
    if (retryStage != null) AlertDialog(onDismissRequest = { retryStage = null },
        title = { Text("Jalankan ulang dengan API?") },
        text = { Text("Tindakan ini dapat memakai token dan membatalkan pemeriksaan terkait. Untuk koreksi manual, pilih tab Hasil lalu simpan revisi.") },
        confirmButton = { TextButton(onClick = { val stage = retryStage!!; retryStage = null; command("retry", JSONObject().put("stage", stage).put("chapter", n)) }, enabled = enabled) { Text("Ya, jalankan ulang") } },
        dismissButton = { TextButton(onClick = { retryStage = null }) { Text("Batal") } })
    Card { Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        // Two separate verdicts: meaning check (per exact text hash) and combined deterministic QA.
        val reviewCurrent = chapter.optBoolean("review_current", true)
        TextButton(onClick = { open = !open }) { Text("Bab $n · Makna: ${chapter.optJSONObject("review")?.optString("gate")?.let { if (reviewCurrent) it else "$it (versi lama)" } ?: "belum ada"} | QA: ${chapter.optJSONObject("qa")?.optString("status")?.let { if (chapter.optBoolean("qa_current")) it else "$it (laporan terakhir)" } ?: "QA belum dijalankan"}") }
        if (open) {
            Text("Refinement: ${chapter.optJSONObject("meta")?.optString("gate") ?: "belum ada"}")
            chapter.optJSONObject("review")?.let {
                Text("Pemeriksaan makna: ${it.optString("gate")}" + if (reviewCurrent) "" else " — untuk teks sebelum revisi; jalankan Periksa makna", style = MaterialTheme.typography.bodySmall)
                val issues = it.optJSONArray("masalah")
                if (issues != null) for (i in 0 until issues.length()) Text(issues.optString(i))
            }
            chapter.optJSONObject("qa")?.optJSONArray("issues")?.let { issues ->
                Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)) { Column(Modifier.padding(10.dp)) {
                    Text("Temuan QA")
                    for (i in 0 until issues.length()) Text(issues.optString(i))
                } }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(enabled = enabled, onClick = { retryStage = "refine" }) { Text("Sunting ulang via API…") }
                OutlinedButton(enabled = enabled, onClick = { retryStage = "check" }) { Text("Periksa makna via API…") }
            }
            Text("Edit manual: pilih Hasil. Draf adalah salinan awal dan hanya-baca.", style = MaterialTheme.typography.bodySmall)
            TabRow(selectedTabIndex = tab) { listOf("Sumber/raw", "Draf", "Hasil").forEachIndexed { i, name ->
                Tab(selected = tab == i, onClick = { tab = i }, text = { Text(name) })
            } }
            when (tab) {
                0 -> SelectionContainer { Text(content?.optString("raw")?.ifBlank { content?.optString("source") ?: "" } ?: "Memuat sumber…") }
                1 -> SelectionContainer { Text(content?.optString("draft") ?: "Memuat draf…") }
                2 -> {
                    // readOnly (not disabled) keeps the text legible while a job runs.
                    OutlinedTextField(text, { edits[editKey] = it }, readOnly = !(enabled && hash != "null" && content != null), modifier = Modifier.fillMaxWidth().heightIn(min = 200.dp, max = 480.dp))
                    Button(enabled = enabled && hash != "null" && content != null, onClick = { command("save_chapter", JSONObject().put("chapter", n).put("text", text).put("expected_hash", hash)) }) { Text("Simpan revisi & batalkan review lama") }
                }
            }
            if (chapter.optJSONObject("qa")?.optString("status") == "GAGAL") {
                Text("Jika nama sudah diterjemahkan dengan benar, setujui padanannya untuk novel ini. Pemeriksaan QA tetap dijalankan.")
                OutlinedTextField(aliasSource, { aliasSource = it }, label = { Text("Nama di sumber, persis seperti laporan QA") }, readOnly = !enabled, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(aliasTarget, { aliasTarget = it }, label = { Text("Padanan yang benar dalam hasil") }, readOnly = !enabled, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(aliasReason, { aliasReason = it }, label = { Text("Alasan setelah membandingkan sumber dan hasil") }, readOnly = !enabled, modifier = Modifier.fillMaxWidth())
                Button(enabled = enabled && aliasSource.isNotBlank() && aliasTarget.isNotBlank() && aliasReason.isNotBlank(), onClick = {
                    command("approve_name_alias", JSONObject().put("source", aliasSource).put("target", aliasTarget).put("reason", aliasReason))
                }) { Text("Setujui padanan & siapkan QA ulang") }
            }
            OutlinedTextField(note, { note = it }, enabled = enabled, label = { Text("Catatan telaah peringatan (bukan override GAGAL)") }, modifier = Modifier.fillMaxWidth())
            Button(enabled = enabled && note.isNotBlank(), onClick = { command("decide", JSONObject().put("chapter", "bab_%03d".format(n)).put("note", note)) }) { Text("Catat telaah") }
        }
    } }
}

@Composable
private fun UsageCard(state: JSONObject) {
    val attempts = state.optJSONArray("attempts") ?: return
    var open by rememberSaveable { mutableStateOf(false) }
    val rows = (0 until attempts.length()).map { attempts.getJSONObject(it) }
    val known = rows.filter { !it.isNull("cost") }.groupBy { it.optJSONObject("price_snapshot")?.optString("currency")?.takeIf(String::isNotBlank) ?: "mata uang tidak diketahui" }
    Card { Column(Modifier.padding(12.dp)) {
        Text("Biaya & retry", style = MaterialTheme.typography.titleMedium)
        Text("${rows.size} percobaan · ${rows.count { it.isNull("cost") }} biaya belum diketahui")
        known.forEach { (currency, calls) -> Text("Estimasi $currency ${"%.4f".format(calls.sumOf { it.optDouble("cost", 0.0) })}") }
        Text("Biaya hanya ditampilkan jika tarif tersedia.", style = MaterialTheme.typography.bodySmall)
        TextButton(onClick = { open = !open }) { Text("Detail per bab/tahap") }
        if (open) rows.groupBy { it.getString("task") }.forEach { (task, calls) ->
            Text("$task · ${calls.size} percobaan", style = MaterialTheme.typography.titleSmall)
            calls.forEach { Text("${it.optString("model")} · ${it.optString("status")} · ${tokenLabel(it.optJSONObject("usage"))} · biaya ${if (it.isNull("cost")) "belum diketahui" else "%.4f".format(it.optDouble("cost"))} ${it.optJSONObject("price_snapshot")?.optString("currency") ?: ""}", style = MaterialTheme.typography.bodySmall) }
        }
    } }
}

@Composable
private fun ConfigEditor(state: JSONObject, enabled: Boolean, save: (JSONObject) -> Unit) {
    var open by rememberSaveable { mutableStateOf(false) }
    val current = state.getJSONObject("config")
    var model by rememberSaveable(state.getString("id")) { mutableStateOf(current.optString("check_model")) }
    var tokens by rememberSaveable(state.getString("id")) { mutableStateOf(current.optInt("max_tokens", 20000).toString()) }
    var parallel by rememberSaveable(state.getString("id")) { mutableStateOf(current.optInt("paralel_bab", 3).toString()) }
    var terms by rememberSaveable(state.getString("id")) { mutableStateOf(current.optJSONArray("izinkan_istilah")?.let { a -> (0 until a.length()).joinToString("\n") { a.optString(it) } } ?: "") }
    var error by remember { mutableStateOf("") }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(onClick = { open = !open }) { Text("Pengaturan editorial") }
        if (open) {
            Text("Model pemeriksa baru: pemeriksaan makna diulang (memakai API). Frasa diizinkan: hanya QA ulang, tanpa API. Batas token: berlaku untuk panggilan berikutnya, hasil yang ada tetap. Bab paralel: jumlah bab yang dikirim bersamaan; turunkan bila provider sering menolak karena batas laju (HTTP 429). Frasa harus spesifik dan sudah ditelaah.")
            OutlinedTextField(model, { model = it }, readOnly = !enabled, label = { Text("Model pemeriksa") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(tokens, { tokens = it }, readOnly = !enabled, label = { Text("Batas token keluaran") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(parallel, { parallel = it }, readOnly = !enabled, label = { Text("Bab paralel (1–8)") }, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(terms, { terms = it }, readOnly = !enabled, label = { Text("Frasa yang diizinkan, satu per baris") }, modifier = Modifier.fillMaxWidth())
            if (error.isNotBlank()) Text(error, color = MaterialTheme.colorScheme.error)
            Button(enabled = enabled, onClick = {
                val count = tokens.toIntOrNull()
                val chapters = parallel.toIntOrNull()
                if (model.isBlank() || count == null || count !in 1..32768) error = "Isi model dan batas token antara 1–32768."
                else if (chapters == null || chapters !in 1..8) error = "Bab paralel harus 1–8."
                else {
                    save(JSONObject().put("check_model", model.trim()).put("max_tokens", count).put("paralel_bab", chapters)
                        .put("izinkan_istilah", org.json.JSONArray(terms.lines().map { it.trim() }.filter { it.isNotEmpty() })))
                    error = ""
                }
            }) { Text("Simpan konfigurasi") }
        }
    }
}

@Composable
private fun DebugCard(state: JSONObject, enabled: Boolean, copy: (JSONObject) -> Unit) {
    var open by rememberSaveable { mutableStateOf(false) }
    Card { Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        TextButton(onClick = { open = !open }) { Text("Debug & laporkan masalah") }
        if (open) {
            Text("Salin snapshot status, progres bab, error, attempt dan retry. Laporan tidak berisi teks novel, prompt, API key, atau URL endpoint.")
            val tasks = state.optJSONObject("tasks") ?: JSONObject()
            val receipts = state.optJSONObject("receipts") ?: JSONObject()
            Text("Job ${state.optString("status")} · tahap ${state.optString("stage")} · ${state.optJSONArray("chapters")?.length() ?: 0} bab · ${tasks.length()} task · ${state.optJSONArray("attempts")?.length() ?: 0} attempt · ${receipts.length()} checkpoint")
            val error = state.optString("error").takeIf { it != "null" && it.isNotBlank() }
            if (error != null) Text("${if (state.optString("status") in listOf("FAILED", "BLOCKED_QA")) "Kesalahan" else "Informasi"}: $error")
            Button(enabled = enabled, onClick = { copy(JSONObject(state.toString())) }) { Text("Salin laporan debug") }
        }
    } }
}

// Items rendered after the chapter list: the merged usage/debug/config/export item + final spacer.
private const val TAIL_ITEMS = 2

private fun actionError(e: Exception): String {
    // Engine ValueError/Blocked/Waiting messages are written for the user and carry no secrets.
    val first = e.message?.lineSequence()?.firstOrNull().orEmpty()
    return Regex("^(?:[\\w.]+\\.)?(?:ValueError|Blocked|Waiting): (.{1,240})").find(first)?.groupValues?.get(1)
        ?: "Aksi gagal. Periksa status job, versi dokumen, dan pengaturan API."
}

private fun stageLabel(stage: String): String = when (stage) {
    "preprocess" -> "Persiapan file"; "gloss" -> "Glosarium"; "trans" -> "Penerjemahan"
    "refine" -> "Penyuntingan"; "check" -> "Pemeriksaan makna"; "qa" -> "Pemeriksaan mutu"
    "build" -> "Pembuatan EPUB"; "validate" -> "Validasi EPUB"; else -> stage
}
private fun statusLabel(status: String): String = when (status) {
    "QUEUED" -> "Dalam antrean"; "READY" -> "Siap dijalankan"; "RUNNING" -> "Sedang berjalan"; "PAUSED" -> "Dijeda"; "PAUSE_REQUESTED" -> "Menjeda…"
    "WAITING_INPUT" -> "Menunggu keputusan"; "BLOCKED_QA" -> "Perlu perbaikan QA"
    "FAILED" -> "Gagal"; "COMPLETED" -> "Selesai"; "RETRY_WAIT" -> "Menunggu percobaan ulang"
    else -> status
}

private fun taskLabel(task: String): String {
    val parts = task.split(":")
    val stage = stageLabel(parts.first())
    return when {
        parts.getOrNull(1) == "style" -> "Menyusun panduan gaya"
        parts.first() == "gloss" -> "$stage · blok ${(parts.getOrNull(1)?.toIntOrNull() ?: 0) + 1}"
        parts.getOrNull(2) == "chunk" -> "$stage · Bab ${parts[1]} · Bagian ${(parts.getOrNull(3)?.toIntOrNull() ?: 0) + 1}"
        parts.getOrNull(1)?.toIntOrNull() != null -> "$stage · Bab ${parts[1]}"
        else -> stage
    }
}
private fun tokenLabel(usage: JSONObject?): String = if (usage == null) "Token belum diketahui" else
    "Input: ${"%,d".format(usage.optLong("prompt_tokens"))} token | Output: ${"%,d".format(usage.optLong("completion_tokens"))} token"
