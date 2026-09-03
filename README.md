# Ride Simulator

**Ride your real routes indoors.** Sync a recorded ride video — *or* a 3D world
generated from the route — to live BLE smart-trainer data, in real time. Pedal
faster and the world speeds up; ease off and it slows; stop and it pauses.
Offline desktop app for **macOS & Windows**.

> **Public beta.** Lightly tested, unsigned, and rough in places — feedback
> wanted. See [USAGE.md](https://github.com/daruigh-wq/ride-sim/blob/main/USAGE.md)
> for the full walkthrough, known issues, and how to report bugs.

## Two ways to ride

| Mode | You supply | You get |
|------|------------|---------|
| **Video** | A recorded ride video + a matching route file (TCX) | Your real footage, played back in sync with your trainer speed |
| **3D World** | Just a route file (`.gpx` / `.tcx` / `.fit`) — no video | A low-poly 3D world baked from real elevation + map data, ridden on-rails |

Both are driven by the same thing: your position along the route, computed live
from trainer telemetry. Pick per route by what you have and the mood you're in.

## Download

- **macOS (Apple Silicon):** [Ride Sim-0.1.0-beta-mac.dmg](https://github.com/daruigh-wq/ride-sim/releases/download/v0.1.0-beta/Ride.Sim-0.1.0-beta-mac.dmg) — an installer, no Python needed
- **Windows 10/11 (x64):** [run from source](#run-from-source) for now — the
  installer is being rebuilt and will reappear here when it lands
- All releases: [github.com/daruigh-wq/ride-sim/releases](https://github.com/daruigh-wq/ride-sim/releases) · Project site: [davedesign.com](https://davedesign.com)

**First launch** on macOS shows a security warning because the beta isn't
code-signed yet: macOS blocks it (*"Apple could not verify… is free of
malware"*). Click **Done**, then **System Settings → Privacy & Security →
Open Anyway**. One time only.

Full step-by-step with exact dialogs is in [USAGE.md → Installing](https://github.com/daruigh-wq/ride-sim/blob/main/USAGE.md#installing).

## Ride a 3D virtual world

Don't have video of a route? Bake a world from it instead:

- From the startup dialog choose **Bake virtual world**, pick a route file
  (`.gpx` / `.tcx` / `.fit`), and a detail level.
- The world is built **fully offline** from public elevation data (DEM) plus
  OpenStreetMap roads, land cover, trees, buildings, and power lines — your
  actual road draped over real terrain.
- Ride it like any other route: the world scrolls to your trainer speed, with a
  **virtual peloton** you can pace against (watts-based, with a live leaderboard
  and minimap), animated riders, and a ghost of a prior effort.
- **Dual-monitor** setups shine — the world goes fullscreen on the big display,
  the cockpit dashboard stays on the laptop. A single screen works too.
- Tune frame rate on the fly with the **World detail** picker (Low / Medium /
  High) or the in-ride `1`/`2`/`3` keys; press **P** for an FPS / frame-time /
  draw-call overlay.

The world renderer is a companion open-source project,
[ride-sim-world](https://github.com/daruigh-wq/ride-sim-world) (Godot 4), bundled
inside the macOS installer — no separate download. From source, check it out
beside this repo (see [Run from source](#run-from-source)).

> Worlds are experimental: how much scenery you see depends on OpenStreetMap
> coverage, so rural routes can look sparse.

## Features

**Both ride modes**

- **BLE FTMS trainer** — connects to any FTMS smart trainer (Wahoo Kickr, Tacx
  Neo, Saris H3, …); reads speed, cadence, power; optionally sends simulated
  grade back to the trainer
- **BLE heart-rate monitor** — auto-discovers a standard BLE HR strap
- **SIM mode** — no trainer needed; generates realistic speed/power/cadence from
  the route profile so you can evaluate the app
- **HUD overlay** — configurable translucent pills (speed, cadence, power, HR,
  grade, distance, elapsed, sync error) with S/M/L sizing and drag-to-reorder
- **Ghost rider** — load a prior TCX to race yourself or a friend; a gap bar
  shows distance ahead/behind in real time
- **Activity recording** — writes a TCX (GPS, power, cadence, HR) for upload to
  Strava / Garmin Connect

**Video mode**

- **Video sync engine** — cruise or proportional control keeps video time aligned
  to your virtual position; handles drift, hard seeks, and cooldowns
- **Leaflet map** — route trace + position dot, as a bottom panel or a video
  overlay (full route or tracking/rotating thumbnail)
- **AR overlay** — pacer cube and road-tangent line (calibrated for GoPro Max 2
  360 reframes)

**3D World mode**

- **Offline world baking** — DEM terrain + OpenStreetMap scenery from a route file
- **Virtual peloton** — watts-paced pack with leaderboard and minimap
- **Animated riders + ghost**, on-rails camera driven by your distance
- **Detail tiers + live perf overlay** for tuning to your GPU

## Run from source

Prefer to run the Python app directly:

```bash
pip install PySide6 PySide6-Addons bleak
python ride_sim.py
```

Requirements: **Python 3.10+**, PySide6 (with PySide6-Addons for QtWebEngine),
and bleak. (Baking worlds from source also needs `Pillow`, `fitparse`, `numpy`,
and a checkout of [ride-sim-world](https://github.com/daruigh-wq/ride-sim-world)
beside this one; the macOS installer bundles all of that for you.)

This is the supported route on **Windows** until the installer is rebuilt — the
same command works there, and Bluetooth/FTMS behaves identically. Video rides
need nothing extra; for 3D-world rides add the bake dependencies above.

A startup dialog lets you pick the TCX/video (or route file for a world), an
optional ghost, video offset, mode (FTMS or SIM), world detail, and recording.

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| F11 / Shift+Enter | Toggle fullscreen |
| Escape | Exit fullscreen |
| M | Cycle map mode (panel → overlay full → overlay tracking) |
| 1 / 2 / 3 | World detail (Low / Medium / High), in a world ride |
| P | World performance overlay, in a world ride |

## Architecture

The app is a single-file Python program (~2,300 lines) that drives a bundled
Godot renderer for world rides over a small UDP contract. Major components:

| Component | Description |
|-----------|-------------|
| `load_tcx_route()` | Parses TCX files into time/distance/elevation/GPS arrays |
| `SharedState` | Thread-safe state object bridging worker thread ↔ GUI |
| `run_ride_loop()` | Core async loop: reads telemetry, advances virtual position, computes sync error, emits rate/seek + world signals |
| `worker_ble()` | BLE FTMS connection, notification handling, grade commands |
| `worker_hr()` / `worker_sim()` | BLE HR monitor / synthetic telemetry generator |
| `VideoPanel` / `OverlayWidget` | QMediaPlayer video surface + QPainter HUD (pills, bars, map thumbnail) |
| `MapWidget` / `OverlayMapWidget` | Leaflet map in QWebEngineView (panel + offscreen snapshot) |
| `launch_world_renderer()` | Spawns the bundled Godot world and feeds it distance/speed |
| `ActivityRecorder` | Accumulates telemetry, writes a valid TCX on completion |

## Security note

Video playback decodes through Qt Multimedia's bundled **FFmpeg** backend
(`libavcodec`/`libavformat`). FFmpeg's media parsers have a history of
memory-safety bugs triggerable by deliberately malformed files, so **only open
video you trust** — your own recordings or footage from sources you control.
Keeping PySide6 up to date (`pip install -U PySide6 PySide6-Addons`) picks up
Qt's patched FFmpeg builds. TCX/GPX route files are parsed by the app's own XML
reader, not FFmpeg.

## Known issues

- **No Windows installer right now** — it's mid-rebuild. Windows works fine
  [from source](#run-from-source) in the meantime.
- **macOS installer is Apple-Silicon-only** (M1 or later); Intel/universal is planned.
- The installer is **unsigned** → the one-time Gatekeeper step above.
- **Worlds are experimental** — scenery density follows OpenStreetMap coverage, and a
  route that doubles back on itself can show a terrain seam where it overlaps.
- **macOS audio stutter** when cruise mode steps the playback rate — use proportional mode, or mute.
- **AR overlay** (cube, tangent line) is calibrated only for **GoPro Max 2** 360 reframes.
- The **map** fetches OpenStreetMap tiles the first time you ride a route, then
  caches them — after that it works with no network.
- This beta refuses to start after **2026-12-31** — grab a newer build when prompted.

## License

Source code in this repository is licensed under the [PolyForm Noncommercial
License 1.0.0](LICENSE). In plain English: you can read, build, run, modify, and
redistribute it for **non-commercial** purposes — personal use, hobby projects,
education, research, not-for-profits. Selling it, bundling it in a commercial
product, or using it in a commercial service is **not** permitted. For
commercial-use inquiries, open an issue.

Copyright 2026 David Ruigh. Third-party components (PySide6, bleak, Leaflet.js,
Godot, etc.) retain their own licenses — see
[THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
