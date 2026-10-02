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
- 雲端 ASR 可自動抽取音軌並分段上傳，支援超過單次附件容量限制的長影音

## 安裝與啟動

### 從 GitHub source 執行

```powershell
git clone https://github.com/taoyutsun/lecture-video-to-pdf-transcription-studio.git
cd lecture-video-to-pdf-transcription-studio
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

Release 提供兩種 Windows portable 壓縮檔：

- `LectureVideo2PDF-v0.2.0-windows-x64.zip`：標準版，適合大多數使用者；支援 PDF 擷取、OpenAI-compatible endpoint、以及 `faster-whisper` CPU 轉錄。
- `LectureVideo2PDF-v0.2.0-windows-x64-cuda.zip`：CUDA GPU 版，內建 CUDA 12 runtime，適合想用 NVIDIA GPU 加速本機 `faster-whisper` 的使用者；檔案明顯較大。

下載後解壓縮，進入解壓縮出的 `LectureVideo2PDF` 資料夾，直接雙擊 `LectureVideo2PDF.exe` 即可啟動 Web UI。

若要從 PowerShell 啟動，或需要指定 port，可執行：

```powershell
.\LectureVideo2PDF.exe run
```

若 `8787` 已被其他程式使用，可改用：

```powershell
.\LectureVideo2PDF.exe run --port 8788
```

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
    transcription_manifest.json  # 雲端分段轉錄時產生
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
lecture-video-studio transcribe "D:\Videos\lecture.mp4" --asr openai-compatible --endpoint-base-url https://api.groq.com/openai/v1 --asr-model whisper-large-v3-turbo --endpoint-upload-strategy auto
```

## faster-whisper

PDF 擷取不需要 ASR。Windows portable 標準版會內建 `faster-whisper` CPU 執行依賴；CUDA GPU 版另內建 NVIDIA CUDA 12 runtime。模型檔仍會在第一次轉錄時依 `faster-whisper` 機制下載，不會包在 release 內。

若從 source/dev 模式執行，可在 Web UI 選擇 **faster-whisper** 後勾選「允許程式在開始轉換前自動安裝 ASR 依賴」，或按「立即安裝 ASR 依賴」。安裝會使用目前啟動本工具的 Python 環境。

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

若電腦有 NVIDIA GPU，但標準 portable 版無法載入 CUDA 12 runtime，請改用 `LectureVideo2PDF-v0.2.0-windows-x64-cuda.zip`。Source/dev 模式的 Web UI 會在 **faster-whisper** 設定區顯示「CUDA GPU 加速依賴」；使用者可勾選允許安裝，讓程式在開始轉換前背景安裝 GPU runtime。這些套件體積較大，因此不會在未確認時自動安裝。Portable release 不會在執行時安裝 CUDA runtime Python 套件；若要自行重新打包 GPU 版 portable，可在 source/dev 模式安裝依賴後執行 `.\scripts\build_portable.ps1 -IncludeCudaRuntime`。

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
- 上傳策略、每段容量上限與每段最長時間

### 服務設定檔與模型

Source/dev 版新增服務設定檔（v0.3.0 分支測試中，尚未更新 portable release）：

1. 在「服務 / 設定檔」選擇 Groq、OpenAI、本機 QwenASR，或「新增自訂端點」。內建服務會自動填入網址。
2. 輸入金鑰，按「測試連線與更新模型」。只會列出已知的轉錄模型，不會混入聊天或文字轉語音模型。自訂端點可手動輸入模型 ID。
3. 若要記住金鑰，勾選「將金鑰安全保存至 Windows 認證管理員」，再按「保存設定」。未勾選時只保存非敏感設定，金鑰僅供本次使用。
4. 再次開啟工具會還原上次服務、模型、語言、格式與分段設定。已保存的金鑰不會填回瀏覽器，只顯示保存狀態；留空即可使用。可按「移除已保存金鑰」刪除。

Groq 預設模型為 `whisper-large-v3-turbo`，也提供 `whisper-large-v3`。OpenAI 提供 `whisper-1`、`gpt-4o-transcribe`、`gpt-4o-mini-transcribe`；後兩者只輸出 TXT，不提供可靠的字幕時間戳，因此不開放 SRT 選項。`text`／`json` 選項使用服務的 JSON 回應，在本機輸出 TXT，不另存原始 JSON。

預設清單不代表帳號一定有權使用；連線後會更新模型清單並顯示更新時間，實際轉錄仍受服務權限、配額與費率限制。不要在金鑰欄輸入其他服務的金鑰。模型與輸出能力可參閱 [Groq Speech-to-Text](https://console.groq.com/docs/speech-to-text) 與 [OpenAI 語音轉錄文件](https://developers.openai.com/api/docs/guides/speech-to-text)。

本機連線會先嘗試 `/health`，雲端服務則使用常見的 `/models` 路徑。若端點像 QwenASR 一樣由服務端固定載入模型，Web UI 會把模型選單固定為 `default（由端點配置）`。其他本機連接埠可建立自訂設定檔。

若使用 [QwenASRMiniTool](https://github.com/dseditor/QwenASRMiniTool) portable 版，可先啟動其 OpenAI 相容轉錄端點，再在本工具填入端點網址。若希望 Web UI 顯示本機 QwenASR 安裝狀態，可設定環境變數 `LECTURE_VIDEO_TO_PDF_QWEN_ASR_ROOT` 或 `QWEN_ASR_HOME` 指向 QwenASR 根目錄。

若電腦上已有其他工具提供 CUDA runtime DLL，且不在系統 `PATH` 中，可用 `LECTURE_VIDEO_TO_PDF_EXTERNAL_CUDA_DIRS` 指定額外 DLL 目錄；多個目錄請使用作業系統的路徑分隔符號。

Groq Speech-to-Text 目前支援的回應格式為 `json`、`verbose_json`、`text`。若需要 `.srt`，本工具會使用 `verbose_json` 取得時間段後，在本機產生 `transcript.srt`。

上傳策略預設為「自動」：

- 雲端端點：媒體檔超過設定容量時，先在本機抽取成 16 kHz 單聲道 FLAC，再依時間與容量安全分段。
- 本機端點（`localhost`、`127.0.0.1`、`::1`）：預設直接上傳，由本機服務決定如何處理。
- `一律分段`：適合已知有附件限制的端點。
- `直接上傳`：適合本機 QwenASR 或確定可接收大檔案的服務。

分段預設最長 10 分鐘、20 MB，切點前後會保留短暫重疊以減少斷字。程式會依序送出片段，遇到暫時性網路中斷、`429` 或常見 `5xx` 狀態時自動重試，再將各段時間戳換算回原始影音並合併成完整 TXT/SRT。成功後暫存音訊片段會刪除；`transcription_manifest.json` 會保留各段狀態與重試次數，但不包含 API key。

### 金鑰與隱私

- 非敏感偏好存於 `%LOCALAPPDATA%\LectureVideo2PDF\preferences.json`，不在專案、免安裝資料夾或輸出檔中。自訂端點名稱／網址也會保存，仍可能包含內部服務資訊，請勿公開此檔。
- 勾選保存的 API key 由 Windows 認證管理員保存，綁定目前 Windows 使用者、服務設定檔與端點；不保存明文檔，也不寫入瀏覽器 localStorage、metadata、分段紀錄或 `config.yaml`。換電腦需重新輸入金鑰。
- 未勾選保存的金鑰只用於目前頁面、請求與背景工作記憶體。工作結束後清除背景工作持有的金鑰。憑證庫失敗時不會改存明文；非 Windows 平台目前僅支援暫時使用。
- 變更自訂端點網址後，原保存金鑰不會送到新端點；保存新網址會移除舊網址綁定的金鑰，需重新輸入。雲端網址必須使用 HTTPS，HTTP 僅允許本機 loopback。
- Web UI 僅允許 loopback host，API 使用 HttpOnly / SameSite cookie 與修改請求的工作階段 token，並拒絕跨站來源。若自行呼叫 API，需先 GET `/` 保留 cookie，再 GET `/api/meta` 取得 `csrf_token`；POST 帶上 `X-Studio-Token`。
- Windows 憑證保護可避免把明文金鑰誤打包或推送 GitHub，但不能防止同一 Windows 使用者權限下的惡意程式讀取憑證。請勿將整個使用者資料目錄一起分享。

OpenAI 不提供時間戳的模型在分段時不使用重疊片段，只按原始順序彙整 TXT，不產生 SRT 或 `slide_map.md`。其餘支援時間戳的模型保留既有字幕合併流程。

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
    transcription_manifest.json  # 雲端分段 ASR 時產生
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
- 原始碼：https://github.com/taoyutsun/lecture-video-to-pdf-transcription-studio
