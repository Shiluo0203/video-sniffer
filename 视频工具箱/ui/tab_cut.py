"""视频剪切标签页：视频预览 + 可拖拽时间轴选择区间。"""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from core.ffmpeg_service import probe_duration
from ui.workers import CutWorker, StripThumbWorker
from ui.widgets.video_timeline import VideoTimelineWidget, format_ms, parse_time


class CutTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._worker = None
        self._duration_ms = 0
        self._thumb_workers: list[StripThumbWorker] = []
        self._preview_only = False  # 是否处于“播放选中片段”模式

        # ---------- 输入文件 ----------
        src_row = QHBoxLayout()
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("选择要剪切的视频文件")
        btn_src = QPushButton("浏览…")
        btn_src.clicked.connect(self._pick_source)
        src_row.addWidget(self.src_edit, 1)
        src_row.addWidget(btn_src)

        # ---------- 视频预览 ----------
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(260)
        self.video_widget.setStyleSheet("background: black;")

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video_widget)
        self.audio.setVolume(0.8)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_player_duration)

        # 播放控制
        ctl = QHBoxLayout()
        self.btn_play = QPushButton("▶ 播放")
        self.btn_play.clicked.connect(self._toggle_play)
        btn_back5 = QPushButton("-5s")
        btn_back5.clicked.connect(lambda: self._seek_relative(-5000))
        btn_fwd5 = QPushButton("+5s")
        btn_fwd5.clicked.connect(lambda: self._seek_relative(5000))
        btn_set_start = QPushButton("「 设为起点")
        btn_set_start.clicked.connect(lambda: self._set_handle_at_playhead("start"))
        btn_set_end = QPushButton("」 设为终点")
        btn_set_end.clicked.connect(lambda: self._set_handle_at_playhead("end"))
        self.btn_preview_sel = QPushButton("试听/看选中片段")
        self.btn_preview_sel.clicked.connect(self._preview_selection)
        for b in (self.btn_play, btn_back5, btn_fwd5, btn_set_start,
                  btn_set_end, self.btn_preview_sel):
            ctl.addWidget(b)

        self.pos_label = QLabel("00:00 / 00:00")

        # ---------- 可视化时间轴 ----------
        self.timeline = VideoTimelineWidget()
        self.timeline.seekRequested.connect(self._seek_to)
        self.timeline.rangeChanged.connect(self._on_range_changed)

        # 时间手动输入（与时间轴双向同步）
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("开始："))
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("00:00")
        self.start_edit.editingFinished.connect(self._edit_to_timeline)
        time_row.addWidget(self.start_edit)
        time_row.addWidget(QLabel("结束："))
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("留空 = 到结尾")
        self.end_edit.editingFinished.connect(self._edit_to_timeline)
        time_row.addWidget(self.end_edit)
        time_row.addWidget(self.pos_label)
        time_row.addStretch()

        # 模式
        mode_row = QHBoxLayout()
        self.radio_fast = QRadioButton("快速无损（切点对齐关键帧，可能差几秒）")
        self.radio_precise = QRadioButton("精确（重编码，切点精确到帧）")
        self.radio_fast.setChecked(True)
        mode_row.addWidget(self.radio_fast)
        mode_row.addWidget(self.radio_precise)

        # 输出
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("输出文件路径，如 D:/videos/clip.mp4")
        btn_out = QPushButton("浏览…")
        btn_out.clicked.connect(self._pick_output)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(btn_out)

        # 剪切控制
        run_row = QHBoxLayout()
        self.btn_start = QPushButton("开始剪切")
        self.btn_start.clicked.connect(self._start)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_cancel.setEnabled(False)
        run_row.addWidget(self.btn_start)
        run_row.addWidget(self.btn_cancel)

        self.progress = QProgressBar()
        self.log = QListWidget()
        self.log.setMaximumHeight(80)

        v = QVBoxLayout(self)
        v.addLayout(src_row)
        v.addWidget(self.video_widget, 3)
        v.addLayout(ctl)
        v.addWidget(self.timeline)
        v.addLayout(time_row)
        v.addLayout(mode_row)
        v.addWidget(QLabel("输出文件："))
        v.addLayout(out_row)
        v.addLayout(run_row)
        v.addWidget(self.progress)
        v.addWidget(self.log)

    # ---------- 文件加载 ----------

    def _pick_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频", "",
            "视频 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm *.ts);;所有文件 (*)")
        if not path:
            return
        self.src_edit.setText(path)
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(path))
        self.btn_play.setText("▶ 播放")

        # ffprobe 时长先顶上，随后以 QMediaPlayer 的 durationChanged 为准
        dur = probe_duration(path)
        self._duration_ms = int(dur * 1000)
        self.timeline.set_duration(self._duration_ms)
        self.timeline.clear_thumbs()
        self._sync_time_edits(0, self._duration_ms)

        # 后台抽缩略图条（保持引用，结束后释放）
        worker = StripThumbWorker(path, dur)
        worker.frames_ready.connect(self._on_frames_ready)
        worker.finished.connect(lambda: self._thumb_workers.remove(worker)
                                if worker in self._thumb_workers else None)
        self._thumb_workers.append(worker)
        worker.start()

    def _on_player_duration(self, duration_ms: int):
        if duration_ms > 0:
            self._duration_ms = duration_ms
            self.timeline.set_duration(duration_ms)
            self._sync_time_edits(0, duration_ms)

    def _on_frames_ready(self, src: str, files: list[str]):
        if src == self.src_edit.text():
            self.timeline.set_thumbs(files)

    # ---------- 播放控制 ----------

    def _toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 播放")
        else:
            self._preview_only = False
            self.player.play()
            self.btn_play.setText("⏸ 暂停")

    def _seek_to(self, ms: int):
        self.player.setPosition(ms)

    def _seek_relative(self, delta_ms: int):
        self.player.setPosition(max(0, self.player.position() + delta_ms))

    def _preview_selection(self):
        start, _ = self.timeline.range_ms()
        self._preview_only = True
        self.player.setPosition(start)
        self.player.play()
        self.btn_play.setText("⏸ 暂停")

    def _set_handle_at_playhead(self, which: str):
        start, end = self.timeline.range_ms()
        pos = self.player.position()
        if which == "start":
            self.timeline.set_range(pos, end)
        else:
            self.timeline.set_range(start, pos)

    def _on_position(self, ms: int):
        self.timeline.set_playhead(ms)
        total = self._duration_ms or self.player.duration()
        self.pos_label.setText(f"{format_ms(ms)} / {format_ms(total)}")
        # 播放选中片段：到终点自动暂停并回起点
        if self._preview_only:
            _, end = self.timeline.range_ms()
            if end > 0 and ms >= end:
                self.player.pause()
                self._preview_only = False
                start, _ = self.timeline.range_ms()
                self.player.setPosition(start)
                self.btn_play.setText("▶ 播放")

    # ---------- 时间轴 ↔ 输入框 ----------

    def _on_range_changed(self, start: int, end: int):
        self._sync_time_edits(start, end)

    def _sync_time_edits(self, start: int, end: int):
        # 避免覆盖用户正在编辑的内容
        if not self.start_edit.hasFocus():
            self.start_edit.setText(format_ms(start))
        if not self.end_edit.hasFocus():
            self.end_edit.setText(format_ms(end) if end < self._duration_ms else "")

    def _edit_to_timeline(self):
        start, end = self.timeline.range_ms()
        try:
            start = parse_time(self.start_edit.text())
        except ValueError:
            pass
        text = self.end_edit.text().strip()
        if text:
            try:
                end = parse_time(text)
            except ValueError:
                QMessageBox.warning(self, "提示", "结束时间格式错误，示例：01:30 或 90")
                return
        else:
            end = self._duration_ms
        self.timeline.set_range(start, end)

    # ---------- 输出与执行 ----------

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存为", "clip.mp4", "MP4 (*.mp4)")
        if path:
            self.out_edit.setText(path)

    def _start(self):
        src = self.src_edit.text().strip()
        output = self.out_edit.text().strip()
        if not src:
            QMessageBox.information(self, "提示", "请选择输入视频")
            return
        if not output:
            QMessageBox.information(self, "提示", "请指定输出文件")
            return

        start_ms, end_ms = self.timeline.range_ms()
        start = f"{start_ms / 1000:.3f}"
        # 终点等于视频总时长时留空（切到结尾）
        end = "" if end_ms >= self._duration_ms - 200 else f"{end_ms / 1000:.3f}"
        if end_ms - start_ms < 500:
            QMessageBox.information(self, "提示", "选中片段太短，请在时间轴上拖动绿色手柄选择区间")
            return

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self._worker = CutWorker(src, output, start, end,
                                 self.radio_precise.isChecked())
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
