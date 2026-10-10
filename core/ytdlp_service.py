"""yt-dlp 封装：链接解析、格式列表、下载（含取消）。"""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import yt_dlp

from .ffmpeg_locator import find_binary


class DownloadCancelled(Exception):
    """用户取消下载。"""


def _base_opts(cookie_file: str = "") -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # 使用系统证书库而非 certifi：开启嗅探时本工具以 MITM 代理接管流量，
        # CA 安装在 Windows 根证书库；certifi 不含该 CA 会导致全部解析失败。
        "compat_opts": ["no-certifi"],
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file
    ffmpeg = find_binary("ffmpeg")
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    return opts


def extract_info(url: str, cookie_file: str = "") -> dict:
    """解析视频信息（不下载）。返回 sanitize 后的 info dict。"""
    with yt_dlp.YoutubeDL(_base_opts(cookie_file)) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)


def list_formats(info: dict) -> list[dict]:
    """从 info 中提取格式列表，按画质从高到低排序。

    每项: {format_id, ext, resolution, fps, filesize_mb, note, vcodec, acodec}
    """
    result = []
    for f in info.get("formats") or []:
        size = f.get("filesize") or f.get("filesize_approx") or 0
        width, height = f.get("width"), f.get("height")
        resolution = f"{width}x{height}" if width and height else ("音频" if f.get("vcodec") == "none" else "未知")
        result.append({
            "format_id": f.get("format_id", ""),
            "ext": f.get("ext", ""),
            "resolution": resolution,
            "height": height or 0,
            "fps": f.get("fps") or 0,
            "filesize_mb": round(size / 1024 / 1024, 1) if size else 0,
            "note": f.get("format_note") or "",
            "vcodec": f.get("vcodec") or "",
            "acodec": f.get("acodec") or "",
        })
    result.sort(key=lambda x: (x["height"], x["filesize_mb"]), reverse=True)
    return result


class YtdlpDownload:
    """单个 URL 的下载任务，支持进度回调与取消。"""

    def __init__(self):
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def download(
        self,
        url: str,
        out_dir: str,
        format_id: str = "",
        progress_cb: Callable[[int], None] | None = None,
        log_cb: Callable[[str], None] | None = None,
        cookie_file: str = "",
    ) -> None:
        def hook(d: dict):
            if self._cancel.is_set():
                raise DownloadCancelled()
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                if total and progress_cb:
                    progress_cb(min(99, int(done * 100 / total)))
                if log_cb:
                    speed = d.get("speed") or 0
                    if speed:
                        log_cb(f"下载中… {d.get('_percent_str', '').strip()} 速度 {d.get('_speed_str', '').strip()}")
            elif d.get("status") == "finished":
                if log_cb:
                    log_cb(f"分片完成：{d.get('filename', '')}，等待合并/后处理")

        opts = _base_opts(cookie_file)
        opts.update({
            "outtmpl": str(Path(out_dir) / "%(title).150s.%(ext)s"),
            "progress_hooks": [hook],
            "format": format_id if format_id else "bv*+ba/b",
            # 合并输出为 mp4（源允许时），需要 ffmpeg
            "merge_output_format": "mp4",
        })
        if log_cb:
            log_cb(f"开始下载：{url}")
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        if progress_cb:
            progress_cb(100)
