package com.videosniffer.app

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/**
 * 入口 Activity：
 * 1. 首次启动时把 assets/ffmpeg_* 拷到 filesDir/bin/ffmpeg 并 chmod 755
 * 2. 检查并申请悬浮窗权限（SYSTEM_ALERT_WINDOW）——检查放在 onResume，
 *    因为用户从系统设置页授权返回时 onCreate 不会重跑，只有 onResume 会
 * 3. 接收其他 App 的 SEND 分享文本，转发给 FloatService
 */
class MainActivity : AppCompatActivity() {

    /** 通知权限（Android 13+） */
    private val notifPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* 用户拒绝也不阻断，只是没下载完成通知 */ }

    private lateinit var permissionStatus: TextView
    private var serviceStarted = false
    private var overlayPromptShown = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        permissionStatus = findViewById(R.id.permission_status)

        // 1. 释放 ffmpeg 到 filesDir
        FfmpegDeploy.ensureFfmpeg(this)

        // 2. 通知权限
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notifPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }

        // 3. 处理分享进入的 intent（onCreate 只跑一次，放这里）
        handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        // 悬浮窗权限检查：每次回到前台都重新判断（覆盖「设置页授权后返回」场景）
        if (Settings.canDrawOverlays(this)) {
            permissionStatus.text = "悬浮窗权限：已授权"
            startFloatService(null)
        } else {
            permissionStatus.text = "悬浮窗权限：未授权，正在跳转设置…"
            if (!overlayPromptShown) {
                overlayPromptShown = true
                Toast.makeText(this, "请授予「显示在其他应用上层」权限", Toast.LENGTH_LONG).show()
                startActivity(Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:$packageName")
                ))
            }
        }
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
        if (serviceStarted && sharedUrl == null) return  // 已启动，避免 onResume 重复拉起
        val svc = Intent(this, FloatService::class.java)
        if (sharedUrl != null) {
            svc.putExtra(FloatService.EXTRA_URL, sharedUrl)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc)
        } else {
            startService(svc)
        }
        serviceStarted = true
        // 启动后可finish到后台，悬浮窗常驻
        if (sharedUrl != null) finish()
    }
}
