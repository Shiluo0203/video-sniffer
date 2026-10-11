package com.videosniffer.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.content.res.Resources
import android.graphics.PixelFormat
import android.media.MediaScannerConnection
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.os.IBinder
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.content.FileProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File
import java.util.regex.Pattern

/**
 * 前台服务：常驻通知 + 悬浮窗 + 剪贴板监听。
 *
 * 生命周期：
 * - startForeground 后常驻，直到 stopSelf 或系统杀掉
 * - ClipboardManager.addPrimaryClipChangedListener 监听复制
 * - 收到 EXTRA_URL（分享）或剪贴板 URL → 显示在悬浮窗
 * - 点下载按钮 → DownloadWorker 跑 yt-dlp
 */
class FloatService : Service() {

    companion object {
        const val EXTRA_URL = "extra_url"
        private const val NOTIF_ID = 1
        private const val CHANNEL_ID = "float_service"
    }

    private lateinit var windowManager: WindowManager
    private var floatView: View? = null
    private var windowParams: WindowManager.LayoutParams? = null
    private lateinit var clipboard: ClipboardManager
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var downloadJob: Job? = null

    // 悬浮窗控件引用
    private var titleText: TextView? = null
    private var progressText: TextView? = null
    private var progressBar: ProgressBar? = null
    private var downloadBtn: TextView? = null
    private var currentUrl: String = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        clipboard = getSystemService(CLIPBOARD_SERVICE) as ClipboardManager

        createNotificationChannel()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            // Android 14+ 要求显式传 FGS 类型，否则可能抛 MissingForegroundServiceTypeException
            startForeground(
                NOTIF_ID, buildNotification("悬浮嗅探运行中"),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIF_ID, buildNotification("悬浮嗅探运行中"))
        }

        // 剪贴板监听
        clipboard.addPrimaryClipChangedListener { onClipboardChanged() }

        // 悬浮窗
        if (Settings.canDrawOverlays(this)) {
            showFloatView()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 收到分享/手动发送的文本（可能是整段分享文案，需要提取其中的 URL）
        intent?.getStringExtra(EXTRA_URL)?.let { raw ->
            val url = extractUrl(raw) ?: raw.trim().takeIf { it.startsWith("http") }
            if (url != null) {
                showUrl(url)
            } else {
                android.os.Handler(mainLooper).post {
                    Toast.makeText(this, "未从文本中识别到视频链接", Toast.LENGTH_LONG).show()
                }
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        clipboard.let {
            try { it.removePrimaryClipChangedListener { } } catch (_: Exception) {}
        }
        floatView?.let { windowManager.removeView(it) }
        super.onDestroy()
    }

    // ---------- 剪贴板 ----------

    private fun onClipboardChanged() {
        checkClipboard(dedupe = true)
    }

    /** 读取剪贴板并识别视频链接。dedupe=true 时跳过与当前相同的链接。
     *  @return 是否识别到视频链接 */
    private fun checkClipboard(dedupe: Boolean): Boolean {
        if (!clipboard.hasPrimaryClip()) return false
        val clip = clipboard.primaryClip ?: return false
        if (clip.description?.getMimeType(0) != ClipDescription.MIMETYPE_TEXT_PLAIN) return false
        val text = clip.getItemAt(0).coerceToText(this).toString()
        // 过滤视频链接
        val url = extractUrl(text) ?: return false
        if (dedupe && url == currentUrl) return true   // 重复不刷新
        showUrl(url)
        return true
    }

    /**
     * Android 10+ 后台服务读不到剪贴板（只有获得窗口焦点的应用才能读）。
     * 点 ↻ 时临时去掉 NOT_FOCUSABLE 让悬浮窗拿到焦点 → 合法读取 → 再恢复。
     * 读不到视频链接时把剪贴板原文显示出来，方便诊断。
     */
    private fun readClipboardFocused() {
        val view = floatView ?: return
        val p = windowParams ?: return
        try {
            p.flags = p.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
            windowManager.updateViewLayout(view, p)
            view.requestFocus()
        } catch (_: Exception) {}

        val found = checkClipboard(dedupe = false)

        try {
            p.flags = p.flags or WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
            windowManager.updateViewLayout(view, p)
        } catch (_: Exception) {}

        if (!found) {
            val raw = if (clipboard.hasPrimaryClip())
                clipboard.primaryClip?.getItemAt(0)?.coerceToText(this)?.toString() else null
            titleText?.text = if (raw.isNullOrBlank())
                "读取不到剪贴板内容\n请回到主页用「粘贴剪贴板」测试"
            else
                "剪贴板没有视频链接：\n${raw.take(80)}"
        }
    }

    private fun extractUrl(text: String): String? {
        // 简单 URL 提取，过滤常见视频站
        val m = Pattern.compile("https?://[^\\s<>\"']+").matcher(text)
        while (m.find()) {
            val url = m.group()
            if (isVideoUrl(url)) return url
        }
        return null
    }

    private fun isVideoUrl(url: String): Boolean {
        val lower = url.lowercase()
        val hosts = listOf(
            "bilibili.com", "b23.tv", "douyin.com", "iesdouyin.com",
            "kuaishou.com", "ixigua.com", "iqiyi.com", "youku.com",
            "mgtv.com", "v.qq.com", "weibo.com", "xiaohongshu.com",
            "xhslink.com", "youtube.com", "youtu.be", "tiktok.com"
        )
        if (hosts.any { it in lower }) return true
        return Regex("""\.(mp4|flv|m3u8|mpd|webm|mov|avi)([?#]|$)""").containsMatchIn(lower)
    }

    // ---------- 悬浮窗 ----------

    private fun showFloatView() {
        // 显式指定窗口宽度（部分国产 ROM 上 WRAP_CONTENT 悬浮窗会塌缩成竖条）
        val params = WindowManager.LayoutParams(
            dp(208),
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        )
        params.gravity = Gravity.TOP or Gravity.START
        params.x = dp(16)
        params.y = dp(80)
        windowParams = params

        val view = LayoutInflater.from(this).inflate(R.layout.float_view, null, false)
        floatView = view

        titleText = view.findViewById(R.id.float_title)
        progressText = view.findViewById(R.id.float_progress_text)
        progressBar = view.findViewById(R.id.float_progress)
        downloadBtn = view.findViewById(R.id.float_download_btn)

        downloadBtn?.setOnClickListener {
            if (currentUrl.isNotEmpty() && downloadJob == null) {
                startDownload(currentUrl)
            }
        }

        view.findViewById<View>(R.id.float_close_btn).setOnClickListener {
            stopSelf()
        }

        // 手动刷新剪贴板（临时获取焦点绕过 Android 10+ 后台剪贴板限制）
        view.findViewById<View>(R.id.float_refresh_btn).setOnClickListener {
            readClipboardFocused()
        }

        // 拖拽
        view.setOnTouchListener(DragHandler(windowManager, view, params))

        windowManager.addView(view, params)

        // 启动即显示状态文字（同时验证悬浮窗渲染正常）
        titleText?.text = "✓ 悬浮嗅探运行中\n复制视频链接后点 ↻ 读取"
    }

    private fun showUrl(url: String) {
        currentUrl = url
        runOnUiThread {
            titleText?.text = "识别到链接：\n${url.take(60)}"
            downloadBtn?.isEnabled = true
            downloadBtn?.text = "立即下载"
            progressText?.text = ""
            progressBar?.progress = 0
            Toast.makeText(this, "已识别视频链接，点悬浮窗「立即下载」", Toast.LENGTH_SHORT).show()
        }
    }

    // ---------- 下载 ----------

    private fun startDownload(url: String) {
        downloadBtn?.isEnabled = false
        downloadBtn?.text = "解析中…"
        downloadJob = scope.launch {
            try {
                val result = DownloadWorker.download(this@FloatService, url) { pct, msg ->
                    runOnUiThread {
                        progressBar?.progress = pct
                        progressText?.text = msg
                    }
                }
                // 找到最新下载的文件，扫描进相册 + 发完成通知
                val dir = File(
                    Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MOVIES),
                    "VideoSniffer"
                )
                val newest = dir.listFiles()?.maxByOrNull { it.lastModified() }
                if (newest != null && newest.exists()) {
                    MediaScannerConnection.scanFile(
                        this@FloatService, arrayOf(newest.absolutePath),
                        arrayOf("video/*"), null
                    )
                }
                notifyResult("下载完成", result, newest)
                runOnUiThread {
                    progressBar?.progress = 100
                    progressText?.text = result
                    downloadBtn?.text = "下载完成"
                    downloadBtn?.isEnabled = true
                }
            } catch (e: Exception) {
                notifyResult("下载失败", e.message ?: "未知错误", null)
                runOnUiThread {
                    progressText?.text = "失败：${e.message}"
                    downloadBtn?.text = "重试"
                    downloadBtn?.isEnabled = true
                }
            } finally {
                downloadJob = null
            }
        }
    }

    /** 下载结果通知：完成时点击可播放视频 */
    private fun notifyResult(title: String, text: String, file: File?) {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val pi = if (file != null && file.exists()) {
            val uri = FileProvider.getUriForFile(
                this, "$packageName.provider", file
            )
            PendingIntent.getActivity(
                this, 2,
                Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "video/*")
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
                },
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
        } else {
            PendingIntent.getActivity(
                this, 0,
                Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
            )
        }
        val notif = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(text.take(120))
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .build()
        try {
            nm.notify((System.currentTimeMillis() and 0x7FFFFFFF).toInt(), notif)
        } catch (_: Exception) {}
    }

    // ---------- 工具 ----------

    private fun runOnUiThread(block: () -> Unit) {
        scope.launch { block() }
    }

    private fun dp(value: Int): Int {
        return TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, value.toFloat(),
            Resources.getSystem().displayMetrics
        ).toInt()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, "悬浮嗅探", NotificationManager.IMPORTANCE_LOW
            ).apply { description = "视频链接自动识别" }
            (getSystemService(NOTIFICATION_SERVICE) as NotificationManager)
                .createNotificationChannel(channel)
        }
    }

    private fun buildNotification(text: String): Notification {
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("视频悬浮嗅探")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }

    // ---------- 拖拽处理器 ----------

    private class DragHandler(
        private val wm: WindowManager,
        private val view: View,
        private val params: WindowManager.LayoutParams
    ) : View.OnTouchListener {
        private var initialX = 0
        private var initialY = 0
        private var initialTouchX = 0f
        private var initialTouchY = 0f

        override fun onTouch(v: View, event: MotionEvent): Boolean {
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    initialX = params.x
                    initialY = params.y
                    initialTouchX = event.rawX
                    initialTouchY = event.rawY
                }
                MotionEvent.ACTION_MOVE -> {
                    params.x = initialX + (event.rawX - initialTouchX).toInt()
                    params.y = initialY + (event.rawY - initialTouchY).toInt()
                    wm.updateViewLayout(view, params)
                }
            }
            return true
        }
    }
}
