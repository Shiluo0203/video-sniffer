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

        // 把项目根的 core/ 目录加入 Python 源码，让 dl_bridge 能 import core
        // 路径相对于本文件所在目录（app/）
        sourceSets {
            create("main") {
                srcDir("../../core")
                // 排除 Windows 专属和不需要的文件
                exclude("ffmpeg_locator.py")    // 用 ffmpeg_locator_android 替代
            }
        }

        pip {
            // yt-dlp 本体（会自动带 websockets 等小依赖）
            install("yt-dlp==2024.8.6")
            // m3u8_service 的 requests 依赖
            install("requests==2.32.3")
        }
    }

    productFlavors { }
}
