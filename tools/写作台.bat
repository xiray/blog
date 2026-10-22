@echo off
setlocal
cd /d "%~dp0"

set "PY=C:\Users\ray\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=C:\Users\ray\.workbuddy\binaries\python\envs\default311\python.exe"
if not exist "%PY%" set "PY=C:\Users\ray\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

title xiray writer
echo Starting...
"%PY%" "%~dp0publish.py"
if errorlevel 1 pause
