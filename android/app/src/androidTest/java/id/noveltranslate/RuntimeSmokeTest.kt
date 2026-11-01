package id.noveltranslate

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class RuntimeSmokeTest {
    @Test fun offlineEightStagesAndNativeImageLibrary() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        synchronized(Python::class.java) { if (!Python.isStarted()) Python.start(AndroidPlatform(context)) }
        val result = Python.getInstance().getModule("mobile_runtime.smoke").callAttr("run", context.cacheDir.absolutePath).toString()
        assertTrue(result.startsWith("OK:"))
    }
}
