@echo off
chcp 65001 >nul
title check@collect
"%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -X utf8 -u "%~dp0start_desktop.py"
echo.
pause
