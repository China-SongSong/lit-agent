@echo off
setlocal
title OpenCLI Browser Bridge

set "CODEDIR=%~dp0scr"
if not exist "%CODEDIR%\cli.py" set "CODEDIR=%~dp0"
set "EXTDIR=%~dp0vendor\opencli-extension"
set "PROFILE=%LOCALAPPDATA%\lit-agent\OpenCLIEdgeProfileLogin"

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if not exist "%EXTDIR%\manifest.json" (
    echo [ERROR] OpenCLI extension was not found:
    echo         %EXTDIR%
    pause
    exit /b 1
)
if not exist "%EDGE%" (
    echo [ERROR] Microsoft Edge was not found.
    pause
    exit /b 1
)

if not exist "%PROFILE%" mkdir "%PROFILE%"

echo Starting the dedicated OpenCLI browser profile...
echo Sign in to CNKI or complete a security check in this window when requested.
echo Keep this browser open while lit-agent uses online tracing.
start "" "%EDGE%" ^
  --user-data-dir="%PROFILE%" ^
  --load-extension="%EXTDIR%" ^
  --disable-extensions-except="%EXTDIR%" ^
  --no-first-run ^
  --no-default-browser-check ^
  "https://kns.cnki.net/kns8s/"

exit /b 0
