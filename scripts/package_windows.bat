@echo off
:: Build a Windows installer for Ride Sim.
::
:: Prerequisites:
::   pip install pyinstaller
::   Install Inno Setup 6: https://jrsoftware.org/isdl.php
::
:: Usage:
::   scripts\package_windows.bat
::
:: Output:
::   dist\Ride Sim-<version>-windows-setup.exe
::
:: Note: this build is NOT code-signed. Windows SmartScreen will warn
:: users until you sign with an EV code-signing certificate (~$200-400/yr).

setlocal enabledelayedexpansion
cd /d "%~dp0.."

where pyinstaller >nul 2>&1 || (
  echo ERROR: pyinstaller not found. Run: pip install pyinstaller
  exit /b 1
)

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "!ISCC!" (
  echo ERROR: Inno Setup 6 not found at !ISCC!
  echo Install from https://jrsoftware.org/isdl.php
  exit /b 1
)

:: Extract APP_VERSION from ride_sim.py via a tiny Python one-liner.
:: Three cmd/Windows gotchas are avoided here, all of which silently broke the
:: old `for /f`-based version on the Windows PC:
::   1. Runs on its own line (NOT inside a `for /f (...)` block) — cmd does
::      paren-matching inside for/f and the parens in .split()/.strip() break it
::      (".read() was unexpected at this time"). Redirect + `set /p` instead.
::   2. Reads ride_sim.py as UTF-8 — Windows `open()` defaults to cp1252 and
::      chokes on the file's non-ASCII bytes.
::   3. No double-quotes INSIDE the -c string (uses chr(34), single quotes) —
::      escaped quotes get mangled by cmd's arg parser, so the regex matched
::      nothing. This form parses the APP_VERSION line without any embedded ".
set "VERFILE=%TEMP%\ridesim_version.txt"
python -c "print(next(l.split('=',1)[1].strip().strip(chr(34)) for l in open('ride_sim.py',encoding='utf-8') if l.lstrip().startswith('APP_VERSION') and '=' in l))" > "!VERFILE!"
if errorlevel 1 (
  echo ERROR: could not extract APP_VERSION from ride_sim.py
  del "!VERFILE!" >nul 2>&1
  exit /b 1
)
set /p VERSION=<"!VERFILE!"
del "!VERFILE!" >nul 2>&1
if "!VERSION!"=="" (
  echo ERROR: could not extract APP_VERSION from ride_sim.py
  exit /b 1
)

echo ==^> Building Ride Sim !VERSION! for Windows

if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

pyinstaller ride_sim.spec --clean --noconfirm
if errorlevel 1 exit /b 1

"!ISCC!" /DMyAppVersion=!VERSION! installer\ride_sim.iss
if errorlevel 1 exit /b 1

echo.
echo ==^> Done: dist\Ride Sim-!VERSION!-windows-setup.exe
endlocal
