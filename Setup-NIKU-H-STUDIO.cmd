@echo off
setlocal
call "%~dp0h3_oss\Setup-H3-Studio.cmd" %*
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo NIKU H STUDIO setup did not complete. See the error above.
)
endlocal & exit /b %EXIT_CODE%
