$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
& $Python -m pip install --upgrade pip
& $Python -m pip install -r requirements.txt
& $Python -m pip install -e .
& $Python -m pip install pyinstaller

if (Test-Path "dist\LectureVideo2PDF") {
    Remove-Item -LiteralPath "dist\LectureVideo2PDF" -Recurse -Force
}

& $Python -m PyInstaller `
    --noconfirm `
    --onedir `
    --name LectureVideo2PDF `
    --add-data "src/lecture_video_to_pdf/web;lecture_video_to_pdf/web" `
    --collect-data lecture_video_to_pdf `
    --collect-all opencc `
    --hidden-import uvicorn `
    --hidden-import fastapi `
    "src/lecture_video_to_pdf/__main__.py"

Copy-Item -LiteralPath "README.md" -Destination "dist\LectureVideo2PDF\README.md" -Force
Copy-Item -LiteralPath "README.en.md" -Destination "dist\LectureVideo2PDF\README.en.md" -Force
Copy-Item -LiteralPath "LICENSE" -Destination "dist\LectureVideo2PDF\LICENSE" -Force

Write-Host "Portable folder: $Root\dist\LectureVideo2PDF"
