@echo off
rem lit-agent one-click launcher
rem Double-click to run. Add the argument "check" to print the environment
rem report without starting the web UI.
rem Keep this file ASCII-only: cmd misreads non-ASCII lines.
title lit-agent one-click launcher

echo.
echo ================================================================
echo   lit-agent  -  one-click launcher
echo ================================================================
echo.

rem ---------- step 0: locate the code folder ----------
rem The code may sit next to this bat, or one level down in a sub-folder
rem such as scr\ or src\. Find whichever folder contains cli.py.
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
    echo.
    echo   Put this bat in the folder that contains cli.py, or in the
    echo   parent folder of the code folder, then run it again.
    goto :fail
)
cd /d "%CODEDIR%"
echo [0/6] Code folder found:
echo       %CD%

rem requirements.txt may live next to cli.py or one level above it.
set "REQ="
if exist "%CD%\requirements.txt" set "REQ=%CD%\requirements.txt"
if not defined REQ if exist "%~dp0requirements.txt" set "REQ=%~dp0requirements.txt"
if not defined REQ if exist "%CD%\..\requirements.txt" set "REQ=%CD%\..\requirements.txt"

rem ---------- step 1: locate python ----------
set "PY="
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY (
    echo [ERROR] Python not found.
    echo.
    echo   Install Python 3.12+ from https://www.python.org/downloads/
    echo   Remember to tick "Add Python to PATH", then run this file again.
    goto :fail
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.12 or newer is required. Current version:
    %PY% -V
    goto :fail
)
echo [1/6] Python check ... OK
%PY% -V

rem ---------- step 2: dependencies ----------
%PY% -c "import requests, jieba, fitz, streamlit, numpy, onnxruntime, sentencepiece" >nul 2>nul
if not errorlevel 1 goto :deps_ok
if not defined REQ goto :deps_noreq
echo [2/6] Missing packages, installing from:
echo       %REQ%
echo       The first run needs internet access and may take 1-3 minutes.
%PY% -m pip install -r "%REQ%"
if not errorlevel 1 goto :deps_ok
echo.
echo [ERROR] Dependency installation failed. Try one of these:
echo   1. Use a mirror:
echo      %PY% -m pip install -r "%REQ%" -i https://pypi.tuna.tsinghua.edu.cn/simple
echo   2. Check your network or proxy settings, then run this file again.
goto :fail

:deps_noreq
echo [ERROR] Packages are missing and requirements.txt was not found.
echo         Install them manually:
echo           %PY% -m pip install requests jieba PyMuPDF streamlit numpy onnxruntime sentencepiece
goto :fail

:deps_ok
echo [2/6] Dependency check ... OK

rem ---------- step 3: init data folders + sqlite schema ----------
echo [3/6] Initializing data folders and database ...
%PY% cli.py init
if errorlevel 1 (
    echo [ERROR] Initialization failed.
    goto :fail
)

rem ---------- step 4: build lexical, fingerprint, and vector indexes (incremental) ----------
echo.
echo [4/6] Building indexes: BM25 + n-gram fingerprints + neural vectors
echo       Incremental: already indexed documents are skipped.
%PY% cli.py index
if errorlevel 1 (
    echo [ERROR] Index building failed.
    goto :fail
)

rem ---------- check-only mode ----------
if /i "%~1"=="check" goto :check_only

rem ---------- step 5: start Browser Bridge and web UI ----------
echo.
echo [5/6] Starting the OpenCLI Browser Bridge ...
rem The helper keeps this launcher ASCII-only. Browser failures are reported
rem but do not block local search or the other online API providers.
call "%~dp0start_opencli_browser.bat" quiet
if errorlevel 1 (
    echo [WARN] OpenCLI browser could not start.
    echo        Local search and online APIs will still work.
)

echo.
echo [6/6] Starting the web UI ...
echo       The browser opens automatically in about 8 seconds:
echo           http://127.0.0.1:8501
echo       If it does not open, visit that address manually.
echo       Keep this window open while using the assistant.
echo.
start "" /min cmd /c "timeout /t 8 >nul & start http://127.0.0.1:8501"
%PY% cli.py ui

goto :done

:check_only
    echo.
    echo [5/5] Check-only mode: printing the environment report.
    echo.
    %PY% cli.py doctor --offline
    goto :done

:fail
echo.
echo ----------------------------------------------------------------
echo Launch aborted. Please send the messages above to the developer.
echo ----------------------------------------------------------------
echo.
pause >nul
exit /b 1

:done
echo.
echo lit-agent stopped. You can close this window now.
pause >nul
exit /b 0
