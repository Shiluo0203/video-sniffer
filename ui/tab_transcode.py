"""转码标签页：本地视频批量转为 mp4。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QMessageBox, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

from ui.workers import TranscodeWorker


class TranscodeTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._worker = None
        self.setAcceptDrops(True)

        # 文件列表
        self.file_list = QListWidget()
        btn_row = QHBoxLayout()
        btn_add = QPushButton("添加文件…")
        btn_add.clicked.connect(self._add_files)
        btn_remove = QPushButton("删除选中")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self.file_list.clear)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addWidget(btn_clear)

        # 选项
        opt_row = QHBoxLayout()
        self.crf_slider = QSlider(Qt.Horizontal)
        self.crf_slider.setRange(18, 28)
        self.crf_slider.setValue(23)
        self.crf_label = QLabel("质量 (CRF): 23")
        self.crf_slider.valueChanged.connect(
            lambda v: self.crf_label.setText(f"质量 (CRF): {v}"))

        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["原始分辨率", "1080p", "720p", "480p"])

        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["H.264 (libx264)", "H.265 (libx265)"])

        opt_row.addWidget(self.crf_label)
        opt_row.addWidget(self.crf_slider)
        opt_row.addWidget(self.scale_combo)
        opt_row.addWidget(self.codec_combo)

        # 输出目录
        out_row = QHBoxLayout()
        self.out_dir = QLabel("输出目录：同目录下 converted/ 子目录")
        btn_out = QPushButton("更改…")
        btn_out.clicked.connect(self._pick_out_dir)
        self._out_dir = ""
        out_row.addWidget(self.out_dir, 1)
        out_row.addWidget(btn_out)

        # 控制
        ctl_row = QHBoxLayout()
        self.btn_start = QPushButton("开始转码")
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
        v.addWidget(QLabel("待转码文件（可拖放）："))
        v.addWidget(self.file_list, 1)
        v.addLayout(btn_row)
        v.addLayout(opt_row)
        v.addLayout(out_row)
        v.addLayout(ctl_row)
        v.addWidget(self.progress)
        v.addWidget(self.log)

    # ---------- 文件操作 ----------

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", "",
            "视频 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm *.ts *.m2ts);;所有文件 (*)")
        for p in paths:
            self.file_list.addItem(p)

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p:
                self.file_list.addItem(p)

    def _pick_out_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self._out_dir = path
            self.out_dir.setText(f"输出目录：{path}")

    # ---------- 转码 ----------

    def _start(self):
        count = self.file_list.count()
        if count == 0:
            QMessageBox.information(self, "提示", "请先添加文件")
            return

        scale_map = {"原始分辨率": None, "1080p": "1920:-2", "720p": "1280:-2", "480p": "854:-2"}
        codec_map = {"H.264 (libx264)": "libx264", "H.265 (libx265)": "libx265"}

        tasks = []
        for i in range(count):
            src = self.file_list.item(i).text()
            src_path = Path(src)
            out_dir = Path(self._out_dir) if self._out_dir else src_path.parent / "converted"
            out_dir.mkdir(parents=True, exist_ok=True)
            dst = str(out_dir / (src_path.stem + "_x264.mp4"))
            tasks.append({
                "src": src,
                "dst": dst,
                "crf": self.crf_slider.value(),
                "scale": scale_map[self.scale_combo.currentText()],
                "vcodec": codec_map[self.codec_combo.currentText()],
            })

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self._worker = TranscodeWorker(tasks)
        self._worker.progress.connect(self.progress.setValue)
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
        self.progress.setValue(100)
        self._log("✅ " + msg)

    def _fail(self, msg: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log("❌ " + msg)

    def _log(self, msg: str):
        self.log.addItem(msg)
        self.log.scrollToBottom()
