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

### Step 1 — High-pass / sim-driven reframe  ✅ DONE 2026-07-13 (`build_road_follow.py`)
Built `build_road_follow.py` (road-follower) alongside the world-lock `build_stab_fbf.py`.
- **Yaw high-pass:** `yaw = +(rv_y − lp(rv_y))`, `lp` = centered moving average (LP_S≈0.7 s,
  reflect-padded). Counter only the fast residual → slow heading is left uncountered so the view
  follows the road. (Sign is `+`, consistent with the validated world-lock `counter = +rv`.)
- **Roll:** forced `roll = 0`.
- **Pitch:** `PITCH0 + lp(rv_x)` (keep slow grade, drop bounce).
- **VALIDATED on the 40–50 s 75° turn (GS0004)** — the exact window where world-lock failed:
  heading rv_y swings 0→75.7°, slow heading tracks it, `yaw_road` residual only −2.4..+1.7°
  (pure wobble). `montage_road.png` (rows static / world-lock / road-follow): road-follow keeps
  the road framed through the whole turn; world-lock swings to the fence/hand/parked-SUV by t≈7.5 s.
  Phase-corr high-freq wobble: static 0.70 px, **road-follow 0.56 px (−20%)**, world 0.31 (degenerate,
  wrong content). Renders `rf_stat.mp4` / `rf_world.mp4` / `rf_road.mp4` + `montage_road.png`.
  `REUSE_EQF=1` skips re-extraction for fast LP_S iteration.
- **Still TODO (the real payoff):** replace `lp(rv_y)` with the **sim route bearing** so the view
  heading == the overlay heading by construction → free registration. Needs the GPS/route-bearing
  source wired in (see Step 4). Also: a one-time absolute-heading alignment (CORI is relative to
  clip start; see Open questions / MNOR).

### Step 1.5 — Sim-bearing yaw + overlay registration  ✅ DONE 2026-07-13
Validated that our owned reframe co-registers with ride_sim's overlay (`overlay_register.py`
straight-window projection cal; `cori_register.py` the good CORI-consistent turn test; both
in `research/`, reuse `eqf/`). `turn_register.py` = earlier csv-bearing version, superseded.
- **Projection: EXACT.** Owned true-rectilinear reframe (`v360 eac→e→flat`) at **FOV=130** matches
  `_project()` when `video_fov_h_deg` is set to the reframe FOV. On a straight window (t≈99–107 s)
  the overlay cube + depth-ruler + centerline land on the road and the horizon sits at `v=H/2`.
  **This retires the POLY-projection mismatch that killed the old effort** (`reg_F130_P0_H1.0.png`).
- **Pitch = 0 (horizontal axis).** GRAV `gz≈0` → camera is level in pitch; the cosmetic −12°
  from `build_stab_fbf` BREAKS the overlay (horizon must be at center). Use pitch 0.
- **Roll: GRAV de-roll** `roll = −atan2(gx, gy)` (GRAV **+Y = DOWN**, gy≈+1 level). ~±3° on
  straights, −15° at the turn apex; levels the horizon through the lean (verified `roll_m1`).
- **Yaw: drive from CORI, not the csv.** `applied = rv_y − lp(rv_y)` (road-follow); drive BOTH
  the reframe yaw and the overlay route from the SAME CORI heading so they're timed-consistent.
  The committed `GS010004_yaw_gps.csv` (likely magnetometer) has a timing/source mismatch that
  swings the centerline — do NOT use it for this.
- **Centerline through the sharp turn: app-data-gated.** Exact on straights; approximate through
  the corner because GS0004 has **NO GPS** (no true positions/speed) — the route is reconstructed
  from heading integrated at a guessed speed V (≈6–8 m/s here), which can't reproduce a corner
  where speed varies and the rider cuts the line. **In the app the loaded TCX gives true positions
  → the centerline registers by construction.** (V-tuning also estimates ride speed as a bonus.)
- **⚠ APP ACTION when productionizing:** set `ride_sim.py:829 video_fov_h_deg` from `118.8`
  (POLY-calibrated to the old GoPro export) to the owned-reframe FOV (130), else it misregisters.

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
python build_stab_fbf.py    gpmd_0004.bin "$F" 179.88 40 10 -12 130      # world-lock (Step 0)
python build_road_follow.py gpmd_0004.bin "$F" 179.88 40 10 -12 130 0.7  # ROAD-FOLLOW (Step 1) ✅
# road_follow -> rf_road.mp4 (+ rf_stat/rf_world) + montage_road.png. REUSE_EQF=1 to skip re-extract.
```
