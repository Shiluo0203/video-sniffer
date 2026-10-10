"""用 ffmpeg 抽取视频缩略图（供可视化时间轴与合并列表使用）。"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .ffmpeg_locator import find_binary

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def extract_strip_frames(src: str, duration: float, count: int = 12,
                         width: int = 160) -> list[str]:
    """沿视频时间轴均匀抽取 count 帧缩略图，返回 jpg 文件路径列表。"""
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg or duration <= 0:
        return []

    out_dir = Path(tempfile.mkdtemp(prefix="vtool_thumbs_"))
    # fps 滤镜：帧率 = 帧数 / 总时长，均匀取样
    fps = count / duration
    pattern = str(out_dir / "f_%03d.jpg")
    cmd = [
        ffmpeg, "-y", "-i", src,
        "-vf", f"fps={fps:.6f},scale={width}:-1",
        "-frames:v", str(count),
        "-q:v", "3", pattern,
    ]
    r = subprocess.run(cmd, capture_output=True, creationflags=_CREATE_NO_WINDOW)
    if r.returncode != 0:
        return []
    files = sorted(out_dir.glob("f_*.jpg"))
    return [str(p) for p in files]


def extract_cover(src: str, duration: float = 0, width: int = 192) -> str | None:
    """抽取一张封面（约 20% 处的帧），返回 jpg 路径，失败返回 None。"""
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg:
        return None

    seek = max(0, duration * 0.2) if duration else 1.0
    out_dir = Path(tempfile.mkdtemp(prefix="vtool_cover_"))
    out = out_dir / "cover.jpg"
    cmd = [
        ffmpeg, "-y", "-ss", f"{seek:.2f}", "-i", src,
        "-frames:v", "1", "-vf", f"scale={width}:-1", "-q:v", "3", str(out),
    ]
    r = subprocess.run(cmd, capture_output=True, creationflags=_CREATE_NO_WINDOW)
    return str(out) if r.returncode == 0 and out.exists() else None
