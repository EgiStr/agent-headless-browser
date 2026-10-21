@echo off
setlocal
echo.
echo   agent-headless-browser installer
echo   ===============================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set CODE=%ERRORLEVEL%
echo.
if %CODE% NEQ 0 (
  echo   The installer failed. Read the messages above.
) else (
  echo   Done. Restart your MCP client so it picks up the new server.
)
echo.
pause
exit /b %CODE%
