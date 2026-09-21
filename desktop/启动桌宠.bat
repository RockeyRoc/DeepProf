@echo off
rem ============================================================
rem  KEEP THIS FILE PURE ASCII.  (no chcp -- see below)
rem
rem  cmd.exe reads .bat files by BYTE OFFSET. Changing the code page
rem  (chcp) partway through makes that offset go out of sync, so pieces
rem  of rem/echo lines get executed as commands -- which is exactly the
rem  "black window flashes and disappears" bug we hit before.
rem  ASCII-only means the code page can never matter.
rem
rem  Also keep line endings as CRLF. LF-only .bat files confuse cmd's
rem  line splitting and swallow the final `pause`.
rem ============================================================
rem
rem  This file is now only a DOUBLE-CLICK SHORTCUT.
rem  The real entry point is the CLI at the repository root:
rem
rem      deepprof            chat / switch model / switch voice
rem      deepprof /pet       open the pet
rem
rem  The pet is a GUI app and starts its own Python backend, so there is
rem  no dev server and no console needed -- this wrapper just calls the CLI.
rem ============================================================

cd /d "%~dp0.."
call "deepprof.cmd" /pet
