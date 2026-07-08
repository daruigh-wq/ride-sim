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
- **Windows `.exe` REBUILT + uploaded 18:03 PDT — now fully current.** Rebuilt with the avatar
  GLBs (`.pck` grew 0.17 MB → 31.4 MB, confirming they're bundled) + the F11 toggle
  (`ride-sim-world` `4ad8905`). Installer is ~204 MB; clobber-uploaded to `v0.1.0-beta`. Both
  the mac dmg and the Windows exe on the release are now up to date.
- **`package_windows.bat` version-extraction bug FIXED** (`ride-sim` `0226e0f`). It had failed
  three Windows-only ways (cmd `for /f` paren-matching; cp1252 vs UTF-8 read; mangled escaped
  quotes). The one-shot `\\NAS2\nas share1\ride-sim\build_windows_release.bat` now runs
  end-to-end; see the Gotchas note at the bottom.
- **Marketing site is LIVE**: https://davedesign.com (Ubuntu 24.04 + nginx + Let's Encrypt;
  deploy via `ssh davedesign` + `scp` to `/var/www/html`).

### ✅ No open build TODO — both installers current (F11 + real avatars)
Both the mac dmg and the Windows exe on `v0.1.0-beta` are up to date as of 2026-07-07. To
refresh the Windows installer in future, the easiest path is the one-shot script (now that
`package_windows.bat` is fixed): run `\\NAS2\nas share1\ride-sim\build_windows_release.bat`
(verify its 4 CONFIG paths first). It does all 6 steps — pull both repos, extract the avatar
GLBs (gitignored → from `ride-sim-avatars-2026-07-07.zip`), Godot import, export the renderer,
`package_windows.bat`, then clobber-upload (keeps the tag — YouTube descriptions hard-code
`/releases/tag/v0.1.0-beta`; the release is published, so never bump the tag):
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
- **`package_windows.bat` APP_VERSION extraction is Windows-fragile** (fixed in
  `0226e0f` — don't regress it). Three cmd/Windows traps, all of which made the
  build die at the package step with `.read() was unexpected at this time`:
  (1) don't put the `python -c` inside a `for /f (...)` block — cmd paren-matches
  the block and the `()` in the Python break it (redirect to a temp file +
  `set /p` instead); (2) read `ride_sim.py` as `encoding='utf-8'` — Windows
  `open()` defaults to cp1252 and chokes on its non-ASCII bytes; (3) use **no
  double-quotes inside** the `-c "..."` arg (parse via `chr(34)` + single quotes)
  — cmd's arg parser mangles escaped quotes so the regex matched nothing.
