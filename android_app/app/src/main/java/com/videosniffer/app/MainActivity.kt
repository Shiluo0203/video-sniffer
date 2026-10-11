package com.videosniffer.app

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import java.io.File

/**
 * 入口 Activity：权限可视化面板 + 服务控制 + 手动测试。
 *
 * 权限检查全部放在 onResume（从系统设置页返回时必触发），
 * 用户看到的永远是当前真实状态。
 */
class MainActivity : AppCompatActivity() {

    private val notifPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { refreshStatus() }

    private val storagePermission = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { refreshStatus() }

    private lateinit var statusOverlay: TextView
    private lateinit var statusNotify: TextView
    private lateinit var statusBattery: TextView
    private lateinit var statusStorage: TextView
    private lateinit var statusService: TextView
    private lateinit var statusFfmpeg: TextView
    private lateinit var inputUrl: EditText
    private lateinit var mainHint: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusOverlay = findViewById(R.id.status_overlay)
        statusNotify = findViewById(R.id.status_notify)
        statusBattery = findViewById(R.id.status_battery)
        statusStorage = findViewById(R.id.status_storage)
        statusService = findViewById(R.id.status_service)
        statusFfmpeg = findViewById(R.id.status_ffmpeg)
        inputUrl = findViewById(R.id.input_url)
        mainHint = findViewById(R.id.main_hint)

        // 首次启动释放 ffmpeg
        FfmpegDeploy.ensureFfmpeg(this)

        // ---- 权限按钮 ----
        findViewById<Button>(R.id.btn_overlay).setOnClickListener {
            startActivity(Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:$packageName")
            ))
        }
        findViewById<Button>(R.id.btn_notify).setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                notifPermission.launch(android.Manifest.permission.POST_NOTIFICATIONS)
            } else {
                startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).apply {
                    putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                })
            }
        }
        findViewById<Button>(R.id.btn_battery).setOnClickListener {
            try {
                startActivity(Intent(
                    Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName")
                ))
            } catch (e: Exception) {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            }
        }
        findViewById<Button>(R.id.btn_storage).setOnClickListener {
            val perms = when {
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU ->
                    arrayOf(android.Manifest.permission.READ_MEDIA_VIDEO)
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ->
                    arrayOf(android.Manifest.permission.READ_EXTERNAL_STORAGE)
                else -> arrayOf(
                    android.Manifest.permission.READ_EXTERNAL_STORAGE,
                    android.Manifest.permission.WRITE_EXTERNAL_STORAGE
                )
            }
            storagePermission.launch(perms)
        }

        // ---- 服务控制 ----
        findViewById<Button>(R.id.btn_service_start).setOnClickListener {
            if (!Settings.canDrawOverlays(this)) {
                Toast.makeText(this, "请先开启悬浮窗权限", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            startFloatService(null)
            refreshStatus()
        }
        findViewById<Button>(R.id.btn_service_stop).setOnClickListener {
            stopService(Intent(this, FloatService::class.java))
            refreshStatus()
        }

        // ---- 手动测试 ----
        findViewById<Button>(R.id.btn_paste).setOnClickListener {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
            val text = cm.primaryClip?.takeIf { it.itemCount > 0 }
                ?.getItemAt(0)?.coerceToText(this)?.toString().orEmpty()
            if (text.isNotEmpty()) {
                inputUrl.setText(text)
                inputUrl.setSelection(text.length)
            } else {
                Toast.makeText(this, "剪贴板为空", Toast.LENGTH_SHORT).show()
            }
        }
        findViewById<Button>(R.id.btn_send).setOnClickListener {
            val url = inputUrl.text.toString().trim()
            if (url.isEmpty()) {
                Toast.makeText(this, "请先粘贴链接", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (!Settings.canDrawOverlays(this)) {
                Toast.makeText(this, "请先开启悬浮窗权限", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            startFloatService(url)
            Toast.makeText(this, "已发送到悬浮窗", Toast.LENGTH_SHORT).show()
        }

        handleIntent(intent)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    /** 刷新所有权限/服务/组件状态显示 */
    private fun refreshStatus() {
        // 悬浮窗
        val overlay = Settings.canDrawOverlays(this)
        statusOverlay.text = if (overlay) "悬浮窗权限：✓ 已开启" else "悬浮窗权限：✗ 未开启（必须）"

        // 通知
        val notify = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(this,
                android.Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        } else true
        statusNotify.text = if (notify) "通知权限：✓ 已开启" else "通知权限：✗ 未开启（服务常驻必需）"

        // 电池优化
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        val battery = pm.isIgnoringBatteryOptimizations(packageName)
        statusBattery.text = if (battery) "忽略电池优化：✓ 已开启" else "忽略电池优化：✗ 未开启（建议开启）"

        // 存储
        val storage = when {
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU ->
                ContextCompat.checkSelfPermission(this,
                    android.Manifest.permission.READ_MEDIA_VIDEO) == PackageManager.PERMISSION_GRANTED
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q -> true // 范围存储，写公共目录无需权限
            else -> ContextCompat.checkSelfPermission(this,
                android.Manifest.permission.WRITE_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED
        }
        statusStorage.text = if (storage) "存储权限：✓ 已开启" else "存储权限：✗ 未开启（保存视频需要）"

        // 服务运行状态
        statusService.text = if (isServiceRunning()) "服务状态：✓ 运行中" else "服务状态：✗ 未运行"

        // ffmpeg
        val ff = File(filesDir, "bin/ffmpeg")
        val missing = File(filesDir, "bin/_MISSING_FFMPEG")
        statusFfmpeg.text = when {
            ff.exists() && ff.canExecute() -> "ffmpeg：✓ 就绪（${ff.length() / 1024 / 1024} MB）"
            missing.exists() -> "ffmpeg：✗ 部署失败（架构不匹配）"
            else -> "ffmpeg：… 待部署"
        }

        mainHint.text = "下载保存位置：相册 → Movies/VideoSniffer，下载完成会弹通知（点击播放）。\n" +
            "注意：Android 10+ 后台读剪贴板被系统禁止，自动识别可能不触发——" +
            "在抖音里「分享 → 复制链接」后，点悬浮窗上的 ↻ 即可读取；" +
            "也可以直接「分享 → 视频悬浮嗅探」。无法读取时回主页用「粘贴剪贴板」。"
    }

    @Suppress("DEPRECATION")
    private fun isServiceRunning(): Boolean {
        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        return am.getRunningServices(Int.MAX_VALUE)
            .any { it.service.className == FloatService::class.java.name }
    }

    /** 从 SEND intent 提取文本，转给 FloatService */
    private fun handleIntent(intent: Intent?) {
        if (intent?.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
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
        if (sharedUrl != null && !isTaskRoot) finish()
    }
}
