@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Avocat AI Moldova

echo.
echo   Avocat AI Moldova
echo   Interfata se deschide singura in browser.
echo   Lasati aceasta fereastra deschisa. Inchideti-o pentru a opri programul.
echo.

set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 (
    set "UV=%~dp0.runtime\uv\uv.exe"
    if not exist "%~dp0.runtime\uv\uv.exe" (
        echo   Descarc componenta de instalare, doar la prima pornire...
        if not exist ".runtime\uv" mkdir ".runtime\uv"
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile '.runtime\uv.zip' -UseBasicParsing; Expand-Archive '.runtime\uv.zip' '.runtime\uv' -Force; Remove-Item '.runtime\uv.zip'"
        if errorlevel 1 goto :fail
    )
)

set "UV_PYTHON_INSTALL_DIR=%~dp0.runtime\python"
"%UV%" run --quiet --no-dev --python 3.12 python -m app.main
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo   A aparut o eroare. Verificati conexiunea la internet si incercati din nou.
pause
exit /b 1
