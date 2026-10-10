"""ffmpeg 子进程封装：命令执行、进度解析、取消。"""
from __future__ import annotations

import collections
import os
import re
import subprocess
import threading
from collections.abc import Callable

from .ffmpeg_locator import find_binary

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# run() 的返回值：取消
CANCELLED = -1

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(source: str) -> float:
    """获取本地媒体时长（秒）。

    直接调用 `ffmpeg -i`（不带输出参数），从 stderr 的
    "Duration: HH:MM:SS.xx" 中解析，无需 ffprobe。失败返回 0。
    """
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg:
        return 0.0
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", source],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=_CREATE_NO_WINDOW,
        )
        m = _DURATION_RE.search(r.stderr or "")
        if not m:
            return 0.0
        h, mm, ss = m.groups()
        return int(h) * 3600 + int(mm) * 60 + float(ss)
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.0


class FFmpegJob:
    """运行一条 ffmpeg 命令，支持进度回调与取消。"""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def run(
        self,
        args: list[str],
        total_duration: float = 0,
        progress_cb: Callable[[int], None] | None = None,
        log_cb: Callable[[str], None] | None = None,
        proxy: str | None = None,
    ) -> int:
        """执行 ffmpeg。args 为 ffmpeg 参数（不含 ffmpeg 本身）。

        proxy 形如 "http://127.0.0.1:8899"，设置后经 http_proxy 环境变量
        让 ffmpeg 的网络请求走该代理（ffmpeg 不读系统代理设置）。

        返回进程退出码；被取消时返回 CANCELLED(-1)。失败抛 RuntimeError。
        """
        ffmpeg = find_binary("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("未找到 ffmpeg，请先在「设置」中下载")

        cmd = [ffmpeg, "-hide_banner", "-nostats", "-y", "-progress", "pipe:1", *args]
        if log_cb:
            log_cb("ffmpeg " + " ".join(args))
            if proxy:
                log_cb(f"  ↳ 经代理：{proxy}")

        env = None
        if proxy:
            env = os.environ.copy()
            env["http_proxy"] = proxy
            env["https_proxy"] = proxy
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            env=env,
        )

        # stderr 由后台线程收集（保留最后若干行用于错误报告）
        stderr_lines: collections.deque[str] = collections.deque(maxlen=30)

        def _drain_stderr():
            assert self._proc is not None and self._proc.stderr is not None
            for line in self._proc.stderr:
                line = line.rstrip()
                if line:
                    stderr_lines.append(line)

        drain = threading.Thread(target=_drain_stderr, daemon=True)
        drain.start()

        try:
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        us = int(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    if progress_cb and total_duration > 0:
                        pct = int(us / (total_duration * 1_000_000) * 100)
                        progress_cb(min(99, pct))
        finally:
            code = self._proc.wait()
            drain.join(timeout=2)

        if self._cancelled.is_set():
            return CANCELLED
        if code != 0:
            tail = "\n".join(list(stderr_lines)[-10:])
            raise RuntimeError(f"ffmpeg 执行失败 (退出码 {code}):\n{tail}")
        if progress_cb:
            progress_cb(100)
        return code


def build_transcode_args(
    src: str,
    dst: str,
    crf: int = 23,
    preset: str = "medium",
    scale: str | None = None,
    vcodec: str = "libx264",
) -> list[str]:
    """本地视频转码为 mp4。scale 形如 '1280:-2'。"""
    args = ["-i", src, "-map", "0", "-c:v", vcodec, "-crf", str(crf), "-preset", preset]
    if scale:
        args += ["-vf", f"scale={scale}"]
    args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dst]
    return args
