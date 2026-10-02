param(
    [switch]$IncludeCudaRuntime,
    [switch]$SkipDependencyInstall,
    [string]$DistPath = "dist"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not $SkipDependencyInstall) {
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip update failed." }
    & $Python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "Core dependency installation failed." }
    if ($IncludeCudaRuntime) {
        & $Python -m pip install -r requirements-asr-cuda.txt
    } else {
        & $Python -m pip install -r requirements-asr.txt
    }
    if ($LASTEXITCODE -ne 0) { throw "ASR dependency installation failed." }
    & $Python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw "Project installation failed." }
    & $Python -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller installation failed." }
}

$OutputRoot = [System.IO.Path]::GetFullPath((Join-Path $Root $DistPath))
$RootPrefix = [System.IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
if (-not $OutputRoot.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "DistPath must stay inside the project directory."
}
$PortableRoot = Join-Path $OutputRoot "LectureVideo2PDF"
if (Test-Path -LiteralPath $PortableRoot) {
    throw "Output already exists. Preserve it and choose a new -DistPath before rebuilding."
}
$Flavor = if ($IncludeCudaRuntime) { "cuda" } else { "standard" }
$WorkRoot = Join-Path $Root "build\portable-$Flavor"

$PyInstallerArgs = @(
    "--noconfirm",
    "--onedir",
    "--name", "LectureVideo2PDF",
    "--distpath", $OutputRoot,
    "--workpath", $WorkRoot,
    "--specpath", $WorkRoot,
    "--add-data", "$(Join-Path $Root 'src\lecture_video_to_pdf\web');lecture_video_to_pdf/web",
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
} else {
    $PyInstallerArgs += @("--exclude-module", "nvidia")
}

$PyInstallerArgs += (Join-Path $Root "scripts\pyinstaller_entry.py")

& $Python -m PyInstaller @PyInstallerArgs
if ($LASTEXITCODE -ne 0) { throw "Portable build failed." }

# Collected package data can include compiled caches with build-machine paths.
Get-ChildItem -LiteralPath $PortableRoot -Directory -Recurse -Filter "__pycache__" | ForEach-Object {
    $CachePath = [System.IO.Path]::GetFullPath($_.FullName)
    if (-not $CachePath.StartsWith($PortableRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected cache path outside portable output."
    }
    $Quarantine = Join-Path $WorkRoot ("excluded-data\" + [guid]::NewGuid().ToString("N"))
    if (-not ([System.IO.Path]::GetFullPath($Quarantine)).StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unexpected quarantine path outside project."
    }
    New-Item -ItemType Directory -Path $Quarantine -Force | Out-Null
    Move-Item -LiteralPath $CachePath -Destination $Quarantine
}

Copy-Item -LiteralPath "README.md" -Destination (Join-Path $PortableRoot "README.md") -Force
Copy-Item -LiteralPath "README.en.md" -Destination (Join-Path $PortableRoot "README.en.md") -Force
Copy-Item -LiteralPath "LICENSE" -Destination (Join-Path $PortableRoot "LICENSE") -Force

Write-Host "Portable folder: $PortableRoot"
