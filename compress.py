#!/usr/bin/env python3
"""
compress.py - Compress MP4 and MKV video files to reduce file size.

Reads configuration from a .env file in the same directory:
  INPUT_DIR        - folder containing source video files
  OUTPUT_DIR       - folder where compressed files will be saved
  CRF              - Constant Rate Factor for H.265 encoding (default: 28)
  TIMEOUT_SECONDS  - maximum ffmpeg runtime per file (default: 36000)
    OUTPUT_FORMAT    - output container: source, mkv, mp4, avi (default: source)

Requires ffmpeg to be installed and available on PATH.
"""

import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

SUPPORTED_EXTENSIONS = {".mp4", ".mkv"}
SUPPORTED_OUTPUT_FORMATS = {"source", "mkv", "mp4", "avi"}


def timestamp_prefix() -> str:
    """Return a standard timestamp prefix for console output."""
    return f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"


def log(message: str = "", *, end: str = "\n", flush: bool = False) -> None:
    """Print a timestamped log message."""
    if message == "" and end == "\n":
        print()
        return
    print(f"{timestamp_prefix()} {message}", end=end, flush=flush)


def log_error(message: str) -> None:
    """Print a timestamped error log message."""
    print(f"{timestamp_prefix()} {message}", file=sys.stderr)


def load_config() -> tuple[Path, Path, int, int, str]:
    """Load and validate configuration from the .env file."""
    load_dotenv()

    input_dir = os.getenv("INPUT_DIR", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()
    crf_str = os.getenv("CRF", "28").strip()
    timeout_str = os.getenv("TIMEOUT_SECONDS", "36000").strip()
    output_format = os.getenv("OUTPUT_FORMAT", "source").strip().lower()

    if not input_dir:
        sys.exit("Error: INPUT_DIR is not set in the .env file.")
    if not output_dir:
        sys.exit("Error: OUTPUT_DIR is not set in the .env file.")

    input_path = Path(input_dir)
    output_path = Path(output_dir)

    if not input_path.is_dir():
        sys.exit(f"Error: INPUT_DIR '{input_dir}' is not a valid directory.")

    try:
        crf = int(crf_str)
        if not 0 <= crf <= 51:
            raise ValueError
    except ValueError:
        sys.exit(f"Error: CRF must be an integer between 0 and 51, got '{crf_str}'.")

    try:
        timeout_seconds = int(timeout_str)
        if timeout_seconds <= 0:
            raise ValueError
    except ValueError:
        sys.exit(
            "Error: TIMEOUT_SECONDS must be a positive integer, "
            f"got '{timeout_str}'."
        )

    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        allowed = ", ".join(sorted(SUPPORTED_OUTPUT_FORMATS))
        sys.exit(
            "Error: OUTPUT_FORMAT must be one of "
            f"{allowed}, got '{output_format}'."
        )

    return input_path, output_path, crf, timeout_seconds, output_format


def find_video_files(directory: Path) -> list[Path]:
    """Return all MP4 and MKV files in *directory* (non-recursive)."""
    return sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def resolve_executable(name: str) -> str | None:
    """Resolve executable from PATH, including .exe fallback on Windows."""
    candidates = [name]
    if os.name == "nt":
        candidates.insert(0, f"{name}.exe")

    for candidate in candidates:
        executable_path = shutil.which(candidate)
        if executable_path:
            return executable_path

    return None


def resolve_ffmpeg_executable() -> str:
    """Return a callable ffmpeg executable path or exit with a helpful message."""
    ffmpeg_path = resolve_executable("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    sys.exit(
        "Error: ffmpeg executable was not found. Install ffmpeg and add it to PATH, "
        "or use its full executable path in your system PATH settings. "
        "On Windows: choco install ffmpeg -y or winget install Gyan.FFmpeg"
    )


def resolve_ffprobe_executable() -> str | None:
    """Return ffprobe executable path if available, otherwise None."""
    return resolve_executable("ffprobe")


def get_media_duration_seconds(src: Path, ffprobe_executable: str | None) -> float | None:
    """Return media duration in seconds if available, otherwise None."""
    if ffprobe_executable is None:
        return None

    probe_cmd = [
        ffprobe_executable,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(src),
    ]
    try:
        result = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.SubprocessError:
        return None

    if result.returncode != 0:
        return None

    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return None

    return duration if duration > 0 else None


def format_duration(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def print_progress_bar(processed_seconds: float, total_seconds: float) -> None:
    """Render an in-place progress bar line."""
    clamped = min(max(processed_seconds, 0.0), total_seconds)
    percent = (clamped / total_seconds) * 100 if total_seconds > 0 else 0.0
    bar_width = 30
    filled = int((percent / 100) * bar_width)
    bar = "#" * filled + "-" * (bar_width - filled)
    print(
        f"\r{timestamp_prefix()} Progress   : [{bar}] {percent:5.1f}% "
        f"({format_duration(clamped)}/{format_duration(total_seconds)})",
        end="",
        flush=True,
    )


def compress_file(
    src: Path,
    dst: Path,
    crf: int,
    ffmpeg_executable: str,
    ffprobe_executable: str | None,
    timeout: int = 36000,
) -> bool:
    """
    Compress *src* into *dst* using H.265 (libx265) video and AAC audio.

    Returns True on success, False on failure.
    The *timeout* parameter limits how long ffmpeg may run (default: 10 hours).
    """
    output_container = dst.suffix.lower()
    sidecar_srt = src.with_suffix(".srt")
    include_sidecar_srt = output_container == ".mkv" and sidecar_srt.is_file()

    if sidecar_srt.is_file() and output_container != ".mkv":
        log(
            f"  Captions   : Found sidecar '{sidecar_srt.name}' but only MKV "
            "output supports auto-embedding; skipping sidecar"
        )

    cmd = [
        ffmpeg_executable,
        "-v",
        "error",
        "-i",
        str(src),
    ]

    if include_sidecar_srt:
        log(f"  Captions   : Found sidecar '{sidecar_srt.name}', embedding into MKV")
        cmd.extend(["-i", str(sidecar_srt)])

    if output_container == ".avi":
        video_codec_args = ["-c:v", "libxvid", "-q:v", "4"]
        audio_codec_args = ["-c:a", "libmp3lame", "-b:a", "192k"]
        subtitle_args = ["-sn"]
    else:
        video_codec_args = ["-c:v", "libx265", "-crf", str(crf), "-preset", "medium"]
        audio_codec_args = ["-c:a", "aac", "-b:a", "128k"]
        subtitle_codec = "copy" if output_container == ".mkv" else "mov_text"
        subtitle_args = ["-c:s", subtitle_codec]

    cmd.extend([
        "-map",
        "0:v?",
        "-map",
        "0:a?",
    ])

    if output_container != ".avi":
        cmd.extend(["-map", "0:s?"])

    if include_sidecar_srt:
        cmd.extend(["-map", "1:0"])

    cmd.extend(video_codec_args)
    cmd.extend(audio_codec_args)
    cmd.extend(subtitle_args)
    cmd.extend([
        "-progress",
        "pipe:1",
        "-nostats",
        "-y",
        str(dst),
    ])

    total_duration = get_media_duration_seconds(src, ffprobe_executable)
    timeout_reached = threading.Event()

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        log_error("[ERROR] ffmpeg executable was not found while starting compression.")
        return False

    killer = threading.Timer(timeout, lambda: (timeout_reached.set(), process.kill()))
    killer.start()

    latest_processed_seconds = 0.0
    try:
        if process.stdout is not None:
            for line in process.stdout:
                output_line = line.strip()
                if output_line.startswith("out_time_ms="):
                    try:
                        out_time_ms = int(output_line.split("=", 1)[1])
                        latest_processed_seconds = out_time_ms / 1_000_000
                    except ValueError:
                        continue
                elif output_line.startswith("out_time_us="):
                    try:
                        out_time_us = int(output_line.split("=", 1)[1])
                        latest_processed_seconds = out_time_us / 1_000_000
                    except ValueError:
                        continue
                else:
                    continue

                if total_duration is not None:
                    print_progress_bar(latest_processed_seconds, total_duration)
                else:
                    print(
                        f"\r{timestamp_prefix()} Progress   : "
                        f"{format_duration(latest_processed_seconds)} processed",
                        end="",
                        flush=True,
                    )

        process.wait()
    finally:
        killer.cancel()

    if total_duration is not None and process.returncode == 0:
        print_progress_bar(total_duration, total_duration)
    print()

    stderr_text = ""
    if process.stderr is not None:
        stderr_text = process.stderr.read().strip()

    if timeout_reached.is_set():
        log_error(f"[ERROR] ffmpeg timed out after {timeout}s.")
        return False

    if process.returncode != 0:
        log_error(f"[ERROR] ffmpeg failed:\n{stderr_text}")
        return False

    return True


def format_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def main() -> None:
    input_path, output_path, crf, timeout_seconds, output_format = load_config()
    ffmpeg_executable = resolve_ffmpeg_executable()
    ffprobe_executable = resolve_ffprobe_executable()

    output_path.mkdir(parents=True, exist_ok=True)

    video_files = find_video_files(input_path)
    if not video_files:
        log(f"No MP4 or MKV files found in '{input_path}'.")
        return

    log(f"Found {len(video_files)} file(s) to compress (CRF={crf}).")
    log(f"Timeout per file: {format_duration(timeout_seconds)} ({timeout_seconds}s)")
    log(f"Output format: {output_format}")
    log(f"Output folder: {output_path}")
    log()

    success_count = 0
    for index, src in enumerate(video_files, start=1):
        if output_format == "source":
            dst = output_path / src.name
        else:
            dst = output_path / f"{src.stem}.{output_format}"
        log(f"[{index}/{len(video_files)}] {src.name} -> {dst.name}")
        log(f"  Input size : {format_size(src.stat().st_size)}")

        if compress_file(
            src,
            dst,
            crf,
            ffmpeg_executable,
            ffprobe_executable,
            timeout_seconds,
        ):
            input_size = src.stat().st_size
            output_size = dst.stat().st_size
            saved = input_size - output_size
            log(f"  Output size: {format_size(output_size)}")
            if saved >= 0:
                log(f"  Saved      : {format_size(saved)}")
            else:
                log(f"  Warning    : Output is {format_size(-saved)} larger than input.")
            success_count += 1
        else:
            log(f"  Skipping '{src.name}' due to errors.")

        log()

    log(f"Done. {success_count}/{len(video_files)} file(s) compressed successfully.")


if __name__ == "__main__":
    main()
