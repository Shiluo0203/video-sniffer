"""定位或自动下载 ffmpeg 二进制（兼容源码运行与 PyInstaller 打包运行）。"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path
from collections.abc import Callable

import requests

# 多个下载源，依次尝试（gh-proxy 镜像优先，其次 GitHub 直连、gyan.dev）
FFMPEG_ZIP_URLS = [
    "https://gh-proxy.com/https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
]


def is_frozen() -> bool:
    """是否运行于 PyInstaller 打包后的环境。"""
    return bool(getattr(sys, "frozen", False))


def project_dir() -> Path:
    """开发态项目根目录。"""
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """打包内嵌资源根目录（onefile 中为解压临时目录 _MEIPASS）。"""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return project_dir()


def app_bin_dir() -> Path:
    """用户级 ffmpeg 存放目录（下载的 ffmpeg 放这里，升级不丢）。

    打包后放在 exe 同级的 bin/；源码运行时放在项目 bin/。
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent / "bin"
    return project_dir() / "bin"


def _candidate_paths(name: str) -> list[Path]:
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    dirs = [
        app_bin_dir(),                 # 用户下载（优先级最高）
        resource_dir() / "bin",        # 打包内嵌
    ]
    return [d / exe_name for d in dirs]


def find_binary(name: str) -> str | None:
    """依次在 用户bin、打包内嵌bin、PATH 中查找可执行文件。"""
    for candidate in _candidate_paths(name):
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def find_ffmpeg() -> tuple[str, str | None] | None:
    """返回 (ffmpeg路径, ffprobe路径或None)；ffmpeg 缺失则返回 None。

    ffprobe 为可选项（时长探测已改用 ffmpeg -i 解析）。
    """
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg:
        return None
    return ffmpeg, find_binary("ffprobe")


def download_ffmpeg(progress_cb: Callable[[int], None] | None = None,
                    log_cb: Callable[[str], None] | None = None) -> tuple[str, str | None]:
    """依次尝试多个镜像源下载 ffmpeg，解压 ffmpeg.exe 到 bin/。"""
    bin_dir = app_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    zip_path = bin_dir / "_ffmpeg_download.zip"

    last_error: Exception | None = None
    downloaded = False
    try:
        for url in FFMPEG_ZIP_URLS:
            try:
                if log_cb:
                    log_cb(f"尝试下载源：{url}")
                with requests.get(url, stream=True, timeout=(10, 60)) as resp:
                    resp.raise_for_status()
                    total = int(resp.headers.get("Content-Length") or 0)
                    done = 0
                    with open(zip_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                            done += len(chunk)
                            if progress_cb and total:
                                progress_cb(min(99, int(done * 100 / total)))
                downloaded = True
                break
            except Exception as e:
                last_error = e
                if log_cb:
                    log_cb(f"该源失败：{e}")
                continue
        if not downloaded:
            raise RuntimeError(f"所有下载源均不可用，最后错误：{last_error}")

        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.namelist():
                file_name = Path(member).name
                # zip 内结构为 <构建目录>/bin/ffmpeg.exe（ffprobe 可选）
                if file_name in ("ffmpeg.exe", "ffprobe.exe") and "/bin/" in member.replace("\\", "/"):
                    with zf.open(member) as src, open(bin_dir / file_name, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    finally:
        zip_path.unlink(missing_ok=True)

    if progress_cb:
        progress_cb(100)
    result = find_ffmpeg()
    if not result:
        raise RuntimeError("ffmpeg 下载完成但仍不可用，请检查 bin 目录")
    return result
