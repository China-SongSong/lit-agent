@echo off
setlocal
title OpenCLI Browser Bridge

set "ROOT=%~dp0"
set "EXTDIR=%ROOT%vendor\opencli-extension"
set "PROFILE=%LOCALAPPDATA%\lit-agent\OpenCLIEdgeProfileLogin"
set "RUNTIME_EXT=%LOCALAPPDATA%\lit-agent\opencli-extension-1.0.27"
set "QUIET=0"
if /i "%~1"=="quiet" set "QUIET=1"

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if not exist "%EXTDIR%\manifest.json" (
    echo [ERROR] OpenCLI Browser Bridge files were not found:
    echo         %EXTDIR%
    goto :fail
)
if not exist "%EDGE%" (
    echo [ERROR] Microsoft Edge was not found.
    goto :fail
)

if not exist "%PROFILE%" mkdir "%PROFILE%" >nul 2>nul
if not exist "%RUNTIME_EXT%\manifest.json" (
    if not exist "%LOCALAPPDATA%\lit-agent" mkdir "%LOCALAPPDATA%\lit-agent" >nul 2>nul
    xcopy /E /I /Y /Q "%EXTDIR%" "%RUNTIME_EXT%" >nul
)

if not exist "%RUNTIME_EXT%\manifest.json" (
    echo [ERROR] Could not prepare the Browser Bridge extension.
    goto :fail
)

if "%QUIET%"=="0" (
    echo Starting the dedicated OpenCLI browser profile...
    echo Keep this window open while lit-agent uses online academic sources.
)

start "" "%EDGE%" ^
  --user-data-dir="%PROFILE%" ^
  --load-extension="%RUNTIME_EXT%" ^
  --disable-extensions-except="%RUNTIME_EXT%" ^
  --disable-features=DisableLoadExtensionCommandLineSwitch ^
  --enable-unsafe-extension-debugging ^
  --no-first-run ^
  --no-default-browser-check ^
  "https://kns.cnki.net/kns8s/"

rem Edge 158+ may disable command-line developer extensions as a security
rem measure. Verify the bridge and open the one-time setup pages if needed.
timeout /t 6 /nobreak >nul
node -e "const http=require('http');const r=http.get({hostname:'127.0.0.1',port:19825,path:'/status',headers:{'X-OpenCLI':'lit-agent-health'}},x=>{let d='';x.on('data',c=>d+=c);x.on('end',()=>{try{process.exit(JSON.parse(d).extensionConnected?0:1)}catch(e){process.exit(1)}})});r.on('error',()=>process.exit(1));r.setTimeout(1500,()=>{r.destroy();process.exit(1)});" >nul 2>nul
if not errorlevel 1 (
    if "%QUIET%"=="0" echo OpenCLI Browser Bridge is connected.
    exit /b 0
)

echo [WARN] OpenCLI Browser Bridge is not connected yet.
echo        A one-time browser security confirmation is required.
echo        1. Enable Developer mode.
echo        2. Choose "Load unpacked".
echo        3. Select the opened extension folder.
start "" "%EDGE%" "edge://extensions/"
start "" explorer.exe "%RUNTIME_EXT%"
exit /b 0

:fail
if "%QUIET%"=="0" pause
exit /b 1
