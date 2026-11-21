@echo off
setlocal
title Install OpenCLI CNKI adapter override

set "SRC=%~dp0vendor\opencli-adapters\cnki\search.js"
set "DSTDIR=%USERPROFILE%\.opencli\clis\cnki"
set "DST=%DSTDIR%\search.js"
set "CLI=%~dp0vendor\opencli-cli\node_modules\@jackwener\opencli\dist\src\main.js"

if not exist "%SRC%" (
    echo [ERROR] Local CNKI adapter was not found:
    echo         %SRC%
    exit /b 1
)
if not exist "%CLI%" (
    echo [ERROR] Bundled OpenCLI CLI was not found:
    echo         %CLI%
    exit /b 1
)

if not exist "%DSTDIR%" mkdir "%DSTDIR%"
copy /Y "%SRC%" "%DST%" >nul
if errorlevel 1 (
    echo [ERROR] Could not install the CNKI adapter override.
    exit /b 1
)

node "%CLI%" daemon restart >nul
echo Installed CNKI KNS8S adapter:
echo   %DST%
exit /b 0
