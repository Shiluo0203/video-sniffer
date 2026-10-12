package com.videosniffer.app

import android.content.Context
import android.os.Environment
import android.webkit.CookieManager
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

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
 *
 * 抖音特殊处理：抖音对纯 HTTP 客户端有 Argus 风控（yt-dlp 失效），
 * 走 DouyinResolver（WebView 过风控）解析出无水印直链后直接下载，
 * 不经过 yt-dlp。
 */
object DownloadWorker {

    private var pythonReady = false

    private val MOBILE_UA =
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

    private fun ensurePython(ctx: Context) {
        if (!pythonReady) {
            if (!Python.isStarted()) {
                Python.start(AndroidPlatform(ctx))
            }
            pythonReady = true
        }
    }

    private fun isDouyin(url: String): Boolean =
        url.contains("douyin.com") || url.contains("iesdouyin.com")

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
        if (isDouyin(url)) {
            downloadDouyin(ctx, url, progressCb)
        } else {
            downloadYtdlp(ctx, url, progressCb)
        }
    }

    // ---------- 抖音：WebView 解析 + 直链下载 ----------

    private fun downloadDouyin(
        ctx: Context,
        url: String,
        progressCb: (Int, String) -> Unit
    ): String {
        progressCb(0, "抖音解析中（WebView 过风控）…")
        val r = DouyinResolver.resolve(ctx, url)
        progressCb(5, "解析成功：${r.title.take(24)}")

        val outDir = File(
            Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_MOVIES
            ), "VideoSniffer"
        ).apply { mkdirs() }

        val safe = r.title.replace(Regex("[\\\\/:*?\"<>|\\n\\r]"), "_").take(60)
            .ifEmpty { "douyin_${System.currentTimeMillis()}" }
        val outFile = File(outDir, "$safe.mp4")
        // 同名文件加序号
        var f = outFile
        var i = 1
        while (f.exists()) {
            f = File(outDir, "$safe($i).mp4")
            i++
        }

        downloadDirect(r.directUrl, f, r.referer) { pct, read, total ->
            if (total > 0) {
                progressCb(5 + (pct * 95 / 100), "下载中… $pct%")
            } else {
                progressCb(-1, "下载中… ${read / 1024 / 1024} MB")
            }
        }
        progressCb(100, "完成")
        return "完成：${r.title}"
    }

    /** 直链下载：手动跟随 302，带移动 UA / Referer / Cookie */
    private fun downloadDirect(
        url: String,
        outFile: File,
        referer: String,
        progress: (Int, Long, Long) -> Unit
    ) {
        var current = url
        var conn: HttpURLConnection? = null
        repeat(6) { attempt ->
            val c = URL(current).openConnection() as HttpURLConnection
            c.connectTimeout = 15_000
            c.readTimeout = 30_000
            c.instanceFollowRedirects = false
            c.setRequestProperty("User-Agent", MOBILE_UA)
            c.setRequestProperty("Referer", referer)
            try {
                val cookie = CookieManager.getInstance().getCookie(current)
                if (!cookie.isNullOrBlank()) c.setRequestProperty("Cookie", cookie)
            } catch (_: Exception) {}
            if (attempt == 0) c.setRequestProperty("Accept", "*/*")

            val code = c.responseCode
            if (code in 300..399) {
                val loc = c.getHeaderField("Location")
                c.disconnect()
                if (loc.isNullOrBlank()) throw IOException("重定向缺少 Location")
                current = if (loc.startsWith("http")) loc
                          else URL(URL(current), loc).toString()
                return@repeat
            }
            if (code != 200) throw IOException("HTTP $code（视频地址可能已过期，请重新识别）")

            val total = c.contentLengthLong
            c.inputStream.use { input ->
                java.io.FileOutputStream(outFile).use { out ->
                    val buf = ByteArray(64 * 1024)
                    var read = 0L
                    var lastPct = -1
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        out.write(buf, 0, n)
                        read += n
                        if (total > 0) {
                            val pct = (read * 100 / total).toInt()
                            if (pct != lastPct) {
                                lastPct = pct
                                progress(pct, read, total)
                            }
                        } else {
                            progress(-1, read, total)
                        }
                    }
                }
            }
            c.disconnect()
            conn = c
            return
        }
        throw IOException("重定向次数过多")
    }

    // ---------- 通用：yt-dlp ----------

    private fun downloadYtdlp(
        ctx: Context,
        url: String,
        progressCb: (Int, String) -> Unit
    ): String {
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

        // 进度文件路径作为参数传给 Python（dl_bridge.download 内部写 os.environ）

        // 起后台线程跑 download（阻塞），主逻辑轮询进度文件
        var downloadDone = false
        var downloadError: Exception? = null
        val thread = Thread {
            try {
                bridge.callAttr("download", url, outDir, formatId, "", progressFile.absolutePath)
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
        return "完成：$title"
    }
}
