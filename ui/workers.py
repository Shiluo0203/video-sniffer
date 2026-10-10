"""QThread 工作线程封装，统一 Signal 协议与取消机制。"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from core.ffmpeg_service import FFmpegJob, build_transcode_args, CANCELLED, probe_duration
from core.m3u8_service import build_m3u8_args
from core.video_edit_service import build_merge_args, build_cut_args
from core.ytdlp_service import YtdlpDownload, extract_info
from core.thumbnail_service import extract_strip_frames, extract_cover


class BaseWorker(QThread):
    """所有工作线程基类。"""
    progress = Signal(int)           # 0-100
    log = Signal(str)                # 日志文本
    finished = Signal(str)           # 完成，参数为结果描述
    error = Signal(str)              # 出错

    def __init__(self):
        super().__init__()
        self._job = None  # 当前正在执行的任务（FFmpegJob / YtdlpDownload），用于取消

    def stop(self):
        job = self._job
        if job is not None:
            job.cancel()


class TranscodeWorker(BaseWorker):
    """批量转码。"""

    def __init__(self, tasks: list[dict]):
        super().__init__()
        self._tasks = tasks  # [{src, dst, crf, preset, scale, vcodec}, ...]

    def run(self):
        total = len(self._tasks)
        try:
            for idx, t in enumerate(self._tasks, 1):
                job = FFmpegJob()
                self._job = job
                self.log.emit(f"[{idx}/{total}] 转码：{t['src']}")
                dur = probe_duration(t["src"])
                args = build_transcode_args(
                    t["src"], t["dst"],
                    crf=t.get("crf", 23),
                    preset=t.get("preset", "medium"),
                    scale=t.get("scale"),
                    vcodec=t.get("vcodec", "libx264"),
                )
                code = job.run(
                    args, total_duration=dur,
                    progress_cb=lambda p, i=idx, n=total: self.progress.emit(int(((i - 1) + p / 100) / n * 100)),
                    log_cb=self.log.emit)
                if code == CANCELLED:
                    self.error.emit("已取消")
                    return
        except Exception as e:
            self.error.emit(str(e))
            return
        self.finished.emit(f"完成 {total} 个文件")


class M3U8Worker(BaseWorker):
    """M3U8 合并。"""

    def __init__(self, source: str, output: str, referer: str = "",
                 user_agent: str = "", cookie: str = "", estimate_duration: float = 0,
                 proxy: str | None = None):
        super().__init__()
        self._source = source
        self._output = output
        self._referer = referer
        self._ua = user_agent
        self._cookie = cookie
        self._dur = estimate_duration
        self._proxy = proxy

    def run(self):
        args = build_m3u8_args(self._source, self._output,
                               self._referer, self._ua, self._cookie)
        job = FFmpegJob()
        self._job = job
        try:
            code = job.run(args, total_duration=self._dur,
                           progress_cb=self.progress.emit,
                           log_cb=self.log.emit,
                           proxy=self._proxy)
            if code == CANCELLED:
                self.error.emit("已取消")
                return
        except Exception as e:
            self.error.emit(str(e))
            return
        self.finished.emit(self._output)


class MergeWorker(BaseWorker):
    """视频合并。"""

    def __init__(self, inputs: list[str], output: str, reencode: bool,
                 work_dir: str):
        super().__init__()
        self._inputs = inputs
        self._output = output
        self._reencode = reencode
        self._work_dir = work_dir

    def run(self):
        args, _list_file = build_merge_args(self._inputs, self._output,
                                            self._reencode, self._work_dir)
        job = FFmpegJob()
        self._job = job
        try:
            code = job.run(args, progress_cb=self.progress.emit,
                           log_cb=self.log.emit)
            if code == CANCELLED:
                self.error.emit("已取消")
                return
        except Exception as e:
            self.error.emit(str(e))
            return
        self.finished.emit(self._output)


class CutWorker(BaseWorker):
    """视频剪切。"""

    def __init__(self, src: str, output: str, start: str, end: str, precise: bool):
        super().__init__()
        self._src = src
        self._output = output
        self._start = start
        self._end = end
        self._precise = precise

    def run(self):
        args = build_cut_args(self._src, self._output, self._start, self._end, self._precise)
        dur = probe_duration(self._src)
        job = FFmpegJob()
        self._job = job
        try:
            code = job.run(args, total_duration=dur,
                           progress_cb=self.progress.emit, log_cb=self.log.emit)
            if code == CANCELLED:
                self.error.emit("已取消")
                return
        except Exception as e:
            self.error.emit(str(e))
            return
        self.finished.emit(self._output)


class YtdlpParseWorker(BaseWorker):
    """解析视频信息（不下载）。"""

    parsed = Signal(dict)  # 完整 info dict

    def __init__(self, url: str, cookie_file: str = ""):
        super().__init__()
        self._url = url
        self._cookie_file = cookie_file

    def run(self):
        try:
            info = extract_info(self._url, self._cookie_file)
            self.parsed.emit(info)
        except Exception as e:
            self.error.emit(str(e))


class YtdlpDownloadWorker(BaseWorker):
    """单个 URL 下载。"""

    def __init__(self, url: str, out_dir: str, format_id: str = "",
                 cookie_file: str = ""):
        super().__init__()
        self._url = url
        self._out_dir = out_dir
        self._format_id = format_id
        self._cookie_file = cookie_file

    def run(self):
        dl = YtdlpDownload()
        self._job = dl
        try:
            dl.download(self._url, self._out_dir, self._format_id,
                        progress_cb=self.progress.emit,
                        log_cb=self.log.emit,
                        cookie_file=self._cookie_file)
        except Exception as e:
            if dl.cancelled:
                self.error.emit("已取消")
                return
            self.error.emit(str(e))
            return
        self.finished.emit("下载完成")


class StripThumbWorker(QThread):
    """为剪切页时间轴抽取均匀缩略图条。"""

    frames_ready = Signal(str, list)  # src, [jpg路径,...]

    def __init__(self, src: str, duration: float, count: int = 14):
        super().__init__()
        self._src = src
        self._duration = duration
        self._count = count

    def run(self):
        files = extract_strip_frames(self._src, self._duration, self._count)
        self.frames_ready.emit(self._src, files)


class CoverWorker(QThread):
    """为合并页文件列表批量抽取封面。"""

    cover_ready = Signal(str, str, float)  # src, cover路径, 时长

    def __init__(self, items: list[str]):
        super().__init__()
        self._items = items

    def run(self):
        for src in self._items:
            dur = probe_duration(src)
            cover = extract_cover(src, dur)
            if cover:
                self.cover_ready.emit(src, cover, dur)
