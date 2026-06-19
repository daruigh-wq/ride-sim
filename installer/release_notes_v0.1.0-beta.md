# Ride Simulator v0.1.0-beta

First public beta of Ride Simulator — a desktop app that plays back a
recorded cycling video and adjusts playback rate in real time so the video
matches your position on the route as you ride a smart trainer. **New this
build:** you can also bake a low-poly 3D virtual world from a route file and
ride that instead of a video — no footage required.

> **Beta software. Use at your own risk.**
> This build is unsigned and lightly tested. Expect rough edges and the
> occasional crash. See [USAGE.md](../USAGE.md) for the full beta warning,
> install walkthrough, known issues, and how to report bugs.

## Download

Pick the installer for your OS from the **Assets** section below:

| Platform | File |
|---|---|
| Windows 10 / 11 (x64) | `Ride Sim-0.1.0-beta-windows-setup.exe` |
| macOS 12+ (**Apple Silicon only**) | `Ride Sim-0.1.0-beta-mac.dmg` |

> The macOS build is **Apple-Silicon-only** this round (M1 or later). An
> Intel/universal mac build is planned for a later beta.

## Installing

Both builds are **unsigned**, so the first launch triggers a security
warning on both OSes. Full step-by-step (with the exact dialog text and
buttons to click) is in [USAGE.md → Installing](../USAGE.md#installing).
Short version:

- **Windows:** SmartScreen → *More info* → *Run anyway*
- **macOS:** drag **Ride Sim** to Applications, then right-click it → *Open*
  → *Open* (needed once, because the app isn't notarized)

## New in this build: ride a 3D virtual world

You can now generate a 3D world straight from a route and ride it as an
alternative to a recorded video:

- From the startup dialog choose **Bake virtual world**, pick a route file
  (`.gpx` / `.tcx` / `.fit`), and a terrain-detail level
  (**Standard / High / Ultra** — higher looks better but costs GPU and a
  bigger download).
- The world is built offline from public elevation data (DEM) plus
  OpenStreetMap roads, land cover, trees, buildings, and power lines.
- Ride it like any other route — the world scrolls to your trainer speed.
- **Two-monitor setups** are ideal: the world goes full-screen on the larger
  display, the cockpit dashboard on the laptop. One screen works too.
- Press **P** in the world for an FPS / frame-time / draw-call overlay —
  handy for judging whether a detail level suits your GPU.

This is experimental: how much scenery you see depends on OpenStreetMap
coverage for your area, so rural routes can look sparse.

## What you need

- **BLE FTMS smart trainer** (Wahoo Kickr, Tacx Neo, Saris H3, etc.) — or
  **SIM mode** if you just want to evaluate the app without a trainer
- For a **video ride:** a matched **TCX file** + **video file** of the same
  recorded ride (forward-facing camera; the AR cube overlay is calibrated
  for GoPro Max 2 reframes)
- For a **virtual-world ride:** just a route file (`.gpx` / `.tcx` / `.fit`)
  — no video needed
- Bluetooth-capable Mac (Apple Silicon, 12+) or Windows 10/11 PC
- Optional: BLE heart-rate monitor

Dumb-trainer support via separate BT speed / power / cadence sensors is
planned for v0.2.

## What's in this build

- Real-time video sync to BLE FTMS speed; two strategies (**proportional**
  and **cruise** — cruise just got an adaptive step in this build to fix a
  long-ride backward-seek bug)
- **Virtual 3D world renderer** — bake a world from a route and ride it
  (DEM terrain + OpenStreetMap scenery); terrain-detail picker; in-world
  performance overlay
- Configurable HUD pills: speed, cadence, power, HR, distance, elapsed,
  grade, gradient bar
- Map overlay (off / minimap / minimap-tracking)
- Ghost rider from a prior TCX recording
- AR pacer cube and road-tangent overlay (GoPro Max 2 calibration)
- TCX recording for upload to Strava / Garmin Connect / etc.
- Session persistence (video offset, HUD layout, last-used files) and
  dual-monitor window placement

## Known issues

- **macOS build is Apple-Silicon-only** — won't run on Intel Macs yet.
- **Virtual world is experimental.** Scenery density follows OpenStreetMap
  coverage (rural routes look bare); a route that doubles back on itself can
  show a terrain seam where it overlaps.
- **macOS audio stutter** when cruise mode steps the playback rate.
  Workaround: use proportional mode, or mute audio.
- **AR overlay** (cube, tangent line) is calibrated only for **GoPro Max
  2** 360 reframes — other cameras render but cube is misaligned. Use
  `,` / `.` keys to nudge FOV.
- **Low-speed (<5 km/h) handlebar wobble** is a fundamental limit of
  bar-mounted footage — no telemetry correction can fully remove it.
- Crash recovery is not automatic. If the app dies mid-ride, the partial
  TCX recording (if enabled) is preserved up to the last flush.

## Feedback

- **Bug reports:** inside the app → **F1 → Report an Issue** (pre-fills
  your version number in the issue title).
- **Questions / general discussion:** **F1 → Discuss / Ask a Question**.

## Expiration

This beta refuses to start after **2026-09-01**. Grab a newer build when
prompted; this is a courtesy reminder, not DRM.

---

Built from `main` at commit `397b94a`. PyInstaller + PySide6; world renderer
built with Godot 4.6.
