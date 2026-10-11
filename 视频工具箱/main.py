"""视频工具箱入口。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

# 确保可以导入 core / ui
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def run_selftest() -> int:
    """打包后的环境自检：检查 ffmpeg、yt-dlp、Qt 多媒体插件是否随 exe 就位。

    结果写入 exe 同目录的 _selftest_result.txt。
    """
    from core.ffmpeg_locator import find_binary, is_frozen, resource_dir

    result: dict = {"frozen": is_frozen()}

    # ffmpeg 可执行
    ffmpeg = find_binary("ffmpeg")
    result["ffmpeg"] = ffmpeg or ""
    if ffmpeg:
        try:
            r = subprocess.run([ffmpeg, "-version"], capture_output=True,
                               text=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            result["ffmpeg_version"] = (r.stdout or "").splitlines()[0] if r.returncode == 0 else ""
        except Exception as e:
            result["ffmpeg_version"] = f"ERROR: {e}"

    # yt-dlp
    try:
        import yt_dlp
        result["ytdlp"] = yt_dlp.version.__version__
    except Exception as e:
        result["ytdlp"] = f"ERROR: {e}"

    # Qt 多媒体后端插件（QMediaPlayer 播放所需）
    plugins_dir = resource_dir() / "PySide6" / "plugins" / "multimedia"
    if not plugins_dir.exists():
        plugins_dir = resource_dir() / "plugins" / "multimedia"
    result["qt_multimedia_dir"] = str(plugins_dir)
    result["qt_multimedia_plugins"] = (
        [p.name for p in plugins_dir.glob("*.dll")] if plugins_dir.exists() else [])

    # QMediaPlayer 能否实例化（需要 QApplication）
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        from PySide6.QtMultimedia import QMediaPlayer
        player = QMediaPlayer()
        result["qmediaplayer"] = "OK"
        player.deleteLater()
    except Exception as e:
        result["qmediaplayer"] = f"ERROR: {e}"

    out_path = Path(sys.executable).resolve().parent / "_selftest_result.txt"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return 0 if result["ffmpeg"] and not result["ytdlp"].startswith("ERROR") else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())

    app = QApplication(sys.argv)
    app.setApplicationName("视频工具箱")
    app.setOrganizationName("videotool")

    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
