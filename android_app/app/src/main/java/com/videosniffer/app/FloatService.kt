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
import android.content.res.Resources
import android.graphics.PixelFormat
import android.os.Build
import android.os.IBinder
import android.provider.Settings
import android.util.TypedValue
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
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
        startForeground(NOTIF_ID, buildNotification("悬浮嗅探运行中"))

        // 剪贴板监听
        clipboard.addPrimaryClipChangedListener { onClipboardChanged() }

        // 悬浮窗
        if (Settings.canDrawOverlays(this)) {
            showFloatView()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 收到分享 URL
        intent?.getStringExtra(EXTRA_URL)?.let { url ->
            showUrl(url)
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
        if (!clipboard.hasPrimaryClip()) return
        val clip = clipboard.primaryClip ?: return
        if (clip.description?.getMimeType(0) != ClipDescription.MIMETYPE_TEXT_PLAIN) return
        val text = clip.getItemAt(0).coerceToText(this).toString()
        // 过滤视频链接
        val url = extractUrl(text) ?: return
        if (url == currentUrl) return   // 重复不刷新
        showUrl(url)
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
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT
        )
        params.gravity = Gravity.TOP or Gravity.START
        params.x = dp(16)
        params.y = dp(80)

        val view = layoutInflater.inflate(R.layout.float_view, null, false)
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

        // 拖拽
        view.setOnTouchListener(DragHandler(windowManager, view, params))

        windowManager.addView(view, params)
    }

    private fun showUrl(url: String) {
        currentUrl = url
        runOnUiThread {
            titleText?.text = "识别到链接：\n${url.take(60)}"
            downloadBtn?.isEnabled = true
            downloadBtn?.text = "立即下载"
            progressText?.text = ""
            progressBar?.progress = 0
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
                runOnUiThread {
                    progressBar?.progress = 100
                    progressText?.text = result
                    downloadBtn?.text = "下载完成"
                    downloadBtn?.isEnabled = true
                }
            } catch (e: Exception) {
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
