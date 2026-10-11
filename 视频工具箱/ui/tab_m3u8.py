"""M3U8 合并标签页。"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from core.ffmpeg_service import probe_duration
from ui.workers import M3U8Worker


class M3u8Tab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._worker = None

        # 来源
        src_row = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("输入 m3u8 URL，或选择本地 .m3u8 文件")
        btn_file = QPushButton("选择本地文件…")
        btn_file.clicked.connect(self._pick_file)
        src_row.addWidget(self.src_edit, 1)
        src_row.addWidget(btn_file)

        # 请求头
        self.referer_edit = QLineEdit()
        self.referer_edit.setPlaceholderText("Referer（可选，防盗链）")
        self.ua_edit = QLineEdit()
        self.ua_edit.setPlaceholderText("User-Agent（可选）")
        self.cookie_edit = QLineEdit()
        self.cookie_edit.setPlaceholderText("Cookie（可选）")

        # 输出
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("输出文件路径，如 D:/videos/output.mp4")
        btn_out = QPushButton("浏览…")
        btn_out.clicked.connect(self._pick_output)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(btn_out)

        # 控制
        ctl_row = QHBoxLayout()
        self.btn_start = QPushButton("开始合并")
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_cancel.setEnabled(False)
        ctl_row.addWidget(self.btn_start)
        ctl_row.addWidget(self.btn_cancel)

        self.progress = QProgressBar()
        self.log = QListWidget()
        self.log.setMaximumHeight(100)

        v = QVBoxLayout(self)
        v.addWidget(QLabel("M3U8 来源："))
        v.addLayout(src_row)
        v.addWidget(QLabel("请求头（防盗链时填写）："))
        v.addWidget(self.referer_edit)
        v.addWidget(self.ua_edit)
        v.addWidget(self.cookie_edit)
        v.addWidget(QLabel("输出文件："))
        v.addLayout(out_row)
        v.addLayout(ctl_row)
        v.addWidget(self.progress)
        v.addWidget(self.log, 1)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 m3u8 文件", "", "M3U8 (*.m3u8 *.m3u);;所有文件 (*)")
        if path:
            self.src_edit.setText(path)

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存为", "output.mp4", "MP4 (*.mp4)")
        if path:
            self.out_edit.setText(path)

    def _start(self):
        source = self.src_edit.text().strip()
        output = self.out_edit.text().strip()
        if not source:
            QMessageBox.information(self, "提示", "请输入 m3u8 来源")
            return
        if not output:
            QMessageBox.information(self, "提示", "请指定输出文件")
            return

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self.progress.setRange(0, 0)  # 未知时长，显示忙碌条
        self._worker = M3U8Worker(
            source, output,
            self.referer_edit.text().strip(),
            self.ua_edit.text().strip(),
            self.cookie_edit.text().strip(),
        )
        self._worker.log.connect(self._log)
        self._worker.finished.connect(self._done)
        self._worker.error.connect(self._fail)
        self._worker.start()

    def _cancel(self):
        if self._worker:
            self._worker.stop()
        self.btn_cancel.setEnabled(False)

    def _done(self, msg: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self._log("✅ " + msg)

    def _fail(self, msg: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setRange(0, 100)
        self._log("❌ " + msg)

    def _log(self, msg: str):
        self.log.addItem(msg)
        self.log.scrollToBottom()
