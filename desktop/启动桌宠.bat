@echo off
rem ============================================================
rem  KEEP THIS FILE PURE ASCII.
rem  cmd.exe reads .bat files by BYTE OFFSET. Changing the code page
rem  (chcp) partway through makes that offset go out of sync, so pieces
rem  of rem/echo lines get executed as commands -- which is exactly the
rem  "black window flashes and disappears" bug we hit before.
rem  ASCII-only means the code page can never matter.
rem  Chinese text is printed by the app itself instead.
rem
rem  Also keep line endings as CRLF. LF-only .bat files confuse cmd's
rem  line splitting and swallow the final `pause`.
rem ============================================================

chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   DeepProf desktop pet - starting
echo ============================================
echo.
echo   Once running:
echo     - the character appears at the bottom-right corner
echo     - click / drag / right-click it
echo       (the right-click menu switches between walk modes)
echo     - tray icon (bottom-right) right-click to quit
echo     - force quit : Ctrl+Alt+Shift+X
echo     - go home    : Ctrl+Alt+Shift+D
echo.
echo   Closing this window stops the pet.
echo ============================================
echo.

call npm run dev

echo.
echo Pet exited. Press any key to close.
pause >nul
