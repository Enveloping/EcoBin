@echo off
pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0generate-v02-registration-code.ps1" %*
exit /b %ERRORLEVEL%
