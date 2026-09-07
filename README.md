# Compression-Script

A single-file Python script that batch-compresses MP4 and MKV videos with H.265 (HEVC) via [ffmpeg](https://ffmpeg.org/). Point it at a folder, run it, and re-run it any time to pick up where it left off.

## Features

- Compresses `.mp4` and `.mkv` files with H.265 (`libx265` or a GPU encoder) + AAC audio
- **Resumable**: files whose output already exists are skipped, so an interrupted batch can simply be re-run
- **Safe writes**: encodes to a `*.partial.*` file and renames it into place only on success — no half-written outputs
- Output container selection (`OUTPUT_FORMAT=source|mkv|mp4|avi`; AVI uses Xvid + MP3)
- Lossy or lossless mode (`COMPRESSION_MODE=lossy|lossless`)
- CPU or GPU encoding (`ENCODER_TYPE=cpu|nvidia|intel|amd`) with an up-front check that your ffmpeg build has the encoder
- x265 speed/quality tuning (`ENCODER_PRESET`)
- Preserves subtitle streams for MKV/MP4 outputs and auto-embeds a matching sidecar `.srt` into MKV outputs
- Per-file timeout (`TIMEOUT_SECONDS`, `0` to disable) that reliably kills ffmpeg — including through Chocolatey/winget shims
- Live progress bar (percentage when `ffprobe` is available, elapsed media time otherwise)
- Timestamped logs with input/output sizes, space saved, elapsed time, and a batch summary
- Exit code `1` if any file failed, `130` on Ctrl+C (partial output is cleaned up)

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) on `PATH` (`ffprobe`, bundled with ffmpeg, enables percentage progress)

```powershell
winget install Gyan.FFmpeg      # Windows
choco install ffmpeg -y         # Windows (Chocolatey)
brew install ffmpeg             # macOS
sudo apt install ffmpeg         # Debian/Ubuntu
```

## Setup

1. Install the Python dependency:

   ```bash
   pip install -r requirements.txt
   ```

2. Create your `.env` next to `compress.py`:

   ```bash
   cp .env.example .env
   ```

3. Edit `.env`. Only `INPUT_DIR` and `OUTPUT_DIR` are required; everything else has a default. Real environment variables override values in `.env`.

   | Variable             | Default   | Description |
   |----------------------|-----------|-------------|
   | `INPUT_DIR`          | —         | Folder containing the source MP4/MKV files (not recursive) |
   | `OUTPUT_DIR`         | —         | Folder for compressed files; created if missing |
   | `COMPRESSION_MODE`   | `lossy`   | `lossy` re-encodes at `CRF`; `lossless` uses x265 lossless video and copies audio untouched |
   | `CRF`                | `28`      | H.265 quality, 0–51. Lower = better quality, larger file. Ignored in lossless mode |
   | `TIMEOUT_SECONDS`    | `36000`   | Max ffmpeg runtime per file (10 h). `0` disables the timeout |
   | `OUTPUT_FORMAT`      | `source`  | `source` (keep extension), `mkv`, `mp4`, or `avi` |
   | `ENCODER_PRESET`     | `medium`  | x265 preset `ultrafast` … `placebo`. CPU only; ignored for GPU encoders |
   | `ENCODER_TYPE`       | `cpu`     | `cpu` (`libx265`), `nvidia` (`hevc_nvenc`), `intel` (`hevc_qsv`), `amd` (`hevc_amf`) |
   | `OVERWRITE_EXISTING` | `false`   | `true` re-encodes files whose output already exists; `false` skips them (resume) |

### Recommended profiles

| Goal | Settings |
|------|----------|
| Best compression (slow) | `ENCODER_TYPE=cpu` `ENCODER_PRESET=medium` `CRF=28` `TIMEOUT_SECONDS=0` |
| Faster CPU encode | `ENCODER_TYPE=cpu` `ENCODER_PRESET=fast` `CRF=28` |
| NVIDIA GPU | `ENCODER_TYPE=nvidia` `CRF=28` `TIMEOUT_SECONDS=0` |
| Intel Quick Sync | `ENCODER_TYPE=intel` `CRF=28` `TIMEOUT_SECONDS=0` |
| AMD GPU | `ENCODER_TYPE=amd` `CRF=28` `TIMEOUT_SECONDS=0` |
| Archive quality | `COMPRESSION_MODE=lossless` `ENCODER_TYPE=cpu` |

Constraints:
- `COMPRESSION_MODE=lossless` requires `ENCODER_TYPE=cpu` and cannot be combined with `OUTPUT_FORMAT=avi`.
- GPU encoders produce larger files than `libx265` at the same CRF; lower `CRF` by 2–4 for comparable quality.
- If the script reports a missing encoder, install an ffmpeg build that includes your GPU encoder.

## Usage

```bash
python compress.py
```

For each `.mp4`/`.mkv` in `INPUT_DIR` (alphabetical order) the script:

1. Skips it if the output already exists (unless `OVERWRITE_EXISTING=true`)
2. Encodes to `OUTPUT_DIR/<name>.partial.<ext>` with the configured encoder, keeping subtitle streams (and embedding `<name>.srt` for MKV output)
3. Renames the file into place on success, or deletes the partial on failure/timeout
4. Logs sizes, space saved, and elapsed time

## How it works

`compress.py` is intentionally a single module:

| Section | Responsibility |
|---------|----------------|
| `Config` / `load_config()` | Reads `.env`, validates every value and cross-field constraint, exits with a clear message on error |
| `resolve_ffmpeg()` / `available_encoders()` | Locates ffmpeg and verifies the required encoder exists before any work starts |
| `build_ffmpeg_command()` | Assembles the ffmpeg command line (stream mapping, codecs, sidecar subtitles) |
| `compress_file()` | Runs ffmpeg, streams `-progress` output into a throttled progress bar, drains stderr on a background thread, enforces the timeout, and finalises the `.partial` file |
| `kill_process_tree()` | Kills ffmpeg *and its children* — needed because Chocolatey/winget shims spawn the real binary as a child process |
| `main()` | Iterates files, handles skip/overwrite, prints the summary and sets the exit code |

## Example output

```
[2026-02-22 14:01:03] Found 3 file(s) to compress (CRF=28).
[2026-02-22 14:01:03] Timeout per file: disabled
[2026-02-22 14:01:03] Compression mode: lossy
[2026-02-22 14:01:03] Encoder type: cpu
[2026-02-22 14:01:03] Encoder preset: medium
[2026-02-22 14:01:03] Output format: source
[2026-02-22 14:01:03] Output folder: /path/to/compressed/output
[2026-02-22 14:01:03] Existing outputs: skip

[2026-02-22 14:01:03] [1/3] movie.mkv -> movie.mkv
[2026-02-22 14:01:03]   Input size : 4.2 GB
[2026-02-22 14:01:03]   Captions   : Embedding sidecar 'movie.srt'
[2026-02-22 14:20:20]   Progress   : [##############################] 100.0% (02:03:44/02:03:44)
[2026-02-22 14:20:20]   Output size: 1.8 GB
[2026-02-22 14:20:20]   Saved      : 2.4 GB (57.1%)
[2026-02-22 14:20:20]   Elapsed    : 00:19:17

[2026-02-22 14:20:20] [2/3] clip.mp4 -> clip.mp4
[2026-02-22 14:20:20]   Skipping   : output already exists (set OVERWRITE_EXISTING=true to redo)

[2026-02-22 14:20:20] [3/3] short.mp4 -> short.mp4
[2026-02-22 14:20:20]   Input size : 850.0 MB
[2026-02-22 14:30:18]   Progress   : [##############################] 100.0% (00:05:44/00:05:44)
[2026-02-22 14:30:18]   Output size: 310.5 MB
[2026-02-22 14:30:18]   Saved      : 539.5 MB (63.5%)
[2026-02-22 14:30:18]   Elapsed    : 00:09:58

[2026-02-22 14:30:18] Done in 00:29:15. 2 compressed, 1 skipped, 0 failed. Total saved: 2.9 GB.
```
