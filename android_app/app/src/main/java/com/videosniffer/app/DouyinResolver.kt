package com.videosniffer.app

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 抖音解析器（WebView 过风控，2026-10-12 桌面 Chromium 全流程实测通过）。
 *
 * 实测结论（headless Edge 与 Android WebView 同为 Chromium 内核）：
 * 1. 抖音对纯 HTTP 客户端启用 ArgusSecurityPlugin 风控（Uifid/签名要求），yt-dlp、
 *    ttwid+ABogus 签名等纯 Python 方案全部失效；
 * 2. 分享页必须"两跳"加载：先访问 iesdouyin.com 主页让 webid 生成（一跳直接进
 *    分享页时页面拿不到 web_id，数据接口返回"参数不合法"，页面显示"抱歉出错了"）；
 * 3. 分享页渲染后 <video> 元素的 src 含 video_id（reflow 数据接口里 play_addr
 *    为空，hook XHR 抓不到有效 JSON，DOM 是唯一可靠提取点）；
 * 4. 无水印下载端点：/aweme/v1/play/?video_id={vid}&ratio=1080p&line=0
 *    → 302 → douyinvod CDN。必须携带页面挑战产生的完整 cookie
 *    （__ac_signature、x-web-secsdk-uid 等），否则 403。
 *    WebView 的 CookieManager 与页面共享 cookie，下载时直接取用。
 *
 * 用法（IO 线程调用，内部阻塞等待）：
 *   val r = DouyinResolver.resolve(context, shareTextOrUrl)
 *   // r.title / r.directUrl（302 直链，配合 CookieManager cookie 下载）
 */
object DouyinResolver {

    class Result(
        val title: String,
        val directUrl: String,
        val referer: String
    )

    private const val HOME_URL = "https://www.iesdouyin.com/"
    private const val UA =
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 " +
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"

    // 页面里同时取 video src 和标题；JSON.stringify 保证回调可解析
    private const val PROBE_JS =
        "(function(){" +
        "var v=document.querySelector('video');" +
        "var s=v?(v.src||(v.querySelector('source')&&v.querySelector('source').src)||''):'';" +
        "return JSON.stringify({src:s,title:document.title||''});})()"

    /**
     * 解析抖音分享（支持整段分享文案或纯链接）。阻塞，IO 线程调用。
     * @throws RuntimeException 解析失败（超时/无视频数据）
     */
    fun resolve(context: Context, input: String, timeoutMs: Long = 30_000): Result {
        val shareUrl = extractUrl(input)
            ?: throw RuntimeException("未在文本中找到抖音链接")

        val done = CountDownLatch(1)
        val finished = AtomicBoolean(false)
        var resultTitle = ""
        var videoId = ""
        var errorMsg = ""

        val main = Handler(Looper.getMainLooper())
        var webView: WebView? = null
        var overlay: View? = null

        // 轮询：页面 video.src 出现 video_id 即成功
        val probe = object : Runnable {
            override fun run() {
                val wv = webView ?: return
                if (finished.get()) return
                wv.evaluateJavascript(PROBE_JS) { json ->
                    if (finished.get()) return@evaluateJavascript
                    try {
                        val o = JSONObject(json.trim('"').replace("\\\"", "\"")
                            .replace("\\\\", "\\"))
                        val src = o.optString("src")
                        val m = Regex("video_id=([a-z0-9]+)").find(src)
                        if (m != null) {
                            videoId = m.groupValues[1]
                            resultTitle = o.optString("title").removeSuffix(" - 抖音").trim()
                            finished.set(true)
                            done.countDown()
                        }
                    } catch (_: Exception) {
                        // JSON 未就绪，继续轮询
                    }
                }
                if (!finished.get()) main.postDelayed(this, 800)
            }
        }

        main.post {
            try {
                val (wv, ov) = createWebView(context)
                webView = wv
                overlay = ov

                // 两跳：先主页（生成 webid cookie），5s 后进分享页并开始轮询
                wv.loadUrl(HOME_URL)
                main.postDelayed({
                    if (!finished.get()) {
                        wv.loadUrl(shareUrl)
                        main.postDelayed(probe, 1500)
                    }
                }, 5000)
            } catch (e: Exception) {
                errorMsg = e.message ?: e.toString()
                finished.set(true)
                done.countDown()
            }
        }

        try {
            if (!done.await(timeoutMs, TimeUnit.MILLISECONDS)) {
                // 超时兜底：从整页 HTML 正则搜 video_id
                val wv = webView
                if (wv != null) {
                    val latch2 = CountDownLatch(1)
                    var html = ""
                    main.post {
                        wv.evaluateJavascript(
                            "document.documentElement.innerHTML.slice(0, 400000)"
                        ) { v ->
                            html = v ?: ""
                            latch2.countDown()
                        }
                    }
                    latch2.await(3, TimeUnit.SECONDS)
                    val m = Regex("video_id(?:=|%3D|\\\\u003D)([a-z0-9]{20,})").find(html)
                    if (m != null) {
                        videoId = m.groupValues[1]
                        resultTitle = "抖音视频"
                    }
                }
                if (videoId.isEmpty() && errorMsg.isEmpty()) {
                    errorMsg = "解析超时（${timeoutMs / 1000}s）：分享页未出现视频，" +
                        "链接可能已失效或网络无法访问抖音"
                }
            }
        } finally {
            finished.set(true)
            main.post {
                try { main.removeCallbacks(probe) } catch (_: Exception) {}
                webView?.let {
                    try {
                        it.stopLoading()
                        it.loadUrl("about:blank")
                        it.destroy()
                    } catch (_: Exception) {}
                }
                overlay?.let { v ->
                    try {
                        (context.getSystemService(Context.WINDOW_SERVICE) as WindowManager)
                            .removeView(v)
                    } catch (_: Exception) {}
                }
            }
        }

        if (videoId.isEmpty()) {
            throw RuntimeException(errorMsg.ifEmpty { "抖音解析失败：未捕获到视频" })
        }
        return Result(
            title = resultTitle.trim().ifEmpty { "抖音视频" },
            directUrl = "https://www.iesdouyin.com/aweme/v1/play/" +
                "?video_id=$videoId&ratio=1080p&line=0",
            referer = "https://www.iesdouyin.com/"
        )
    }

    /**
     * 创建解析用 WebView（移动 UA、允许 JS/存储、拦截 App 深链）。
     * @return Pair(WebView, overlayView)——overlay 非空表示已挂载 WindowManager，用完需移除
     */
    @SuppressLint("SetJavaScriptEnabled")
    private fun createWebView(context: Context): Pair<WebView, View?> {
        val webView = WebView(context)
        webView.minimumWidth = 720
        webView.minimumHeight = 1280

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            userAgentString = UA
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            }
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                // 拦 intent:///snssdk1128:// 等深链，防止拉起抖音 App
                val scheme = request?.url?.scheme ?: return false
                return scheme != "http" && scheme != "https"
            }
        }
        webView.webChromeClient = WebChromeClient()

        // 挂载为 1px 透明浮层（有悬浮窗权限时），保证 WebView 有真实渲染环境
        var overlay: View? = null
        if (android.provider.Settings.canDrawOverlays(context)) {
            try {
                val wm = context.getSystemService(Context.WINDOW_SERVICE) as WindowManager
                val lp = WindowManager.LayoutParams(
                    1, 1,
                    WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                    WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        or WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
                    PixelFormat.TRANSLUCENT
                )
                lp.x = 0
                lp.y = 0
                wm.addView(webView, lp)
                overlay = webView
            } catch (_: Exception) {
                // 挂载失败：不挂载直接跑（JS 一般仍会执行）
            }
        }
        return Pair(webView, overlay)
    }

    /** 从整段分享文案中提取 URL */
    fun extractUrl(text: String): String? {
        val m = Regex("https?://[^\\s<>\"']+").findAll(text)
        for (mm in m) {
            val u = mm.value
            if (u.contains("douyin.com") || u.contains("iesdouyin.com")) return u
        }
        return Regex("https?://[^\\s<>\"']+").find(text)?.value
    }
}
