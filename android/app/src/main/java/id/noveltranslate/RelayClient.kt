package id.noveltranslate

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.security.KeyStore
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class ProviderClient(context: Context) {
    private val prefs = context.getSharedPreferences("provider", Context.MODE_PRIVATE)
    // Non-streaming completions send no bytes until generation ends, so read/call timeouts must
    // exceed a long chapter's generation time; a premature timeout turns a billed call into UNKNOWN.
    private val client = OkHttpClient.Builder().connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES).callTimeout(10, TimeUnit.MINUTES)
        .retryOnConnectionFailure(false).followRedirects(false).followSslRedirects(false).build()
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("provider-key", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("provider-key", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    private fun secret(): String {
        val saved = prefs.getString("api_key", null) ?: error("Simpan API key di Pengaturan endpoint")
        val data = Base64.decode(saved, Base64.NO_WRAP)
        return Cipher.getInstance("AES/GCM/NoPadding").run {
            init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, data.copyOfRange(0, 12)))
            String(doFinal(data.copyOfRange(12, data.size)), Charsets.UTF_8)
        }
    }
    fun pricing(): String = prefs.getString("pricing", "") ?: ""
    fun savePricing(model: String, input: String, output: String, currency: String) {
        if (input.isBlank() && output.isBlank()) { prefs.edit().remove("pricing").apply(); return }
        val a = input.toDoubleOrNull(); val b = output.toDoubleOrNull()
        require(a != null && b != null && a.isFinite() && b.isFinite() && a >= 0 && b >= 0 && currency.matches(Regex("[A-Z]{3}")))
        prefs.edit().putString("pricing", JSONObject().put("model", model.trim()).put("input_per_million", a).put("output_per_million", b).put("currency", currency).toString()).apply()
    }
    fun shouldPause(): Boolean = PipelineService.pause.get()
    fun configured(): Boolean = prefs.contains("api_key") && endpoint().isNotBlank()
    fun endpoint(): String = prefs.getString("endpoint", "") ?: ""
    fun model(): String = prefs.getString("model", "") ?: ""
    fun save(endpoint: String, model: String, apiKey: String) {
        val base = endpoint.trim().trimEnd('/')
        val parsed = java.net.URI(base)
        require(parsed.scheme == "https" && parsed.host != null && parsed.userInfo == null && parsed.query == null && parsed.fragment == null) { "Endpoint harus HTTPS tanpa kredensial/query" }
        require(model.isNotBlank() && apiKey.isNotBlank())
        val encrypted = Cipher.getInstance("AES/GCM/NoPadding").run { init(Cipher.ENCRYPT_MODE, key()); iv + doFinal(apiKey.toByteArray()) }
        prefs.edit().putString("endpoint", base).putString("model", model.trim())
            .putString("api_key", Base64.encodeToString(encrypted, Base64.NO_WRAP)).apply()
    }
    // OpenAI-compatible Chat Completions endpoint; no relay or key in Python/config files.
    // Outcome contract for engine.ask(): FAILED = known not to have produced a usable result,
    // UNKNOWN = the provider may have processed (and billed) it. `id` is only a local correlation
    // key sent as X-Client-Request-Id; providers do not deduplicate on it.
    fun request(id: String, payload: String): String {
        val p = JSONObject(payload)
        val base = endpoint().removeSuffix("/chat/completions")
        val result = JSONObject().put("status", "UNKNOWN").put("cost", JSONObject.NULL).put("cost_status", "unknown")
        val request = try {
            Request.Builder().url("$base/chat/completions")
                .header("Authorization", "Bearer ${secret()}")
                .header("X-Client-Request-Id", id)
                .post(JSONObject().put("model", p.getString("model"))
                    .put("messages", org.json.JSONArray().put(JSONObject().put("role", "user").put("content", p.getString("prompt"))))
                    .put("max_tokens", p.getInt("max_tokens")).put("temperature", p.getDouble("temperature"))
                    .toString().toRequestBody("application/json".toMediaType())).build()
        } catch (_: Exception) {
            // Nothing left the device: missing/undecryptable key or invalid endpoint.
            return result.put("status", "FAILED").put("retryable", false)
                .put("error", "Endpoint atau API key belum tersimpan/valid; tidak ada request yang dikirim").toString()
        }
        return try {
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    result.put("status", "FAILED").put("error", "Provider HTTP ${response.code}")
                        .put("retryable", response.code == 429 || response.code >= 500)
                        .put("retry_after", response.header("Retry-After")?.toDoubleOrNull() ?: 0)
                } else {
                    val body = response.body!!.string()  // IOException here stays UNKNOWN
                    val json = runCatching { JSONObject(body) }.getOrNull()
                    val choice = json?.optJSONArray("choices")?.optJSONObject(0)
                    val message = choice?.optJSONObject("message")
                    if (json == null || message == null) {
                        // The provider finished the call (HTTP 2xx) but the body is unusable: a known
                        // outcome, possibly billed. Fail visibly instead of inviting a blind resend.
                        result.put("status", "FAILED").put("retryable", false)
                            .put("error", "Respons provider tidak sesuai format; panggilan mungkin sudah ditagih")
                    } else {
                        result.put("status", "SUCCEEDED").put("content", message.optString("content"))
                            .put("finish_reason", choice?.optString("finish_reason") ?: "")
                        val usage = json.optJSONObject("usage")
                        if (usage != null) {
                            result.put("usage", usage)
                            val price = pricing().takeIf { it.isNotBlank() }?.let { JSONObject(it) }
                            if (price != null && price.optString("model") == p.getString("model") && usage.has("prompt_tokens") && usage.has("completion_tokens")) {
                                val estimate = (usage.getLong("prompt_tokens") * price.getDouble("input_per_million") + usage.getLong("completion_tokens") * price.getDouble("output_per_million")) / 1000000.0
                                result.put("cost", estimate).put("cost_status", "estimated").put("price_snapshot", price)
                            }
                        }
                    }; Unit
                }
            }
            result.toString()
        } catch (_: Exception) { result.put("error", "Koneksi gagal; hasil request belum diketahui").toString() }
    }

}
