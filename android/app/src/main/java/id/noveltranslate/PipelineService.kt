package id.noveltranslate

import android.app.*
import android.content.Intent
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.*
import java.util.concurrent.atomic.AtomicBoolean

class PipelineService : Service() {
    companion object { @Volatile var running = false; @Volatile var currentId: String? = null; val pause = AtomicBoolean(false) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var wake: PowerManager.WakeLock? = null
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel("pipeline", "Pekerjaan terjemahan", NotificationManager.IMPORTANCE_LOW))
    }
    private fun notification(text: String): Notification {
        val stop = PendingIntent.getService(this, 2, Intent(this, PipelineService::class.java).setAction("pause"), PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, "pipeline").setSmallIcon(android.R.drawable.ic_menu_edit)
            .setContentTitle("Novel Translate").setContentText(text).setOngoing(true).setContentIntent(open)
            .addAction(android.R.drawable.ic_media_pause, "Jeda", stop).build()
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "pause") {
            pause.set(true)
            getSystemService(NotificationManager::class.java).notify(10, notification("Menunggu checkpoint untuk jeda…"))
            return START_NOT_STICKY
        }
        if (running) return START_NOT_STICKY
        val id = intent?.getStringExtra("id") ?: run { stopSelf(); return START_NOT_STICKY }
        running = true; currentId = id; pause.set(false)
        startForeground(10, notification("Memulai pekerjaan…"))
        wake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "novel:job")
            .apply { acquire(6 * 60 * 60 * 1000L) }
        scope.launch {
            val bridge = (application as NovelApp).bridge
            var settled = false  // journal holds a state that needs no worker (or was paused)
            try {
                bridge.call("resume", id)
                while (isActive) {
                    if (pause.get()) { bridge.call("pause", id); settled = true; break }
                    val state = bridge.call("step", id)
                    val status = state.getString("status")
                    getSystemService(NotificationManager::class.java).notify(10, notification("${state.getString("stage")} · $status"))
                    if (status !in listOf("RUNNING", "QUEUED", "RETRY_WAIT")) { settled = true; break }
                    if (status == "RETRY_WAIT") delay(1000)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                runCatching { bridge.call("pause", id) }
                settled = true
            } finally {
                // Service stopped (timeout/destroy) between steps: without this the journal keeps
                // RUNNING with no worker and the UI shows a job that nothing is executing.
                if (!settled) withContext(NonCancellable) { runCatching { bridge.call("pause", id) } }
                if (wake?.isHeld == true) wake?.release()
                running = false; currentId = null
                stopForeground(STOP_FOREGROUND_REMOVE); stopSelf()
            }
        }
        return START_NOT_STICKY
    }
    override fun onTimeout(startId: Int, fgsType: Int) {
        // Android 15 dataSync budget: stop promptly. Pending remote ID is already journaled.
        pause.set(true)
        if (wake?.isHeld == true) wake?.release()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }
    override fun onDestroy() {
        pause.set(true)
        scope.cancel()
        if (wake?.isHeld == true) wake?.release()
        // Keep running true until the IO/Python checkpoint actually returns.
        super.onDestroy()
    }
}
