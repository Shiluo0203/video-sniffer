package com.videosniffer.app

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * 抖音解析器（WebView 过风控）。
 *
 * 背景：抖音 2025 起对纯 HTTP 客户端启用 ArgusSecurityPlugin 风控，
 * detail/iteminfo API 一律返回空或 403（要求浏览器 JS 挑战产生的 Uifid），
 * yt-dlp 的 DouyinIE 也因此失效。唯一可靠路径是用真实 WebView 加载分享页，
 * 让页面自己的 JS 完成风控挑战并拉取数据，同时 hook fetch/XHR 把含
 * play_addr 的响应回传给原生层。
 *
 * 用法（IO 线程调用，内部阻塞等待）：
 *   val r = DouyinResolver.resolve(context, shareTextOrUrl)
 *   // r.title / r.directUrl
 */
object DouyinResolver {

    class Result(
        val title: String,
        val directUrl: String,
        val referer: String
    )

    private class Bridge(
        private val onJson: (String) -> Unit
    ) {
        @JavascriptInterface
        fun onJson(text: String) {
            onJson(text)
        }
    }

    /**
     * 解析抖音分享（支持整段分享文案或纯链接）。阻塞，IO 线程调用。
     * @throws RuntimeException 解析失败（超时/无视频数据）
     */
    fun resolve(context: Context, input: String, timeoutMs: Long = 25_000): Result {
        val shareUrl = extractUrl(input)
            ?: throw RuntimeException("未在文本中找到抖音链接")

        val done = CountDownLatch(1)
        val finished = AtomicBoolean(false)
        var resultTitle = ""
        var resultUrl = ""
        var errorMsg = ""

        val main = Handler(Looper.getMainLooper())
        var webView: WebView? = null
        var overlayView: View? = null

        main.post {
            try {
                val (wv, overlay) = createWebView(context, shareUrl) { json ->
                    if (finished.get()) return@createWebView
                    // 主线程回调（JS 桥在 WebView 线程，这里转主线程）
                    main.post {
                        if (finished.get()) return@post
                        try {
                            val root = if (json.trimStart().startsWith("["))
                                JSONArray(json) else JSONObject(json)
                            val pa = findFirst(root, "play_addr") as? JSONObject
                            if (pa != null) {
                                val urls = pa.optJSONArray("url_list")
                                if (urls != null && urls.length() > 0) {
                                    var u = urls.optString(0)
                                    if (u.startsWith("//")) u = "https:$u"
                                    if (u.isNotEmpty()) {
                                        resultUrl = u
                                        resultTitle = findFirst(root, "desc") as? String ?: ""
                                        finished.set(true)
                                        done.countDown()
                                    }
                                }
                            }
                        } catch (_: Exception) {
                            // JSON 不完整（分片写入），等待下一段
                        }
                    }
                }
                webView = wv
                overlayView = overlay
            } catch (e: Exception) {
                errorMsg = e.message ?: e.toString()
                finished.set(true)
                done.countDown()
            }
        }

        try {
            if (!done.await(timeoutMs, TimeUnit.MILLISECONDS)) {
                errorMsg = "解析超时（${timeoutMs / 1000}s），请确认网络可访问抖音"
            }
        } finally {
            main.post {
                webView?.let {
                    it.stopLoading()
                    it.destroy()
                }
                overlayView?.let { v ->
                    try {
                        (context.getSystemService(Context.WINDOW_SERVICE) as WindowManager)
                            .removeView(v)
                    } catch (_: Exception) {}
                }
            }
        }

        if (resultUrl.isEmpty()) {
            throw RuntimeException(errorMsg.ifEmpty { "抖音解析失败：未捕获到视频数据" })
        }
        return Result(
            title = resultTitle.trim().ifEmpty { "抖音视频" },
            directUrl = resultUrl,
            referer = "https://www.iesdouyin.com/"
        )
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun createWebView(
        context: Context,
        url: String,
        onJson: (String) -> Unit
    ): Pair<WebView, View?> {
        val webView = WebView(context)
        webView.layoutParams = android.view.ViewGroup.LayoutParams(720, 1280)
        webView.minimumWidth = 720
        webView.minimumHeight = 1280

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            // 移动端 UA：分享页仅对移动 UA 返回 React SPA + 数据请求
            userAgentString =
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 " +
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            mediaPlaybackRequiresUserGesture = false
            cacheMode = WebSettings.LOAD_DEFAULT
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            }
        }

        webView.addJavascriptInterface(Bridge(onJson), "AndroidSniffer")

        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView?, u: String?, favicon: android.graphics.Bitmap?) {
                super.onPageStarted(view, u, favicon)
                // 尽早注入 hook，覆盖页面最初的 fetch/XHR
                view?.evaluateJavascript(HOOK_JS, null)
            }

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
                // 无权限/挂载失败：不挂载直接跑（JS 一般仍会执行）
            }
        }

        webView.loadUrl(url)
        return Pair(webView, overlay)
    }

    /** 从整段分享文案中提取 URL */
    fun extractUrl(text: String): String? {
        val m = Regex("https?://[^\\s<>\"']+").findAll(text)
        for (mm in m) {
            val u = mm.value
            if (u.contains("douyin.com") || u.contains("iesdouyin.com")) return u
        }
        // 退化：返回第一个 http 链接（调用方已确认是抖音分享文案）
        return Regex("https?://[^\\s<>\"']+").find(text)?.value
    }

    /** 递归查找 JSON 中第一个指定 key 的值 */
    private fun findFirst(obj: Any?, key: String): Any? {
        when (obj) {
            is JSONObject -> {
                val v = obj.opt(key)
                if (v != null && v != JSONObject.NULL) return v
                val it = obj.keys()
                while (it.hasNext()) {
                    val r = findFirst(obj.opt(it.next()), key)
                    if (r != null) return r
                }
            }
            is JSONArray -> {
                for (i in 0 until obj.length()) {
                    val r = findFirst(obj.opt(i), key)
                    if (r != null) return r
                }
            }
        }
        return null
    }

    /**
     * fetch/XHR hook：把含 play_addr 的响应 JSON 回传给 AndroidSniffer。
     * onPageStarted 注入，先于页面业务 JS 执行。
     */
    private val HOOK_JS = """
    (function(){
      if (window.__sniffer_hooked) return;
      window.__sniffer_hooked = true;
      function report(t){
        try { if (t && t.indexOf('play_addr') !== -1) {
          AndroidSniffer.onJson(String(t));
        } } catch(e){}
      }
      var origFetch = window.fetch;
      if (origFetch) {
        window.fetch = function(){
          var p = origFetch.apply(this, arguments);
          try {
            p.then(function(res){
              try { res.clone().text().then(function(t){ report(t); }).catch(function(){}); } catch(e){}
            }).catch(function(){});
          } catch(e){}
          return p;
        };
      }
      var XO = XMLHttpRequest.prototype.open;
      var XS = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.open = function(m, u){
        try { this.__sniffer_url = u; } catch(e){}
        return XO.apply(this, arguments);
      };
      XMLHttpRequest.prototype.send = function(){
        try {
          var self = this;
          this.addEventListener('load', function(){
            try { report(self.responseText); } catch(e){}
          });
        } catch(e){}
        return XS.apply(this, arguments);
      };
      // onPageFinished 后页面可能用内联 JSON 渲染（_ROUTER_DATA），主动扫一遍
      document.addEventListener('DOMContentLoaded', function(){
        try {
          var scripts = document.querySelectorAll('script');
          for (var i = 0; i < scripts.length; i++) {
            report(scripts[i].textContent);
          }
        } catch(e){}
      });
    })();
    """.trimIndent()
}
