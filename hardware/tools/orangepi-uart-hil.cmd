@echo off
setlocal
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0orangepi_uart_hil.ps1" %*
exit /b %ERRORLEVEL%

