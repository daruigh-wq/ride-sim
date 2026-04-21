# CLAUDE.md — Project Context for Claude Code

## Project overview

Ride Simulator is a single-file Python desktop app (~2,300 lines in `ride_sim.py`) that syncs a recorded cycling video to live telemetry from a BLE FTMS smart trainer. The video playback rate adjusts in real time so the video matches the rider's virtual position on the route.

## Tech stack

- **Python 3.10+** with PySide6 (Qt 6) for GUI
- **PySide6 QtMultimedia** — QMediaPlayer + QVideoWidget for hardware-decoded video
- **PySide6 QtWebEngine** — QWebEngineView for Leaflet.js map rendering
- **bleak** — async BLE (Bluetooth Low Energy) library for trainer and HR monitor
- **asyncio + threading** — BLE worker runs in a background thread with its own event loop; Qt GUI runs on the main thread; communication via `threading.Lock` (SharedState) and Qt Signals (WorkerSignals)

## Key architecture decisions

1. **Single file** — entire app is in one `ride_sim.py`. No package structure yet.
2. **Overlay as Tool window** — QVideoWidget uses a native surface that renders above Qt child widgets, so the HUD overlay is a separate frameless `Qt.Tool` window positioned over the video. This is the only reliable cross-platform approach.
3. **Offscreen map for overlay** — a second QWebEngineView (`OverlayMapWidget`) is positioned offscreen and `.grab()`'d periodically to create a pixmap for the overlay. This avoids flashing/scrollbar issues from grabbing the visible map.
4. **Rate change suppression** — `setPlaybackRate()` only fires when delta > 1% to avoid Qt Multimedia's audio buffer flush stutter.

## Current status

- Works well on **Windows** — all features functional including HUD pills, map overlay, ghost rider, activity recording
- **macOS** has issues: audio stutter at non-1× playback rates, occasional overlay flash during window transitions
- The file is currently named `ride_sim_mac.py` but is intended to be cross-platform; rename to `ride_sim.py`

## Build / run

```bash
pip install PySide6 PySide6-Addons bleak
python ride_sim.py
```

## Open questions / future work

- Split into multiple files / proper package structure
- Fix macOS audio stutter (may need alternative to Qt Multimedia's FFmpeg backend)
- Add tests
- PyInstaller packaging for distribution
- Consider adding ANT+ FE-C support alongside BLE FTMS
