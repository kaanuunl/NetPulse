@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=py -3"
where py >/dev/null 2>&1 || set "PY=python"

echo Building NetPulse.exe...
%PY% -m pip install --upgrade -r requirements.txt pyinstaller || goto :error
%PY% -m PyInstaller --noconfirm --clean packaging\agnabzi.spec || goto :error
echo.
echo Done: dist\NetPulse.exe
pause
exit /b 0

:error
echo Build failed.
pause
exit /b 1
