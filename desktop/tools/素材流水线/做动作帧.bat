@echo off
rem  KEEP THIS FILE PURE ASCII and CRLF.
rem  cmd.exe reads .bat by byte offset; a non-ASCII byte or a mid-file chcp
rem  can desync that offset, so pieces of lines run as commands and the final
rem  `pause` gets swallowed -> the window flashes and disappears.
rem  Chinese-named scripts are invoked through run_frames.py (ASCII wrapper).

chcp 65001 >nul
cd /d "%~dp0"

set ACTION=%~1

echo ============================================
echo   DeepProf animation frames
echo   normalize  +  preview
echo ============================================
echo.

if "%ACTION%"=="" (
  echo   No action given - processing every folder under the raw dir.
) else (
  echo   Action: %ACTION%
)
echo.

python "%~dp0run_frames.py" %ACTION%
if errorlevel 1 goto fail

echo.
echo   Done. The preview folder is opened by run_frames.py.
echo   Check the contact sheet and the GIF before wiring anything in.
goto end

:fail
echo.
echo   [FAILED] See the error above. Usual causes:
echo     1. python is not on PATH
echo     2. the action folder has no images yet
echo     3. background is not pure white, so the cutout failed
echo.

:end
echo.
pause >nul
