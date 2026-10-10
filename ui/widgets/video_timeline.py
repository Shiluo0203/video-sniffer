"""可视化时间轴：缩略图条 + 可拖拽的起点/终点手柄 + 播放头。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget


def format_ms(ms: int) -> str:
    """毫秒 -> HH:MM:SS（不足 1 小时显示 MM:SS）。"""
    ms = max(0, int(ms))
    s, ms = divmod(ms, 1000)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def parse_time(text: str) -> int:
    """解析时间文本为毫秒。支持 '90'、'1:30'、'01:23'、'1:02:03'。失败抛 ValueError。"""
    text = text.strip()
    if not text:
        raise ValueError("时间为空")
    parts = text.split(":")
    values = [float(x) for x in parts]
    if len(values) == 1:
        seconds = values[0]
    elif len(values) == 2:
        seconds = values[0] * 60 + values[1]
    elif len(values) == 3:
        seconds = values[0] * 3600 + values[1] * 60 + values[2]
    else:
        raise ValueError("时间格式错误")
    return int(seconds * 1000)


class VideoTimelineWidget(QWidget):
    """显示视频缩略图条，支持拖拽左右手柄选择剪切区间、点击定位播放头。"""

    rangeChanged = Signal(int, int)   # start_ms, end_ms
    seekRequested = Signal(int)       # 用户点击时间轴请求跳转

    _PADDING = 4          # 两侧内边距
    _TOP = 18             # 顶部时间标签预留
    _HANDLE = 10          # 手柄宽度（像素）
    _MIN_SPAN_MS = 200    # 选区最小跨度

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(98)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

        self._duration = 0
        self._start = 0
        self._end = 0
        self._playhead = 0
        self._thumbs: list[QPixmap] = []

        self._drag: str | None = None  # 'start' | 'end' | None

    # ---------- 公共 API ----------

    def set_duration(self, duration_ms: int):
        self._duration = max(0, duration_ms)
        self._start = 0
        self._end = self._duration
        self._playhead = 0
        self.update()

    def set_thumbs(self, paths: list[str]):
        thumbs = []
        for p in paths:
            pix = QPixmap(p)
            if not pix.isNull():
                thumbs.append(pix)
        self._thumbs = thumbs
        self.update()

    def clear_thumbs(self):
        self._thumbs = []
        self.update()

    def range_ms(self) -> tuple[int, int]:
        return self._start, self._end

    def set_range(self, start_ms: int, end_ms: int, emit: bool = True):
        self._start = self._clamp_start(int(start_ms))
        self._end = self._clamp_end(int(end_ms))
        self.update()
        if emit:
            self.rangeChanged.emit(self._start, self._end)

    def set_playhead(self, ms: int):
        self._playhead = max(0, min(int(ms), self._duration))
        self.update()

    # ---------- 坐标换算 ----------

    def _track_rect(self) -> QRect:
        return QRect(self._PADDING, self._TOP,
                     max(1, self.width() - 2 * self._PADDING),
                     self.height() - self._TOP - self._PADDING)

    def _x_to_ms(self, x: int) -> int:
        r = self._track_rect()
        if self._duration <= 0 or r.width() <= 0:
            return 0
        ratio = (x - r.left()) / r.width()
        return int(max(0, min(1, ratio)) * self._duration)

    def _ms_to_x(self, ms: int) -> int:
        r = self._track_rect()
        if self._duration <= 0:
            return r.left()
        return r.left() + int(ms / self._duration * r.width())

    def _clamp_start(self, v: int) -> int:
        return max(0, min(v, self._end - self._MIN_SPAN_MS))

    def _clamp_end(self, v: int) -> int:
        return min(self._duration, max(v, self._start + self._MIN_SPAN_MS))

    # ---------- 绘制 ----------

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = self._track_rect()

        # 背景
        p.fillRect(r, QColor(30, 30, 30))

        # 缩略图平铺（裁剪填充）
        if self._thumbs:
            n = len(self._thumbs)
            cw = r.width() / n
            for i, thumb in enumerate(self._thumbs):
                cell = QRect(r.left() + int(i * cw), r.top(),
                             int(cw) + 1, r.height())
                self._draw_cover(p, thumb, cell)

        # 非选区暗化遮罩
        sx, ex = self._ms_to_x(self._start), self._ms_to_x(self._end)
        p.fillRect(QRect(r.left(), r.top(), sx - r.left(), r.height()),
                   QColor(0, 0, 0, 150))
        p.fillRect(QRect(ex, r.top(), r.right() - ex, r.height()),
                   QColor(0, 0, 0, 150))

        # 选区边框 + 手柄
        green = QColor(76, 175, 80)
        p.setPen(QPen(green, 2))
        p.drawRect(QRect(sx, r.top(), ex - sx, r.height()))
        for hx in (sx, ex):
            p.fillRect(QRect(hx - self._HANDLE // 2, r.top(),
                             self._HANDLE, r.height()), green)
            # 顶部三角抓手
            p.setPen(Qt.NoPen)
            p.setBrush(green)
            p.drawPolygon([
                QPoint(hx - 6, r.top() - 2),
                QPoint(hx + 6, r.top() - 2),
                QPoint(hx, r.top() + 6),
            ])
            p.setPen(QPen(green, 2))
            p.setBrush(Qt.NoBrush)

        # 播放头（红线 + 顶部圆点）
        px = self._ms_to_x(self._playhead)
        p.setPen(QPen(QColor(244, 67, 54), 2))
        p.drawLine(px, r.top() - 2, px, r.bottom())
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(244, 67, 54))
        p.drawEllipse(QPoint(px, r.top() - 4), 4, 4)

        # 时间文字
        p.setPen(QColor(220, 220, 220))
        fm = QFontMetrics(self.font())
        p.drawText(QRect(sx - 60, 0, 120, self._TOP - 2),
                   Qt.AlignCenter, format_ms(self._start))
        p.drawText(QRect(ex - 60, 0, 120, self._TOP - 2),
                   Qt.AlignCenter, format_ms(self._end))
        # 播放头时间（左下小字）
        p.setPen(QColor(255, 138, 128))
        p.drawText(QRect(r.left(), self.height() - 16, 110, 16),
                   Qt.AlignLeft | Qt.AlignVCenter, format_ms(self._playhead))

    @staticmethod
    def _draw_cover(p: QPainter, pix: QPixmap, cell: QRect):
        """按 cover 方式（裁剪填满）绘制缩略图。"""
        if pix.isNull():
            return
        pw, ph = pix.width(), pix.height()
        if pw <= 0 or ph <= 0:
            return
        scale = max(cell.width() / pw, cell.height() / ph)
        dw, dh = int(pw * scale), int(ph * scale)
        x = cell.left() + (cell.width() - dw) // 2
        y = cell.top() + (cell.height() - dh) // 2
        p.drawPixmap(QRect(x, y, dw, dh), pix)

    # ---------- 鼠标交互 ----------

    def _hit_handle(self, x: int) -> str | None:
        if abs(x - self._ms_to_x(self._start)) <= self._HANDLE:
            return "start"
        if abs(x - self._ms_to_x(self._end)) <= self._HANDLE:
            return "end"
        return None

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() != Qt.LeftButton or self._duration <= 0:
            return
        hit = self._hit_handle(e.position().x())
        if hit:
            self._drag = hit
        else:
            self.seekRequested.emit(self._x_to_ms(e.position().x()))

    def mouseMoveEvent(self, e: QMouseEvent):
        if not self._drag or self._duration <= 0:
            # 悬停时在手柄上显示横向调整光标
            hit = self._hit_handle(e.position().x())
            self.setCursor(Qt.SizeHorCursor if hit else Qt.PointingHandCursor)
            return
        ms = self._x_to_ms(e.position().x())
        if self._drag == "start":
            self.set_range(ms, self._end)
        else:
            self.set_range(self._start, ms)

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag = None
