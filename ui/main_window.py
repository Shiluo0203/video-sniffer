"""主窗口：标签页 + 设置区。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from core.ffmpeg_locator import find_ffmpeg, is_frozen


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("视频工具箱")
        self.resize(900, 640)
        self._settings = QSettings("videotool", "videotool")

        tabs = QTabWidget()
        # 延迟导入，避免循环依赖
        from ui.tab_download import DownloadTab
        from ui.tab_transcode import TranscodeTab
        from ui.tab_m3u8 import M3u8Tab
        from ui.tab_merge import MergeTab
        from ui.tab_cut import CutTab

        self.tab_download = DownloadTab(self._settings)
        self.tab_transcode = TranscodeTab(self._settings)
        self.tab_m3u8 = M3u8Tab(self._settings)
        self.tab_merge = MergeTab(self._settings)
        self.tab_cut = CutTab(self._settings)

        tabs.addTab(self.tab_download, "链接下载")
        tabs.addTab(self.tab_transcode, "视频转码")
        tabs.addTab(self.tab_m3u8, "M3U8 合并")
        tabs.addTab(self.tab_merge, "视频合并")
        tabs.addTab(self.tab_cut, "视频剪切")

        # 设置区
        settings_box = QWidget()
        h = QHBoxLayout(settings_box)
        h.setContentsMargins(8, 4, 8, 4)

        self.ffmpeg_label = QLabel()

        self.btn_ffmpeg = QPushButton("下载 ffmpeg")
        self.btn_ffmpeg.clicked.connect(self._download_ffmpeg)

        self._refresh_ffmpeg_status()

        self.btn_update_ytdlp = QPushButton("更新 yt-dlp")
        self.btn_update_ytdlp.clicked.connect(self._update_ytdlp)
        if is_frozen():
            # 打包后没有 pip 环境，内置版本无法热更新
            self.btn_update_ytdlp.setVisible(False)

        h.addWidget(QLabel("ffmpeg:"))
        h.addWidget(self.ffmpeg_label)
        h.addStretch()
        h.addWidget(self.btn_ffmpeg)
        h.addWidget(self.btn_update_ytdlp)

        # Cookie 设置（供下载页使用）
        cookie_row = QWidget()
        ch = QHBoxLayout(cookie_row)
        ch.setContentsMargins(8, 0, 8, 4)
        ch.addWidget(QLabel("Cookie 文件:"))
        self.cookie_edit = QLineEdit(self._settings.value("cookie_file", ""))
        self.cookie_edit.setPlaceholderText("可选，Netscape cookies.txt 路径，用于需要登录的站点")
        self.cookie_edit.editingFinished.connect(
            lambda: self._settings.setValue("cookie_file", self.cookie_edit.text().strip()))
        btn_cookie = QPushButton("浏览…")
        btn_cookie.clicked.connect(self._pick_cookie)
        ch.addWidget(self.cookie_edit, 1)
        ch.addWidget(btn_cookie)

        # 下载目录
        dir_row = QWidget()
        dh = QHBoxLayout(dir_row)
        dh.setContentsMargins(8, 0, 8, 4)
        dh.addWidget(QLabel("下载目录:"))
        self.dir_edit = QLineEdit(self._settings.value(
            "download_dir", str(Path.home() / "Videos" / "videotool")))
        self.dir_edit.editingFinished.connect(
            lambda: self._settings.setValue("download_dir", self.dir_edit.text().strip()))
        btn_dir = QPushButton("浏览…")
        btn_dir.clicked.connect(self._pick_dir)
        dh.addWidget(self.dir_edit, 1)
        dh.addWidget(btn_dir)

        # ffmpeg 下载进度条（平时隐藏）
        self.ffmpeg_progress = QProgressBar()
        self.ffmpeg_progress.setVisible(False)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(tabs, 1)
        v.addWidget(cookie_row)
        v.addWidget(dir_row)
        v.addWidget(settings_box)
        v.addWidget(self.ffmpeg_progress)
        self.setCentralWidget(root)

    # ---------- 设置区 ----------

    def _refresh_ffmpeg_status(self):
        found = find_ffmpeg()
        self.ffmpeg_label.setText(f"✓ 已就绪" if found else "✗ 未安装")
        self.btn_ffmpeg.setVisible(not found)

    def _download_ffmpeg(self):
        from ui.workers import BaseWorker
        from core.ffmpeg_locator import download_ffmpeg

        class _DlWorker(BaseWorker):
            def run(self):
                try:
                    download_ffmpeg(progress_cb=self.progress.emit)
                except Exception as e:
                    self.error.emit(str(e))
                    return
                self.finished.emit("ffmpeg 已就绪")

        self._dl_worker = _DlWorker()
        self._dl_worker.progress.connect(self.ffmpeg_progress.setValue)
        self._dl_worker.log.connect(lambda s: None)
        self._dl_worker.finished.connect(self._on_ffmpeg_ready)
        self._dl_worker.error.connect(self._on_ffmpeg_failed)
        self.ffmpeg_progress.setVisible(True)
        self.ffmpeg_progress.setValue(0)
        self.btn_ffmpeg.setEnabled(False)
        self._dl_worker.start()

    def _on_ffmpeg_ready(self, msg: str):
        self.ffmpeg_progress.setVisible(False)
        self.btn_ffmpeg.setEnabled(True)
        self._refresh_ffmpeg_status()
        QMessageBox.information(self, "完成", msg)

    def _on_ffmpeg_failed(self, msg: str):
        self.ffmpeg_progress.setVisible(False)
        self.btn_ffmpeg.setEnabled(True)
        QMessageBox.warning(self, "ffmpeg 下载失败", msg)

    def _update_ytdlp(self):
        import subprocess, sys
        self.btn_update_ytdlp.setEnabled(False)
        try:
            subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            QMessageBox.information(self, "yt-dlp", "已在后台更新，请稍后重启应用生效")
        finally:
            self.btn_update_ytdlp.setEnabled(True)

    def _pick_cookie(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Cookie 文件", "", "Cookies (*.txt);;所有文件 (*)")
        if path:
            self.cookie_edit.setText(path)
            self._settings.setValue("cookie_file", path)

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择下载目录")
        if path:
            self.dir_edit.setText(path)
            self._settings.setValue("download_dir", path)
