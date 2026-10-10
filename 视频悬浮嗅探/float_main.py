"""悬浮窗视频嗅探工具入口（独立程序，复用 core 与 ui.workers）。

用法：python float_main.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

# 确保可以导入 core / ui（位于上级项目根）与本级 floatapp
tool_dir = Path(__file__).resolve().parent
project_root = tool_dir.parent
for _p in (str(tool_dir), str(project_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("视频悬浮嗅探")
    app.setQuitOnLastWindowClosed(False)  # 悬浮窗隐藏后程序仍驻留托盘

    # 与主工具箱共用同一组设置（下载目录、Cookie）
    settings = QSettings("videotool", "videotool")

    from floatapp.float_window import FloatWindow, make_app_icon
    icon = make_app_icon()
    app.setWindowIcon(icon)
    win = FloatWindow(settings)

    # ---------------- 托盘菜单 ----------------
    tray = QSystemTrayIcon(icon)
    menu = QMenu()

    act_show = QAction("显示/隐藏悬浮窗", menu)
    act_clip = QAction("剪贴板监听", menu)
    act_clip.setCheckable(True)
    act_sniff = QAction("流量嗅探", menu)
    act_sniff.setCheckable(True)
    act_settings = QAction("设置…", menu)
    act_dir = QAction("打开下载目录", menu)
    act_quit = QAction("退出", menu)

    menu.addAction(act_show)
    menu.addSeparator()
    menu.addAction(act_clip)
    menu.addAction(act_sniff)
    menu.addSeparator()
    menu.addAction(act_settings)
    menu.addAction(act_dir)
    menu.addSeparator()
    menu.addAction(act_quit)

    def show_or_hide():
        if win.isVisible():
            win.hide()
        else:
            win.show_expanded()

    def sync_menu():
        act_clip.setChecked(
            settings.value("clipboard_enabled", "true") == "true")
        act_sniff.setChecked(
            win._sniffer is not None and win._sniffer.isRunning())

    act_show.triggered.connect(show_or_hide)
    act_clip.triggered.connect(win.set_clipboard_enabled)
    act_sniff.triggered.connect(
        lambda on: win.start_sniffer() if on else win.stop_sniffer())
    act_settings.triggered.connect(win._open_settings)
    act_dir.triggered.connect(win._open_download_dir)
    act_quit.triggered.connect(win.quit_app)
    menu.aboutToShow.connect(sync_menu)

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: show_or_hide()
        if reason == QSystemTrayIcon.Trigger else None)

    win.notify = lambda title, msg: tray.showMessage(title, msg, icon, 3000)

    tray.show()
    win.show()

    if settings.value("sniffer_autostart", "false") == "true":
        win.start_sniffer()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
