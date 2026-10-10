# Android 视频悬浮嗅探原型（分享 + 剪贴板路线）

## Context

现有 Windows 工具靠 MITM 代理自动嗅探流量，但 Android 上第三方 App 拿不到系统代理权限，多数 App 还有证书固定，MITM 不可行。本原型改走「分享 + 剪贴板识别」路线：用户从抖音/快手/B站等 App 点「分享」或复制链接，Android 版悬浮窗自动识别并下载。

技术栈：原生 Kotlin + Chaquopy（Android Studio 里嵌 CPython，`pip install yt-dlp`），UI/悬浮窗/剪贴板/分享全用 Kotlin 原生 API，yt-dlp 解析复用现有 `core/ytdlp_service.py`、`core/m3u8_service.py`。ffmpeg 用预编译 arm64 二进制放 assets。

## 目标功能

1. 悬浮窗（TYPE_APPLICATION_OVERLAY）显示当前链接 + 下载按钮 + 进度
2. 剪贴板监听（前台服务 + ClipboardManager）：复制链接自动识别
3. 分享接收（intent-filter SEND/text/plain）：从其他 App 分享文本
4. yt-dlp 解析 + ffmpeg 下载
5. 下载完成通知 + 打开文件

## 目录结构

新建 `android_app/`（与 `core/`、`视频悬浮嗅探/` 同级）：

```
android_app/
  settings.gradle.kts
  build.gradle.kts (project)
  gradle.properties
  app/
    build.gradle.kts                    # Chaquopy 插件 + 依赖
    src/main/
      AndroidManifest.xml              # 权限 + intent-filter + service
      assets/
        ffmpeg_arm64                   # 预编译 ffmpeg 二进制
        ffmpeg_x86_64                  # 模拟器用
      python/
        __init__.py
        ffmpeg_locator_android.py      # 适配 core 依赖，指向 filesDir/bin/ffmpeg
        dl_bridge.py                   # Chaquopy 入口，调 core.ytdlp_service
      java/com/videosniffer/app/
        MainActivity.kt                # 分享接收 + 悬浮窗权限引导
        FloatService.kt                # 前台服务 + 悬浮窗 + 剪贴板监听
        DownloadWorker.kt              # 调 Python 跑下载，回传进度
        FloatView.kt                   # 悬浮窗 UI（标题/按钮/进度条）
      res/
        layout/activity_main.xml
        layout/float_view.xml
        values/strings.xml
```

## 复用现有代码

- `core/ytdlp_service.py`：通过 Chaquopy `includeSource` 把项目根 `core/` 加入 sys.path，直接 `from core import ytdlp_service`
- `core/m3u8_service.py`：同上
- `core/ffmpeg_service.py`：复用 `FFmpegJob.run()`，但 `proxy` 参数在 Android 不用
- `core/ffmpeg_locator.py`：**不直接复用**，写 `ffmpeg_locator_android.py` 注入到 `sys.modules["core.ffmpeg_locator"]`，指向 `context.filesDir/bin/ffmpeg`

## 实现步骤

### 1. Gradle 工程骨架
- 创建 `android_app/` Android Studio 工程模板
- `app/build.gradle.kts`：加 Chaquopy 插件 `com.chaquo.python` (version 15.0.1)，`chaquopy { defaultConfig { pip { install("yt-dlp") } } }`
- `includeSource` 指向 `../../core` 让 Python 能 `import core`

### 2. ffmpeg 二进制
- 下载 BtbN `ffmpeg-master-android-clang-aarch64`（或 ffbinaries）
- 放 `app/src/main/assets/ffmpeg_arm64`
- `MainActivity.onCreate` 首次启动时拷到 `filesDir/bin/ffmpeg` 并 `setExecutable(true)`

### 3. AndroidManifest.xml
- 权限：`SYSTEM_ALERT_WINDOW`、`FOREGROUND_SERVICE`、`FOREGROUND_SERVICE_CLIPBOARD`（API 34+）、`POST_NOTIFICATIONS`
- MainActivity 注册 `intent-filter` SEND/text/plain
- FloatService 声明 `foregroundServiceType="clipboard"`

### 4. MainActivity.kt
- `onCreate`：检查 `Settings.canDrawOverlays()`，无权限则跳转设置页
- 收到 SEND intent 时提取 `Intent.EXTRA_TEXT`，启动 FloatService 并传 URL
- 启动 FloatService（前台服务）

### 5. FloatService.kt（核心）
- `onCreate`：startForeground + 通知
- `ClipboardManager.addPrimaryClipChangedListener`：读剪贴板，匹配 URL 正则
- `WindowManager.addView(floatView, params)`：TYPE_APPLICATION_OVERLAY 悬浮窗
- 悬浮窗拖拽：`OnTouchListener` 改 `params.x/y`
- 识别到 URL → 显示标题 → 点下载 → 启 `DownloadWorker`

### 6. DownloadWorker.kt
- 用 Chaquopy `Python.getInstance().getModule("dl_bridge")`
- 调 `dl_bridge.parse(url)` → 返回格式列表
- 调 `dl_bridge.download(url, out_dir, format_id, progress_callback)` → 下载
- 进度通过 Kotlin coroutine 回传到 FloatView 进度条
- 完成发通知 + 可打开文件

### 7. dl_bridge.py（Python 侧）
```python
import sys, os
# Chaquopy includeSource 已把 core/ 加入 sys.path
# 注入 Android 版 ffmpeg_locator
import ffmpeg_locator_android
sys.modules["core.ffmpeg_locator"] = ffmpeg_locator_android
from core import ytdlp_service, m3u8_service

def parse(url, cookie_file=""):
    return ytdlp_service.extract_info(url, cookie_file)

def download(url, out_dir, format_id, progress_cb, log_cb, cookie_file=""):
    dl = ytdlp_service.YtdlpDownload()
    dl.download(url, out_dir, format_id, progress_cb, log_cb, cookie_file)
```

### 8. ffmpeg_locator_android.py
```python
from pathlib import Path
def find_binary(name):
    if name == "ffmpeg":
        # Chaquopy 把 filesDir 映射到此处；首次启动已拷贝 ffmpeg
        p = Path("/data/data/com.videosniffer.app/files/bin/ffmpeg")
        return str(p) if p.exists() else None
    return None
```

## 关键坑

- yt-dlp on Android：删掉 `compat_opts: ["no-certifi"]`（无 MITM，用 certifi 即可）
- `mutagen`/`pycryptodome` 体积大，pip 只装 `yt_dlp` 本体
- Android 10+ 后台读剪贴板限制：必须用前台服务 + 通知
- Android 13+ 通知权限要运行时申请
- ffmpeg 二进制要 `setExecutable(true)`，否则 EACCES

## 验证

1. Android Studio 里 Build APK → 装到手机
2. 打开 App → 授权悬浮窗权限
3. 复制一个 B站链接 → 悬浮窗自动识别 → 点下载 → 进度条更新 → 完成通知
4. 在浏览器里点分享 → 选「视频悬浮嗅探」→ 悬浮窗识别 → 下载
5. 测试抖音/快手分享链接是否能被 yt-dlp 解析

## 不做的

- MITM 代理嗅探（Android 不可行）
- 系统代理切换
- CA 证书安装
