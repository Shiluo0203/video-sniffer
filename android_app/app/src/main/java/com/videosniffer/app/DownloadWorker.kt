package com.videosniffer.app

import android.content.Context
import android.os.Environment
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * 调用 Chaquopy 嵌入的 Python 执行 yt-dlp 解析 + 下载。
 *
 * Python 侧 dl_bridge.py 提供：
 *   - parse(url) -> info dict
 *   - list_formats(info) -> [fmt, ...]
 *   - download(url, out_dir, format_id) -> out_dir  （阻塞，无回调）
 *
 * 进度展示方案：dl_bridge.download 是阻塞调用，Python 侧把进度写到一个
 * JSON 文件，Kotlin 定时轮询该文件更新 UI。原型够用，后续可改为
 * Chaquopy 的 EventListener 或 Python queue。
 */
object DownloadWorker {

    private var pythonReady = false

    private fun ensurePython(ctx: Context) {
        if (!pythonReady) {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(ctx))
            }
            pythonReady = true
        }
    }

    /**
     * 下载视频（阻塞，在 IO 线程执行）。
     * @param progressCb (pct: Int, msg: String) -> Unit，由调用方驱动轮询
     * @return 完成提示文本
     */
    suspend fun download(
        ctx: Context,
        url: String,
        progressCb: (Int, String) -> Unit
    ): String = withContext(Dispatchers.IO) {
        ensurePython(ctx)
        val py = Python.getInstance()
        val bridge = py.getModule("dl_bridge")

        // 1. 解析
        progressCb(0, "正在解析…")
        val info = bridge.callAttr("parse", url).toMap()
        val title = (info["title"] as? String) ?: url.take(30)

        // 2. 选最高画质
        val formatsObj = bridge.callAttr("list_formats", info)
        val formats = formatsObj.asList()
        if (formats.isEmpty()) {
            throw RuntimeException("无可下载的格式")
        }
        val topFmt = formats[0].toMap()
        val formatId = topFmt["format_id"].toString()

        // 3. 输出目录
        val outDir = File(
            Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_MOVIES
            ), "VideoSniffer"
        ).apply { mkdirs() }.absolutePath

        // 4. 轮询进度文件（dl_bridge.download 会写）
        val progressFile = File(ctx.cacheDir, "dl_progress.json")
        progressFile.writeText("""{"pct":0,"msg":"开始下载…"}""")

        // 通过环境变量把进度文件路径传给 Python
        bridge["CHAQUOPY_PROGRESS_FILE"] = progressFile.absolutePath

        // 起后台线程跑 download（阻塞），主逻辑轮询进度文件
        var downloadDone = false
        var downloadError: Exception? = null
        val thread = Thread {
            try {
                bridge.callAttr("download", url, outDir, formatId)
            } catch (e: Exception) {
                downloadError = e
            } finally {
                downloadDone = true
            }
        }
        thread.start()

        // 轮询（每 500ms）
        while (!downloadDone) {
            try {
                val content = progressFile.readText()
                // 简单解析 JSON（不引第三方库）
                val pctMatch = Regex(""""pct"\s*:\s*(\d+)""").find(content)
                val msgMatch = Regex(""""msg"\s*:\s*"([^"]*)"""").find(content)
                val pct = pctMatch?.groupValues?.get(1)?.toIntOrNull() ?: 0
                val msg = msgMatch?.groupValues?.get(1) ?: "下载中…"
                progressCb(pct, msg)
            } catch (_: Exception) {}
            Thread.sleep(500)
        }

        downloadError?.let { throw it }
        progressCb(100, "完成")
        "完成：$title"
    }
}
