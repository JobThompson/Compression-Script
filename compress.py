#!/usr/bin/env python3
"""
compress.py - Compress MP4 and MKV video files to reduce file size.

Reads configuration from a .env file in the same directory:
  INPUT_DIR  - folder containing source video files
  OUTPUT_DIR - folder where compressed files will be saved
  CRF        - Constant Rate Factor for H.265 encoding (default: 28)

Requires ffmpeg to be installed and available on PATH.
"""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

SUPPORTED_EXTENSIONS = {".mp4", ".mkv"}


def load_config() -> tuple[Path, Path, int]:
    """Load and validate configuration from the .env file."""
    load_dotenv()

    input_dir = os.getenv("INPUT_DIR", "").strip()
    output_dir = os.getenv("OUTPUT_DIR", "").strip()
    crf_str = os.getenv("CRF", "28").strip()

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

    return input_path, output_path, crf


def find_video_files(directory: Path) -> list[Path]:
    """Return all MP4 and MKV files in *directory* (non-recursive)."""
    return sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def compress_file(src: Path, dst: Path, crf: int, timeout: int = 3600) -> bool:
    """
    Compress *src* into *dst* using H.265 (libx265) video and AAC audio.

    Returns True on success, False on failure.
    The *timeout* parameter limits how long ffmpeg may run (default: 1 hour).
    """
    cmd = [
        "ffmpeg",
        "-i", str(src),
        "-c:v", "libx265",
        "-crf", str(crf),
        "-preset", "medium",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",          # overwrite output without asking
        str(dst),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"  [ERROR] ffmpeg timed out after {timeout}s.", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"  [ERROR] ffmpeg failed:\n{result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def format_size(size_bytes: int) -> str:
    """Return a human-readable file size string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def main() -> None:
    input_path, output_path, crf = load_config()

    output_path.mkdir(parents=True, exist_ok=True)

    video_files = find_video_files(input_path)
    if not video_files:
        print(f"No MP4 or MKV files found in '{input_path}'.")
        return

    print(f"Found {len(video_files)} file(s) to compress (CRF={crf}).")
    print(f"Output folder: {output_path}\n")

    success_count = 0
    for index, src in enumerate(video_files, start=1):
        # Preserve the original extension to avoid name collisions (e.g. video.mp4 vs video.mkv)
        dst = output_path / src.name
        print(f"[{index}/{len(video_files)}] {src.name} -> {dst.name}")
        print(f"  Input size : {format_size(src.stat().st_size)}")

        if compress_file(src, dst, crf):
            input_size = src.stat().st_size
            output_size = dst.stat().st_size
            saved = input_size - output_size
            print(f"  Output size: {format_size(output_size)}")
            if saved >= 0:
                print(f"  Saved      : {format_size(saved)}")
            else:
                print(f"  Warning    : Output is {format_size(-saved)} larger than input.")
            success_count += 1
        else:
            print(f"  Skipping '{src.name}' due to errors.")

        print()

    print(f"Done. {success_count}/{len(video_files)} file(s) compressed successfully.")


if __name__ == "__main__":
    main()
