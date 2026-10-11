plugins {
    id("com.android.application")
    id("com.chaquo.python")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.videosniffer.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.videosniffer.app"
        minSdk = 24          // Android 7.0：chaquopy 最低要求
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    // 协程：下载进度回传
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}

chaquopy {
    defaultConfig {
        version = "3.11"           // CPython 版本

        pip {
            // yt-dlp 本体（会自动带 websockets 等小依赖）
            install("yt-dlp==2024.8.6")
            // m3u8_service 的 requests 依赖
            install("requests==2.32.3")
        }
    }

    // 注意：sourceSets 必须在 chaquopy 顶层（不是 defaultConfig 里），
    // 且 "main" 已内置存在，只能 getByName 不能 create
    sourceSets {
        getByName("main") {
            srcDir(layout.buildDirectory.dir("generated/pythonSrc"))
        }
    }

    productFlavors { }
}

// 把仓库根的 core/ 同步成 Python 包 core.*（保留包结构，dl_bridge 才能
// `from core import ytdlp_service`）。排除 Windows 版 ffmpeg_locator.py，
// 由 dl_bridge 运行时注入 ffmpeg_locator_android 替代。
val syncCorePython = tasks.register<Sync>("syncCorePython") {
    from(file("../../core")) {
        exclude("ffmpeg_locator.py")
        exclude("__pycache__/**")
        exclude("*.pyc")
    }
    into(layout.buildDirectory.dir("generated/pythonSrc/core"))
    doLast {
        // 确保 core 是可导入包
        val initFile = layout.buildDirectory.file(
            "generated/pythonSrc/core/__init__.py").get().asFile
        if (!initFile.exists()) {
            initFile.writeText("# generated: make 'core' an importable package\n")
        }
    }
}

afterEvaluate {
    tasks.named("preBuild") { dependsOn(syncCorePython) }
}
