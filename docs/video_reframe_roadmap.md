# Video Reframe & Avatar Overlay — Roadmap

_Last updated: 2026-07-13. Written to survive a compacted conversation: it should be enough,
together with the memory files, to resume without re-deriving anything._

## Goal

Own the reframe of the GoPro Max 2 `.360` so the ride video is a **pinhole/rectilinear
view that matches ride_sim's overlay projection**, with **yaw we control** (eventually driven
by the sim's route bearing). This is what makes the pacer cube / road-centerline / future
**pedaling avatars** actually road-lock. It replaces the GoPro-app reframe, whose follow-damping
(a black-box high-pass on yaw that leaks sustained turns) and proprietary POLY projection we
could never match.

Related memory (auto-loaded next session): `project_video_reframe_pipeline`,
`project_gopro_imu_rate_table`, `project_avatar_pipeline`, `project_road_locking`.

---

## Current state — DONE & validated (2026-07-13)

1. **IMU frame fully characterized** (rate-table on a MyActuator RMD-L-7025 servo). GoPro Max 2
   gyro: **yaw = GYRO.X, pitch = GYRO.Y, roll = GYRO.Z**, cleanly orthogonal, per-axis scale
   ~939/962/950 (X/Y/Z). **CORI** (CameraOrientation quat, 30 Hz) is drift-free and faithfully
   tracks physical motion; **IORI** (ImageOrientation) proved GoPro's own stabilization world-locks
   yaw to **<0.6°** — so any visible wobble is *downstream* (30 Hz ceiling >15 Hz, rolling shutter,
   or the export), not CORI fidelity.

2. **ffmpeg de-EAC verified — no GUI needed** ("drop your .360 here" is viable). The `.360` is two
   HEVC tracks (`0:0` + `0:4`), each 4096×1344 equi-angular cube faces; gpmd telemetry is stream `3`.
   One command yields a forward **pinhole** view (matches `ride_sim.py _project()`):
   ```
   ffmpeg -y -an -i IN.360 -filter_complex \
     "[0:0][0:4]vstack,v360=eac:flat:w=1600:h=900:h_fov=140:v_fov=79:yaw=0:pitch=-12" out.png
   ```
   `v360=eac:e` instead of `:flat` gives an equirect (forward-center clean; minor face-order seams).

3. **Per-frame CORI stabilization built & validated.** `.360 → equirect (once) → per-frame
   `v360=e:flat` with that frame's rotation → reassemble`. Signs validated: **counter = +rv**
   (rv = rotation vector of `q_rel = conj(CORI(T0))·CORI(t)` in the CORI Y-up frame):
   `yaw = +rv_y`, `pitch = PITCH0 + rv_x`, `roll = +rv_z`. Wobble metric (phase-corr on distant
   band, calm window): static **0.76 px**, wrong-sign(−rv) **1.37 px** (doubles it), **+rv 0.71 px**.

4. **Design finding — full world-lock is WRONG for riding.** Stabilizing a 75° turn showed full
   CORI stabilization pins the view to the *start compass heading*, so in a corner the road swings
   out of frame (`montage_turn.png`: static follows road, full-stab swings to the hand/ground by
   t=9 s). The app must **high-pass**: keep the slow heading (follow road / drive from sim), counter
   only the fast wobble, and **kill roll**.

---

## Hard-won gotchas (do NOT rediscover these)

- **`sendcmd` ACCUMULATES v360 rotation.** Driving per-frame yaw/pitch/roll via `sendcmd` makes the
  view tumble progressively even though every command value is small/bounded (static v360 with the
  *same* values is perfect). **Do not use sendcmd — render static v360 frame-by-frame.**
- **Always pass `-an`.** The `.360` has a 24-bit PCM audio track that breaks the encoder otherwise.
- **zsh mangles `$H:h_fov`** in filter strings (`:h` is a zsh modifier). Use literal numbers or
  `${H}` braces in shell; a Python driver sidesteps it.
- **Naive `eac` has minor face-order seams** (GoPro packing ≠ ffmpeg standard EAC). Forward center is
  clean; a proper crop+per-face-rotate fixes edges. Low priority for a forward riding view.
- **Motor clips (GS0008/10/11) are visually DARK** (bench low-light, raw brightness ~0.006) — great
  telemetry, useless for a *visual* stabilization demo. Use ride footage (GS0004) for visuals.

---

## Where everything lives

- **Reframe/analysis scripts** — in `research/motor-imu-rnd/` (this dir is **gitignored**: it holds a
  ChatGPT transcript + copyrighted MyActuator PDFs — keep it ignored). On disk, ready to run:
  - `build_stab_fbf.py` — **THE working stabilizer** (frame-by-frame). `args: gpmd src.360 clipdur T0 DUR [PITCH0] [FOV]`.
  - `reproc_signs.py` — fast sign-sweep, reuses extracted `eqf/` frames.
  - `reframe_hunt.py` — CORI/IORI vs encoder wobble analysis.
  - `gpmf_yaw_analysis.py`, `gpmf_cori_analysis.py` — axis-ID / CORI faithfulness.
  - `build_stabilized_clip.py` — **DEPRECATED** sendcmd version (accumulates; kept as a cautionary artifact).
  - Extracted telemetry: `gpmd_0004.bin` (ride), `gpmd_0008/0010/0011.bin` (motor yaw/pitch/roll).
- **`.360` source files** — GoPro SD card, mounts at `/Volumes/Untitled/DCIM/100GOPRO/` (GS010001–011).
- **`tools/gpmf_inventory.py`** — committed, PUBLIC. The KLV walker/decoder reused by every analysis
  script (`import` it; `walk()`, `decode()`, `TYPE_SIZE`). Run: `gpmf_inventory.py <gpmd.bin> <dur_s>`.
- **Prior in-house-reframe artifacts (committed, repo root)** — `GS010004_yaw_iori.csv`,
  `GS010004_yaw_gps.csv`, `GS010004_yaw_lp.csv` (yaw from IORI / GPS / low-pass) + `GS010004.mp4`
  (a GoPro-app reframe export). These are the earlier effort that stalled on the POLY-projection
  mismatch; useful reference for heading sources.
- **Overlay code** — `ride_sim.py`: `_project()` (pinhole, ~L1789), `_draw_cube()`,
  `_draw_tangent_line()` (the "R" road path, ~L1877); `video_fov_h_deg = 118.8` (calibrated 2026-05-10
  vs a GS0004 Player export — becomes *our* FOV once we own the reframe).

---

## Next steps (ordered)

### Step 1 — High-pass / sim-driven reframe  ← START HERE
Turn `build_stab_fbf.py` from world-lock into a road-follower:
- **Yaw:** counter only the *fast* deviation. `yaw_counter(t) = -(yaw_cori(t) − smooth(yaw_cori(t)))`
  where `smooth` is a low-pass (~0.5–1 s window). Keeps the slow heading (follows the road), removes
  weave/buzz. Later: replace `smooth(yaw_cori)` with the **sim route bearing** so the view heading ==
  the overlay heading by construction (registration for free).
- **Roll:** force `roll = 0` (kill lean — biggest nausea/wrongness source).
- **Pitch:** keep a gentle smoothed pitch (grade), or fixed `PITCH0`.
- **Validate:** render a ride window with both weave and a curve; confirm it *follows the road*,
  horizon level, wobble gone. Re-use the phase-corr wobble metric (should drop vs static while the
  slow heading still tracks).

### Step 2 — Fix de-EAC seams (polish, low priority)
Crop + per-face rotate the two tracks into ffmpeg's standard EAC face order; validate against
`track0_frame.png` / `track4_frame.png`. Forward-only riding view barely needs it.

### Step 3 — Productionize the reframe
- Offline pre-render: one script `reframe.py IN.360 route.tcx → OUT.mp4` (extract gpmd → CORI →
  high-pass/sim yaw → frame-by-frame v360 → encode). Decide default FOV (~130–150°) and output res.
- Later: live in-app GLSL shader for the true "drop `.360` → ride" experience (app bundles ffmpeg
  for the offline path first).

### Step 4 — Re-test overlay registration
With an owned pinhole + sim-yaw video, the existing `_project` / `_draw_cube` / `_draw_tangent_line`
should road-lock (same projection, same heading). Re-run the "R" road path over the new reframe and
confirm the old registration failures are gone.

### Step 5 — Pedaling avatars (design already discussed; see `project_avatar_pipeline`)
- **One rider = the ghost**, drawn only when **ahead** (hide when behind). Reuse the existing
  `cube_follows_ghost` placement (road-projected at `ghost_gap_m`).
- **Cadence:** ghost's TCX cadence if present, else synthesize from ghost speed (~60–100 rpm).
- **Render:** procedural QPainter rear-view rider first (validate placement + cadence + curve), then
  swap in pre-rendered sprites from `bike_optimized1.blend`.

---

## Open questions
- **Absolute heading:** to match the sim route bearing to the sphere we may need MNOR (magnetic north)
  or a one-time alignment; CORI is drift-free but relative to clip start. (Magnetometer is disturbed
  near steel/motor but fine on a bike.)
- **>15 Hz / rolling shutter:** the regime the 30 Hz orientation can't correct — untested (bench maxed
  at 4 Hz). Gyroflow (open source, can reframe + preserve telemetry) is the tool to probe it *later*.
- **Live reframe feasibility** on the target hardware (shader vs pre-render).

## One-command quickstart (reproduce the current state)
```
cd research/motor-imu-rnd            # (gitignored; scripts live here)
F=/Volumes/Untitled/DCIM/100GOPRO/GS010004.360
ffmpeg -y -an -i "$F" -map 0:3 -c copy -f data gpmd_0004.bin      # telemetry
python build_stab_fbf.py gpmd_0004.bin "$F" 179.88 40 10 -12 130  # stabilize 40–50s window
# -> fbf_stab.mp4 (world-lock) + fbf_stat.mp4 (raw pinhole). Step 1 makes stab a road-follower.
```
