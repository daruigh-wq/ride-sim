# Ride Simulator — Usage Guide

A quick reference for testers. For installation issues or feature requests,
**Help → Report an Issue** (or press **F1**) inside the app.

## Beta warning — read this first

**This is unsigned, lightly tested beta software. Use at your own risk.**

It works on the author's machines, but it will inevitably have bugs you'll
hit that haven't surfaced yet. Realistic risks:

- **The app may crash** mid-ride. You'll lose the unrecorded part of
  whatever session you were running.
- **Your recorded TCX may have sync glitches** — gaps, bad pacing, or odd
  average speeds during periods where sync corrected aggressively.
  Spot-check the file before uploading to Strava / Garmin Connect.
- **The installer is unsigned.** Windows SmartScreen and macOS Gatekeeper
  will warn you the first time. The bypass steps below are normal beta
  workflow, but you should also be the kind of person who only runs
  unsigned software from sources they trust.
- **No automatic updates.** When a newer beta drops, you re-download.
- **This build expires on 2026-09-01.** After that the app refuses to
  start until you grab a newer one. (This is a courtesy reminder, not
  DRM — it's there so testers don't keep running stale builds.)

No warranty, express or implied. If something goes wrong: **F1 → Report
an Issue**. That's the whole point of running the beta.

## What it does

Ride Simulator plays back a recorded cycling video and adjusts playback rate
in real time so the video matches the position of:

- a **real rider on a smart trainer** (BLE FTMS mode), or
- a **simulated rider** with synthetic power/cadence/HR (SIM mode).

You ride alongside an optional **ghost** — yourself or a friend from a
previous TCX recording — and an AR **pacer cube** painted in the video.

## What you need

| Item | Notes |
|---|---|
| **TCX file** of the recorded route | From Strava, Garmin Connect, etc. Must include GPS + speed. |
| **Video file** of the same ride | MP4 / MOV / MKV / AVI. Camera-locked (forward-facing handlebar mount is ideal). The avatar overlay is **calibrated for GoPro Max 2 360 reframes only** — other cameras work but the cube alignment will be off. |
| **A BLE FTMS smart trainer** (BLE mode) | Wahoo Kickr, Tacx Neo, Saris H3, etc. |
| **Bluetooth-capable computer** | macOS 12+ or Windows 10/11 with built-in or USB BLE. |

## Installing

### macOS
1. Open `Ride Sim-<version>-mac.dmg`. Drag **Ride Sim.app** to the **Applications** folder shortcut shown in the DMG window.
2. **First launch — Gatekeeper warning.** Open **Applications** in Finder. Double-clicking **Ride Sim** the normal way will show *"Ride Sim cannot be opened because the developer cannot be verified."* with only a **Cancel** button (or **Move to Trash** on newer macOS). Click **Cancel**.
3. Now **right-click** (or Control-click) **Ride Sim** in Applications → **Open**. The same dialog reappears, but this time it has an **Open** button. Click **Open**. macOS will remember this for all future launches — you only need the right-click dance once.
4. **Bluetooth permission prompt.** On first run, macOS asks for permission to use Bluetooth. **Allow it** — the app cannot find your trainer otherwise. If you refuse, you can re-enable it in **System Settings → Privacy & Security → Bluetooth**.

### Windows
1. Run `Ride Sim-<version>-windows-setup.exe`.
2. **First launch — SmartScreen warning.** Windows shows *"Windows protected your PC"* with a default **Don't run** button. Click the small **More info** link near the top of the dialog. The dialog will expand and reveal a **Run anyway** button — click it.
3. Step through the installer (default install location is fine). When it finishes you'll have a **Ride Sim** entry in the Start Menu and (optionally) a desktop shortcut.
4. Make sure Bluetooth is **on** in **Settings → Bluetooth & devices**.

## Connecting to your trainer (BLE mode)

The app auto-scans for FTMS-compatible trainers — there is **no** "pair device" dialog.

1. **Wake your trainer** by pedaling once (most trainers sleep after a few minutes of idle).
2. **Disconnect from other apps** — Zwift, MyWhoosh, the manufacturer's app, etc. Only one BLE host can hold the trainer at a time.
3. Launch Ride Simulator.
4. In the startup dialog: pick your TCX, video, set **Mode: BLE FTMS**, click **Start Ride**.
5. The app scans for ~6 seconds. On success the status bar shows `● Trainer name`.
6. Start pedaling — the video begins playing once your speed crosses ~2 km/h.

If no trainer is found: cycle the trainer's power (or pedal it harder to wake it), then close and relaunch the app.

## SIM mode

Pick **Mode: SIM** instead of BLE. Synthetic speed/power/cadence/HR are generated from the TCX. Useful for testing without a trainer or testing video sync.

## Recording your ride

The **Record activity (TCX)** checkbox on the startup dialog produces a TCX file you can upload to Strava, Garmin Connect, etc. when finished. Default is ON for BLE, OFF for SIM. Files are written to your home directory as `ride_<timestamp>.tcx`.

## In-ride hotkeys

| Key | Action |
|---|---|
| **Space** | Pause / resume video, ghost, timer, and telemetry |
| **F11** | Toggle fullscreen |
| **Escape** | Exit fullscreen |
| **M** | Cycle map overlay: off → minimap → minimap-tracking |
| **C** | Toggle pacer cube (the wireframe AR box) |
| **G** | Toggle "cube follows ghost" — when on, the cube *is* the ghost rider |
| **R** | Toggle road-tangent dashed line |
| **;** / **'** | Decrease / increase pacer cube gap by 0.5 m |
| **,** / **.** | Decrease / increase video FOV by 1° (only adjust if you're not using a GoPro Max 2) |
| **Shift+,** / **Shift+.** | Fine FOV adjustment, 0.1° steps |
| **F1** | About / version / feedback links |

## Tuning the sync

Two playback strategies, picked in the in-ride settings panel (⚙):

- **Proportional** *(recommended)* — playback rate scales with the sync error. Smooth, stays glued to telemetry.
- **Cruise** — playback hovers near 1× and only steps when error grows. Lower battery / CPU on the video decoder, but may have audio stutter when transitioning between rates.

If video drifts visibly off telemetry: the in-ride **Video offset** spinner in the gear (⚙) panel lets you slide the alignment manually. Save a session and the offset is remembered.

## Known issues / beta caveats

- **Avatar overlay** (cube, tangent line) is calibrated for **GoPro Max 2** 360 reframes. Other cameras play fine, but the cube alignment will be wrong.
- **macOS audio stutter** in cruise mode when the playback rate steps. Workaround: use proportional mode, or mute audio.
- **Slow-speed handlebar wobble** (sub-5 km/h) is a fundamental limit of bar-mounted footage — no telemetry correction can fully remove it.
- **First-run permission prompts** on macOS (Bluetooth) and Windows (SmartScreen) are normal — beta builds aren't yet signed.

## Reporting bugs

Inside the app: **F1 → Report an Issue**. The button pre-fills your version number in the issue title. For questions or general feedback (not bugs), use **Discuss / Ask a Question** instead — it opens GitHub Discussions for the project.

When reporting, please include:
- OS + version
- BLE or SIM mode
- TCX/video filenames (no need to send files unless asked)
- What you did and what went wrong

Thanks for testing!
