"""m3u8/HLS 流合并：组装 ffmpeg 参数（支持自定义请求头应对防盗链）。

AES-128 加密的 HLS 无需特殊处理：只要 key 地址可访问，ffmpeg 会自动下载并解密；
若 key 同样有防盗链，相同的 -headers 会一并生效。
"""
from __future__ import annotations


def build_m3u8_args(
    source: str,
    output: str,
    referer: str = "",
    user_agent: str = "",
    cookie: str = "",
) -> list[str]:
    """source 可以是 m3u8 的 http(s) URL 或本地 .m3u8 文件路径。"""
    args: list[str] = []
    headers = ""
    if referer:
        headers += f"Referer: {referer}\r\n"
    if user_agent:
        headers += f"User-Agent: {user_agent}\r\n"
    if cookie:
        headers += f"Cookie: {cookie}\r\n"
    if headers and source.startswith(("http://", "https://")):
        args += ["-headers", headers]

    args += ["-i", source, "-c", "copy", "-bsf:a", "aac_adtstoasc",
             "-movflags", "+faststart", output]
    return args
