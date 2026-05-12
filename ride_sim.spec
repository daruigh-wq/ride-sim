# PyInstaller spec for Ride Simulator.
#
# Usage:
#   1. Edit ride_sim.py to set APP_VERSION and (optionally) BETA_EXPIRES.
#   2. Activate the venv that has PySide6 + bleak installed.
#   3. Run: pyinstaller ride_sim.spec --clean --noconfirm
#   4. Output:
#      - macOS:   dist/Ride Sim.app
#      - Windows: dist/Ride Sim/Ride Sim.exe   (folder bundle)
#      - Linux:   dist/Ride Sim/Ride Sim
#
# Why --onedir (not --onefile): Qt apps unpack slowly with --onefile (10+ s
# cold start) and QtWebEngine's helper process plays badly with the tempdir
# extraction. --onedir is the safer default for Qt 6.

from PyInstaller.utils.hooks import collect_all
import sys

APP_NAME = "Ride Sim"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# PySide6 needs everything — QtWebEngine in particular has a separate helper
# process (QtWebEngineProcess) and a chunk of resources/data files that
# PyInstaller's default analysis misses.
pyside_datas, pyside_binaries, pyside_hidden = collect_all("PySide6")

a = Analysis(
    ["ride_sim.py"],
    pathex=[],
    binaries=pyside_binaries,
    datas=pyside_datas + [
        ("THIRD_PARTY_LICENSES.txt", "."),
        ("USAGE.md", "."),
    ],
    hiddenimports=pyside_hidden + [
        "bleak",
        "bleak.backends.corebluetooth",  # macOS
        "bleak.backends.winrt",          # Windows
        "bleak.backends.bluezdbus",      # Linux
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim the bundle by excluding modules we don't use. Cuts ~30-80 MB.
        "PySide6.QtQuick", "PySide6.QtQml", "PySide6.Qt3DCore",
        "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtSql", "PySide6.QtTest",
        "tkinter", "unittest", "pydoc_data",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=IS_MAC,
    target_arch=None,    # universal2 builds are slow; default to host arch
    codesign_identity=None,
    entitlements_file=None,
    # icon="resources/icon.icns" if IS_MAC else "resources/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        # icon="resources/icon.icns",
        bundle_identifier="com.daruigh.ridesim",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            # Bluetooth permission prompt text shown on first launch.
            "NSBluetoothAlwaysUsageDescription":
                "Ride Sim connects to your smart trainer via Bluetooth to "
                "receive speed, power, cadence, and heart rate data.",
            "NSBluetoothPeripheralUsageDescription":
                "Ride Sim connects to your smart trainer via Bluetooth.",
            "LSMinimumSystemVersion": "12.0",
        },
    )
