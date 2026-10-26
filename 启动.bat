@echo off
rem ===========================================================================
rem FJHY POC one-click launcher. Dispatcher ONLY - real logic is in
rem scripts\start.ps1 (all Chinese output lives there; it is UTF-8 with BOM
rem and PowerShell reads it correctly).
rem
rem !! TWO HARD RULES FOR THIS FILE !!
rem 1) ASCII ONLY. No Chinese, no multi-byte text, no UTF-8 BOM.
rem    cmd.exe mis-parses batch files containing multi-byte text while the
rem    console is on codepage 65001: it resumes reading at byte offsets
rem    computed from character counts, so the read position drifts and
rem    comment fragments get executed as commands.
rem    2026-10-25 incident: every rem line exploded into
rem    "'xxx' is not recognized as an internal or external command".
rem    This is NOT deterministic - the same file can work for days and then
rem    break after any edit that shifts byte offsets. ASCII-only kills the
rem    whole bug class. If you need Chinese here, put it in start.ps1.
rem 2) CRLF line endings. LF-only also mis-aligns cmd.exe reads.
rem Both rules apply to the sibling stop script too.
rem
rem Usage:
rem   (double-click)          default ports: api 8000, web 5173
rem   (this file) 8010 5174   first number = api port, second = web port
rem   (this file) -NoBrowser  dash-prefixed args are passed to start.ps1 as-is
rem   (this file) -Reset      rebuild database (data loss; asks inside)
rem   (this file) /?          usage only, starts nothing
rem ===========================================================================
title FJHY POC launcher
setlocal
cd /d "%~dp0"

if not exist "%~dp0scripts\start.ps1" (
  echo.
  echo [ERROR] scripts\start.ps1 not found.
  echo This launcher must stay in the project root, next to the scripts folder.
  echo Current location: "%~dp0"
  echo.
  pause
  exit /b 1
)

set "ARGS="
set "A1=%~1"
if not defined A1 goto :run
if "%A1%"=="/?" goto :help
if "%A1%"=="-?" goto :help
if not "%A1:~0,1%"=="-" goto :ports
set "ARGS= %*"
goto :run

:ports
set "ARGS= -ApiPort %~1"
if not "%~2"=="" set "ARGS=%ARGS% -WebPort %~2"
if not "%~3"=="" set "ARGS=%ARGS% %~3"
if not "%~4"=="" set "ARGS=%ARGS% %~4"

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1"%ARGS%
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo Startup FAILED. The reason is in the output above. Logs:
echo   backend  var\log\backend.log
echo   frontend var\log\frontend.log
echo.
echo Common causes:
echo   - Docker Desktop not running, DB container cannot come up
echo   - backend contract check failed at startup (Fail Fast by design;
echo     the log says which rule)
echo   - powershell or python not found on PATH
echo.
pause
exit /b 1

:help
echo.
echo FJHY POC one-click launcher
echo.
echo   (this file)            default api 8000, web 5173
echo   (this file) 8010 5174  api port, web port
echo   (this file) -NoBrowser start services but do not open a browser
echo   (this file) -Reset     rebuild database (asks for confirmation)
echo.
echo Stop services: double-click the sibling stop script in this folder
echo (Chinese-named .bat; it only stops this project's processes).
echo.
exit /b 0
