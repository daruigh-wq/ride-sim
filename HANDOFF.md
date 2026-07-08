# Releasing Ride Sim (build + publish)

Cross-platform build/release guide. Also serves as the **machine-to-machine
handoff**: Claude Code sessions and their memory are local to one machine and
don't sync, so this committed doc is how a fresh session (e.g. on the Windows
PC) picks up the current state. `git pull`, open Claude in this repo, point it
here.

## Update — 2026-07-07 (READ THIS FIRST)

Since the 2026-06-19 state below:
- **`ride-sim-world` is now PUBLIC** (was private).
- **New source commit `6f4cba6`** on `main` (pushed): macOS HUD screen-capture fix
  (`WA_MacAlwaysShowToolWindow` at `ride_sim.py:~2096` — the `Qt.Tool` overlay is an
  NSPanel that auto-hid whenever the app lost focus, so the HUD pills were missing from
  OBS / screen recordings), plus a startup **Browse-hidden-worlds** fix and a **TCX route
  preview + world auto-link** feature.
- **mac `.dmg` REBUILT + install-tested + uploaded** to `v0.1.0-beta` — Jul-5 renderer with the
  peloton/tree/avatar work, PLUS the **F11 / Shift+Enter fullscreen toggle + ⚙ Detail-panel
  "Fullscreen" checkbox** (`ride-sim-world` commit `4ad8905`). Rebuilt + re-uploaded 17:21 PDT.
- **Windows `.exe` rebuilt 16:15 PDT — but already one step behind.** It carries the Browse fix
  + route preview + peloton *code*, BUT was built (a) 50 min before the avatar GLBs were staged
  on the NAS (17:05) and (b) an hour before the F11 toggle was pushed (17:15). The GLBs are
  gitignored → that export bundled NONE, so the pack still shows the procedural placeholder
  ("old avatars"), and it has no F11 toggle. **NEEDS one more rebuild — see the TODO below.**
- **Marketing site is LIVE**: https://davedesign.com (Ubuntu 24.04 + nginx + Let's Encrypt;
  deploy via `ssh davedesign` + `scp` to `/var/www/html`).

### ⏳ Open TODO — one more Windows rebuild (mac is current)
The mac dmg is up to date (F11 + real avatars). The Windows exe is NOT — it predates both the
avatar GLBs and the F11 toggle. On the PC:
1. `git pull` **both** repos (brings `ride-sim-world` `4ad8905` = the F11 toggle + current Main.gd).
2. **Copy the avatar GLBs** (gitignored → NOT in git): extract `male_opt.glb` + `female_opt.glb`
   from `\\NAS2\nas share1\ride-sim\ride-sim-avatars-2026-07-07.zip` into
   `ride-sim-world\godot\assets\`, then `Godot --headless --path godot --import`.
3. Re-export the Windows renderer + `package_windows.bat`, then clobber-upload (keep the tag —
   YouTube descriptions hard-code `/releases/tag/v0.1.0-beta`; the release is published, so
   never bump the tag):
```
gh release upload v0.1.0-beta "dist\Ride Sim-0.1.0-beta-windows-setup.exe" --clobber
```

> **Godot isn't on `PATH` on the Windows PC.** The build commands below say `Godot ...`; the
> actual editor is `C:\Users\Dave\godot-dl\editor\Godot_v4.6-stable_win64.exe` (use the
> `..._console.exe` sibling for headless stdout). Either add it to `PATH` or substitute the
> full path.

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

`v0.1.0-beta` is **already published and live** (tag on `main`). To refresh an installer,
clobber-upload the new asset — keep the same tag, since YouTube video descriptions link to
`/releases/tag/v0.1.0-beta`:
```
gh release upload v0.1.0-beta "dist/Ride Sim-0.1.0-beta-windows-setup.exe" --clobber
```
Release notes live in `installer/release_notes_v0.1.0-beta.md`.

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
