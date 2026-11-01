plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.kapt")
    id("com.chaquo.python")
}
android {
    namespace = "id.noveltranslate"
    compileSdk = 35
    defaultConfig {
        applicationId = "id.noveltranslate"
        minSdk = 29
        targetSdk = 35
        versionCode = 7
        versionName = "0.2.5"
        ndk { abiFilters += providers.gradleProperty("testAbi").getOrElse("arm64-v8a") }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        resValue("string", "app_label", "Novel Translate")
    }
    // Debug installs beside the user's signed app (different key, separate data) instead of replacing it.
    buildTypes { getByName("debug") { applicationIdSuffix = ".debug"; resValue("string", "app_label", "Novel Translate (debug)") } }
    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.15" }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}
val syncPython by tasks.registering(Sync::class) {
    from(rootProject.projectDir.parentFile) {
        include("mobile_runtime/**", "scripts/**", "prompts/**", "config/rules.json", "glossary/global.md", "fonts/**")
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    into(layout.buildDirectory.dir("generated/python"))
}
chaquopy {
    defaultConfig {
        version = "3.11"
        pip { install("Pillow==11.0.0") }
        extractPackages("*")
    }
    sourceSets { getByName("main") { srcDir(layout.buildDirectory.dir("generated/python")) } }
}
tasks.configureEach {
    if (name.contains("Python") && name != "syncPython") dependsOn(syncPython)
}
dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    kapt("androidx.room:room-compiler:2.6.1")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
}
