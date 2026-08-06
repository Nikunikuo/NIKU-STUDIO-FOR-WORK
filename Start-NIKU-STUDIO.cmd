@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0drama-studio\scripts\start-story-studio.ps1" %*
if errorlevel 1 (
  echo.
  echo NIKU STUDIO FOR WORK could not be started. See the error above.
  pause
  exit /b 1
)
endlocal
