@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Avocat AI Moldova

echo.
echo   Avocat AI Moldova
echo   Interfata se deschide singura in browser.
echo   Lasati aceasta fereastra deschisa. Inchideti-o pentru a opri programul.
echo   Accesul din retea se activeaza din pagina Setari.
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
rem The app exits with code 3 to be started again, e.g. after LAN access is turned on in Setari.
set "AVOCAT_RESTART=1"
:start
"%UV%" run --quiet --no-dev --python 3.12 python -m app.main
if errorlevel 4 goto :fail
if errorlevel 3 (
    echo   Repornesc...
    set "AVOCAT_RESTARTED=1"
    goto :start
)
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo   A aparut o eroare. Verificati conexiunea la internet si incercati din nou.
pause
exit /b 1
