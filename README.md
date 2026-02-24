# Compression-Script

A Python script that compresses MP4 and MKV video files into a smaller file size using H.265 (HEVC) encoding via [ffmpeg](https://ffmpeg.org/).

## Features

- Compresses `.mp4` and `.mkv` files using H.265 (`libx265`) + AAC audio (AVI output uses Xvid + MP3)
- Preserves original filenames/extensions in output (e.g. `movie.mkv -> movie.mkv`)
- Supports output container selection via `.env` (`OUTPUT_FORMAT=source|mkv|mp4|avi`)
- Supports compression mode selection via `.env` (`COMPRESSION_MODE=lossy|lossless`)
- Supports encoder backend selection via `.env` (`ENCODER_TYPE=cpu|nvidia|intel|amd`)
- Supports x265 speed/quality tuning via `.env` (`ENCODER_PRESET`)
- Preserves caption/subtitle streams from the source file for MKV/MP4 outputs
- Auto-embeds matching sidecar `.srt` (same filename) into `.mkv` outputs
- Configurable per-file timeout via `.env` (`TIMEOUT_SECONDS`, set `0` to disable)
- Timestamped console logs for all status/error messages
- Live per-file progress output during compression
   - Shows percentage progress bar when `ffprobe` is available
   - Falls back to elapsed processed time when duration cannot be determined

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) installed and available on `PATH`
- Optional: `ffprobe` on `PATH` for percentage progress bars (usually installed with ffmpeg)

Install ffmpeg on Windows with Chocolatey:

```powershell
choco install ffmpeg -y
```

Or with winget:

```powershell
winget install Gyan.FFmpeg
```

## Setup

1. **Install Python dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

2. **Create your `.env` file** by copying the example:

   ```bash
   cp .env.example .env
   ```

3. **Edit `.env`** with the correct paths for your system:

   ```ini
   INPUT_DIR=/path/to/your/videos
   OUTPUT_DIR=/path/to/compressed/output
   COMPRESSION_MODE=lossy
   CRF=28
   TIMEOUT_SECONDS=36000
   OUTPUT_FORMAT=source
   ENCODER_PRESET=medium
   ENCODER_TYPE=cpu
   ```

   | Variable          | Description                                                                 |
   |------------------|-----------------------------------------------------------------------------|
   | `INPUT_DIR`       | Folder containing the source MP4/MKV files to compress                      |
   | `OUTPUT_DIR`      | Folder where the compressed files will be saved (created if it doesn't exist) |
   | `COMPRESSION_MODE`| Compression mode: `lossy` (default) uses re-encode for smaller files; `lossless` preserves source quality using x265 lossless video + copied audio. |
   | `CRF`             | Constant Rate Factor for H.265 (0–51). Lower = better quality, larger file. Default: `28` |
   | `TIMEOUT_SECONDS` | Max runtime per file before ffmpeg is stopped. Integer `>= 0`; `0` disables timeout. Default: `36000` (10 hours) |
   | `OUTPUT_FORMAT`   | Output container format: `source`, `mkv`, `mp4`, or `avi`. Default: `source` |
   | `ENCODER_PRESET`  | x265 preset for CPU encoding (`ultrafast`..`placebo`). Ignored for `nvidia`, `intel`, and `amd`. Default: `medium` |
   | `ENCODER_TYPE`    | Video encoder backend for `source`/`mkv`/`mp4`: `cpu` (`libx265`), `nvidia` (`hevc_nvenc`), `intel` (`hevc_qsv`), `amd` (`hevc_amf`). Default: `cpu` |

### Recommended `.env` profiles

Use one of these as a starting point:

- **CPU (best compression efficiency, slower):**

   ```ini
   ENCODER_TYPE=cpu
   ENCODER_PRESET=medium
   CRF=28
   TIMEOUT_SECONDS=0
   ```

- **Nvidia (`hevc_nvenc`, fastest on supported NVIDIA GPUs):**

   ```ini
   ENCODER_TYPE=nvidia
   CRF=28
   TIMEOUT_SECONDS=0
   ```

- **Intel (`hevc_qsv`, good speed on Intel iGPU):**

   ```ini
   ENCODER_TYPE=intel
   CRF=28
   TIMEOUT_SECONDS=0
   ```

- **AMD (`hevc_amf`, good speed on supported AMD GPUs):**

   ```ini
   ENCODER_TYPE=amd
   CRF=28
   TIMEOUT_SECONDS=0
   ```

Notes:
- `ENCODER_PRESET` is used only when `ENCODER_TYPE=cpu`.
- `COMPRESSION_MODE=lossless` currently supports `ENCODER_TYPE=cpu` only.
- `COMPRESSION_MODE=lossless` cannot be used with `OUTPUT_FORMAT=avi`.
- If ffmpeg exits with an encoder-not-available error, install an ffmpeg build that includes your GPU encoder.

## Usage

```bash
python compress.py
```

The script will:
- Scan `INPUT_DIR` for `.mp4` and `.mkv` files
- Compress each file using `COMPRESSION_MODE`:
   - `lossy`: H.265 video + AAC audio
   - `lossless`: x265 lossless video + copied audio streams
- Keep subtitle/caption streams from the source file
- Add `movie.srt` automatically when output is MKV and `movie.mkv` is being compressed (if found in the same folder)
- Write outputs using `OUTPUT_FORMAT` (including `avi`)
- Save the results to `OUTPUT_DIR`
- Print a startup banner with CRF and timeout settings
- Show timestamped logs for status and errors
- Show live progress while each file is being compressed
- Print input/output sizes and space saved for each file

## Example Output

```
[2026-02-22 14:01:03] Found 3 file(s) to compress (CRF=28).
[2026-02-22 14:01:03] Timeout per file: 10:00:00 (36000s)
[2026-02-22 14:01:03] Encoder type: nvidia
[2026-02-22 14:01:03] Encoder preset: medium
[2026-02-22 14:01:03] Output format: source
[2026-02-22 14:01:03] Output folder: /path/to/compressed/output

[2026-02-22 14:01:03] [1/3] movie.mkv -> movie.mkv
[2026-02-22 14:01:03]   Input size : 4.2 GB
[2026-02-22 14:07:41] Progress   : [##########--------------------] 34.1% (00:42:12/02:03:44)
[2026-02-22 14:20:20] Progress   : [##############################] 100.0% (02:03:44/02:03:44)
[2026-02-22 14:20:20]   Output size: 1.8 GB
[2026-02-22 14:20:20]   Saved      : 2.4 GB

[2026-02-22 14:20:20] [2/3] clip.mp4 -> clip.mp4
[2026-02-22 14:20:20]   Input size : 850.0 MB
[2026-02-22 14:26:05] Progress   : 00:05:44 processed
[2026-02-22 14:30:18]   Output size: 310.5 MB
[2026-02-22 14:30:18]   Saved      : 539.5 MB
...
[2026-02-22 15:02:11] Done. 3/3 file(s) compressed successfully.
```