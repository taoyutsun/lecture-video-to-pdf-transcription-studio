# Lecture Video to PDF & Transcription Studio

Traditional Chinese: [README.md](README.md)

**Lecture Video to PDF & Transcription Studio** is a local Web utility for extracting clean slide PDFs from lecture videos, online courses, study group recordings, and presentation videos. It also provides standalone transcription for common video and audio files.

PDF extraction does not require ASR. Transcription can use local `faster-whisper` or an OpenAI-compatible ASR endpoint.

## Features

- Detect slide changes from videos and export `result.pdf`
- Keep slide images, thumbnails, and `metadata.json`
- Review thumbnails, remove false positives, reorder slides, and rebuild the PDF
- Crop a lecture area with `x,y,w,h` when the video contains a speaker camera or extra UI
- Standalone transcription for videos and audio files
- Optional ASR engines:
  - `faster-whisper`
  - OpenAI-compatible ASR endpoint
  - Compatible with QwenASR, faster-whisper server, speaches, whisper.cpp server, Groq Whisper API, and similar services

## Install And Run

### From Source

```powershell
git clone https://github.com/taoyutsun/lecture-video-to-pdf-transcription-studio.git
cd lecture-video-to-pdf-transcription-studio
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m lecture_video_to_pdf run
```

By default, open `http://127.0.0.1:8787/`. If that port is already in use, choose another port:

```powershell
.\.venv\Scripts\python.exe -m lecture_video_to_pdf run --port 8788
```

### From Release

Download and extract `LectureVideo2PDF-v0.1.0-windows-x64.zip`, open PowerShell in the extracted `LectureVideo2PDF` folder, then run:

```powershell
.\LectureVideo2PDF.exe run
```

For large videos, pasting a local file path is recommended. File picker and drag-and-drop upload the media into the local `uploads/` folder before processing.

## Web UI Modes

### Video To Slide PDF

Use this mode to extract lecture slides from a video.

- `PDF only`: exports slide PDF, images, and metadata
- `PDF + transcription`: also exports `transcript.txt`, `transcript.srt`, and `slide_map.md`

### Transcription

Use this mode when you only need captions or a transcript. It accepts common video and audio files:

- Video: `.mp4`, `.mkv`, `.avi`, `.mov`, `.wmv`, `.webm`, `.m4v`
- Audio: `.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`, `.ogg`, `.opus`, `.wma`

ASR-only output:

```text
output/
  media_name_YYYYMMDD_HHMMSS/
    transcript.txt
    transcript.srt
    metadata.json
```

## CLI

Start the Web UI:

```powershell
lecture-video-to-pdf run
lecture-video-studio run
```

These two console scripts are aliases; use either one. When installed from source, `pip install -e .` creates them in the current Python environment's `Scripts` directory, so they do not appear as files in the project root. In the portable release, use `LectureVideo2PDF.exe run`.

Convert a lecture video to slide PDF:

```powershell
lecture-video-to-pdf convert "D:\Videos\lecture.mp4" --out output --mode balanced --crop auto --asr none
```

Create PDF + transcription:

```powershell
lecture-video-to-pdf convert "D:\Videos\lecture.mp4" --out output --asr openai-compatible --endpoint-base-url http://127.0.0.1:11435 --response-format srt
```

Transcribe only:

```powershell
lecture-video-to-pdf transcribe "D:\Audio\lecture.mp3" --out output --asr faster-whisper --asr-model base --response-format srt
lecture-video-studio transcribe "D:\Videos\lecture.mp4" --asr openai-compatible --endpoint-base-url https://api.groq.com/openai/v1 --asr-model whisper-large-v3-turbo
```

## faster-whisper

Video-to-PDF extraction does not require ASR. To use built-in `faster-whisper`, select **faster-whisper** in the Web UI and either allow automatic ASR dependency installation before conversion or click the install button. The installer uses the same Python environment that is running this tool; model files are still downloaded lazily by `faster-whisper` on first transcription.

Manual install:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-asr.txt
```

Common models:

- `tiny`, `base`, `small`, `medium`
- `large-v2`, `large-v3`
- `turbo`
- `distil-large-v3`

When the device is set to `auto`, the tool prefers a usable GPU. If `cuda` is selected, or `auto` chooses GPU, the tool first tries to add available CUDA 12 DLL directories before loading `faster-whisper`. It falls back to CPU/int8 only after GPU loading actually fails, and reports the reason in the result warning.

If the computer has an NVIDIA GPU but no CUDA 12 runtime DLLs are available from existing tools, the Web UI shows a **CUDA GPU acceleration dependencies** block under **faster-whisper** settings. Users can explicitly allow installation, and the tool will install GPU runtime dependencies in the background before conversion. These packages are large, so the tool never installs them without confirmation.

Manual CUDA runtime install:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-asr-cuda.txt
```

The generated `transcript.srt` is formatted for video players with short subtitle cues, defaulting to at most two lines per cue. The complete transcript remains available in `transcript.txt`.

The language selector includes `Auto detect (Traditional Chinese for Chinese)`, `Traditional Chinese`, and `Simplified Chinese`. In auto mode, if ASR detects Chinese or the returned text looks Chinese, the tool prefers Traditional Chinese output. Select `Simplified Chinese` explicitly when simplified output is desired.

## OpenAI-compatible Endpoint

For OpenAI-compatible ASR endpoints, configure `base_url`, `api_key/token`, `model`, language, and `response_format` in the Web UI.

The connection test tries `/health` and common OpenAI `/models` endpoints. If the endpoint manages the loaded model server-side, such as QwenASR, the Web UI fixes the model selector to `default (configured by endpoint)` to avoid implying that this tool can switch the local model.

For a portable [QwenASRMiniTool](https://github.com/dseditor/QwenASRMiniTool) installation, start its OpenAI-compatible transcription endpoint and enter the endpoint URL in this tool. To let the Web UI show local QwenASR installation status, set `LECTURE_VIDEO_TO_PDF_QWEN_ASR_ROOT` or `QWEN_ASR_HOME` to the QwenASR root directory.

If another local tool provides CUDA runtime DLLs but they are not on the system `PATH`, set `LECTURE_VIDEO_TO_PDF_EXTERNAL_CUDA_DIRS` to the extra DLL directories. Use the operating system path separator for multiple directories.

Groq Speech-to-Text currently supports `json`, `verbose_json`, and `text`. If SRT output is needed, this tool requests `verbose_json` and generates `transcript.srt` locally.

API keys are not written to output files, metadata, or project config. The Web UI only keeps them in the current page, request payload, and background job memory.

## Output

PDF mode:

```text
output/
  video_name_YYYYMMDD_HHMMSS/
    result.pdf
    metadata.json
    slides/
    thumbs/
    transcript.txt      # PDF + ASR only
    transcript.srt      # PDF + ASR only
    slide_map.md        # PDF + ASR only
```

Transcription mode:

```text
output/
  media_name_YYYYMMDD_HHMMSS/
    transcript.txt
    transcript.srt
    metadata.json
```

## References And License

This project is released under the MIT License. When using external models, FFmpeg, ASR endpoints, or cloud APIs, follow their respective licenses and terms.

References:

- LearnOpenCV background subtraction workflow: https://learnopencv.com/video-to-slides-converter-using-background-subtraction/
- PySceneDetect: https://www.scenedetect.com/docs/latest/cli.html
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Qwen3-ASR: https://github.com/QwenLM/Qwen3-ASR
- WhisperX: https://github.com/m-bain/whisperX

## Author

- Arthur Tao
- Blog: https://taoyutsun.blogspot.com/
- Facebook: https://facebook.com/arthurtaoyutsun
- Source code: https://github.com/taoyutsun/lecture-video-to-pdf-transcription-studio
