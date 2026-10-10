"""视频合并与剪切（基于 ffmpeg）。"""
from __future__ import annotations

from pathlib import Path


def write_concat_list(inputs: list[str], list_file: str) -> str:
    """生成 ffmpeg concat demuxer 列表文件，返回文件路径。

    使用绝对路径，避免 ffmpeg 工作目录不同导致找不到文件。
    """
    with open(list_file, "w", encoding="utf-8") as f:
        for p in inputs:
            abs_p = str(Path(p).resolve())
            # concat demuxer 语法中单引号需转义为 '\''
            escaped = abs_p.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    return list_file


def build_merge_args(inputs: list[str], output: str, reencode: bool,
                     work_dir: str) -> tuple[list[str], str]:
    """合并多个视频。返回 (ffmpeg 参数, concat列表文件路径)。"""
    list_file = str(Path(work_dir) / "_concat_list.txt")
    write_concat_list(inputs, list_file)
    args = ["-f", "concat", "-safe", "0", "-i", list_file]
    if reencode:
        # 重编码兼容模式：统一为 H.264/AAC，兼容不同编码/分辨率的输入
        args += ["-c:v", "libx264", "-crf", "23", "-preset", "medium",
                 "-c:a", "aac", "-movflags", "+faststart"]
    else:
        args += ["-c", "copy"]
    args += [output]
    return args, list_file


def build_cut_args(src: str, output: str, start: str, end: str,
                   precise: bool) -> list[str]:
    """按起止时间剪切。start/end 形如 'HH:MM:SS' 或秒数；end 可为空（切到结尾）。

    precise=False 为快速无损模式（-c copy，切点对齐关键帧，可能差几秒）；
    precise=True 重编码，切点精确到帧。
    """
    args = ["-ss", start]
    if end:
        args += ["-to", end]
    args += ["-i", src]
    if precise:
        args += ["-c:v", "libx264", "-crf", "20", "-preset", "medium",
                 "-c:a", "aac", "-movflags", "+faststart"]
    else:
        args += ["-c", "copy"]
    args += [output]
    return args
