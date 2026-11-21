@echo off
rem lit-agent web UI only (no dependency check, no index build)
rem The code folder may be this one, or a sub-folder such as scr\ or src\.
title lit-agent web UI  (close this window = stop the server)

set "CODEDIR="
if exist "%~dp0cli.py" set "CODEDIR=%~dp0"
if not defined CODEDIR if exist "%~dp0scr\cli.py" set "CODEDIR=%~dp0scr"
if not defined CODEDIR if exist "%~dp0src\cli.py" set "CODEDIR=%~dp0src"
if not defined CODEDIR (
    for /d %%D in ("%~dp0*") do (
        if not defined CODEDIR if exist "%%~fD\cli.py" set "CODEDIR=%%~fD"
    )
)
if not defined CODEDIR (
    echo [ERROR] cli.py was not found next to this bat, nor in any sub-folder.
    echo         Put this bat in the folder that contains cli.py, or in the
    echo         parent folder of the code folder, then run it again.
    pause >nul
    exit /b 1
)
cd /d "%CODEDIR%"

set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
    echo [ERROR] Python not found in PATH.
    pause >nul
    exit /b 1
)

echo.
echo   ================================================================
echo     lit-agent web UI
echo   ================================================================
echo.
echo     Code folder: %CD%
echo     Starting... the browser opens automatically in ~8 seconds.
echo     If it does not, open this URL manually:  http://127.0.0.1:8501
echo.
echo     Close this window to stop the server.
echo.
echo   ================================================================
echo.

start "" /min cmd /c "timeout /t 8 >nul & start http://127.0.0.1:8501"

%PY% cli.py ui

echo.
echo   Server stopped. You can close this window.
pause >nul
exit /b 0
