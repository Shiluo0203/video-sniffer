"""Chaquopy 入口：从 Kotlin 调用，桥接到项目 core/ 的 yt-dlp 服务。

复用现有纯 Python 模块：
- core/ytdlp_service.py（yt-dlp 解析/下载）
- core/m3u8_service.py（构造 ffmpeg HLS 参数）
- core/ffmpeg_service.py（FFmpegJob 执行 + 进度）

Windows 专属模块 core/ffmpeg_locator.py 被 build.gradle.kts 的
sourceSets exclude 掉，这里注入 Android 版 ffmpeg_locator_android 替代。
"""
from __future__ import annotations

import sys
import types

# Chaquopy 的 sourceSets.srcDir("../../core") 把 core/ 加进了 sys.path，
# 但 build.gradle.kts 已 exclude("ffmpeg_locator.py")，所以 core 包里
# 没有 ffmpeg_locator。我们用 Android 版顶上去，让 ytdlp_service 的
# `from .ffmpeg_locator import find_binary` 能 import 成功。
import ffmpeg_locator_android  # 本目录下的 Android 版

# 把它注册成 core.ffmpeg_locator，这样 core.ytdlp_service 的
# from .ffmpeg_locator import find_binary 会拿到 Android 版
_core = sys.modules.get("core") or types.ModuleType("core")
_core.__path__ = [p for p in getattr(_core, "__path__", [])]
sys.modules["core"] = _core
sys.modules["core.ffmpeg_locator"] = ffmpeg_locator_android

# ytdlp_service 在模块顶层 import ffmpeg_locator，所以注入要先行
from core import ytdlp_service  # noqa: E402


def parse(url: str, cookie_file: str = "") -> dict:
    """解析视频信息（不下载）。返回 sanitize 后的 info dict。

    Kotlin 侧调：
        py.getModule("dl_bridge").callAttr("parse", url)
    返回值是 Python dict，Chaquopy 会转成 Java LinkedHashMap。
    """
    return ytdlp_service.extract_info(url, cookie_file)


def list_formats(info: dict) -> list:
    """从 info 提取格式列表，按画质降序。"""
    return ytdlp_service.list_formats(info)


def _write_progress(pct: int, msg: str) -> None:
    """把进度写到环境变量 CHAQUOPY_PROGRESS_FILE 指定的路径，Kotlin 轮询读。"""
    import json, os
    path = os.environ.get("CHAQUOPY_PROGRESS_FILE")
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pct": pct, "msg": msg}, f, ensure_ascii=False)
    except Exception:
        pass


def download(url: str, out_dir: str, format_id: str,
             cookie_file: str = "", progress_file: str = "") -> str:
    """下载视频（阻塞调用）。进度写到 progress_file 供 Kotlin 轮询。

    Kotlin 侧调：
        bridge.callAttr("download", url, out_dir, format_id, "", progressFile)
    内部会把 progress_file 写进 os.environ["CHAQUOPY_PROGRESS_FILE"]，
    _write_progress 从那里读取（见 DownloadWorker）。

    返回输出目录。
    """
    import os
    if progress_file:
        os.environ["CHAQUOPY_PROGRESS_FILE"] = progress_file
    os.makedirs(out_dir, exist_ok=True)

    _write_progress(0, "开始下载…")

    def _progress_cb(pct: int) -> None:
        _write_progress(pct, f"下载中… {pct}%")

    def _log_cb(msg: str) -> None:
        _write_progress(-1, msg[:200])

    dl = ytdlp_service.YtdlpDownload()
    dl.download(url, out_dir, format_id,
                progress_cb=_progress_cb,
                log_cb=_log_cb,
                cookie_file=cookie_file)
    _write_progress(100, "完成")
    return out_dir


def is_video_url(text: str) -> bool:
    """快速判定一段文本是否像视频链接。

    用于剪贴板/分享文本过滤，避免每段文字都触发 yt-dlp 解析。
    """
    import re
    text = text.strip()
    if not text or len(text) > 2000:
        return False
    # 必须以 http(s):// 开头
    if not re.match(r"^https?://", text, re.IGNORECASE):
        return False
    # 常见视频站域名
    video_hosts = (
        "bilibili.com", "b23.tv", "douyin.com", "iesdouyin.com",
        "kuaishou.com", "ixigua.com", "iqiyi.com", "youku.com",
        "mgtv.com", "qq.com", "v.qq.com", "t.cn", "url.cn",
        "weibo.com", "xiaohongshu.com", "xhslink.com",
        "youtube.com", "youtu.be", "tiktok.com", "vm.tiktok.com",
    )
    lower = text.lower()
    if any(h in lower for h in video_hosts):
        return True
    # 路径含视频扩展名
    if re.search(r"\.(mp4|flv|m3u8|mpd|webm|mov|avi)(?:[?#]|$)", lower):
        return True
    return False


def extract_urls(text: str) -> list:
    """从一段文本中提取所有 http(s) 链接。"""
    import re
    return re.findall(r"https?://[^\s<>'\"]+", text)
