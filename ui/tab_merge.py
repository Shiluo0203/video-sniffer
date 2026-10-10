"""视频合并标签页：带封面缩略图的可拖拽排序列表。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QListView, QListWidget, QMessageBox, QProgressBar, QPushButton,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

from ui.workers import CoverWorker, MergeWorker
from ui.widgets.video_timeline import format_ms

PATH_ROLE = Qt.UserRole + 1
DURATION_ROLE = Qt.UserRole + 2
COVER_ROLE = Qt.UserRole + 3


class ThumbDelegate(QStyledItemDelegate):
    """绘制 [封面] [文件名 / 时长] 行，并缓存封面 pixmap。"""

    THUMB_W, THUMB_H = 104, 58
    ROW_H = 70

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: dict[str, QPixmap] = {}

    def sizeHint(self, option, index):
        return QSize(0, self.ROW_H)

    def _cover(self, path: str) -> QPixmap | None:
        if not path:
            return None
        pix = self._cache.get(path)
        if pix is None:
            loaded = QPixmap(path)
            if loaded.isNull():
                return None
            pix = loaded.scaled(self.THUMB_W, self.THUMB_H,
                                Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            self._cache[path] = pix
        return pix

    def paint(self, painter: QPainter, option, index):
        painter.save()
        rect = option.rect

        # 选中/悬停背景
        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, option.palette.highlight())
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(rect, option.palette.alternateBase())

        x = rect.left() + 10
        y = rect.top() + (self.ROW_H - self.THUMB_H) // 2

        # 封面
        cover = self._cover(index.data(COVER_ROLE) or "")
        if cover:
            # 居中裁剪绘制到固定框
            cw, ch = cover.width(), cover.height()
            scale = max(self.THUMB_W / cw, self.THUMB_H / ch)
            dw, dh = int(cw * scale), int(ch * scale)
            tx, ty = x, y
            painter.setClipRect(tx, ty, self.THUMB_W, self.THUMB_H)
            painter.drawPixmap(tx + (self.THUMB_W - dw) // 2,
                               ty + (self.THUMB_H - dh) // 2, cover)
            painter.setClipping(False)
            painter.setPen(QColor(90, 90, 90))
            painter.drawRect(tx, ty, self.THUMB_W - 1, self.THUMB_H - 1)
        else:
            painter.setPen(QColor(110, 110, 110))
            painter.drawRect(x, y, self.THUMB_W - 1, self.THUMB_H - 1)
            painter.drawText(QRect(x, y, self.THUMB_W, self.THUMB_H),
                             Qt.AlignCenter, "加载中…")

        # 序号
        row = index.row() + 1
        painter.setPen(QColor(255, 193, 7) if option.state & QStyle.State_Selected
                       else QColor(120, 120, 120))
        painter.drawText(QRect(x - 2, y - 14, self.THUMB_W, 14),
                         Qt.AlignCenter, f"#{row}")

        # 文字
        text_color = option.palette.highlightedText().color() \
            if option.state & QStyle.State_Selected else option.palette.text().color()
        tx = x + self.THUMB_W + 12
        name = index.data(Qt.DisplayRole)
        f = QFont(option.font)
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(text_color)
        name_rect = QRect(tx, rect.top() + 12, rect.right() - tx - 10, 22)
        painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         option.fontMetrics.elidedText(name, Qt.ElideMiddle, name_rect.width()))

        dur = index.data(DURATION_ROLE) or 0
        info = "时长 " + format_ms(int(dur * 1000)) if dur else "时长 --:--"
        painter.setFont(option.font)
        painter.setPen(QColor(255, 220, 150) if option.state & QStyle.State_Selected
                       else QColor(130, 130, 130))
        painter.drawText(QRect(tx, rect.top() + 38, rect.right() - tx - 10, 20),
                         Qt.AlignLeft | Qt.AlignVCenter, info)
        painter.restore()


class MergeTab(QWidget):
    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._worker = None
        self._cover_workers: list[CoverWorker] = []
        self.setAcceptDrops(True)

        # 封面列表（QListView + 自定义委托，支持拖拽排序）
        self.model = QStandardItemModel(self)
        self.list_view = QListView()
        self.list_view.setModel(self.model)
        self.list_view.setItemDelegate(ThumbDelegate(self.list_view))
        self.list_view.setDragDropMode(QAbstractItemView.InternalMove)
        self.list_view.setDefaultDropAction(Qt.MoveAction)
        self.list_view.setDragDropOverwriteMode(False)
        self.list_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_view.setAlternatingRowColors(True)
        self.list_view.setMouseTracking(True)

        btn_row = QHBoxLayout()
        btn_add = QPushButton("添加文件…")
        btn_add.clicked.connect(self._add_files)
        btn_remove = QPushButton("删除选中")
        btn_remove.clicked.connect(self._remove_selected)
        btn_up = QPushButton("上移")
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton("下移")
        btn_down.clicked.connect(lambda: self._move(1))
        btn_clear = QPushButton("清空")
        btn_clear.clicked.connect(self.model.clear)
        for b in (btn_add, btn_remove, btn_up, btn_down, btn_clear):
            btn_row.addWidget(b)

        self.reencode_chk = QCheckBox("重编码兼容模式（不同格式/分辨率时勾选）")

        hint = QLabel("提示：可直接拖视频文件到列表；拖拽列表项可调整合并顺序")
        hint.setStyleSheet("color: gray;")

        # 输出
        out_row = QHBoxLayout()
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("输出文件路径，如 D:/videos/merged.mp4")
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
        self.log.setMaximumHeight(80)

        v = QVBoxLayout(self)
        v.addWidget(self.list_view, 1)
        v.addLayout(btn_row)
        v.addWidget(hint)
        v.addWidget(self.reencode_chk)
        v.addWidget(QLabel("输出文件："))
        v.addLayout(out_row)
        v.addLayout(ctl_row)
        v.addWidget(self.progress)
        v.addWidget(self.log)

    # ---------- 列表操作 ----------

    def _existing_paths(self) -> set[str]:
        return {self.model.item(i).data(PATH_ROLE) for i in range(self.model.rowCount())}

    def _add_paths(self, paths: list[str]):
        existing = self._existing_paths()
        added: list[str] = []
        for p in paths:
            p = str(Path(p).resolve())
            if p in existing:
                continue
            existing.add(p)
            item = QStandardItem(Path(p).name)
            item.setData(p, PATH_ROLE)
            item.setData(0.0, DURATION_ROLE)
            item.setData("", COVER_ROLE)
            item.setDragEnabled(True)
            self.model.appendRow(item)
            added.append(p)

        if added:
            # 后台抽封面与时长（保持引用，结束后自动释放）
            worker = CoverWorker(added)
            worker.cover_ready.connect(self._on_cover_ready)
            worker.finished.connect(lambda: self._cover_workers.remove(worker)
                                    if worker in self._cover_workers else None)
            self._cover_workers.append(worker)
            worker.start()

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择视频", "",
            "视频 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm *.ts);;所有文件 (*)")
        self._add_paths(paths)

    def _find_row(self, path: str) -> int:
        for i in range(self.model.rowCount()):
            if self.model.item(i).data(PATH_ROLE) == path:
                return i
        return -1

    def _on_cover_ready(self, src: str, cover: str, duration: float):
        row = self._find_row(src)
        if row >= 0:
            item = self.model.item(row)
            item.setData(cover, COVER_ROLE)
            item.setData(duration, DURATION_ROLE)

    def _remove_selected(self):
        for index in sorted({i.row() for i in self.list_view.selectedIndexes()},
                            reverse=True):
            self.model.removeRow(index)

    def _move(self, delta: int):
        row = self.list_view.currentIndex().row()
        new_row = row + delta
        if row < 0 or not (0 <= new_row < self.model.rowCount()):
            return
        items = self.model.takeRow(row)
        self.model.insertRow(new_row, items)
        self.list_view.setCurrentIndex(self.model.index(new_row, 0))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith(
                     (".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv", ".webm", ".ts"))]
        if paths:
            self._add_paths(paths)
            e.acceptProposedAction()

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存为", "merged.mp4", "MP4 (*.mp4)")
        if path:
            self.out_edit.setText(path)

    # ---------- 合并 ----------

    def _start(self):
        count = self.model.rowCount()
        if count < 2:
            QMessageBox.information(self, "提示", "请至少添加两个文件")
            return
        output = self.out_edit.text().strip()
        if not output:
            QMessageBox.information(self, "提示", "请指定输出文件")
            return

        inputs = [self.model.item(i).data(PATH_ROLE) for i in range(count)]
        work_dir = tempfile.gettempdir()

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setValue(0)
        self._worker = MergeWorker(inputs, output, self.reencode_chk.isChecked(), work_dir)
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
