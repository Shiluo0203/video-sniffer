"""悬浮嗅探设置对话框。

下载目录与 Cookie 文件沿用主工具箱的 QSettings（videotool/videotool），两边共享；
其余为本工具专属设置。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from .proxy_sniffer import ca_cert_path


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("悬浮嗅探设置")
        self.setMinimumWidth(520)
        self._settings = settings

        # ---- 下载目录 ----
        self.dir_edit = QLineEdit(settings.value(
            "download_dir", str(Path.home() / "Videos" / "videotool")))
        btn_dir = QPushButton("浏览…")
        btn_dir.clicked.connect(self._pick_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("下载目录:"))
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(btn_dir)

        # ---- Cookie ----
        self.cookie_edit = QLineEdit(settings.value("cookie_file", ""))
        self.cookie_edit.setPlaceholderText(
            "可选，Netscape cookies.txt，用于需要登录的站点")
        btn_cookie = QPushButton("浏览…")
        btn_cookie.clicked.connect(self._pick_cookie)
        cookie_row = QHBoxLayout()
        cookie_row.addWidget(QLabel("Cookie 文件:"))
        cookie_row.addWidget(self.cookie_edit, 1)
        cookie_row.addWidget(btn_cookie)

        # ---- 监听开关 ----
        self.chk_clipboard = QCheckBox("启用剪贴板监听（复制链接自动识别）")
        self.chk_clipboard.setChecked(
            settings.value("clipboard_enabled", "true") == "true")
        self.chk_auto_parse = QCheckBox(
            "检测到网页链接后自动解析标题（关闭则只记录，不联网）")
        self.chk_auto_parse.setChecked(
            settings.value("auto_parse", "true") == "true")
        self.chk_sniffer = QCheckBox("启动程序时自动开启流量嗅探")
        self.chk_sniffer.setChecked(
            settings.value("sniffer_autostart", "false") == "true")

        # ---- 端口 ----
        port_row = QHBoxLayout()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(int(settings.value("sniffer_port", 8899)))
        port_row.addWidget(QLabel("嗅探代理端口:"))
        port_row.addWidget(self.port_spin)
        port_row.addStretch()

        # ---- CA 证书 ----
        self.ca_label = QLabel()
        self.ca_label.setWordWrap(True)
        self._refresh_ca_status()
        btn_ca_install = QPushButton("安装/修复 CA 证书")
        btn_ca_install.clicked.connect(self._install_ca)
        btn_ca_show = QPushButton("打开证书位置")
        btn_ca_show.clicked.connect(self._show_ca)
        ca_row = QHBoxLayout()
        ca_row.addWidget(QLabel("根证书:"))
        ca_row.addWidget(self.ca_label, 1)
        ca_row.addWidget(btn_ca_install)
        ca_row.addWidget(btn_ca_show)

        hint = QLabel(
            "提示：流量嗅探需安装本工具的 CA 证书，浏览器才会正常工作；"
            "做了证书固定的 App 无法被嗅探。不使用嗅探时可随时关闭，"
            "剪贴板监听不受影响。")
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        v = QVBoxLayout(self)
        v.addLayout(dir_row)
        v.addLayout(cookie_row)
        v.addWidget(self.chk_clipboard)
        v.addWidget(self.chk_auto_parse)
        v.addWidget(self.chk_sniffer)
        v.addLayout(port_row)
        v.addLayout(ca_row)
        v.addWidget(hint)
        v.addWidget(buttons)

    # ---------- 选择器 ----------

    def _pick_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择下载目录")
        if path:
            self.dir_edit.setText(path)

    def _pick_cookie(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Cookie 文件", "",
            "Cookies (*.txt);;所有文件 (*)")
        if path:
            self.cookie_edit.setText(path)

    # ---------- CA ----------

    def _refresh_ca_status(self):
        cert = ca_cert_path()
        self.ca_label.setText(
            f"{'已生成' if cert.exists() else '尚未生成（开启嗅探时自动生成）'}"
            f"：{cert}")

    def _show_ca(self):
        cert = ca_cert_path()
        if not cert.exists():
            QMessageBox.information(self, "提示", "证书尚未生成，开启一次嗅探后即会自动创建")
            return
        subprocess.Popen(["explorer", "/select,", str(cert)])

    def _install_ca(self):
        cert = ca_cert_path()
        if not cert.exists():
            # 触发一次生成
            from .proxy_sniffer import ensure_ca
            ensure_ca()
            self._refresh_ca_status()
        # 安装到当前用户的根证书存储，系统会弹安全确认框
        result = subprocess.run(
            ["certutil", "-user", "-addstore", "Root", str(cert)],
            capture_output=True, text=True)
        if result.returncode == 0:
            QMessageBox.information(
                self, "完成", "CA 证书已安装。若浏览器仍提示不安全，请重启浏览器后重试。")
        else:
            QMessageBox.warning(
                self, "安装失败",
                (result.stderr or result.stdout or "certutil 执行失败"))

    # ---------- 保存 ----------

    def _save(self):
        s = self._settings
        s.setValue("download_dir", self.dir_edit.text().strip())
        s.setValue("cookie_file", self.cookie_edit.text().strip())
        s.setValue("clipboard_enabled",
                   "true" if self.chk_clipboard.isChecked() else "false")
        s.setValue("auto_parse",
                   "true" if self.chk_auto_parse.isChecked() else "false")
        s.setValue("sniffer_autostart",
                   "true" if self.chk_sniffer.isChecked() else "false")
        s.setValue("sniffer_port", self.port_spin.value())
        self.accept()
