$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt
& $Python -m pip install -e .

Write-Host "Starting Lecture Video to PDF & Transcription Studio at http://127.0.0.1:8787/"
& $Python -m lecture_video_to_pdf run --host 127.0.0.1 --port 8787
