# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台窗口、内嵌 ffmpeg 与 yt-dlp。

构建：python -m PyInstaller --noconfirm --clean VideoToolbox.spec
产物：dist/视频工具箱.exe
"""
import os

from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# yt-dlp 含大量延迟加载的 extractor 与数据文件，需整包收集
_d, _b, _h = collect_all("yt_dlp")
datas += _d
binaries += _b
hiddenimports += _h

# 内嵌 ffmpeg（ffprobe 已不再需要，时长探测改用 ffmpeg -i）
binaries += [("bin/ffmpeg.exe", "bin")]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# 内部产物名保持 ASCII，避免部分构建工具链对非 ASCII 名称的兼容问题
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="VideoToolbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # GUI 程序，不弹黑色终端
    disable_windowed_traceback=False,
    icon=None,
)

# 构建完成后重命名为中文文件名
_src = os.path.join(DISTPATH, "VideoToolbox.exe")
_dst = os.path.join(DISTPATH, "视频工具箱.exe")
if os.path.exists(_src):
    if os.path.exists(_dst):
        os.remove(_dst)
    os.rename(_src, _dst)
