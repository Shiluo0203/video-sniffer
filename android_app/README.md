# 视频悬浮嗅探 - Android 版

## 快速开始

1. 用 Android Studio 打开 `android_app/` 目录（Open Existing Project）
2. 等 Gradle sync 完成（首次会下载 Chaquopy 和 Android SDK 组件）
3. 按 README_ffmpeg.txt 的说明把 ffmpeg 二进制放到 `app/src/main/assets/`
4. 连接手机或启动模拟器，点 Run ▶

## 目录结构

```
android_app/
├── settings.gradle.kts          # Gradle 工程
├── build.gradle.kts             # 顶层插件
├── gradle.properties
└── app/
    ├── build.gradle.kts         # Chaquopy + yt-dlp + 复用项目根 core/
    └── src/main/
        ├── AndroidManifest.xml # 权限 + SEND intent-filter + FloatService
        ├── assets/              # ffmpeg 预编译二进制
        ├── python/
        │   ├── dl_bridge.py         # Chaquopy 入口，调 core.ytdlp_service
        │   └── ffmpeg_locator_android.py  # 替代 Windows 版 ffmpeg 定位
        ├── java/com/videosniffer/app/
        │   ├── MainActivity.kt      # 分享接收 + 悬浮窗权限引导
        │   ├── FfmpegDeploy.kt       # 首次启动释放 ffmpeg
        │   ├── FloatService.kt       # 前台服务 + 悬浮窗 + 剪贴板监听
        │   └── DownloadWorker.kt     # Chaquopy 调 yt-dlp 下载
        └── res/                     # 布局/字符串/主题
```

## 工作流程

1. 用户打开 App → 授权悬浮窗权限 → FloatService 启动常驻
2. 在其他 App（抖音/B站）里复制链接 → 剪贴板监听自动识别
3. 或在其他 App 点「分享」→ 选「视频悬浮嗅探」→ 传 URL 给 FloatService
4. 悬浮窗显示识别到的链接 → 点「立即下载」
5. Chaquopy 跑 yt-dlp 解析 → 选最高画质 → ffmpeg 下载
6. 进度实时更新 → 完成

## 关键技术点

- **Chaquopy**：Android Studio 里嵌入 CPython，`pip install yt-dlp`
- **复用 core/**：通过 `sourceSets.srcDir("../../core")` 让 Python import 现有 ytdlp_service
- **ffmpeg 定位**：exclude 掉 `core/ffmpeg_locator.py`，注入 `ffmpeg_locator_android.py`
- **进度回传**：Python 写 JSON 文件到 cacheDir，Kotlin 轮询（原型方案）
- **悬浮窗**：`WindowManager.addView` + `TYPE_APPLICATION_OVERLAY`
- **剪贴板**：`ClipboardManager.addPrimaryClipChangedListener` + 前台服务

## 限制

- Android 10+ 后台读剪贴板要前台服务
- yt-dlp 的某些 extractor 需要较新版本
- 抖音/快手等 App 分享的可能是短链接，yt-dlp 会自动重定向解析
- 不做 MITM（Android 不可行）
