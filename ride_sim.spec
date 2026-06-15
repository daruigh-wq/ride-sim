# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the unified RideSim app (P6 step 5).
# Bundles ride_sim (the brain), the bake pipeline (tools/, run via the app's
# --run-pyfile re-exec shim), and the exported Godot renderer (world/).
#
# Build:  pyinstaller --noconfirm RideSim.spec
# The renderer must be exported first:
#   (in ride-sim-world/godot) Godot --headless --export-release "macOS" ../build/RideSimWorld.app
#
import os

HERE     = SPECPATH
WORLD    = os.path.abspath(os.path.join(HERE, "..", "ride-sim-world"))
TOOLS    = os.path.join(WORLD, "tools")
RENDERER = os.path.join(WORLD, "build", "RideSimWorld.app")

datas = [
    (os.path.join(HERE, "THIRD_PARTY_LICENSES.txt"), "."),
    (os.path.join(TOOLS, "bake_world.py"),      "tools"),
    (os.path.join(TOOLS, "route_to_world.py"),  "tools"),
    (os.path.join(TOOLS, "dem_to_heightmap.py"), "tools"),
    (os.path.join(TOOLS, "osm_to_features.py"), "tools"),
    (os.path.join(TOOLS, "gpx_to_tcx.py"),      "tools"),
]
# Bundle the renderer if it's been exported; otherwise the app falls back to the
# dev export / a user-picked World app.
if os.path.isdir(RENDERER):
    datas.append((RENDERER, "world/RideSimWorld.app"))

a = Analysis(
    [os.path.join(HERE, "ride_sim.py")],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    # The tools run via runpy at bake time, so PyInstaller can't see their imports.
    hiddenimports=["PIL.Image", "fitparse"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="RideSim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name="RideSim")
app = BUNDLE(coll, name="RideSim.app", icon=None, bundle_identifier="com.ridesim.app")
