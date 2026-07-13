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
- **GS0004 HAS GPS after all (correction).** Earlier "no GPS" was WRONG — a `gpmf_inventory.py`
  bug: it dropped GoPro's **GPS9** compound (`'?'`) type, so the extracted bin looked GPS-less. The
  `.360` carries **GPS9 @10 Hz**, real coords (San Jose 37.32,−121.93), 3D fix, **mean 5.14 m/s**
  (window 6.2). Fixed the walker (see below). `gps_register.py` builds the route from the real track.
  **Big consequence: a GoPro `.360` carries its OWN route — "drop your .360" yields video AND route,
  no external TCX needed** (external TCX still supported for non-GoPro footage).
- **Centerline through the sharp turn: heading-fusion, not missing data.** With the real GPS route the
  near centerline sits on the road; through the messy intersection the raw 10 Hz GPS *bearing* is
  noisy (differentiating ±2–3 m position noise) and disagrees with CORI by ~8° at the apex (applied
  residual ±8.6° vs ±3° CORI-only). Time lag is negligible (0.07 s). Fix = **fuse**: smooth-GPS (or
  1 Hz-like) for the slow road shape + drift-free CORI for high-rate heading; drive reframe yaw and
  overlay route from the SAME fused heading. Not yet built — the productionization step.
- **⚠ APP ACTIONS when productionizing:** (1) set `ride_sim.py:829 video_fov_h_deg` from `118.8`
  (POLY-calibrated to the old GoPro export) to the owned-reframe FOV (130), else it misregisters.
  (2) pull the route straight from the `.360` GPS9 for GoPro rides.

### Step 1.6 — gpmf_inventory GPS9 fix  ✅ DONE 2026-07-13 (committed, public tool)
`tools/gpmf_inventory.py` now decodes GPMF `'?'` complex streams (GPS9, FACE) via the sibling
`TYPE` format string (`decode_complex`, capture `TYPE`, allow `'?'` as a data leaf). Verified GPS9
→ `[37.3206, −121.9267, 24.4 m, …, DOP 3.24, fix 3]`. Legacy GPS5 unaffected (only added `'?'` path).

### Step 2 — Fix de-EAC seams (polish, low priority)
Crop + per-face rotate the two tracks into ffmpeg's standard EAC face order; validate against
`track0_frame.png` / `track4_frame.png`. Forward-only riding view barely needs it.

### Step 3 — Productionize the reframe  ✅ v1 BUILT 2026-07-13 (`research/reframe.py`)
`reframe.py IN.360 [OUT.mp4]` — extracts gpmd from the `.360`, decodes **GPS9 route + CORI + GRAV**,
renders a road-following pinhole clip + `OUT.route.npz` sidecar. `--validate` burns the ride_sim
overlay for QC. **KEY DESIGN — LOCAL fusion** (chosen for simplicity/robustness, not because the sensors disagree —
see correction). CORRECTION 2026-07-13: my first cut claimed CORI heading "drifts −169° vs GPS −43°"
— that was an ANALYSIS BUG: GPS course-over-ground was computed by differentiating position WITHOUT
speed-gating, so the stationary start (first ~10 s) and the single end stop (~last 10 s) — the ONLY
slow parts; the rider blew every stop sign and held 5–7 m/s through the whole middle (0% slow
20–160 s) — corrupted the COG unwrap. **Speed-gated, CORI and GPS heading agree the whole ride** to a
gentle −0.07°/s ramp (~13° over 3 min) plus COG-noise spikes at the corners (`drift_diag.png`). CORI
yaw genuinely has no fiducial (magnetometer not fused → gyro-integral, drifts) but only mildly here;
pitch/roll ARE gravity-anchored (drift-free). Net: **speed-gate GPS COG; CORI drift is minor.** Local
fusion is still the clean choice (drift-immune, no global unwrap to get right):
- **Video = pure CORI road-follow**: `applied = cori_head − lp(cori_head, τ)` — a local high-pass,
  bounded everywhere regardless of drift. No GPS in the video yaw (injecting the drifted GPS frame
  made `applied` blow up to ±260°). GRAV de-roll, pitch 0, FOV 130.
- **Overlay/route = GPS positions + local distance-based bearing**, shifted by the reframe's own
  correction: `overlay_head = gps_bearing(rider) + applied` (`--sgn/--yaw-off` for the small constant
  camera-vs-travel mount offset). Everything local → no global-fit fragility.
- **CORI continuous heading**: integrate *incremental* inter-sample yaw + cumsum (the clip-start
  rotation-vector folds past ±180° over a long ride — can't be used directly).
- Validated on the 40–50 s turn (`reframe_validate*.png`): horizon level, road followed, near
  centerline on the road; through the intersection the centerline traces the rider's actual GPS
  left-turn (the street continuing ahead-right is the one they don't take). Far dashes streak to the
  horizon past a sharp corner — inherent (ride_sim's `DASH_FAR_M=50` does the same).
- **Provenance of `GS010004_yaw_gps.csv`** (settled): correlates only +0.47 (rate) with the `.360`'s
  own GPS9 course, ~3.6° detrended diff → NOT a GPS9 copy; likely a Garmin/magnetometer-blend from an
  earlier session. Moot — reframe.py uses the camera's own GPS9.
### Step 3.5 — Graduate reframe.py into the app  ✅ DONE 2026-07-13 (`tools/reframe.py`, committed)
`reframe.py` now lives in the repo (`tools/`, public), self-contained, `gpmf_inventory` path
`__file__`-relative. **Zero ride_sim code changes needed:** it emits a **TCX** from the `.360`'s own
GPS9 (lat/lon/alt/dist), which loads through the existing `load_tcx_route` (verified: 99 pts round-trip).
- Added **GPS speed-gating** (`--speed-gate`, default 1.5 m/s): course-over-ground is dropped when
  stationary and held from moving samples, so the start ramp / final stop don't corrupt the route.
- **Use in app:** `python tools/reframe.py IN.360 OUT.mp4 [--t0 S --dur S]` → `OUT.mp4` + `OUT.tcx`
  (+ `OUT.route.npz` advanced sidecar). In ride_sim: video = `OUT.mp4`, route = `OUT.tcx`, **offset 0,
  FOV = 130, camera height 1.0 m** (the tool prints this line). FOV is the persisted runtime setting
  (Settings, `ride_sim.py:2791`) — left the 118.8 default alone so existing GoPro-Player exports still
  register; set 130 per reframed video.
- **v_fov STRETCH BUG — fixed 2026-07-13.** First full-ride demo render looked "too tall" (user caught
  it in-app). Cause: rectilinear `v_fov` relates to `h_fov` through **tan**, not linearly — I'd used
  `v_fov = fov*H/W` (=73.1° at 1024×576) instead of `2*atan(tan(h_fov/2)*H/W)` (=100.7°) → non-square
  pixels → **1.63× vertical stretch**. ride_sim `_project` assumes square pixels, so this also threw off
  vertical registration (why the earlier tests needed cam_h fiddling). Fixed in `tools/reframe.py`; same
  bug still in the research scripts (`build_stab_fbf.py`, `gps_register.py` — gitignored, not shipped).
  **Roll & pitch verified OK** on the deployed file (horizon level+centered even at the +25° max-roll
  frame; GRAV de-roll lands, pitch=0 is correct — adding pitch-leveling made it worse). See
  [[feedback_verify_reframe_in_app]]. Renamed the equirect intermediate (`__equirect_tmp.mp4`, deleted
  after frame dump) so it can't be confused with the real output.
- **Known limits / future:** frame-by-frame PNG intermediates are disk-heavy on a full ride (~3 MB ×
  30 fps → ~24 GB); a future pass should pipe frames instead. A deeper integration ("Import .360…" menu
  that calls reframe.py, auto-sets FOV) and the live GLSL shader path remain. `--validate` burns the overlay.

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
