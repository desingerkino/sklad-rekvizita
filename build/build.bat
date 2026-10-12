@echo off
rem Sborka "Sklad Rekvizita". Vse soobshcheniya vyvodit build.ps1.
pushd "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "build\build.ps1"
set CODE=%ERRORLEVEL%
popd
echo.
pause
exit /b %CODE%
