# Lecture Video to PDF & Transcription Studio

中文名稱：**課程影片轉講義 PDF 與逐字稿工具**  
English: [README.en.md](README.en.md)

這是一個本地執行的 Web 工具，主要用於從課程影片、線上教學、讀書會錄影、簡報錄影中擷取乾淨的講義 PDF，並提供可獨立使用的語音轉文字功能。PDF 擷取不需要 ASR；語音轉文字可使用本機 `faster-whisper` 或 OpenAI-compatible ASR endpoint。

## 功能特色

- 從影片偵測投影片變化並輸出 `result.pdf`
- 保留投影片圖片、縮圖與 `metadata.json`
- Web UI 可審核縮圖、刪除誤擷取頁面、調整順序並重建 PDF
- 支援手動裁切 `x,y,w,h`，適合有講者鏡頭、Zoom 畫面或版面不固定的影片
- 語音轉文字可獨立使用，支援常見影片與音檔
- ASR 支援：
  - 內建選配 `faster-whisper`
  - OpenAI-compatible ASR endpoint
  - 可串接 QwenASR、faster-whisper server、speaches、whisper.cpp server、Groq Whisper API 等相容端點

## 安裝與啟動

### 從 GitHub source 執行

```powershell
git clone https://github.com/taoyutsun/lecture-video-to-pdf-studio.git
cd lecture-video-to-pdf-studio
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m lecture_video_to_pdf run
```

預設開啟後進入 `http://127.0.0.1:8787/`。若該 port 已被其他程式使用，可改用其他 port，例如：

```powershell
.\.venv\Scripts\python.exe -m lecture_video_to_pdf run --port 8788
```

### 從 Release 取得打包版

下載 release 壓縮檔後解壓縮，執行 `scripts/start_windows.bat` 或包裝後的啟動檔。  
大型影片建議直接貼上本機檔案路徑；檔案選取或拖曳會先把檔案複製到本機 `uploads/` 資料夾再處理。

## Web UI 工作模式

### 影片轉講義 PDF

適合將課程影片擷取成講義 PDF。

- `只擷取 PDF`：只輸出投影片 PDF、圖片與 metadata
- `PDF + 語音轉文字`：同時輸出 PDF、`transcript.txt`、`transcript.srt`、`slide_map.md`

### 語音轉文字

適合只需要字幕或逐字稿的情境。可匯入常見影片或音檔，例如：

- 影片：`.mp4`、`.mkv`、`.avi`、`.mov`、`.wmv`、`.webm`、`.m4v`
- 音訊：`.mp3`、`.wav`、`.m4a`、`.aac`、`.flac`、`.ogg`、`.opus`、`.wma`

ASR-only 模式輸出：

```text
output/
  media_name_YYYYMMDD_HHMMSS/
    transcript.txt
    transcript.srt
    metadata.json
```

## CLI

啟動 Web UI：

```powershell
lecture-video-to-pdf run
lecture-video-studio run
```

上面兩個是等效的 console script，擇一使用即可。從 source 安裝時，這些命令會由 `pip install -e .` 安裝到目前 Python 環境的 `Scripts` 目錄，不會以同名檔案出現在專案根目錄；若使用 release portable 版，請執行 `LectureVideo2PDF.exe run`。

影片轉講義 PDF：

```powershell
lecture-video-to-pdf convert "D:\Videos\lecture.mp4" --out output --mode balanced --crop auto --asr none
```

PDF + 語音轉文字：

```powershell
lecture-video-to-pdf convert "D:\Videos\lecture.mp4" --out output --asr openai-compatible --endpoint-base-url http://127.0.0.1:11435 --response-format srt
```

只做語音轉文字：

```powershell
lecture-video-to-pdf transcribe "D:\Audio\lecture.mp3" --out output --asr faster-whisper --asr-model base --response-format srt
lecture-video-studio transcribe "D:\Videos\lecture.mp4" --asr openai-compatible --endpoint-base-url https://api.groq.com/openai/v1 --asr-model whisper-large-v3-turbo
```

## faster-whisper

PDF 擷取不需要 ASR。若要使用內建 `faster-whisper`，可在 Web UI 選擇 **faster-whisper** 後勾選「允許程式在開始轉換前自動安裝 ASR 依賴」，或按「立即安裝 ASR 依賴」。安裝會使用目前啟動本工具的 Python 環境，模型檔仍會在第一次轉錄時依 `faster-whisper` 機制下載。

手動安裝：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-asr.txt
```

常用模型：

- `tiny`、`base`、`small`、`medium`
- `large-v2`、`large-v3`
- `turbo`
- `distil-large-v3`

裝置使用 `auto` 時會優先使用可用的 GPU。若選擇 `cuda` 或 `auto` 嘗試 GPU，但 CUDA runtime 無法載入，工具會先嘗試加入可用的 CUDA 12 DLL 目錄；只有 GPU 載入實際失敗時，才會自動退回 CPU/int8 轉錄並在結果區顯示提醒。

若電腦有 NVIDIA GPU，但沒有其他工具提供 CUDA 12 runtime DLL，Web UI 會在 **faster-whisper** 設定區顯示「CUDA GPU 加速依賴」。使用者可勾選允許安裝，讓程式在開始轉換前背景安裝 GPU runtime。這些套件體積較大，因此不會在未確認時自動安裝。

手動安裝 CUDA runtime 依賴：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-asr-cuda.txt
```

產生的 `transcript.srt` 會針對影片播放器閱讀性自動切成短字幕，預設每個字幕 cue 最多 2 行；完整逐字內容仍保留在 `transcript.txt`。

語言選單提供「自動偵測（中文優先繁體）」、「繁體中文」、「簡體中文」等選項。選擇自動偵測時，若 ASR 偵測到中文或輸出內容判斷為中文，工具會優先轉為繁體中文；若需要簡體中文，可明確選擇「簡體中文」。

## OpenAI-compatible endpoint

若已有本機或雲端 ASR 服務，請在 Web UI 選擇 **OpenAI-compatible endpoint**，並填入：

- `base_url`
- `api_key / token`
- `model`
- 語言
- `response_format`

測試連線會依序嘗試 `/health` 與 OpenAI 常見的 `/models` 端點。若端點像 QwenASR 一樣由服務端固定載入模型，Web UI 會把模型選單固定為 `default（由端點配置）`，避免誤導使用者以為可由本工具切換本地模型。

若使用 [QwenASRMiniTool](https://github.com/dseditor/QwenASRMiniTool) portable 版，可先啟動其 OpenAI 相容轉錄端點，再在本工具填入端點網址。若希望 Web UI 顯示本機 QwenASR 安裝狀態，可設定環境變數 `LECTURE_VIDEO_TO_PDF_QWEN_ASR_ROOT` 或 `QWEN_ASR_HOME` 指向 QwenASR 根目錄。

若電腦上已有其他工具提供 CUDA runtime DLL，且不在系統 `PATH` 中，可用 `LECTURE_VIDEO_TO_PDF_EXTERNAL_CUDA_DIRS` 指定額外 DLL 目錄；多個目錄請使用作業系統的路徑分隔符號。

Groq Speech-to-Text 目前支援的回應格式為 `json`、`verbose_json`、`text`。若需要 `.srt`，本工具會使用 `verbose_json` 取得時間段後，在本機產生 `transcript.srt`。

API key 不會寫入輸出檔、metadata 或專案設定；Web UI 只會在目前頁面、HTTP request 與背景 job 記憶體中短暫使用。

## 輸出結構

PDF 模式：

```text
output/
  video_name_YYYYMMDD_HHMMSS/
    result.pdf
    metadata.json
    slides/
    thumbs/
    transcript.txt      # PDF + ASR 時產生
    transcript.srt      # PDF + ASR 時產生
    slide_map.md        # PDF + ASR 時產生
```

語音轉文字模式：

```text
output/
  media_name_YYYYMMDD_HHMMSS/
    transcript.txt
    transcript.srt
    metadata.json
```

## 技術參考與授權

本專案使用 MIT License。使用外部模型、FFmpeg、ASR endpoint 或雲端 API 時，請遵守各自的授權與服務條款。

技術參考：

- LearnOpenCV background subtraction workflow: https://learnopencv.com/video-to-slides-converter-using-background-subtraction/
- PySceneDetect: https://www.scenedetect.com/docs/latest/cli.html
- faster-whisper: https://github.com/SYSTRAN/faster-whisper
- Qwen3-ASR: https://github.com/QwenLM/Qwen3-ASR
- WhisperX: https://github.com/m-bain/whisperX

## 作者

- Arthur Tao
- 部落格：https://taoyutsun.blogspot.com/
- Facebook：https://facebook.com/arthurtaoyutsun
- 原始碼：https://github.com/taoyutsun/lecture-video-to-pdf-studio
