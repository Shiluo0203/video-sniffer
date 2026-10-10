"""下载标签页：粘贴链接 → 解析 → 选格式 → 下载。"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from core.ytdlp_service import list_formats
from ui.workers import YtdlpDownloadWorker, YtdlpParseWorker


class DownloadTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._parse_worker = None
        self._dl_worker = None
        self._formats: list[dict] = []

        # URL 输入
        url_row = QHBoxLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("粘贴视频链接（支持 Telegram 公开频道、YouTube、B 站等）")
        self.url_edit.returnPressed.connect(self.parse)
        btn_parse = QPushButton("解析")
        btn_parse.clicked.connect(self.parse)
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(btn_parse)

        # 解析结果
        self.info_label = QLabel("未解析")
        self.info_label.setWordWrap(True)

        # 格式列表
        self.format_combo = QComboBox()
        self.format_combo.setMinimumWidth(400)

        # 下载控制
        dl_row = QHBoxLayout()
        self.btn_download = QPushButton("下载")
        self.btn_download.clicked.connect(self.download)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_cancel.setEnabled(False)
        dl_row.addWidget(self.btn_download)
        dl_row.addWidget(self.btn_cancel)

        # 进度
        self.progress = QProgressBar()
        self.progress.setValue(0)

        # 日志
        self.log_list = QListWidget()
        self.log_list.setMaximumHeight(120)

        # 组装
        v = QVBoxLayout(self)
        v.addLayout(url_row)
        v.addWidget(self.info_label)
        v.addWidget(QLabel("选择格式："))
        v.addWidget(self.format_combo)
        v.addLayout(dl_row)
        v.addWidget(self.progress)
        v.addWidget(QLabel("日志："))
        v.addWidget(self.log_list, 1)

    # ---------- 解析 ----------

    def parse(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        self.info_label.setText("解析中…")
        self._parse_worker = YtdlpParseWorker(url, self._settings.value("cookie_file", ""))
        self._parse_worker.parsed.connect(self._on_parsed)
        self._parse_worker.error.connect(self._on_error)
        self._parse_worker.start()

    def _on_parsed(self, info: dict):
        self._formats = list_formats(info)
        duration = info.get("duration") or 0
        m, s = divmod(int(duration), 60)
        self.info_label.setText(f"标题：{info.get('title', '未知')} | 时长：{m:02d}:{s:02d}")
        self.format_combo.clear()
        for f in self._formats:
            size = f"{f['filesize_mb']}MB" if f["filesize_mb"] else "未知大小"
            self.format_combo.addItem(
                f"{f['format_id']}  {f['resolution']}  {f['ext']}  {size}  {f['note']}",
                userData=f["format_id"],
            )

    # ---------- 下载 ----------

    def download(self):
        url = self.url_edit.text().strip()
        if not url:
            return
        out_dir = self._settings.value("download_dir", "")
        if not out_dir:
            QMessageBox.warning(self, "提示", "请先在下方设置下载目录")
            return
        fmt = self.format_combo.currentData() or ""
        self.btn_download.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self._dl_worker = YtdlpDownloadWorker(
            url, out_dir, fmt, self._settings.value("cookie_file", ""))
        self._dl_worker.progress.connect(self.progress.setValue)
        self._dl_worker.log.connect(self._log)
        self._dl_worker.finished.connect(self._on_downloaded)
        self._dl_worker.error.connect(self._on_error)
        self._dl_worker.start()

    def cancel(self):
        if self._dl_worker:
            self._dl_worker.stop()  # 线程结束后由 error/finished 槽重置按钮
        self.btn_cancel.setEnabled(False)

    def _on_downloaded(self, msg: str):
        self.progress.setValue(100)
        self.btn_download.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log("✅ " + msg)

    def _on_error(self, msg: str):
        self.btn_download.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log("❌ " + msg)

    def _log(self, msg: str):
        self.log_list.addItem(msg)
        self.log_list.scrollToBottom()
