"""Android 版 ffmpeg 定位器。

替代 Windows 版 core/ffmpeg_locator.py：不查 PATH、不下载、不查 .exe，
只看 app 的 filesDir/bin/ffmpeg（由 MainActivity 首次启动时从 assets 拷出）。
"""
from __future__ import annotations

import os
from pathlib import Path


def _ffmpeg_path() -> str:
    """app 的 filesDir（Chaquopy 运行时 CWD 即此），下挂 bin/ffmpeg。"""
    # Chaquopy 运行 Python 时，CWD 是 app 的 filesDir
    # 也可通过环境变量 CHAQUOPY_FFMPEG_DIR 覆盖（测试用）
    override = os.environ.get("CHAQUOPY_FFMPEG_DIR")
    base = Path(override) if override else Path.cwd()
    return str(base / "bin" / "ffmpeg")


def find_binary(name: str) -> str | None:
    if name == "ffmpeg":
        p = _ffmpeg_path()
        return p if Path(p).exists() else None
    return None


def find_ffmpeg() -> tuple[str, str | None] | None:
    ff = find_binary("ffmpeg")
    if not ff:
        return None
    # ffprobe 可选，Android 版暂不带
    return ff, None


def is_frozen() -> bool:
    return False


def project_dir() -> Path:
    return Path.cwd()


def resource_dir() -> Path:
    return Path.cwd()


def app_bin_dir() -> Path:
    return Path(_ffmpeg_path()).parent


def download_ffmpeg(*args, **kwargs):
    raise RuntimeError("Android 版不支持自动下载 ffmpeg，请把预编译二进制放 assets/")
