"""悬浮窗主界面：无边框、置顶、可拖拽，可在「小药丸 / 完整面板」间切换。

完整面板包含：
- 当前检测卡片：标题、格式选择、立即下载、进度；
- 三个列表：嗅探发现（含抓到的防盗链请求头）、剪贴板历史、日志；
- 流量嗅探开关（联动系统代理）、设置、下载目录、退出。
"""
from __future__ import annotations

import os
import re
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QColor, QGuiApplication, QIcon, QPainter, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from core.ytdlp_service import list_formats
from ui.workers import M3U8Worker, YtdlpDownloadWorker, YtdlpParseWorker

from .clipboard_watcher import ClipboardWatcher
from .proxy_sniffer import SnifferWorker
from .settings_dialog import SettingsDialog
from . import win_proxy

_QSS = """
#card {
    background: #1f2430;
    border: 1px solid #3a4152;
    border-radius: 14px;
}
QLabel { color: #e6e9ef; background: transparent; }
QPushButton {
    background: #2d3446; color: #e6e9ef;
    border: 1px solid #454e64; border-radius: 7px;
    padding: 5px 12px;
}
QPushButton:hover { background: #38405a; }
QPushButton:checked { background: #3d6fd6; border-color: #5a8bee; }
QPushButton:disabled { color: #7a8299; background: #262c3a; }
QComboBox, QListWidget {
    background: #262c3a; color: #e6e9ef;
    border: 1px solid #454e64; border-radius: 7px;
}
QComboBox { padding: 4px 8px; }
QComboBox QAbstractItemView {
    background: #262c3a; color: #e6e9ef; selection-background-color: #3d6fd6;
}
QTabWidget::pane { border: 1px solid #454e64; border-radius: 7px; }
QTabBar::tab {
    background: #2d3446; color: #b9c0d0;
    padding: 5px 14px; border-top-left-radius: 7px;
    border-top-right-radius: 7px;
}
QTabBar::tab:selected { background: #3d6fd6; color: white; }
QProgressBar {
    background: #262c3a; border: 1px solid #454e64; border-radius: 7px;
    text-align: center; color: #e6e9ef; height: 18px;
}
QProgressBar::chunk {
    background-color: #4a8cff; border-radius: 6px;
}
"""


def make_app_icon() -> QIcon:
    """程序化生成应用图标，避免外部资源文件。"""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#4a8cff"))
    painter.drawRoundedRect(4, 4, 56, 56, 14, 14)
    painter.setBrush(QColor("white"))
    painter.drawPolygon(QPolygonF([
        QPointF(26, 19), QPointF(26, 45), QPointF(47, 32)]))
    painter.end()
    return QIcon(pix)


class _DragHandle(QWidget):
    """标题拖拽条，拖动可移动悬浮窗，双击可收起/展开。"""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self._offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._offset = (event.globalPosition().toPoint()
                            - self._window.frameGeometry().topLeft())

    def mouseMoveEvent(self, event):
        if self._offset is not None and event.buttons() & Qt.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._offset)

    def mouseReleaseEvent(self, event):
        self._offset = None
        self._window.save_position()

    def mouseDoubleClickEvent(self, event):
        self._window.toggle_collapse()


class FloatWindow(QWidget):
    def __init__(self, settings):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(_QSS)
        self.setFixedWidth(440)

        self._settings = settings
        self.notify = None  # 由入口注入：notify(title, msg)

        self._current_url = ""
        self._current_kind = "page"       # page / direct_media
        self._current_headers: dict = {}  # 直链附带的 referer/ua/cookie
        self._formats: list[dict] = []

        self._parse_worker = None
        self._worker = None               # 下载类 worker
        self._sniffer: SnifferWorker | None = None
        self._proxy_backup: dict | None = None

        # ---- 卡片容器 ----
        card = QWidget()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 12)
        card_layout.setSpacing(8)

        # ---- 完整面板 ----
        self._expanded = QWidget()
        exp_layout = QVBoxLayout(self._expanded)
        exp_layout.setContentsMargins(0, 0, 0, 0)
        exp_layout.setSpacing(8)

        header = _DragHandle(self)
        hh = QHBoxLayout(header)
        hh.setContentsMargins(2, 2, 2, 2)
        title = QLabel("视频悬浮嗅探")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        self.chip_clipboard = QLabel()
        self.chip_sniffer = QLabel()
        self.btn_sniffer_toggle = QPushButton("开启嗅探")
        self.btn_sniffer_toggle.setCheckable(True)
        self.btn_sniffer_toggle.clicked.connect(self._toggle_sniffer)
        btn_collapse = QPushButton("收起")
        btn_collapse.clicked.connect(self.collapse)
        btn_hide = QPushButton("隐藏")
        btn_hide.clicked.connect(self.hide)
        hh.addWidget(title)
        hh.addStretch()
        hh.addWidget(self.chip_clipboard)
        hh.addWidget(self.chip_sniffer)
        hh.addWidget(self.btn_sniffer_toggle)
        hh.addWidget(btn_collapse)
        hh.addWidget(btn_hide)

        # 当前检测卡片
        self.lbl_title = QLabel("等待链接：复制视频链接即可自动识别")
        self.lbl_title.setWordWrap(True)
        tf = self.lbl_title.font()
        tf.setBold(True)
        self.lbl_title.setFont(tf)
        self.lbl_source = QLabel("")
        self.lbl_source.setStyleSheet("color:#9aa3b5")
        self.format_combo = QComboBox()
        self.format_combo.setMinimumWidth(360)
        self.format_combo.setEnabled(False)

        action_row = QHBoxLayout()
        self.btn_download = QPushButton("立即下载")
        self.btn_download.clicked.connect(self._download_current)
        self.btn_reparse = QPushButton("重新解析")
        self.btn_reparse.clicked.connect(self._reparse_current)
        self.btn_cancel = QPushButton("取消任务")
        self.btn_cancel.clicked.connect(self._cancel_worker)
        self.btn_cancel.setEnabled(False)
        action_row.addWidget(self.btn_download)
        action_row.addWidget(self.btn_reparse)
        action_row.addWidget(self.btn_cancel)
        action_row.addStretch()

        prog_row = QHBoxLayout()
        self.progress = QProgressBar()
        self.lbl_pct = QLabel("")
        self.lbl_pct.setFixedWidth(46)
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.lbl_pct)

        # 列表
        self.tabs = QTabWidget()
        self.sniffer_list = QListWidget()
        self.clip_list = QListWidget()
        self.log_list = QListWidget()
        self.tabs.addTab(self._wrap_list(
            self.sniffer_list, [
                ("下载选中", self._download_sniffer_selected),
                ("复制 URL", self._copy_sniffer_url),
                ("清空", lambda: self.sniffer_list.clear())]),
            "嗅探发现")
        self.tabs.addTab(self._wrap_list(
            self.clip_list, [
                ("解析下载选中", self._download_clip_selected),
                ("清空", lambda: self.clip_list.clear())]),
            "剪贴板历史")
        self.tabs.addTab(self.log_list, "日志")

        footer = QHBoxLayout()
        btn_settings = QPushButton("设置")
        btn_settings.clicked.connect(self._open_settings)
        btn_open_dir = QPushButton("打开下载目录")
        btn_open_dir.clicked.connect(self._open_download_dir)
        btn_quit = QPushButton("退出")
        btn_quit.clicked.connect(self.quit_app)
        footer.addWidget(btn_settings)
        footer.addWidget(btn_open_dir)
        footer.addStretch()
        footer.addWidget(btn_quit)

        exp_layout.addWidget(header)
        exp_layout.addWidget(self.lbl_title)
        exp_layout.addWidget(self.lbl_source)
        exp_layout.addWidget(self.format_combo)
        exp_layout.addLayout(action_row)
        exp_layout.addLayout(prog_row)
        exp_layout.addWidget(self.tabs, 1)
        exp_layout.addLayout(footer)

        # ---- 小药丸 ----
        self._pill = QWidget()
        pill_handle = _DragHandle(self)
        ph = QHBoxLayout(pill_handle)
        ph.setContentsMargins(6, 4, 6, 4)
        self.pill_dot = QLabel("●")
        self.pill_text = QLabel("监听中…")
        btn_pill_expand = QPushButton("展开")
        btn_pill_expand.clicked.connect(self.expand)
        btn_pill_hide = QPushButton("×")
        btn_pill_hide.setFixedWidth(26)
        btn_pill_hide.clicked.connect(self.hide)
        ph.addWidget(self.pill_dot)
        ph.addWidget(self.pill_text, 1)
        ph.addWidget(btn_pill_expand)
        ph.addWidget(btn_pill_hide)
        pill_outer = QHBoxLayout(self._pill)
        pill_outer.setContentsMargins(0, 0, 0, 0)
        pill_outer.addWidget(pill_handle)

        card_layout.addWidget(self._expanded)
        card_layout.addWidget(self._pill)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(card)

        # ---- 剪贴板监听 ----
        self._watcher = ClipboardWatcher(QGuiApplication.clipboard())
        self._watcher.enabled = (
            settings.value("clipboard_enabled", "true") == "true")
        self._watcher.link_found.connect(self._on_clipboard_link)

        self._refresh_chips()
        self.expand()
        self._restore_position()

    # ---------- 小工具 ----------

    def _wrap_list(self, list_widget, buttons):
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        row = QHBoxLayout()
        for text, slot in buttons:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch()
        v.addLayout(row)
        v.addWidget(list_widget, 1)
        return box

    def _log(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_list.addItem(f"{stamp}  {msg}")
        self.log_list.scrollToBottom()
        # 同步落盘到 ~/.videosniffer/sniffer.log，便于事后排查
        try:
            log_path = Path.home() / ".videosniffer" / "sniffer.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d')} {stamp}  {msg}\n")
        except Exception:
            pass

    def _download_dir(self) -> Path:
        path = Path(self._settings.value(
            "download_dir", str(Path.home() / "Videos" / "videotool")))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _cookie_file(self) -> str:
        return self._settings.value("cookie_file", "")

    def _refresh_chips(self) -> None:
        clip_on = self._watcher.enabled
        sniffer_on = self._sniffer is not None and self._sniffer.isRunning()
        self.chip_clipboard.setText("剪贴板:开" if clip_on else "剪贴板:关")
        self.chip_clipboard.setStyleSheet(
            "color:#4cd964" if clip_on else "color:#ff6b6b")
        self.chip_sniffer.setText("嗅探:开" if sniffer_on else "嗅探:关")
        self.chip_sniffer.setStyleSheet(
            "color:#4cd964" if sniffer_on else "color:#9aa3b5")
        self.pill_dot.setStyleSheet(
            "color:#4cd964" if clip_on or sniffer_on else "color:#9aa3b5")
        self.btn_sniffer_toggle.blockSignals(True)
        self.btn_sniffer_toggle.setChecked(sniffer_on)
        self.btn_sniffer_toggle.setText(
            "关闭嗅探" if sniffer_on else "开启嗅探")
        self.btn_sniffer_toggle.blockSignals(False)

    # ---------- 收起 / 展开 / 位置 ----------

    def toggle_collapse(self) -> None:
        if self._expanded.isVisible():
            self.collapse()
        else:
            self.expand()

    def collapse(self) -> None:
        self._expanded.setVisible(False)
        self._pill.setVisible(True)
        self.setFixedHeight(72)

    def expand(self) -> None:
        self._pill.setVisible(False)
        self._expanded.setVisible(True)
        self.setFixedHeight(560)

    def show_expanded(self) -> None:
        self.expand()
        self.show()
        self.raise_()

    def save_position(self) -> None:
        p = self.pos()
        self._settings.setValue("float_pos", f"{p.x()},{p.y()}")

    def _restore_position(self) -> None:
        raw = self._settings.value("float_pos", "")
        if raw:
            try:
                x, y = (int(v) for v in raw.split(","))
                # 安全检查：位置必须在某块屏幕的可见区域内
                screen = QGuiApplication.screenAt(
                    QPointF(x + self.width() / 2, y + self.height() / 2)
                    .toPoint())
                if screen:
                    avail = screen.availableGeometry()
                    if (avail.left() <= x <= avail.right() - 50 and
                            avail.top() <= y <= avail.bottom() - 50):
                        self.move(x, y)
                        return
            except ValueError:
                pass
        screen = QGuiApplication.screenAt(QGuiApplication.primaryScreen()
                                          .availableGeometry().center())
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width() - 24,
                  geo.top() + 80)

    def closeEvent(self, event):
        # 没有任务栏入口，任何关闭动作都按「隐藏到托盘」处理
        event.ignore()
        self.hide()

    # ---------- 剪贴板 ----------

    def _on_clipboard_link(self, url: str, kind: str) -> None:
        host = urllib.parse.urlparse(url).hostname or ""
        self.clip_list.addItem(f"[{kind}] {host}  {url}")
        self._set_current(url, kind, {})
        if kind == "page" and \
                self._settings.value("auto_parse", "true") == "true":
            self._start_parse(url)
        else:
            self.lbl_title.setText(f"检测到直链（{host}），点击「立即下载」")
        self.pill_text.setText("● 检测到视频，点展开查看")
        self.show_expanded()
        if self.notify:
            self.notify("检测到视频链接", host)

    def _set_current(self, url: str, kind: str, headers: dict) -> None:
        self._current_url = url
        self._current_kind = kind
        self._current_headers = headers
        self.format_combo.clear()
        self.format_combo.setVisible(kind == "page")
        self.format_combo.setEnabled(False)
        self.btn_reparse.setVisible(kind == "page")
        host = urllib.parse.urlparse(url).hostname or ""
        self.lbl_source.setText(host)

    def _reparse_current(self) -> None:
        if self._current_url and self._current_kind == "page":
            self._start_parse(self._current_url)

    # ---------- 解析 ----------

    def _start_parse(self, url: str) -> None:
        self.lbl_title.setText("解析中…")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.lbl_pct.setText("")
        self._parse_worker = YtdlpParseWorker(url, self._cookie_file())
        self._parse_worker.parsed.connect(self._on_parsed)
        self._parse_worker.error.connect(self._on_parse_error)
        self._parse_worker.start()

    def _on_parsed(self, info: dict) -> None:
        self._formats = list_formats(info)
        duration = info.get("duration") or 0
        m, s = divmod(int(duration), 60)
        self.lbl_title.setText(
            f"{info.get('title', '未知标题')}  （{m:02d}:{s:02d}）")
        self.format_combo.clear()
        self.format_combo.setEnabled(bool(self._formats))
        for fmt in self._formats:
            size = f"{fmt['filesize_mb']}MB" if fmt["filesize_mb"] else "大小未知"
            self.format_combo.addItem(
                f"{fmt['format_id']}  {fmt['resolution']}  {fmt['ext']}"
                f"  {size}  {fmt['note']}",
                userData=fmt["format_id"])

    def _on_parse_error(self, msg: str) -> None:
        self.lbl_title.setText(f"解析失败：{msg}")
        self._log("解析失败：" + msg)

    # ---------- 下载 ----------

    def _download_current(self) -> None:
        url = self._current_url
        if not url:
            return
        if self._current_kind == "page":
            fmt = self.format_combo.currentData() or ""
            worker = YtdlpDownloadWorker(
                url, str(self._download_dir()), fmt, self._cookie_file())
        else:
            worker = self._direct_worker(url, self._current_headers)
        self._run_worker(worker)

    def _direct_worker(self, url: str, headers: dict) -> M3U8Worker:
        output = self._direct_output(url)
        self.lbl_title.setText(f"下载直链 → {Path(output).name}")
        # ffmpeg 不读系统代理，嗅探运行中时经 http_proxy 环境变量走嗅探代理。
        # 走盲转发专用端口（主端口 +1）：ffmpeg 的 Schannel 栈无法与 MITM
        # 自签证书握手，见 proxy_sniffer._serve 的注释。
        proxy = None
        if self._sniffer is not None and self._sniffer.isRunning():
            port = int(self._settings.value("sniffer_port", 8899))
            proxy = f"http://127.0.0.1:{port + 1}"
        return M3U8Worker(
            url, output,
            headers.get("referer", ""),
            headers.get("user_agent", ""),
            headers.get("cookie", ""),
            proxy=proxy)

    def _direct_output(self, url: str) -> str:
        stem = Path(urllib.parse.urlparse(url).path).stem \
            or f"video_{int(time.time())}"
        stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)[:80]
        base = self._download_dir()
        candidate = base / f"{stem}.mp4"
        idx = 1
        while candidate.exists():
            candidate = base / f"{stem}_{idx}.mp4"
            idx += 1
        return str(candidate)

    def _run_worker(self, worker) -> None:
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.log.connect(self._log)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_worker_error)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.btn_download.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        worker.start()

    def _on_progress(self, value: int) -> None:
        self.progress.setValue(value)
        self.lbl_pct.setText(f"{value}%")

    def _on_finished(self, msg: str) -> None:
        self.progress.setValue(100)
        self.lbl_pct.setText("100%")
        self.btn_download.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log("完成：" + msg)
        if self.notify:
            self.notify("下载完成", msg)

    def _on_worker_error(self, msg: str) -> None:
        self.btn_download.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log("失败：" + msg)
        # 下载失败时把当前 URL 和错误详情一并落盘，便于排查
        try:
            log_path = Path.home() / ".videosniffer" / "sniffer.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"  ↳ URL={self._current_url} ext={self._current_headers.get('ext','')} "
                        f"referer={self._current_headers.get('referer','')[:80]}\n")
        except Exception:
            pass

    def _cancel_worker(self) -> None:
        if self._worker:
            self._worker.stop()
        self.btn_cancel.setEnabled(False)

    # ---------- 嗅探列表操作 ----------

    def _selected_sniffer_item(self) -> dict | None:
        item = self.sniffer_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _download_sniffer_selected(self) -> None:
        data = self._selected_sniffer_item()
        if not data:
            return
        self.tabs.setCurrentIndex(0)
        self._set_current(data["url"], "direct_media", data)
        self.lbl_title.setText(
            f"嗅探到 {data['ext']} 直链（{data['host']}），开始下载…")
        self._run_worker(self._direct_worker(data["url"], data))

    def _copy_sniffer_url(self) -> None:
        data = self._selected_sniffer_item()
        if data:
            QGuiApplication.clipboard().setText(data["url"])

    def _download_clip_selected(self) -> None:
        item = self.clip_list.currentItem()
        if not item:
            return
        text = item.text()
        # 列表项格式 "[kind] host  url"
        match = re.search(r"https?://\S+", text)
        if not match:
            return
        url = match.group(0)
        kind = "direct_media" if text.startswith("[direct_media]") else "page"
        self._set_current(url, kind, {})
        if kind == "page":
            self._start_parse(url)
        else:
            self.lbl_title.setText("检测到直链，点击「立即下载」")

    # ---------- 流量嗅探开关 ----------

    def _toggle_sniffer(self) -> None:
        if self._sniffer is not None and self._sniffer.isRunning():
            self.stop_sniffer()
        else:
            self.start_sniffer()

    def start_sniffer(self) -> None:
        port = int(self._settings.value("sniffer_port", 8899))
        self._log(f"正在启动嗅探代理（端口 {port}）…")
        self._sniffer = SnifferWorker(port)
        self._sniffer.discovered.connect(self._on_discovered)
        self._sniffer.status.connect(self._on_sniffer_status)
        self._sniffer.failed.connect(self._on_sniffer_failed)
        self._sniffer.start()

    def stop_sniffer(self) -> None:
        if self._proxy_backup is not None:
            win_proxy.restore(self._proxy_backup)
            self._proxy_backup = None
            self._log("已恢复系统代理设置")
        if self._sniffer is not None:
            self._sniffer.stop()
            self._sniffer.wait(3000)
            self._sniffer = None
        self._refresh_chips()

    def _on_sniffer_status(self, msg: str) -> None:
        self._log(msg)
        if "运行中" in msg and self._proxy_backup is None:
            port = int(self._settings.value("sniffer_port", 8899))
            self._proxy_backup = win_proxy.enable("127.0.0.1", port)
            self._log("系统代理已指向本机嗅探代理")
        self._refresh_chips()

    def _on_sniffer_failed(self, msg: str) -> None:
        self._log("嗅探启动失败：" + msg)
        if self._proxy_backup is not None:
            win_proxy.restore(self._proxy_backup)
            self._proxy_backup = None
        self._sniffer = None
        self._refresh_chips()

    def _on_discovered(self, data: dict) -> None:
        path = urllib.parse.urlparse(data["url"]).path
        short_name = Path(path).name or data["url"]
        self.sniffer_list.addItem(
            QListWidgetItem(
                f"{data['time']} [{data['ext']}] {data['host']}/…{short_name}",
                self.sniffer_list))
        self.sniffer_list.item(self.sniffer_list.count() - 1) \
            .setData(Qt.UserRole, data)
        self._set_current(data["url"], "direct_media", data)
        self.lbl_title.setText(
            f"嗅探到 {data['ext']} 直链（{data['host']}），点击「立即下载」")
        self.pill_text.setText("● 嗅探到媒体，点展开查看")
        if not self.isVisible():
            self.show_expanded()
        if self.notify:
            self.notify(f"嗅探到 {data['ext']}", data["host"])

    # ---------- 其它 ----------

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec():
            self._watcher.enabled = (
                self._settings.value("clipboard_enabled", "true") == "true")
            self._refresh_chips()
            self._log("设置已保存")

    def _open_download_dir(self) -> None:
        os.startfile(str(self._download_dir()))

    def set_clipboard_enabled(self, on: bool) -> None:
        self._watcher.enabled = on
        self._settings.setValue("clipboard_enabled", "true" if on else "false")
        self._refresh_chips()

    def quit_app(self) -> None:
        self.stop_sniffer()
        self.save_position()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().quit()
