#!/usr/bin/env python3
"""
compress.py - Batch-compress MP4 and MKV video files with ffmpeg.

Every ``.mp4``/``.mkv`` file in ``INPUT_DIR`` is re-encoded (H.265 video by
default) and written to ``OUTPUT_DIR``. Outputs that already exist are skipped,
so an interrupted batch can simply be re-run to resume where it left off.

Configuration is read from a ``.env`` file next to this script (real
environment variables take precedence):

  INPUT_DIR           folder containing source video files            (required)
  OUTPUT_DIR          folder where compressed files are written       (required)
  COMPRESSION_MODE    lossy | lossless                                (default: lossy)
  CRF                 quality, 0-51, lower = better/larger            (default: 28)
  TIMEOUT_SECONDS     max ffmpeg runtime per file, 0 = no limit       (default: 36000)
  OUTPUT_FORMAT       source | mkv | mp4 | avi                        (default: source)
  ENCODER_PRESET      x265 preset, CPU encoding only                  (default: medium)
  ENCODER_TYPE        cpu | nvidia | intel | amd                      (default: cpu)
  OVERWRITE_EXISTING  true to re-encode files whose output exists     (default: false)

Requires ffmpeg on PATH. ffprobe (bundled with ffmpeg) is optional and enables
percentage progress bars.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent

SUPPORTED_EXTENSIONS = frozenset({".mp4", ".mkv"})
SUPPORTED_OUTPUT_FORMATS = frozenset({"source", "mkv", "mp4", "avi"})
SUPPORTED_COMPRESSION_MODES = frozenset({"lossy", "lossless"})
SUPPORTED_X265_PRESETS = frozenset({
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow", "placebo",
})

# ENCODER_TYPE -> ffmpeg HEVC encoder name.
HEVC_ENCODERS = {
    "cpu": "libx265",
    "nvidia": "hevc_nvenc",
    "intel": "hevc_qsv",
    "amd": "hevc_amf",
}
AVI_VIDEO_ENCODER = "libxvid"

# Minimum interval between progress-bar redraws (seconds).
PROGRESS_REDRAW_INTERVAL = 0.5
PROGRESS_BAR_WIDTH = 30


# --------------------------------------------------------------------------- #
# Logging helpers
# --------------------------------------------------------------------------- #

def _stamp() -> str:
    return datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")


def log(message: str = "") -> None:
    """Print a timestamped message (or a bare blank line when *message* is empty)."""
    print(f"{_stamp()} {message}" if message else "", flush=True)


def log_error(message: str) -> None:
    """Print a timestamped message to stderr."""
    print(f"{_stamp()} {message}", file=sys.stderr, flush=True)


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as ``HH:MM:SS``."""
    hours, remainder = divmod(max(0, int(seconds)), 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_size(size_bytes: int) -> str:
    """Format a byte count as a human-readable string (e.g. ``1.8 GB``)."""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Config:
    """Validated user configuration loaded from ``.env`` / the environment."""

    input_dir: Path
    output_dir: Path
    compression_mode: str
    crf: int
    timeout_seconds: int
    output_format: str
    encoder_preset: str
    encoder_type: str
    overwrite_existing: bool

    @property
    def lossless(self) -> bool:
        return self.compression_mode == "lossless"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_choice(name: str, default: str, allowed: frozenset[str]) -> str:
    value = _env(name, default).lower()
    if value not in allowed:
        sys.exit(f"Error: {name} must be one of {', '.join(sorted(allowed))}, got '{value}'.")
    return value


def _env_int(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw = _env(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or value < minimum or (maximum is not None and value > maximum):
        bounds = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        sys.exit(f"Error: {name} must be an integer {bounds}, got '{raw}'.")
    return value


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, str(default)).lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    sys.exit(f"Error: {name} must be true or false, got '{value}'.")


def load_config() -> Config:
    """Load, validate and cross-check configuration, exiting on any error."""
    load_dotenv(SCRIPT_DIR / ".env")

    input_dir = _env("INPUT_DIR")
    output_dir = _env("OUTPUT_DIR")
    if not input_dir:
        sys.exit("Error: INPUT_DIR is not set in the .env file.")
    if not output_dir:
        sys.exit("Error: OUTPUT_DIR is not set in the .env file.")

    input_path = Path(input_dir)
    if not input_path.is_dir():
        sys.exit(f"Error: INPUT_DIR '{input_dir}' is not a valid directory.")

    cfg = Config(
        input_dir=input_path,
        output_dir=Path(output_dir),
        compression_mode=_env_choice("COMPRESSION_MODE", "lossy", SUPPORTED_COMPRESSION_MODES),
        crf=_env_int("CRF", 28, 0, 51),
        timeout_seconds=_env_int("TIMEOUT_SECONDS", 36000, 0),
        output_format=_env_choice("OUTPUT_FORMAT", "source", SUPPORTED_OUTPUT_FORMATS),
        encoder_preset=_env("ENCODER_PRESET", "medium").lower(),
        encoder_type=_env_choice("ENCODER_TYPE", "cpu", frozenset(HEVC_ENCODERS)),
        overwrite_existing=_env_bool("OVERWRITE_EXISTING", False),
    )

    if cfg.encoder_type == "cpu" and cfg.encoder_preset not in SUPPORTED_X265_PRESETS:
        sys.exit(
            "Error: ENCODER_PRESET must be one of "
            f"{', '.join(sorted(SUPPORTED_X265_PRESETS))}, got '{cfg.encoder_preset}'."
        )
    if cfg.lossless and cfg.output_format == "avi":
        sys.exit("Error: COMPRESSION_MODE=lossless is not supported when OUTPUT_FORMAT=avi.")
    if cfg.lossless and cfg.encoder_type != "cpu":
        sys.exit("Error: COMPRESSION_MODE=lossless currently supports ENCODER_TYPE=cpu only.")

    return cfg


# --------------------------------------------------------------------------- #
# ffmpeg / ffprobe helpers
# --------------------------------------------------------------------------- #

def resolve_ffmpeg() -> str:
    """Return the ffmpeg executable path, or exit with install instructions."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    sys.exit(
        "Error: ffmpeg was not found on PATH. Install it and try again.\n"
        "  Windows: winget install Gyan.FFmpeg   (or: choco install ffmpeg -y)\n"
        "  macOS:   brew install ffmpeg\n"
        "  Linux:   sudo apt install ffmpeg"
    )


def available_encoders(ffmpeg: str) -> set[str]:
    """Return encoder names reported by ``ffmpeg -encoders`` (empty set on failure)."""
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except subprocess.SubprocessError:
        return set()
    if result.returncode != 0:
        return set()

    # Each encoder line looks like " V....D libx265   libx265 H.265 / HEVC".
    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            encoders.add(parts[1])
    return encoders


def media_duration(src: Path, ffprobe: str | None) -> float | None:
    """Return the media duration in seconds, or None if it cannot be determined."""
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(src),
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
        duration = float(result.stdout.strip()) if result.returncode == 0 else 0.0
    except (subprocess.SubprocessError, ValueError):
        return None
    return duration if duration > 0 else None


def video_codec_args(cfg: Config, container: str) -> list[str]:
    """Return the ffmpeg video codec arguments for *container* (e.g. ``.mkv``)."""
    if container == ".avi":
        return ["-c:v", AVI_VIDEO_ENCODER, "-q:v", "4"]

    encoder = HEVC_ENCODERS[cfg.encoder_type]
    crf = str(cfg.crf)
    if cfg.encoder_type == "cpu":
        quality = ["-x265-params", "lossless=1"] if cfg.lossless else ["-crf", crf]
        return ["-c:v", encoder, "-preset", cfg.encoder_preset, *quality]
    if cfg.encoder_type == "nvidia":
        return ["-c:v", encoder, "-cq", crf, "-preset", "p5"]
    if cfg.encoder_type == "intel":
        return ["-c:v", encoder, "-global_quality", crf, "-preset", "medium"]
    return ["-c:v", encoder, "-rc", "cqp", "-qp_i", crf, "-qp_p", crf]  # amd


def build_ffmpeg_command(
    cfg: Config, ffmpeg: str, src: Path, dst: Path, sidecar_srt: Path | None
) -> list[str]:
    """Assemble the full ffmpeg command line for one file."""
    container = dst.suffix.lower()
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-i", str(src)]
    if sidecar_srt is not None:
        cmd += ["-i", str(sidecar_srt)]

    cmd += ["-map", "0:v?", "-map", "0:a?"]
    if container != ".avi":
        cmd += ["-map", "0:s?"]
    if sidecar_srt is not None:
        cmd += ["-map", "1:0"]

    cmd += video_codec_args(cfg, container)

    if container == ".avi":
        cmd += ["-c:a", "libmp3lame", "-b:a", "192k", "-sn"]
    else:
        cmd += ["-c:a", "copy"] if cfg.lossless else ["-c:a", "aac", "-b:a", "128k"]
        cmd += ["-c:s", "copy" if container == ".mkv" else "mov_text"]

    cmd += ["-progress", "pipe:1", "-nostats", "-y", str(dst)]
    return cmd


# --------------------------------------------------------------------------- #
# Compression
# --------------------------------------------------------------------------- #

def _render_progress(processed: float, total: float | None) -> None:
    """Redraw the in-place progress line."""
    if total is None:
        text = f"{format_duration(processed)} processed"
    else:
        done = min(max(processed, 0.0), total)
        filled = int(done / total * PROGRESS_BAR_WIDTH)
        bar = "#" * filled + "-" * (PROGRESS_BAR_WIDTH - filled)
        text = f"[{bar}] {done / total * 100:5.1f}% ({format_duration(done)}/{format_duration(total)})"
    print(f"\r{_stamp()}   Progress   : {text}", end="", flush=True)


def _parse_progress_seconds(line: str) -> float | None:
    """Extract processed seconds from an ffmpeg ``-progress`` line, if it has one."""
    # ffmpeg reports both keys in microseconds (out_time_ms is misnamed upstream).
    if line.startswith(("out_time_us=", "out_time_ms=")):
        try:
            return int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            return None
    return None


def kill_process_tree(process: subprocess.Popen) -> None:
    """
    Terminate *process* and any children it spawned.

    Package-manager shims (e.g. Chocolatey's ``ffmpeg.EXE``) start the real
    ffmpeg as a child process, so a plain ``kill()`` would orphan the encoder
    and leave it running to completion.
    """
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, check=False,
        )
    else:
        process.kill()


def compress_file(
    cfg: Config, ffmpeg: str, ffprobe: str | None, src: Path, dst: Path
) -> bool:
    """
    Compress *src* into *dst*.

    Output is written to a temporary ``.partial`` file and renamed into place
    only on success, so a failed or interrupted run never leaves a half-written
    file at *dst*. Returns True on success, False otherwise.
    """
    container = dst.suffix.lower()
    sidecar_srt: Path | None = src.with_suffix(".srt")
    if not sidecar_srt.is_file():
        sidecar_srt = None
    elif container == ".mkv":
        log(f"  Captions   : Embedding sidecar '{sidecar_srt.name}'")
    else:
        log(f"  Captions   : Sidecar '{sidecar_srt.name}' found but only MKV output embeds it; skipping")
        sidecar_srt = None

    partial = dst.with_name(f"{dst.stem}.partial{dst.suffix}")
    cmd = build_ffmpeg_command(cfg, ffmpeg, src, partial, sidecar_srt)
    total = media_duration(src, ffprobe)

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        log_error(f"[ERROR] Could not start ffmpeg: {exc}")
        return False

    # Drain stderr concurrently so a chatty ffmpeg can never fill the pipe and stall.
    stderr_chunks: list[str] = []
    stderr_thread = threading.Thread(
        target=lambda: stderr_chunks.append(process.stderr.read()), daemon=True  # type: ignore[union-attr]
    )
    stderr_thread.start()

    timed_out = threading.Event()
    killer: threading.Timer | None = None
    if cfg.timeout_seconds > 0:
        killer = threading.Timer(
            cfg.timeout_seconds, lambda: (timed_out.set(), kill_process_tree(process))
        )
        killer.daemon = True
        killer.start()

    processed = 0.0
    last_draw = 0.0
    try:
        for line in process.stdout:  # type: ignore[union-attr]
            seconds = _parse_progress_seconds(line.strip())
            if seconds is None:
                continue
            processed = seconds
            now = time.monotonic()
            if now - last_draw >= PROGRESS_REDRAW_INTERVAL:
                _render_progress(processed, total)
                last_draw = now
        process.wait()
    except KeyboardInterrupt:
        kill_process_tree(process)
        process.wait()
        partial.unlink(missing_ok=True)
        print()
        raise
    finally:
        if killer is not None:
            killer.cancel()
        stderr_thread.join(timeout=5)

    if process.returncode == 0:
        _render_progress(total if total is not None else processed, total)
    print()

    if timed_out.is_set():
        log_error(f"[ERROR] ffmpeg timed out after {cfg.timeout_seconds}s.")
    elif process.returncode != 0:
        details = "".join(stderr_chunks).strip()
        log_error(f"[ERROR] ffmpeg exited with code {process.returncode}:\n{details}")
    else:
        os.replace(partial, dst)
        return True

    partial.unlink(missing_ok=True)
    return False


# --------------------------------------------------------------------------- #
# Main workflow
# --------------------------------------------------------------------------- #

def find_video_files(directory: Path) -> list[Path]:
    """Return all supported video files directly inside *directory*, sorted by name."""
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def output_path_for(cfg: Config, src: Path) -> Path:
    """Return the destination path for *src* based on ``OUTPUT_FORMAT``."""
    if cfg.output_format == "source":
        return cfg.output_dir / src.name
    return cfg.output_dir / f"{src.stem}.{cfg.output_format}"


def check_encoder_available(cfg: Config, ffmpeg: str) -> None:
    """Exit early if this ffmpeg build lacks the encoder the configuration needs."""
    required = AVI_VIDEO_ENCODER if cfg.output_format == "avi" else HEVC_ENCODERS[cfg.encoder_type]
    encoders = available_encoders(ffmpeg)
    if encoders and required not in encoders:
        sys.exit(
            f"Error: This ffmpeg build does not include the '{required}' encoder required by "
            f"the current OUTPUT_FORMAT/ENCODER_TYPE settings."
        )


def print_banner(cfg: Config, file_count: int) -> None:
    """Print the run configuration summary."""
    quality = "mode=lossless" if cfg.lossless else f"CRF={cfg.crf}"
    timeout = (
        "disabled" if cfg.timeout_seconds == 0
        else f"{format_duration(cfg.timeout_seconds)} ({cfg.timeout_seconds}s)"
    )
    preset = cfg.encoder_preset if cfg.encoder_type == "cpu" else f"{cfg.encoder_preset} (ignored for {cfg.encoder_type})"

    log(f"Found {file_count} file(s) to compress ({quality}).")
    log(f"Timeout per file: {timeout}")
    log(f"Compression mode: {cfg.compression_mode}")
    log(f"Encoder type: {cfg.encoder_type}")
    log(f"Encoder preset: {preset}")
    log(f"Output format: {cfg.output_format}")
    log(f"Output folder: {cfg.output_dir}")
    log(f"Existing outputs: {'overwrite' if cfg.overwrite_existing else 'skip'}")
    log()


def main() -> None:
    """Compress every supported video in ``INPUT_DIR`` into ``OUTPUT_DIR``."""
    cfg = load_config()
    ffmpeg = resolve_ffmpeg()
    ffprobe = shutil.which("ffprobe")
    check_encoder_available(cfg, ffmpeg)

    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    video_files = find_video_files(cfg.input_dir)
    if not video_files:
        log(f"No MP4 or MKV files found in '{cfg.input_dir}'.")
        return

    print_banner(cfg, len(video_files))

    succeeded = skipped = failed = 0
    total_saved = 0
    batch_start = time.monotonic()

    for index, src in enumerate(video_files, start=1):
        dst = output_path_for(cfg, src)
        log(f"[{index}/{len(video_files)}] {src.name} -> {dst.name}")

        if dst.exists() and not cfg.overwrite_existing:
            log("  Skipping   : output already exists (set OVERWRITE_EXISTING=true to redo)")
            log()
            skipped += 1
            continue

        input_size = src.stat().st_size
        log(f"  Input size : {format_size(input_size)}")
        file_start = time.monotonic()

        if compress_file(cfg, ffmpeg, ffprobe, src, dst):
            output_size = dst.stat().st_size
            saved = input_size - output_size
            total_saved += saved
            log(f"  Output size: {format_size(output_size)}")
            if saved >= 0:
                percent = f" ({saved / input_size * 100:.1f}%)" if input_size else ""
                log(f"  Saved      : {format_size(saved)}{percent}")
            else:
                log(f"  Warning    : Output is {format_size(-saved)} larger than input.")
            log(f"  Elapsed    : {format_duration(time.monotonic() - file_start)}")
            succeeded += 1
        else:
            log(f"  Failed     : '{src.name}' was not compressed.")
            failed += 1
        log()

    net = (
        f"Total saved: {format_size(total_saved)}." if total_saved >= 0
        else f"Warning: outputs are {format_size(-total_saved)} larger than inputs in total."
    )
    log(
        f"Done in {format_duration(time.monotonic() - batch_start)}. "
        f"{succeeded} compressed, {skipped} skipped, {failed} failed. {net}"
    )
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log_error("Interrupted by user.")
        sys.exit(130)
