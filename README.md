# Ride Simulator

Syncs a recorded ride video to live GPS/TCX data from a BLE FTMS smart trainer (or a built-in simulator). Ride your real routes on screen with real-time telemetry overlay.

## What it does

You supply a TCX file (from Garmin, Strava, etc.) and a video of the same route. The app plays the video in sync with your trainer speed — pedal faster and the video speeds up, stop and it pauses. A HUD overlay shows speed, power, cadence, HR, grade, distance, and elapsed time as translucent pills over the video.

### Key features

- **BLE FTMS trainer support** — connects to any FTMS-compatible smart trainer via Bluetooth LE; reads speed, cadence, power; optionally sends simulated grade back to the trainer
- **BLE heart rate monitor** — auto-discovers and connects to a standard BLE HR strap
- **SIM mode** — no trainer needed; generates realistic speed/power/cadence from the TCX route profile
- **Video sync engine** — cruise or proportional control strategy keeps video time aligned to your virtual position on the route; handles drift, hard seeks, and cooldowns
- **HUD overlay** — configurable translucent pills (speed, cadence, power, HR, grade, distance, elapsed, sync error) with S/M/L sizing and drag-to-reorder
- **Leaflet map** — bottom-panel map with route trace and position dot; overlay modes (full route or tracking/rotating) rendered as a thumbnail on the video
- **Ghost rider** — load a second TCX to race against yourself or a friend; gap bar shows distance ahead/behind in real time
- **Activity recording** — records a TCX file with GPS, power, cadence, HR for upload to Strava / Garmin Connect
- **Cross-platform** — runs on Windows and macOS (Qt Multimedia / PySide6)
- **PyInstaller-ready** — frozen-app path detection and Chromium sandbox fix included

## Requirements

- Python 3.10+
- PySide6 (with PySide6-Addons for QtWebEngine)
- bleak (BLE library)

```bash
pip install PySide6 PySide6-Addons bleak
```

## Usage

```bash
python ride_sim.py
```

A startup dialog lets you select:
- **TCX file** — the route to ride
- **Video file** — MP4/MKV/AVI/MOV of the route
- **Ghost TCX** (optional) — a second TCX to race against
- **Video offset** — seconds to shift video vs. route timing
- **Mode** — BLE FTMS (real trainer) or SIM (no hardware)
- **Record activity** — toggle TCX recording on/off

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| F11 | Toggle fullscreen |
| Escape | Exit fullscreen |
| M | Cycle map mode (panel → overlay full → overlay tracking) |

### Settings dialog (⚙ button)

- **HUD Pills** — toggle visibility, set size (S/M/L), drag to reorder
- **Map Overlay** — corner placement, size %, opacity
- **Sync / PID** — strategy (cruise/proportional), Kp, deadband, rate limits

## Security note

Video playback decodes through Qt Multimedia's bundled **FFmpeg** backend (`libavcodec`/`libavformat`). FFmpeg's media parsers have a history of memory-safety bugs that can be triggered by deliberately malformed files, so **only open video you trust** — your own ride recordings or footage from sources you control. Don't load video handed to you from unknown or untrusted sources. Keeping PySide6 up to date (`pip install -U PySide6 PySide6-Addons`) picks up Qt's patched FFmpeg builds. TCX route files are parsed by the app's own XML reader, not FFmpeg.

## Architecture

Single-file Python application (~2,300 lines). Major components:

| Component | Description |
|-----------|-------------|
| `load_tcx_route()` | Parses TCX files into time/distance/elevation/GPS arrays |
| `SharedState` | Thread-safe state object bridging worker thread ↔ GUI |
| `WorkerSignals` | Qt signal bridge for cross-thread media player control |
| `run_ride_loop()` | Core async loop: reads telemetry, advances virtual position, computes sync error, emits rate/seek signals |
| `worker_ble()` | BLE FTMS connection, notification handling, grade commands |
| `worker_hr()` | BLE HR monitor connection |
| `worker_sim()` | Synthetic telemetry generator using route speed profile |
| `VideoPanel` | QMediaPlayer + QVideoWidget with transparent overlay window |
| `OverlayWidget` | QPainter-based HUD: pills, progress bar, ghost bar, map thumbnail |
| `MapWidget` | Leaflet map in QWebEngineView (bottom panel) |
| `OverlayMapWidget` | Offscreen Leaflet map for overlay snapshot grabs |
| `ActivityRecorder` | Accumulates telemetry samples, writes valid TCX on ride completion |
| `MainWindow` | Orchestrates layout, timers, scrubber, fullscreen, settings |

## Known issues

- **macOS audio stutter** — Qt Multimedia's FFmpeg backend flushes audio buffers on `setPlaybackRate()` changes; mitigated by only applying rate changes >1% delta, but some stutter remains at non-1× rates
- **macOS overlay** — the HUD overlay uses a frameless Tool window positioned over the video (QVideoWidget's native surface renders above child widgets); this works but can occasionally flash during window transitions
- **Leaflet CDN** — the map requires an internet connection to load tiles from CARTO CDN
- **Avatar overlay (pacer cube / tangent line) — GoPro Max 2 only** — the projection used for 3D overlay rendering is calibrated for GoPro Player exports of GoPro Max 2 360 footage (effective rectilinear h_fov ≈ 118.8° in the central region). Other cameras and other reframe pipelines use different lens projections; the cube will appear correctly scaled and road-locked only for footage produced by this specific workflow. Off-axis 3D content (e.g., side-passing riders) and slow-speed/handlebar-wobble scenes are limitations of the current data pipeline.

## License

Source code in this repository is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE) — see the full text in `LICENSE`.

In plain English: you can read, build, run, modify, and redistribute this software for **non-commercial** purposes — personal use, hobby projects, education, research, and not-for-profit organizations. Selling the software, including it in a commercial product, or using it as part of a commercial service is **not** permitted under this license. For commercial-use inquiries, open an issue on this repository.

Copyright 2026 David Ruigh. Third-party components (PySide6, bleak, Leaflet.js, etc.) retain their own licenses — see [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt).
