# Ride Simulator v0.1.0-beta

First public beta of Ride Simulator — a desktop app that plays back a
recorded cycling video and adjusts playback rate in real time so the video
matches your position on the route as you ride a smart trainer.

> **Beta software. Use at your own risk.**
> This build is unsigned and lightly tested. Expect rough edges and the
> occasional crash. See [USAGE.md](../USAGE.md) for the full beta warning,
> install walkthrough, known issues, and how to report bugs.

## Download

Pick the installer for your OS from the **Assets** section below:

| Platform | File |
|---|---|
| Windows 10 / 11 (x64) | `Ride Sim-0.1.0-beta-windows-setup.exe` |
| macOS 12+ (Intel / Apple Silicon) | `Ride Sim-0.1.0-beta-mac.dmg` |

## Installing

Both builds are **unsigned**, so the first launch triggers a security
warning on both OSes. Full step-by-step (with the exact dialog text and
buttons to click) is in [USAGE.md → Installing](../USAGE.md#installing).
Short version:

- **Windows:** SmartScreen → *More info* → *Run anyway*
- **macOS:** Right-click the app in Applications → *Open* → *Open*

## What you need

- **BLE FTMS smart trainer** (Wahoo Kickr, Tacx Neo, Saris H3, etc.) — or
  **SIM mode** if you just want to evaluate the app without a trainer
- A matched **TCX file** + **video file** of the same recorded ride
  (forward-facing camera; the AR cube overlay is calibrated for GoPro
  Max 2 reframes)
- Bluetooth-capable Mac (12+) or Windows 10/11 PC
- Optional: BLE heart-rate monitor

Dumb-trainer support via separate BT speed / power / cadence sensors is
planned for v0.2.

## What's in this build

- Real-time video sync to BLE FTMS speed; two strategies (**proportional**
  and **cruise** — cruise just got an adaptive step in this build to fix a
  long-ride backward-seek bug)
- Configurable HUD pills: speed, cadence, power, HR, distance, elapsed,
  grade, gradient bar
- Map overlay (off / minimap / minimap-tracking)
- Ghost rider from a prior TCX recording
- AR pacer cube and road-tangent overlay (GoPro Max 2 calibration)
- TCX recording for upload to Strava / Garmin Connect / etc.
- Session persistence (video offset, HUD layout, last-used files)

## Known issues

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

Built from `main` at commit `5984012`. PyInstaller + PySide6.
