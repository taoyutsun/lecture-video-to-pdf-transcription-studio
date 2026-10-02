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
- Cloud ASR can extract and chunk audio automatically for long media that exceeds a provider's single-upload limit

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

The release provides two Windows portable archives:

- `LectureVideo2PDF-v0.3.0-windows-x64.zip`: standard build for most users; supports PDF extraction, OpenAI-compatible endpoints, and `faster-whisper` CPU transcription.
- `LectureVideo2PDF-v0.3.0-windows-x64-cuda.zip`: CUDA GPU build with CUDA 12 runtime bundled, for users who want NVIDIA GPU acceleration with local `faster-whisper`; this archive is significantly larger.

Download and extract the archive, open the extracted `LectureVideo2PDF` folder, then double-click `LectureVideo2PDF.exe` to start the Web UI.

To start from PowerShell, or to choose a port, run:

```powershell
.\LectureVideo2PDF.exe run
```

If `8787` is already in use:

```powershell
.\LectureVideo2PDF.exe run --port 8788
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
    transcription_manifest.json  # cloud chunked transcription only
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
lecture-video-studio transcribe "D:\Videos\lecture.mp4" --asr openai-compatible --endpoint-base-url https://api.groq.com/openai/v1 --asr-model whisper-large-v3-turbo --endpoint-upload-strategy auto
```

## faster-whisper

Video-to-PDF extraction does not require ASR. The Windows portable standard build bundles the CPU runtime dependencies for `faster-whisper`; the CUDA GPU build also bundles NVIDIA CUDA 12 runtime. Model files are still downloaded lazily by `faster-whisper` on first transcription and are not bundled in the release.

When running from source/dev mode, select **faster-whisper** in the Web UI and either allow automatic ASR dependency installation before conversion or click the install button. The installer uses the same Python environment that is running this tool.

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

If the computer has an NVIDIA GPU but the standard portable build cannot load CUDA 12 runtime, use `LectureVideo2PDF-v0.3.0-windows-x64-cuda.zip`. In source/dev mode, the Web UI shows a **CUDA GPU acceleration dependencies** block under **faster-whisper** settings. Users can explicitly allow installation, and the tool will install GPU runtime dependencies in the background before conversion. These packages are large, so the tool never installs them without confirmation. Portable releases do not install CUDA runtime Python packages at runtime; to rebuild a GPU portable package yourself, install the dependencies in source/dev mode and run `.\scripts\build_portable.ps1 -IncludeCudaRuntime -DistPath dist-gpu`. Builds do not overwrite existing output; choose a fresh `-DistPath` when rebuilding.

Manual CUDA runtime install:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-asr-cuda.txt
```

The generated `transcript.srt` is formatted for video players with short subtitle cues, defaulting to at most two lines per cue. The complete transcript remains available in `transcript.txt`.

The language selector includes `Auto detect (Traditional Chinese for Chinese)`, `Traditional Chinese`, and `Simplified Chinese`. In auto mode, if ASR detects Chinese or the returned text looks Chinese, the tool prefers Traditional Chinese output. Select `Simplified Chinese` explicitly when simplified output is desired.

## OpenAI-compatible Endpoint

For OpenAI-compatible ASR endpoints, configure `base_url`, `api_key/token`, `model`, language, `response_format`, upload strategy, maximum chunk size, and chunk duration in the Web UI.

### Provider Profiles and Models

Provider profiles are available in both source/dev and portable editions from v0.3.0.

1. Select Groq, OpenAI, local QwenASR, or a new custom endpoint. Built-in providers populate the URL automatically.
2. Enter the API key and test the connection to refresh transcription models. Chat/TTS models are excluded. Custom endpoints also support manually entered model IDs.
3. To remember the key, check the Windows Credential Manager option and click Save settings. Without that checkbox, only non-secret preferences are saved; the key stays temporary.
4. Subsequent launches restore provider, model, language, format and chunk settings. Saved keys are not returned to the browser. Leave the key field empty to use the saved credential, or remove it with the dedicated button.

Groq defaults to `whisper-large-v3-turbo` and also offers `whisper-large-v3`. OpenAI offers `whisper-1`, `gpt-4o-transcribe` and `gpt-4o-mini-transcribe`. The latter two lack subtitle timestamps, so only TXT output is available, not SRT. Both the `text` and `json` selections request JSON from these models and write local TXT, not a separate raw JSON file.

Preset model lists are not proof of account access. Connection tests refresh the list with a last-checked timestamp; actual transcription is still subject to permissions, quotas and charges. Use a separate key for each provider. See the official [Groq Speech-to-Text](https://console.groq.com/docs/speech-to-text) and [OpenAI transcription guides](https://developers.openai.com/api/docs/guides/speech-to-text) for model capabilities.

Local connection tests try `/health` first; cloud providers use common `/models` endpoints. Server-managed endpoints such as QwenASR use `default (configured by endpoint)` rather than implying that the client can switch loaded models. Create a custom profile for a different local port.

For a portable [QwenASRMiniTool](https://github.com/dseditor/QwenASRMiniTool) installation, start its OpenAI-compatible transcription endpoint and enter the endpoint URL in this tool. To let the Web UI show local QwenASR installation status, set `LECTURE_VIDEO_TO_PDF_QWEN_ASR_ROOT` or `QWEN_ASR_HOME` to the QwenASR root directory.

If another local tool provides CUDA runtime DLLs but they are not on the system `PATH`, set `LECTURE_VIDEO_TO_PDF_EXTERNAL_CUDA_DIRS` to the extra DLL directories. Use the operating system path separator for multiple directories.

Groq Speech-to-Text currently supports `json`, `verbose_json`, and `text`. If SRT output is needed, this tool requests `verbose_json` and generates `transcript.srt` locally.

The default upload strategy is `Auto`:

- Cloud endpoints: when the media exceeds the configured size, the tool extracts 16 kHz mono FLAC audio locally and splits it by duration and size.
- Local endpoints (`localhost`, `127.0.0.1`, or `::1`): upload directly by default and let the local service handle the media.
- `Always chunk`: useful for endpoints with a known attachment limit.
- `Direct upload`: useful for local QwenASR or services known to accept large files.

The default limit is 10 minutes and 20 MB per chunk. A short overlap around each boundary reduces clipped words. Chunks are submitted sequentially, with retries for temporary network failures, `429`, and common `5xx` responses. Segment timestamps are then converted back to the original media timeline and merged into complete TXT/SRT files. Temporary audio chunks are removed after success. `transcription_manifest.json` retains chunk status and retry counts but never contains the API key.

### Credentials and Privacy

- Non-secret preferences live in `%LOCALAPPDATA%\LectureVideo2PDF\preferences.json`, outside the repository, portable folder and outputs. Custom names and URLs may still disclose internal services; do not publish this file.
- Opt-in saved keys use Windows Credential Manager, scoped to the current Windows user and bound to the profile and endpoint. No plaintext key file, browser localStorage, metadata, chunk manifest or `config.yaml` is written. Re-enter keys when moving to another computer.
- Temporary keys remain in the page, request and job memory; completed jobs release their key. Credential store failures never fall back to plaintext. Non-Windows platforms currently support temporary keys only.
- Changing a custom endpoint cannot reuse its saved key; saving the new URL removes the old credential binding. Cloud endpoints must use HTTPS; HTTP is limited to loopback.
- The Web UI accepts loopback hosts only. API access requires an HttpOnly/SameSite session cookie; mutations also require a token and cross-site origins are rejected. Programmatic clients must GET `/` and retain cookies, GET `/api/meta` for `csrf_token`, then supply `X-Studio-Token` on POST requests.
- Windows credentials prevent accidental plaintext packaging or Git publication, but are not protection against malware running as the same Windows user. Do not share the application user-data directory.

OpenAI text-only models use non-overlapping chunks and concatenate TXT in media order, without generating SRT or `slide_map.md`. Timestamp-capable models retain the existing subtitle merge behavior.

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
    transcription_manifest.json  # cloud chunked ASR only
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
