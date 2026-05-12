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
for /f "usebackq tokens=*" %%v in (`python -c "import re; print(re.search(r'^APP_VERSION\s*=\s*\"([^\"]+)\"', open('ride_sim.py').read(), re.M).group(1))"`) do (
  set "VERSION=%%v"
)
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
