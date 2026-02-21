# Compression-Script

A Python script that compresses MP4 and MKV video files into a smaller file size using H.265 (HEVC) encoding via [ffmpeg](https://ffmpeg.org/).

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) installed and available on `PATH`

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
   CRF=28
   ```

   | Variable     | Description                                                                 |
   |-------------|-----------------------------------------------------------------------------|
   | `INPUT_DIR`  | Folder containing the source MP4/MKV files to compress                      |
   | `OUTPUT_DIR` | Folder where the compressed files will be saved (created if it doesn't exist) |
   | `CRF`        | Constant Rate Factor for H.265 (0–51). Lower = better quality, larger file. Default: `28` |

## Usage

```bash
python compress.py
```

The script will:
- Scan `INPUT_DIR` for `.mp4` and `.mkv` files
- Compress each file using H.265 video and AAC audio
- Save the results to `OUTPUT_DIR`
- Print input/output sizes and space saved for each file

## Example Output

```
Found 3 file(s) to compress (CRF=28).
Output folder: /path/to/compressed/output

[1/3] movie.mkv -> movie.mp4
  Input size : 4.2 GB
  Output size: 1.8 GB
  Saved      : 2.4 GB

[2/3] clip.mp4 -> clip.mp4
  Input size : 850.0 MB
  Output size: 310.5 MB
  Saved      : 539.5 MB
...
Done. 3/3 file(s) compressed successfully.
```