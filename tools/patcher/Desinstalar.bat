@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0programa\python\python.exe" (
  echo Primero descomprime el .zip completo en una carpeta y ejecuta este archivo desde alli.
  echo.
  pause
  exit /b 1
)
"%~dp0programa\python\python.exe" -I -B -X utf8 "%~dp0programa\instalador.py" desinstalar %*
echo.
pause
