package id.noveltranslate

import android.app.Application
import android.content.Context
import androidx.room.*
import androidx.work.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

@Entity(tableName = "jobs")
data class Job(@PrimaryKey val id: String, val title: String, val status: String, val stage: String, val snapshot: String, val updatedAt: Long)
@Entity(tableName = "tasks", primaryKeys = ["jobId", "taskKey"])
data class TaskRow(val jobId: String, val taskKey: String, val status: String, val snapshot: String)
@Entity(tableName = "attempts")
data class AttemptRow(@PrimaryKey val id: String, val jobId: String, val taskKey: String, val status: String, val snapshot: String)
@Entity(tableName = "events", primaryKeys = ["jobId", "sequence"])
data class EventRow(val jobId: String, val sequence: Int, val snapshot: String)

@Dao
interface JobDao {
    @Query("SELECT * FROM jobs ORDER BY updatedAt DESC") fun observe(): Flow<List<Job>>
    @Query("SELECT * FROM jobs WHERE id=:id") suspend fun get(id: String): Job?
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun put(job: Job)
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun tasks(rows: List<TaskRow>)
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun attempts(rows: List<AttemptRow>)
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun events(rows: List<EventRow>)
}
@Database(entities = [Job::class, TaskRow::class, AttemptRow::class, EventRow::class], version = 1, exportSchema = false)
abstract class AppDb : RoomDatabase() { abstract fun jobs(): JobDao }

class NovelApp : Application() {
    lateinit var db: AppDb
    lateinit var bridge: Bridge
    override fun onCreate() {
        super.onCreate()
        db = Room.databaseBuilder(this, AppDb::class.java, "novels.db").build()
        bridge = Bridge(this, db)
        WorkManager.getInstance(this).enqueueUniqueWork("reconcile", ExistingWorkPolicy.KEEP,
            OneTimeWorkRequestBuilder<RecoveryWorker>().build())
    }
}

class Bridge(private val context: Context, private val db: AppDb) {
    private val mutex = Mutex()
    private val root = File(context.filesDir, "novels").apply { mkdirs() }
    private val relay = ProviderClient(context)
    @Volatile var busy = false
        private set
    private fun invoke(action: String, id: String, data: JSONObject): String {
        synchronized(Python::class.java) { if (!Python.isStarted()) Python.start(AndroidPlatform(context)) }
        return Python.getInstance().getModule("mobile_runtime.bridge")
            .callAttr("call", root.absolutePath, action, id, data.toString(), relay).toString()
    }
    private suspend fun index(state: JSONObject) {
        if (!state.has("id")) return
        val id = state.getString("id")
        db.withTransaction {
            db.jobs().put(Job(id, state.getString("title"), state.getString("status"), state.getString("stage"), JSONObject().put("error", state.opt("error")).put("config", state.opt("config")).toString(), System.currentTimeMillis()))
            val tasks = state.optJSONObject("tasks") ?: JSONObject()
            db.jobs().tasks(tasks.keys().asSequence().map { key ->
                TaskRow(id, key, tasks.getJSONObject(key).optString("status"), tasks.getJSONObject(key).toString())
            }.toList())
            val attempts = state.optJSONArray("attempts") ?: JSONArray()
            db.jobs().attempts((0 until attempts.length()).map { i ->
                val a = attempts.getJSONObject(i)
                AttemptRow(a.getString("id"), id, a.getString("task"), a.getString("status"), a.toString())
            })
            val events = state.optJSONArray("events") ?: JSONArray()
            db.jobs().events((0 until events.length()).map { EventRow(id, it, events.getJSONObject(it).toString()) })
        }
    }
    suspend fun call(action: String, id: String = "", data: JSONObject = JSONObject()): JSONObject = withContext(Dispatchers.IO) {
        mutex.withLock {
            busy = true
            try {
                val result = JSONObject(invoke(action, id, data))
                if (action != "detail") index(result)
                result
            } finally { busy = false }
        }
    }
    suspend fun recover() = withContext(Dispatchers.IO) {
        mutex.withLock {
            val states = JSONArray(invoke("list", "", JSONObject()))
            for (i in 0 until states.length()) {
                var state = states.getJSONObject(i)
                if (state.getString("status") in listOf("RUNNING", "PAUSE_REQUESTED", "RETRY_WAIT")) {
                    state = JSONObject(invoke("pause", state.getString("id"), JSONObject()))
                }
                index(state)
            }
        }
    }
}

class RecoveryWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        // Recovery never silently starts a paid job.
        if (PipelineService.running) return Result.success()
        (applicationContext as NovelApp).bridge.recover()
        return Result.success()
    }
}
