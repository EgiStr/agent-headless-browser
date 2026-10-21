@echo off
setlocal
rem Double-click this file to install agent-headless-browser on Windows.
rem It hands over to install.ps1, which does the real work.
rem
rem The final "press any key" pause is meant for the human who started the
rem file by double-clicking it. Set AHB_INSTALL_NO_PAUSE=1 to skip it: CI
rem jobs, agents and scripts cannot press a key and would hang forever.

echo.
echo   agent-headless-browser installer
echo   ===============================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set CODE=%ERRORLEVEL%
echo.

rem A dry run changes nothing, so it must not say that it installed anything.
set "AHB_DRYRUN="
for %%A in (%*) do (
  if /I "%%~A"=="--dry-run" set "AHB_DRYRUN=1"
)

if "%CODE%"=="0" (
  if defined AHB_DRYRUN (
    echo   Dry run finished. Nothing was installed or registered.
  ) else (
    echo   Done. Restart your MCP client so it picks up the new server.
  )
) else (
  echo   The installer failed. Read the messages above.
)
echo.

rem Wait for a keypress only when a real console is watching and the caller
rem did not opt out. A redirected stdin or stdout means nobody is there to
rem press a key, so waiting would hang whatever started us.
set "AHB_PAUSE=1"
if defined AHB_INSTALL_NO_PAUSE set "AHB_PAUSE="
powershell -NoProfile -Command "if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) { exit 1 } else { exit 0 }" >nul 2>&1
if errorlevel 1 set "AHB_PAUSE="

if defined AHB_PAUSE pause
exit /b %CODE%
