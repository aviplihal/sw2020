@echo off
rem Start the SolidWorks bridge on this repository's code (double-click, or run in your own
rem Command Prompt; never from the Claude app). Open SolidWorks first. Stop with Ctrl+C.
rem
rem It runs ..\sw_bridge\bridge_dev.py with this repo's .venv and src folder, so the bridge
rem always runs the code on this checkout (call_dev.py bridge_reload picks up edits).
rem Knowledge-base jobs save DN<n>.SLDPRT / .SLDDRW to SWMCP_OUTPUT_DIR for your review.

setlocal
set "REPO=%~dp0.."
for %%I in ("%REPO%") do set "REPO=%%~fI"
set "PROJECTS=%REPO%\.."
for %%I in ("%PROJECTS%") do set "PROJECTS=%%~fI"

set "TMP=%PROJECTS%\_tmp"
set "TEMP=%PROJECTS%\_tmp"
if not exist "%TMP%" mkdir "%TMP%"
if not defined SWMCP_OUTPUT_DIR set "SWMCP_OUTPUT_DIR=%PROJECTS%\SW-Output"
if not exist "%SWMCP_OUTPUT_DIR%" mkdir "%SWMCP_OUTPUT_DIR%"

if not exist "%REPO%\.venv\Scripts\python.exe" (
    echo No .venv in %REPO%. Create it: py -3.12 -m venv .venv ^& .venv\Scripts\python.exe -m pip install -e .[dev]
    pause
    exit /b 1
)
if not exist "%PROJECTS%\sw_bridge\bridge_dev.py" (
    echo No bridge at %PROJECTS%\sw_bridge\bridge_dev.py
    pause
    exit /b 1
)

echo Bridge on %REPO%\src
echo Output folder: %SWMCP_OUTPUT_DIR%
cd /d "%REPO%"
"%REPO%\.venv\Scripts\python.exe" -u "%PROJECTS%\sw_bridge\bridge_dev.py" "%REPO%\src"
pause
