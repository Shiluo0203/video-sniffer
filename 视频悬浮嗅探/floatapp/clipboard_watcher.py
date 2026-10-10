"""剪贴板监听：自动提取并分类视频链接。

复制任何包含 http(s) 链接的文本都会触发；同一链接本次运行只触发一次。
分类：
- direct_media：链接本身指向 m3u8/mp4/flv/mov/dash，可直接用 ffmpeg 下载；
- page：网页链接（YouTube/B站/抖音等），交给 yt-dlp 解析。
"""
from __future__ import annotations

import re

from PySide6.QtCore import QObject, Signal

# 匹配文本中的 http(s) URL，排除常见中英文标点与空白
_URL_RE = re.compile(r"https?://[^\s<>\"'，。；：、）】」』\]\)]+", re.IGNORECASE)

# 直链媒体（扩展名出现在路径中，允许后跟 query/fragment）
_DIRECT_RE = re.compile(r"\.(m3u8?|mp4|flv|mov|mpd)(?:[?#]|$)", re.IGNORECASE)

# 复制后可能被一起带上的尾部标点
_TRIM = ".,;:!?)】」』]\\\"'"


def classify(url: str) -> str:
    """返回 direct_media 或 page。"""
    return "direct_media" if _DIRECT_RE.search(url) else "page"


def extract_urls(text: str) -> list[str]:
    """从任意文本中提取 URL 列表。"""
    return [u.rstrip(_TRIM) for u in _URL_RE.findall(text)]


class ClipboardWatcher(QObject):
    link_found = Signal(str, str)  # url, kind（direct_media / page）

    def __init__(self, clipboard):
        super().__init__()
        self._clipboard = clipboard
        self.enabled = True
        self._seen: set[str] = set()
        clipboard.dataChanged.connect(self._on_changed)

    def _on_changed(self) -> None:
        if not self.enabled:
            return
        text = self._clipboard.text()
        if not text:
            return
        for url in extract_urls(text):
            if url in self._seen:
                continue
            self._seen.add(url)
            self.link_found.emit(url, classify(url))
            # 一次复制只处理第一个新链接，避免刷屏
            return

    def rearm(self, url: str = "") -> None:
        """允许某个链接（或全部）再次触发。"""
        if url:
            self._seen.discard(url)
        else:
            self._seen.clear()
