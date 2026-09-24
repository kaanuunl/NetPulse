@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where py >/dev/null 2>&1 && set "PY=py -3"
if not defined PY (
  where python >/dev/null 2>&1 && set "PY=python"
)
if not defined PY (
  echo Python not found. Install Python 3.9+ from https://www.python.org/downloads/
  echo or download the ready-made NetPulse.exe from the Releases page.
  pause
  exit /b 1
)

%PY% -c "import psutil" >/dev/null 2>&1
if errorlevel 1 (
  echo Installing the required package: psutil
  %PY% -m pip install --user -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

%PY% run.py --console %*
if errorlevel 1 pause
