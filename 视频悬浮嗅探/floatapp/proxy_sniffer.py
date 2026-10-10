"""本地 MITM 代理嗅探：识别浏览器/App 流量中的 m3u8/mp4 等媒体请求。

工作原理：
1. 首次运行生成自签名 CA（~/.videosniffer/ca.crt），需用户安装到「受信任的根证书颁发机构」；
2. 浏览器经 CONNECT 建立隧道后，用 CA 即时签发目标域名证书并与客户端完成 TLS 握手；
   ALPN 只提供 http/1.1，强制浏览器回退到 HTTP/1.1 以便读取明文请求行；
3. 与上游重新建立 TLS（同样限定 http/1.1），完整中继请求/响应（含 chunked、keep-alive、
   Expect: 100-continue、WebSocket 升级），同时嗅探媒体 URL 及其 Referer/UA/Cookie；
4. 非 443 端口不做 MITM，仅盲转发，避免破坏其它协议。

证书固定（certificate pinning）的 App 会拒绝握手，这是系统级限制，非本工具可绕过。
"""
from __future__ import annotations

import asyncio
import datetime
import ipaddress
import os
import re
import ssl
import time
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# 可直接下载的清单或完整媒体扩展名——这些 URL 可作为 ffmpeg -i 的输入
# 切片类（.ts/.m4s/.aac 等）不在此列：单独下载片段没意义，且 .m4s 无 moov
# atom 时 ffmpeg 会直接报错
_PLAYABLE_EXT = ("m3u8", "m3u", "mp4", "flv", "mov", "mpd",
                 "webm", "mkv", "avi")
_MEDIA_RE = re.compile(r"\.([a-z0-9]+)(?:[?#]|$)", re.IGNORECASE)

# 响应 Content-Type 判定——只匹配清单和完整媒体类型，不匹配切片
# （video/mp2t 是 .ts 切片，application/octet-stream 常伴随 .m4s，均排除）
_MEDIA_CT_RE = re.compile(
    r"^(?:application/(?:vnd\.apple\.mpegurl|x-mpegurl|"
    r"vnd\.ms-sstr\+xml|dash\+xml)"
    r"|video/(?:mp4|x-flv|x-msvideo|webm|quicktime|3gpp|mp4-url)"
    r"|audio/(?:mpeg|mp4|aac|webm|ogg|x-wav))",
    re.IGNORECASE)

# 扩展名到 Content-Type 都没有时，通过 URL 关键字判定
_MEDIA_URL_HINTS = ("m3u8", "m3u", "mpd", ".flv", "/video/", "/stream/",
                     "playback", "/vod/", "hls", "dash", "manifest")


# ---------------- CA 与域名证书 ----------------

def _data_dir() -> Path:
    # VIDEOSNIFFER_HOME 可覆盖存储位置（便携模式/测试用）
    override = os.environ.get("VIDEOSNIFFER_HOME")
    path = Path(override) if override else Path.home() / ".videosniffer"
    (path / "certs").mkdir(parents=True, exist_ok=True)
    return path


def ca_cert_path() -> Path:
    return _data_dir() / "ca.crt"


def ensure_ca():
    """加载或新建 CA。返回 (ca_key, ca_cert)。"""
    directory = _data_dir()
    key_path, cert_path = directory / "ca.key", directory / "ca.crt"
    if key_path.exists() and cert_path.exists():
        ca_key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=None)
        ca_cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        return ca_key, ca_cert

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "VideoSniffer"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Video Sniffer CA"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                       critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, key_encipherment=False,
            content_commitment=False, data_encipherment=False,
            key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False),
            critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def _host_ssl_context(ca_key, ca_cert, host: str) -> ssl.SSLContext:
    """用 CA 为 host 签发叶子证书（带缓存），返回服务端 SSLContext。"""
    directory = _data_dir() / "certs"
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", host) or "unknown"
    cert_file, key_file = directory / f"{safe}.crt", directory / f"{safe}.key"

    if not cert_file.exists() or not key_file.exists():
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        try:
            san = x509.IPAddress(ipaddress.ip_address(host))
        except ValueError:
            san = x509.DNSName(host)
        now = datetime.datetime.now(datetime.timezone.utc)
        leaf = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=365))
            .add_extension(x509.SubjectAlternativeName([san]), critical=False)
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(
                    leaf_key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    ctx.set_alpn_protocols(["http/1.1"])
    return ctx


# ---------------- HTTP/1.1 帧解析与中继 ----------------

def _parse_head(head_lines: list[bytes]):
    """解析「首行 + 请求/响应头」。返回 (首行str, [(name_lower, value)])。"""
    first = head_lines[0].decode("latin1").strip()
    headers: list[tuple[str, str]] = []
    for raw in head_lines[1:]:
        name, _, value = raw.decode("latin1").partition(":")
        headers.append((name.strip().lower(), value.strip()))
    return first, headers


def _header(headers, name: str, default: str = "") -> str:
    for n, v in headers:
        if n == name:
            return v
    return default


def _wants_close(headers) -> bool:
    conn = _header(headers, "connection").lower()
    return "close" in {t.strip() for t in conn.split(",")}


async def _pipe_until_eof(src, dst) -> None:
    try:
        while True:
            chunk = await src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, OSError):
        pass


async def _relay_exact(src, dst, size: int) -> None:
    remaining = size
    while remaining > 0:
        chunk = await src.read(min(65536, remaining))
        if not chunk:
            raise ConnectionError("消息体传输中途断开")
        dst.write(chunk)
        remaining -= len(chunk)
    await dst.drain()


async def _relay_chunked(src, dst) -> None:
    while True:
        size_line = await src.readline()
        if not size_line:
            raise ConnectionError("chunked 传输中途断开")
        dst.write(size_line)
        try:
            size = int(size_line.split(b";", 1)[0].strip(), 16)
        except ValueError:
            raise ConnectionError("chunk size 无效")
        if size == 0:
            while True:  # trailer，直到空行
                line = await src.readline()
                dst.write(line)
                if line in (b"\r\n", b"\n", b""):
                    break
            await dst.drain()
            return
        await _relay_exact(src, dst, size)
        dst.write(await src.readexactly(2))
        await dst.drain()


async def _relay_body(src, dst, headers, *, allow_to_eof: bool) -> bool:
    """中继一个消息体。返回 True 表示消息体以「读到连接结束」终止，会话应关闭。"""
    te = _header(headers, "transfer-encoding").lower()
    cl = _header(headers, "content-length")
    if "chunked" in te:
        await _relay_chunked(src, dst)
        return False
    if cl:
        await _relay_exact(src, dst, int(cl))
        return False
    if allow_to_eof:
        await _pipe_until_eof(src, dst)
        return True
    return False


# ---------------- 嗅探工作线程 ----------------

class SnifferWorker(QThread):
    discovered = Signal(dict)   # 嗅探到一条媒体资源
    status = Signal(str)        # 运行状态文本
    failed = Signal(str)        # 启动失败

    def __init__(self, port: int = 8899):
        super().__init__()
        self._port = port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._seen: set[str] = set()
        self._host_ctx: dict[str, ssl.SSLContext] = {}
        self._ca_key = None
        self._ca_cert = None
        self._actual_port = port

    # ---- 线程入口 ----

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._ca_key, self._ca_cert = ensure_ca()
            self._loop.run_until_complete(self._serve())
        except Exception as e:
            self.failed.emit(str(e))

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        server = await asyncio.start_server(self._handle_client, "127.0.0.1", self._port)
        self._actual_port = server.sockets[0].getsockname()[1]
        # 盲转发专用端口（主端口 +1）：给 ffmpeg 用。ffmpeg 的 Schannel 栈对
        # 自签 CA 做吊销检查会失败，且 HLS 切片连接不继承 -tls_verify 0，
        # 无法走 MITM；其 Referer/Cookie 已由 -headers 自带，无需解密，
        # 所以该端口所有 CONNECT 一律盲转发。
        blind_server = await asyncio.start_server(
            self._handle_blind_client, "127.0.0.1", self._actual_port + 1)
        self.status.emit(f"嗅探代理运行中（端口 {self._actual_port}）")
        await self._stop_event.wait()
        server.close()
        blind_server.close()
        await server.wait_closed()
        await blind_server.wait_closed()
        self.status.emit("嗅探代理已停止")

    async def _handle_blind_client(self, reader, writer) -> None:
        """盲转发端口的连接入口：只接受 CONNECT，全程不解密。"""
        try:
            first_line = await asyncio.wait_for(reader.readline(), timeout=20)
            if not first_line:
                return
            parts = first_line.decode("latin1").split(" ", 2)
            if len(parts) < 2 or parts[0].upper() != "CONNECT":
                return
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            host, _, port_s = parts[1].partition(":")
            await self._blind_tunnel(reader, writer, host, int(port_s or "443"))
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    def stop(self) -> None:
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    @property
    def actual_port(self) -> int:
        """服务器实际监听端口（端口传 0 时由系统分配）。"""
        return self._actual_port

    # ---- 连接入口 ----

    def _mitm_ports(self) -> tuple[int, ...]:
        """对哪些 CONNECT 目标端口做 TLS 中间人（仅 443，其它端口盲转发）。"""
        return (443,)

    def _context_for(self, host: str) -> ssl.SSLContext:
        ctx = self._host_ctx.get(host)
        if ctx is None:
            ctx = _host_ssl_context(self._ca_key, self._ca_cert, host)
            self._host_ctx[host] = ctx
        return ctx

    async def _handle_client(self, reader, writer) -> None:
        try:
            first_line = await asyncio.wait_for(reader.readline(), timeout=20)
            if not first_line:
                return
            parts = first_line.decode("latin1").split(" ", 2)
            if len(parts) < 3:
                return
            method, target, _ver = parts[0].upper(), parts[1], parts[2]
            header_lines: list[bytes] = []
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                header_lines.append(line)

            if method == "CONNECT":
                host, _, port_s = target.partition(":")
                port = int(port_s or "443")
                if port in self._mitm_ports():
                    await self._connect_tls(reader, writer, host, port)
                else:
                    await self._blind_tunnel(reader, writer, host, port)
            else:
                await self._forward_plain(
                    reader, writer, method, _ver, target, header_lines)
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
            pass
        except Exception:
            # 单条连接的任何异常都不应影响代理整体
            if os.environ.get("VIDEOSNIFFER_DEBUG"):
                import traceback
                dbg = Path(_data_dir()) / "_debug_errors.log"
                with open(dbg, "a", encoding="utf-8") as f:
                    f.write(traceback.format_exc() + "\n")
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def _blind_tunnel(self, reader, writer, host: str, port: int) -> None:
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        u_reader, u_writer = await asyncio.open_connection(host, port)
        await asyncio.gather(
            _pipe_until_eof(reader, u_writer),
            _pipe_until_eof(u_reader, writer))

    async def _connect_tls(self, reader, writer, host: str, port: int) -> None:
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        loop = asyncio.get_running_loop()
        protocol = writer.transport.get_protocol()
        # start_tls 会把明文 transport 包成 TLS，握手完成后通过 connection_made
        # 回调把 reader 重绑到新 transport，并返回新的 app transport；
        # 必须用它重建 StreamWriter，否则 writer 仍写向旧的明文 socket，
        # 浏览器会收到乱码并报「无效的响应」。
        app_transport = await loop.start_tls(
            writer.transport, protocol,
            self._context_for(host), server_side=True)
        writer = asyncio.StreamWriter(app_transport, protocol, reader, loop)
        # 上游 TLS 同样强制 http/1.1，保证我们发出的请求能被理解
        up_ctx = ssl.create_default_context()
        up_ctx.set_alpn_protocols(["http/1.1"])
        u_reader, u_writer = await asyncio.open_connection(
            host, port, ssl=up_ctx, server_hostname=host)
        try:
            await self._run_session(
                reader, writer, u_reader, u_writer, "https", host)
        finally:
            u_writer.close()

    async def _forward_plain(self, reader, writer, method, ver, target,
                             header_lines) -> None:
        parsed = urllib.parse.urlsplit(target)
        host = parsed.hostname or ""
        port = parsed.port or 80
        u_reader, u_writer = await asyncio.open_connection(host, port)
        origin_target = urllib.parse.urlunsplit(
            ("", "", parsed.path or "/", parsed.query, ""))
        initial_head = [
            f"{method} {origin_target} {ver}".encode("latin1"), *header_lines]
        try:
            await self._run_session(
                reader, writer, u_reader, u_writer, "http", host,
                initial_head=initial_head)
        finally:
            u_writer.close()

    # ---- 完整的请求/响应中继循环 ----

    async def _run_session(self, c_reader, c_writer, u_reader, u_writer,
                           scheme: str, host: str,
                           initial_head: list[bytes] | None = None) -> None:
        while True:
            if initial_head is not None:
                req_head = initial_head
                initial_head = None
            else:
                line = await asyncio.wait_for(c_reader.readline(), timeout=30)
                if not line:
                    break
                req_head = [line]
                while True:
                    h = await c_reader.readline()
                    if h in (b"\r\n", b"\n", b""):
                        break
                    req_head.append(h)

            first, req_headers = _parse_head(req_head)
            parts = first.split(" ", 2)
            if len(parts) < 2:
                break
            method = parts[0].upper()
            path = parts[1]

            self._inspect(scheme, host, req_head)

            # WebSocket / 任意 Upgrade：头转发后双向盲中继
            if "upgrade" in _header(req_headers, "connection").lower():
                for raw in req_head:
                    u_writer.write(raw)
                u_writer.write(b"\r\n")
                await u_writer.drain()
                await asyncio.gather(
                    _pipe_until_eof(c_reader, u_writer),
                    _pipe_until_eof(u_reader, c_writer))
                return

            expect_continue = _header(req_headers, "expect").lower() == "100-continue"
            if expect_continue:
                c_writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                await c_writer.drain()

            # 转发请求头（去掉已由我们应答的 Expect）
            for idx, raw in enumerate(req_head):
                if idx > 0 and expect_continue and \
                        raw.split(b":", 1)[0].strip().lower() == b"expect":
                    continue
                u_writer.write(raw)
            u_writer.write(b"\r\n")

            await _relay_body(c_reader, u_writer, req_headers, allow_to_eof=False)
            await u_writer.drain()

            # 读取并转发响应
            resp_line = await asyncio.wait_for(u_reader.readline(), timeout=60)
            if not resp_line:
                break
            resp_head = [resp_line]
            while True:
                h = await u_reader.readline()
                if h in (b"\r\n", b"\n", b""):
                    break
                resp_head.append(h)
            for raw in resp_head:
                c_writer.write(raw)
            c_writer.write(b"\r\n")
            await c_writer.drain()

            _, resp_headers = _parse_head(resp_head)
            status_tokens = resp_head[0].decode("latin1").split(" ", 2)
            status_code = int(status_tokens[1]) \
                if len(status_tokens) > 1 and status_tokens[1].isdigit() else 0

            # 响应头到达后，结合 Content-Type 二次嗅探：很多视频流 URL
            # 没有可识别的扩展名（如抖音的 /video/play/?video_id=xxx），
            # 但响应头 Content-Type 能明确判定是视频/音频/HLS 清单。
            self._inspect_response(scheme, host, path, req_headers,
                                   resp_headers, status_code)

            close = _wants_close(req_headers) or _wants_close(resp_headers)
            no_body = method == "HEAD" or status_code in (204, 304) \
                or 100 <= status_code < 200
            if not no_body:
                eof = await _relay_body(
                    u_reader, c_writer, resp_headers, allow_to_eof=True)
                close = close or eof
            if close:
                break

    # ---- 嗅探判定 ----

    def _inspect(self, scheme: str, host: str, req_head: list[bytes]) -> None:
        """请求阶段的快速嗅探：URL 扩展名命中即报出，不必等响应。"""
        first, headers = _parse_head(req_head)
        parts = first.split(" ", 2)
        if len(parts) < 2 or parts[0].upper() != "GET":
            return
        path = parts[1]
        path = path if path.startswith("/") else "/" + path
        match = _MEDIA_RE.search(path)
        if not match or match.group(1).lower() not in _PLAYABLE_EXT:
            return
        self._emit(scheme, host, path, headers, match.group(1).lower())

    def _inspect_response(self, scheme: str, host: str, path: str,
                          req_headers: dict, resp_headers: dict,
                          status_code: int) -> None:
        """响应阶段的二次嗅探：通过 Content-Type 识别无扩展名的视频流。"""
        if status_code != 200:
            return
        # 请求阶段已命中扩展名的不再重复
        match = _MEDIA_RE.search(path)
        if match and match.group(1).lower() in _PLAYABLE_EXT:
            return
        ct = _header(resp_headers, "content-type").split(";")[0].strip()
        if not ct:
            # 仍无 Content-Type 时，通过 URL 关键字兜底
            lower = path.lower()
            if not any(h in lower for h in _MEDIA_URL_HINTS):
                return
            ext = "m3u8" if "m3u" in lower else (
                "mpd" if "mpd" in lower or "dash" in lower else "mp4")
        elif _MEDIA_CT_RE.match(ct):
            # 从 Content-Type 反推扩展名
            if "mpegurl" in ct:
                ext = "m3u8"
            elif "dash" in ct:
                ext = "mpd"
            elif "mp2t" in ct:
                # HLS .ts 切片：不报出，单独下载片段无意义
                return
            elif "x-flv" in ct:
                ext = "flv"
            elif "webm" in ct:
                ext = "webm"
            elif "audio" in ct:
                ext = "m4a" if "mp4" in ct else "mp3"
            else:
                ext = "mp4"
        else:
            return
        self._emit(scheme, host, path, req_headers, ext)

    def _emit(self, scheme: str, host: str, path: str,
              headers: dict, ext: str) -> None:
        url = f"{scheme}://{host}{path}"
        if url in self._seen:
            return
        self._seen.add(url)
        self.discovered.emit({
            "url": url,
            "ext": ext,
            "host": host,
            "time": time.strftime("%H:%M:%S"),
            "referer": _header(headers, "referer"),
            "user_agent": _header(headers, "user-agent"),
            "cookie": _header(headers, "cookie"),
        })
