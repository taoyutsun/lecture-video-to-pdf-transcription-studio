@echo off
setlocal
cd /d "%~dp0.."
powershell -NoProfile -Command "$s = New-Object Microsoft.PowerShell.Commands.WebRequestSession; try { $null = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8790/' -WebSession $s -TimeoutSec 3; $m = Invoke-RestMethod -Uri 'http://127.0.0.1:8790/api/meta' -WebSession $s -TimeoutSec 10; if ($m.app.version -eq '0.3.0') { exit 0 } } catch {}; exit 1"
if not errorlevel 1 (
  start "" "http://127.0.0.1:8790/"
  exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
  echo Source/dev environment is required. See README.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m lecture_video_to_pdf run --port 8790
if errorlevel 1 pause
