package com.videosniffer.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/**
 * 入口 Activity：
 * 1. 首次启动时把 assets/ffmpeg_* 拷到 filesDir/bin/ffmpeg 并 chmod 755
 * 2. 检查并申请悬浮窗权限（SYSTEM_ALERT_WINDOW）
 * 3. 接收其他 App 的 SEND 分享文本，转发给 FloatService
 */
class MainActivity : AppCompatActivity() {

    /** 通知权限（Android 13+） */
    private val notifPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* 用户拒绝也不阻断，只是没下载完成通知 */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // 1. 释放 ffmpeg 到 filesDir
        FfmpegDeploy.ensureFfmpeg(this)

        // 2. 通知权限
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }

        // 3. 悬浮窗权限
        if (!Settings.canDrawOverlays(this)) {
            Toast.makeText(this, "请授予悬浮窗权限", Toast.LENGTH_LONG).show()
            startActivity(Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            ))
            return
        }

        // 4. 处理分享进入的 intent
        handleIntent(intent)

        // 5. 启动悬浮窗服务
        startFloatService(null)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** 从 SEND intent 提取文本，过滤 URL，转给 FloatService */
    private fun handleIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
            // 启动服务并带 URL
            startFloatService(text)
        }
    }

    private fun startFloatService(sharedUrl: String?) {
        val svc = Intent(this, FloatService::class.java)
        if (sharedUrl != null) {
            svc.putExtra(FloatService.EXTRA_URL, sharedUrl)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc)
        } else {
            startService(svc)
        }
        // 启动后可finish到后台，悬浮窗常驻
        if (sharedUrl != null) finish()
    }
}
