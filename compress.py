#!/usr/bin/env python3
"""
compress.py - Compress MP4 and MKV video files to reduce file size.

Reads configuration from a .env file in the same directory:
  INPUT_DIR        - folder containing source video files
  OUTPUT_DIR       - folder where compressed files will be saved
  CRF              - Constant Rate Factor for H.265 encoding (default: 28)
    TIMEOUT_SECONDS  - maximum ffmpeg runtime per file (default: 36000, 0=disabled)
    OUTPUT_FORMAT    - output container: source, mkv, mp4, avi (default: source)
    ENCODER_PRESET   - x265 preset for CPU encoding (default: medium)
    ENCODER_TYPE     - encoder backend: cpu, nvidia, intel, amd (default: cpu)

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
SUPPORTED_X265_PRESETS = {
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
}
SUPPORTED_ENCODER_TYPES = {"cpu", "nvidia", "intel", "amd"}


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


def load_config() -> tuple[Path, Path, int, int, str, str, str]:
    """Load and validate configuration from the .env file."""
    load_dotenv()

    input_dir = os.getenv("INPUT_DIR", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()
    crf_str = os.getenv("CRF", "28").strip()
    timeout_str = os.getenv("TIMEOUT_SECONDS", "36000").strip()
    output_format = os.getenv("OUTPUT_FORMAT", "source").strip().lower()
    encoder_preset = os.getenv("ENCODER_PRESET", "medium").strip().lower()
    encoder_type = os.getenv("ENCODER_TYPE", "cpu").strip().lower()

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
        if timeout_seconds < 0:
            raise ValueError
    except ValueError:
        sys.exit(
            "Error: TIMEOUT_SECONDS must be an integer >= 0 (0 disables timeout), "
            f"got '{timeout_str}'."
        )

    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        allowed = ", ".join(sorted(SUPPORTED_OUTPUT_FORMATS))
        sys.exit(
            "Error: OUTPUT_FORMAT must be one of "
            f"{allowed}, got '{output_format}'."
        )

    if encoder_type not in SUPPORTED_ENCODER_TYPES:
        allowed = ", ".join(sorted(SUPPORTED_ENCODER_TYPES))
        sys.exit(
            "Error: ENCODER_TYPE must be one of "
            f"{allowed}, got '{encoder_type}'."
        )

    if encoder_type == "cpu" and encoder_preset not in SUPPORTED_X265_PRESETS:
        allowed = ", ".join(sorted(SUPPORTED_X265_PRESETS))
        sys.exit(
            "Error: ENCODER_PRESET must be one of "
            f"{allowed}, got '{encoder_preset}'."
        )

    return (
        input_path,
        output_path,
        crf,
        timeout_seconds,
        output_format,
        encoder_preset,
        encoder_type,
    )


def get_required_video_encoder_name(encoder_type: str) -> str:
    """Return the ffmpeg video encoder name for the selected encoder backend."""
    return {
        "cpu": "libx265",
        "nvidia": "hevc_nvenc",
        "intel": "hevc_qsv",
        "amd": "hevc_amf",
    }[encoder_type]


def get_available_ffmpeg_encoders(ffmpeg_executable: str) -> set[str]:
    """Return the set of encoder names reported by ffmpeg -encoders."""
    cmd = [ffmpeg_executable, "-hide_banner", "-encoders"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.SubprocessError:
        return set()

    if result.returncode != 0:
        return set()

    encoders: set[str] = set()
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("-") or line.startswith("Encoders:"):
            continue

        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) >= 6:
            encoders.add(parts[1])

    return encoders


def get_video_codec_args(crf: int, encoder_preset: str, encoder_type: str) -> tuple[list[str], str]:
    """Build ffmpeg video codec args and return args plus resolved encoder name."""
    if encoder_type == "cpu":
        return ["-c:v", "libx265", "-crf", str(crf), "-preset", encoder_preset], "libx265"

    if encoder_type == "nvidia":
        return ["-c:v", "hevc_nvenc", "-cq", str(crf), "-preset", "p5"], "hevc_nvenc"

    if encoder_type == "intel":
        return ["-c:v", "hevc_qsv", "-global_quality", str(crf), "-preset", "medium"], "hevc_qsv"

    return ["-c:v", "hevc_amf", "-rc", "cqp", "-qp_i", str(crf), "-qp_p", str(crf)], "hevc_amf"


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
    encoder_preset: str,
    encoder_type: str,
    ffmpeg_executable: str,
    ffprobe_executable: str | None,
    timeout: int = 36000,
) -> bool:
    """
    Compress *src* into *dst* using the selected video encoder backend and AAC audio.

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
        video_codec_args, _ = get_video_codec_args(crf, encoder_preset, encoder_type)
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

    killer: threading.Timer | None = None
    if timeout > 0:
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
        if killer is not None:
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
    """Run the compression workflow for all supported input files."""
    (
        input_path,
        output_path,
        crf,
        timeout_seconds,
        output_format,
        encoder_preset,
        encoder_type,
    ) = load_config()
    ffmpeg_executable = resolve_ffmpeg_executable()
    ffprobe_executable = resolve_ffprobe_executable()

    if output_format != "avi":
        required_encoder = get_required_video_encoder_name(encoder_type)
        available_encoders = get_available_ffmpeg_encoders(ffmpeg_executable)
        if available_encoders and required_encoder not in available_encoders:
            sys.exit(
                "Error: Selected ENCODER_TYPE requires ffmpeg encoder "
                f"'{required_encoder}', but it is not available in this ffmpeg build."
            )

    output_path.mkdir(parents=True, exist_ok=True)

    video_files = find_video_files(input_path)
    if not video_files:
        log(f"No MP4 or MKV files found in '{input_path}'.")
        return

    log(f"Found {len(video_files)} file(s) to compress (CRF={crf}).")
    if timeout_seconds == 0:
        log("Timeout per file: disabled")
    else:
        log(f"Timeout per file: {format_duration(timeout_seconds)} ({timeout_seconds}s)")
    log(f"Encoder type: {encoder_type}")
    if encoder_type == "cpu":
        log(f"Encoder preset: {encoder_preset}")
    else:
        log(f"Encoder preset: {encoder_preset} (ignored for {encoder_type})")
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
            encoder_preset,
            encoder_type,
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
