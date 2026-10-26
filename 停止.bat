@echo off
rem FJHY POC - stop this project's backend/frontend services.
rem Real logic: scripts\start.ps1 -StopOnly
rem
rem HARD RULES (same as the launcher .bat in this folder - read its header):
rem   1) ASCII ONLY - no Chinese / multi-byte text in this file.
rem   2) CRLF line endings.
rem cmd.exe mis-parses multi-byte batch files; see the launcher's header
rem for the 2026-10-25 incident notes.
rem
rem Only THIS project's processes are stopped (matched by command line).
rem The DB container is left running - use "docker compose stop" for that.
title FJHY POC stop
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -StopOnly
echo.
pause
