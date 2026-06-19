# Releasing Ride Sim (build + publish)

Cross-platform build/release guide. Also serves as the **machine-to-machine
handoff**: Claude Code sessions and their memory are local to one machine and
don't sync, so this committed doc is how a fresh session (e.g. on the Windows
PC) picks up the current state. `git pull`, open Claude in this repo, point it
here.

## Current state (2026-06-19)

- **Repos** (must be cloned **side by side** — the spec reaches `../ride-sim-world`):
  - `github.com/daruigh-wq/ride-sim` (public) — the Python/Qt app + packaging.
  - `github.com/daruigh-wq/ride-sim-world` (private) — Godot world renderer + bake tools.
- **macOS**: `.dmg` built, ad-hoc signed, and install-tested on Apple Silicon.
  Bundles the Godot renderer. **Apple-Silicon-only** (`target_arch=None` in the
  spec → host-arch build).
- **GitHub Release**: `v0.1.0-beta` is **PUBLISHED** (prerelease, tag on `main`)
  carrying both the mac dmg and the Windows installer:
  https://github.com/daruigh-wq/ride-sim/releases/tag/v0.1.0-beta
- **Windows**: **built and shipped** — `Ride Sim-0.1.0-beta-windows-setup.exe`
  (PyInstaller + Inno Setup), install-tested on Windows 11 x64. Bundles the
  Godot Windows renderer and the bake pipeline.

## Prerequisites

- Python 3.10+ with `PySide6`, `PySide6-Addons`, `bleak`, plus `pyinstaller`.
  The bake pipeline also needs `Pillow`, `fitparse`, and `numpy` — the spec's
  `PIL.Image`/`fitparse` hidden imports are silently dropped (and baking fails
  in the frozen app) if they aren't installed.
- **Godot 4.6** with the matching **export templates** installed (macOS templates
  on the Mac; **Windows Desktop** templates on the PC).
- macOS only: `brew install create-dmg`.
- Windows only: **Inno Setup 6** (https://jrsoftware.org/isdl.php).

## Build — Windows (on the PC)

1. Export the Godot Windows renderer (the output dir must pre-exist — Godot
   won't create it):
   ```
   mkdir ..\build\win
   Godot --headless --path godot --export-release "Windows Desktop" ..\build\win\RideSimWorld.exe
   ```
   (run from `ride-sim-world\godot`; preset already in `export_presets.cfg`.)
   Produces `RideSimWorld.exe` + `RideSimWorld.pck`.
2. Build the installer (from the `ride-sim` repo):
   ```
   scripts\package_windows.bat
   ```
   Runs PyInstaller (`ride_sim.spec`, which bundles `build/win/` as `world/`)
   then Inno Setup → `dist\Ride Sim-0.1.0-beta-windows-setup.exe`.

## Build — macOS (already done; for reference / rebuilds)

1. Export + ad-hoc sign the renderer (arm64 SIGKILLs unsigned):
   ```
   Godot --headless --path godot --export-release "macOS" ../build/RideSimWorld.app
   (cd ../build && codesign --force --deep -s - RideSimWorld.app)
   ```
2. `scripts/package_mac.sh` → `dist/Ride Sim-0.1.0-beta-mac.dmg`.

## Publish the release (after the Windows build)

```
gh release upload v0.1.0-beta "dist/Ride Sim-0.1.0-beta-windows-setup.exe"
gh release edit  v0.1.0-beta --draft=false
```
Publishing creates the `v0.1.0-beta` tag at `main`. Release notes live in
`installer/release_notes_v0.1.0-beta.md`.

## Gotchas / hard-won notes

- **App name must be `Ride Sim` (with a space).** The spec's EXE/COLLECT/BUNDLE
  names must match `package_mac.sh` (`Ride Sim.app`) and `ride_sim.iss`
  (`dist\Ride Sim\`, `Ride Sim.exe`). A mismatch silently breaks the installers.
  (The data dir `Application Support/RideSim` is intentionally space-free and
  unrelated — don't change it.)
- **Both builds are unsigned** → Gatekeeper (Ctrl-click → Open) / SmartScreen
  (More info → Run anyway). Signing is a later cost (Apple Dev ID; Windows EV cert).
- **PyInstaller doesn't cross-compile** — run the spec on each target OS. The
  Godot renderer *can* be cross-exported (the Windows preset exports fine from
  the Mac), but the PyInstaller wrap can't.
- `dist/` and `build/` are gitignored — installers are local artifacts, attached
  to the Release, never committed.
- **Frozen bake tools must emit UTF-8.** The `--run-pyfile` shim re-execs the
  tools with piped stdout/stderr, which on Windows default to cp1252 and raise
  `UnicodeEncodeError` on any non-ASCII output (e.g. the `→`/`✓` progress lines).
  `main()` forces UTF-8 on those streams; don't remove that.
