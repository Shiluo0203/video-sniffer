"""Windows 系统代理开关（当前用户，无需管理员）。

开启前备份原有 ProxyEnable/ProxyServer/ProxyOverride，停止或退出时原样恢复。
修改注册表后通过 WinINet InternetSetOption 通知系统立即生效。
"""
from __future__ import annotations

import ctypes
import winreg

_INTERNET_SETTINGS = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
_INTERNET_OPTION_SETTINGS_CHANGED = 39
_INTERNET_OPTION_REFRESH = 37


def _notify_system() -> None:
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(0, _INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
    wininet.InternetSetOptionW(0, _INTERNET_OPTION_REFRESH, 0, 0)


def _query(key: str):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _INTERNET_SETTINGS) as h:
        try:
            value, _ = winreg.QueryValueEx(h, key)
            return value
        except FileNotFoundError:
            return None


def read_config() -> dict:
    """读取当前代理配置，用于恢复。"""
    return {
        "enable": int(_query("ProxyEnable") or 0),
        "server": str(_query("ProxyServer") or ""),
        "override": str(_query("ProxyOverride") or ""),
    }


def enable(server_host: str, server_port: int) -> dict:
    """把系统代理指向本机代理，返回备份配置。"""
    backup = read_config()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE
    ) as h:
        winreg.SetValueEx(h, "ProxyServer", 0, winreg.REG_SZ,
                          f"{server_host}:{server_port}")
        winreg.SetValueEx(h, "ProxyEnable", 0, winreg.REG_DWORD, 1)
        if not backup["override"]:
            winreg.SetValueEx(h, "ProxyOverride", 0, winreg.REG_SZ,
                              "localhost;127.*;10.*;172.16.*;192.168.*;<local>")
    _notify_system()
    return backup


def restore(backup: dict) -> None:
    """根据备份恢复系统代理。"""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _INTERNET_SETTINGS, 0, winreg.KEY_SET_VALUE
    ) as h:
        winreg.SetValueEx(h, "ProxyEnable", 0, winreg.REG_DWORD,
                          int(backup.get("enable", 0)))
        server = backup.get("server", "")
        if server:
            winreg.SetValueEx(h, "ProxyServer", 0, winreg.REG_SZ, server)
        override = backup.get("override", "")
        if override:
            winreg.SetValueEx(h, "ProxyOverride", 0, winreg.REG_SZ, override)
    _notify_system()
