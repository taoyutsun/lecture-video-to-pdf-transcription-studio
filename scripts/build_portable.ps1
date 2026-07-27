param(
    [switch]$IncludeCudaRuntime
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt
if ($IncludeCudaRuntime) {
    & $Python -m pip install -r requirements-asr-cuda.txt
} else {
    & $Python -m pip install -r requirements-asr.txt
}
& $Python -m pip install -e .
& $Python -m pip install pyinstaller

if (Test-Path "dist\LectureVideo2PDF") {
    Remove-Item -LiteralPath "dist\LectureVideo2PDF" -Recurse -Force
}

$PyInstallerArgs = @(
    "--noconfirm",
    "--onedir",
    "--name", "LectureVideo2PDF",
    "--add-data", "src/lecture_video_to_pdf/web;lecture_video_to_pdf/web",
    "--collect-data", "lecture_video_to_pdf",
    "--collect-all", "opencc",
    "--collect-all", "faster_whisper",
    "--collect-all", "ctranslate2",
    "--collect-all", "tokenizers",
    "--collect-all", "huggingface_hub",
    "--collect-all", "av",
    "--collect-all", "imageio_ffmpeg",
    "--hidden-import", "uvicorn",
    "--hidden-import", "fastapi",
    "--hidden-import", "faster_whisper",
    "--hidden-import", "ctranslate2"
)

if ($IncludeCudaRuntime) {
    $PyInstallerArgs += @("--collect-all", "nvidia")
}

$PyInstallerArgs += "scripts/pyinstaller_entry.py"

& $Python -m PyInstaller @PyInstallerArgs

Copy-Item -LiteralPath "README.md" -Destination "dist\LectureVideo2PDF\README.md" -Force
Copy-Item -LiteralPath "README.en.md" -Destination "dist\LectureVideo2PDF\README.en.md" -Force
Copy-Item -LiteralPath "LICENSE" -Destination "dist\LectureVideo2PDF\LICENSE" -Force

Write-Host "Portable folder: $Root\dist\LectureVideo2PDF"
