package com.videosniffer.app

import android.content.Context
import android.os.Build
import java.io.File
import java.io.FileOutputStream

/**
 * 把 assets/ffmpeg_<abi> 释放到 filesDir/bin/ffmpeg 并 setExecutable。
 * arm64-v8a 设备用 ffmpeg_arm64，x86_64 模拟器用 ffmpeg_x86_64。
 */
object FfmpegDeploy {

    private const val FFMPEG_NAME = "ffmpeg"

    fun ensureFfmpeg(ctx: Context) {
        val binDir = File(ctx.filesDir, "bin")
        binDir.mkdirs()
        val target = File(binDir, FFMPEG_NAME)
        if (target.exists() && target.canExecute()) return

        // 根据 ABI 选 assets 资源
        val abi = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            Build.SUPPORTED_ABIS.firstOrNull() ?: "arm64-v8a"
        } else {
            Build.CPU_ABI ?: "arm64-v8a"
        }
        val assetName = when {
            abi.startsWith("arm64") -> "ffmpeg_arm64"
            abi.startsWith("x86_64") || abi == "x86_64" -> "ffmpeg_x86_64"
            abi.startsWith("armeabi") -> "ffmpeg_arm"   // 32位 ARM（如有）
            else -> "ffmpeg_arm64"
        }

        try {
            val asset = ctx.assets.open(assetName)
            FileOutputStream(target).use { out ->
                asset.copyTo(out)
            }
            asset.close()
            // chmod 755
            target.setExecutable(true, true)
        } catch (e: Exception) {
            // assets 里没有对应二进制（用户还没下载），留一个标记文件
            File(binDir, "_MISSING_FFMPEG").writeText(
                "请把 $assetName 放到 app/src/main/assets/ 下"
            )
        }
    }
}
