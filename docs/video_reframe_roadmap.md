# Video Reframe & Avatar Overlay — Roadmap

_Last updated: 2026-07-28. Written to survive a compacted conversation: it should be enough,
together with the memory files, to resume without re-deriving anything._

## Goal

Own the reframe of the GoPro Max 2 `.360` so the ride video's **projection and yaw are both
ours** — chosen, documented, and exactly modellable — instead of a black box we have to
reverse-engineer. That is what lets the pacer cube / road-centerline / **pedaling avatars**
road-lock.

**Both of the things the GoPro-app reframe was opaque about have since been MEASURED**
(2026-07-28), so "we could never match it" is no longer the reason to own the reframe — we
own it because a projection we generate has zero residual by construction:
- its **follow-damping** is a **zero-phase low-pass on yaw, tau ≈ 0.236 s** (f_c 0.67 Hz), the
  same law `reframe.py` already implements via `applied = rvy − lp(rvy, tau_road)`; ours just
  defaults to a heavier 0.7 s. See memory `project_gopro_stabilizer_characterized`.
- its **"GoPro lens" projection** is **stereographic @ h_fov 158 + a degree-4 radial law**
  (tangential residual 1.135 px, anisotropy 0.066 px ⇒ a pure centred radial law), and the
  radial term is worth only ~1% of half-width — i.e. **plain SG is already within ~1% of it**.
  Fit lives in `gopro-max2/footage/projection_compare/characterize/model.json`.

⚠ The bullets below dated 2026-07-13 describe the **rectilinear** era. `tools/reframe.py`
now defaults to **stereographic** — see Step 3.7.

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
   `v360=eac:e` instead of `:flat` gives an equirect (validated against GoPro Player's own equirect —
   0.049° in the 130° view; see the `eac` gotcha below before suspecting it).

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
- **`v360=eac` is CORRECT for the Max 2 `.360` — do NOT "fix" it.** This entry used to claim GoPro's
  packing ≠ ffmpeg's standard EAC and that a crop+per-face-rotate was needed. That was wrong, and it
  cost a session to disprove. Measured 2026-07-29 against GoPro Player's own unstabilized CineForm
  equirect (`footage/equirect/GS010004_equirect.mov`, verified frame-aligned: best shift dx=dy=0 at
  t=20/50/80/110/140):
  - Real geometric error in the **130° forward product view: 0.72 px of 1920 = 0.049°**. Negligible.
  - The raw `mean |diff| ≈ 13–18` is dominated by an **additive photometric floor** (Player applies its
    own sharpening/tone), not misregistration — Gaussian blur at σ=3 removes only 39% of it.
  - The apparent "clean forward, bad at the sides" pattern is a **texture artifact**: error tracks local
    image gradient (flat 6.05 / edgy 23.06 / sharp 39.42), and error-vs-texture across longitude
    correlates **r = 0.784**. The sides just contain trees and the rider's arms.
  - Packing is **standard 3×1365.33, no padding**: `fin_pad` is a no-op, an edge-crop sweep has no
    minimum, and the strong seams at x=688/3408 sit at face *centers* (682.7/3413.3) — they're the
    dual-lens stitch at ±90°, which lands mid-face, not a face boundary.
  - The family is right too: `c3x2` scores **76.3** vs `eac` **12.5**.
  - **Where the error actually is: the stabilizer (3.21° sd of uncorrected pitch), 65× larger.**
  - *Method note:* block-matching the displacement field does NOT work on this footage (sd 5.59 px,
    sign flips between adjacent bins) — repetitive trees/shadows/road paint defeat it. Use
    gradient-regression + blur-collapse; those two agree.
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
- **Projection: EXACT** — *for `--proj flat`, which was the only mode when this was written and
  is no longer the default (see Step 3.7).* Owned true-rectilinear reframe (`v360 eac→e→flat`)
  at **FOV=130** matches `_project()` when `video_fov_h_deg` is set to the reframe FOV. On a
  straight window (t≈99–107 s) the overlay cube + depth-ruler + centerline land on the road and
  the horizon sits at `v=H/2`. **This retires the POLY-projection mismatch that killed the old
  effort** (`reg_F130_P0_H1.0.png`). ⚠ It does NOT hold for `--proj sg`: ride_sim `_project()`
  is pure pinhole, so an sg clip needs the matching lens law in the app first.
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

### Step 2 — Fix de-EAC seams  ❌ CANCELLED 2026-07-29 — THERE IS NOTHING TO FIX
Was: "crop + per-face rotate the two tracks into ffmpeg's standard EAC face order." Measured
against GoPro Player's own unstabilized equirect and the real geometric error in the 130° view
is **0.049°**. `v360=eac` is correct as-is. See the `eac` entry under "Hard-won gotchas".

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
- **Known limits / future:** frame-by-frame PNG intermediates are disk-heavy on a full ride — and
  **worse since Step 3.7 raised the intermediate to 5376×2688: ~8.5 MB/frame** (measured: 768 MB for
  a 3 s window), so ~15 GB/min, i.e. hundreds of GB for a full ride. A future pass should pipe frames
  instead of dumping PNGs. Also `FPS=30` is hardcoded while the source is 29.97 — ffmpeg's `-r 30`
  keeps it self-consistent but duplicates a frame every ~1000, so it will not sit 1:1 with the
  29.92 Hz per-frame pose track. A deeper integration ("Import .360…" menu that calls reframe.py,
  auto-sets FOV) and the live GLSL shader path remain. `--validate` burns the overlay.

### Step 3.6 — Stabilization rework (jitter fix)  ⏳ IN PROGRESS — RESUME HERE (2026-07-13)
**Problem:** the reframe has bad high-freq roll/pitch JITTER (~10× GoPro). **Root cause:** the 30 Hz GRAV
vector is GoPro's *smoothed/lagged* gravity — inadequate for per-frame horizon leveling (subtracting a
lagged gravity leaves the fast motion AND adds counter-motion = jitter). Confirmed by user: the RAW `.360`
is **unstabilized** (baked-in camera tilt + vibration); the GoPro app's horizon-lock + re-level is perfect
because it integrates the **789 Hz gyro fused with accel**. Our earlier (April) equirect exports looked
fine only because the GoPro app had stabilization applied (it remembers settings). *(Also fixed along the
way: the v_fov 1.63× stretch, and the GRAV sampling-step → use `np.interp` at frame times.)*

**Solution (research-backed — replicate Gyroflow's core, don't use Gyroflow directly):** Gyroflow can't
reframe a `.360` (flat-footage stabilizer only; it *will* read the `.360`'s gyro but needs a separately-
supplied unstabilized flat crop to stabilize). So do the leveling at OUR reframe stage:
- Use the **MIT-licensed `vqf` pip package** (the exact fusion algo Gyroflow defaults to) → feed 789 Hz
  GYRO (÷939 rad/s) + ~197 Hz ACCL (÷417 m/s²) → accurate gravity-referenced orientation quaternions.
  **No GPL entanglement** (VQF is MIT; write our own leveling/smoothing, don't lift Gyroflow's GPLv3 Rust).
- Per frame: sample the fused quat → body-frame gravity → **roll (level horizon)** + **smooth pitch**
  (EMA two-pass low-pass); **keep the existing road-follow yaw**. Feed to the per-frame `v360=e:flat`
  yaw/pitch/roll machinery we already have. Export the per-frame quat for overlay registration.
- ~1–2 days. Prototype first on the 1:20 window (t=74–86) and compare to `jit_A_current.mp4`.
- **Gyroflow-assisted alternative (validation only):** feed Gyroflow our de-EAC flat reframe + the gyro
  → it stabilizes. Needs GoPro Player (or our export) for the unstabilized flat crop (user hunting the
  unlabeled GoPro-Player button that exports an *unstabilized* reframe; used it in April).

**Resume recipe (VQF path):** `pip install vqf`; read GYRO+ACCL w/ timestamps from `demo/GS0004_reframe_gpmd.bin`;
resample accel→gyro rate; `offlineVQF` → quats @789 Hz; sample @30 fps → body gravity → roll=`atan2(gx,gy)`,
pitch=`atan2(gz,gy)` (VERIFY vs GRAV + signs — the axis mapping was never confirmable on this horizon-less
footage, so validate by eye on the render); apply interpolated + smoothed pitch; render t=74–86 → compare.
**Source refs (READ, don't copy — GPLv3):** gyroflow `src/core/{imu_integration/vqf.rs, smoothing/default_algo.rs,
smoothing/horizon.rs, gyro_source/mod.rs}`; `smoothed_quat_at_timestamp()` = the per-frame stabilized orientation.

### Step 3.7 — Stereographic default @ 1920×1080  ✅ DONE 2026-07-28 (`tools/reframe.py`, UNCOMMITTED)
David's call, after the projection question was re-opened and the earlier "we can't match GoPro's
lens" claim turned out to be wrong (it was the *premise* of `radial_characterizer.py`, not its
result — the script succeeded; see Goal).

**New flags / defaults:** `--proj {sg,flat}` (default **sg**), `--size` (default **1920x1080**),
`--eq-size` (default **5376x2688**). `VFOV` now picks the per-projection square-pixel relation:
`4·atan(tan(h/4)·H/W)` = **78.861°** for sg, `2·atan(tan(h/2)·H/W)` = **100.683°** for flat at 130.
The `--validate` overlay's `project()` became a general **radial** law (`lens_r`), so the QC montage
draws correctly under either projection.

**Why sg** — it is not only about looks:
| at h_fov 130 | centre | edge | equirect the render demands |
|---|---|---|---|
| rectilinear | 7.8 px/deg | 43.7 px/deg (5.60× radial / 2.37× tangential, **anisotropy 2.37**) | **15748 px** |
| stereographic | 13.2 px/deg | 18.5 px/deg (1.41× **uniform**, conformal) | **6655 px** |

The `.360`'s two 4096×1344 EAC tracks are worth ~5376 equirect ≈ 14.9 px/deg. So **rectilinear at
1920 wide is edge-starved** (wants 15.7K, gets 5.4K → outer field upsampled ~3× and visibly soft),
while sg lands near what the sensor has and puts **1.68× more pixels on the centre of the road**,
which is exactly where avatars go. `--eq-size 5376` matters for the same reason: the old 2880 was
matched to the old 1024-wide output and would resample an already-downsampled sphere at 1920.

**Verified** (not merely run): `lens_r` and `project` were extracted from the file with `ast` and
tested directly — `lens_r(fov/2)` = W/2 exactly for both projections, a point at the horizontal
half-fov lands on x=W and one at the vertical half-fov on y=0 (that second test is what proves the
v_fov pairing), and **`flat` reproduces the original pinhole formula to 1.1e-13 px** ⇒ no regression
on the old path. End-to-end on `GS010004.360 --t0 79 --dur 3` for both projections, 23 s wall.

**✅ APP SIDE DONE 2026-07-28 — see Step 3.9.** `ride_sim._project()` now carries the radial
lens law, defaulting to pinhole so nothing changes for existing footage.

**Banked, not built:** the GoPro-lens radial polynomial (`model.json`). It only matters for footage
*someone else* reframed (a GoPro Player export) — our own sg render has zero residual by
construction. If we ever consume a Player export as plain SG the error is ~20 px on a 1920 frame,
larger than the whole telemetry heading error, so it is worth applying *then*. Note the fit is tied
to SG@158 on a 1280×720 raster and needs re-fitting for another fov/resolution.

### Step 3.8 — Frame rate + single-pass render  (2026-07-28)

**(a) Frame rate: FIXED and verified.** `FPS=30` was hardcoded against a 29.97 source and
`-r 30` was forcing a resample. Measured cost: **1 duplicated frame per 30 s** (≈240 over a
2-hour ride) — a hitch every ~33 s, and worse, it broke the 1:1 mapping between video frames
and per-frame telemetry (CORI is exactly ONE sample per frame at 29.97 — measured 29.928 Hz =
1.000 samples/frame — and that alignment is what makes the avatar pose track cheap). Now read
from the file with `ffprobe` (`--fps` overrides), kept as the RATIONAL string `30000/1001` for
ffmpeg so a long ride can't drift, and `-r` dropped from the equirect stage. Verified by
framemd5 over a 30 s window: **old 900 frames / 1 duplicate, new 900 frames / 0 duplicates.**
No interpolation involved — just not resampling.

**(b) Single-pass render: ABANDONED — and it was already documented.** The idea was to kill the
PNG intermediates by driving v360's yaw/roll per frame with `sendcmd` in one ffmpeg process. It
renders wrong: mean 22 gray at frame 0 growing to ~68 by frame 45, while the identical invocation
WITHOUT sendcmd is correct (1.18). **The cause is in this very document**, under "Hard-won gotchas":
*"`sendcmd` ACCUMULATES v360 rotation … the view tumbles progressively even though every command
value is small/bounded … Do not use sendcmd."* It is not a timing bug and there is nothing to fix.
`--single-pass` now refuses to run and says so. **Read the gotchas section before optimising the
render — that is what it is for.**

Two things from the attempt are worth keeping:
- **`v360=eac:PROJ` direct is equivalent to `eac→e→PROJ`** — mean **1.15** at pipeline params,
  best-fit zoom **1.00×**. So the equirect stage could be dropped for ONE resample instead of two,
  which would also moot the "is 5376 enough" question. That is orthogonal to sendcmd and would
  combine with a **piped-frames** design (per-frame ffmpeg processes handing raw frames over pipes
  rather than files) — the remaining route to zero scratch, if it is ever worth it.
- Measured scratch cost: **8.5 MB/frame (equirect) + 2.07 MB/frame (view) = ~317 MB per second of
  ride**, i.e. ~1.14 TB for a 1-hour ride expanded in one go. That is what (c) fixes.

**(c) Chunked render — the terabyte problem, solved.** See Step 3.9(b).

### Step 3.9 — Lens law in ride_sim + chunked render  ✅ DONE 2026-07-28 (UNCOMMITTED)

**(a) `_project()` is no longer hard-wired to pinhole.** It takes a `proj` argument and applies a
general radial law: `pinhole` r = f·tan(θ), `stereographic` r = 2f·tan(θ/2). This is NOT the ~1%
GoPro-lens polynomial (still banked) — it is the pinhole-vs-stereographic difference, which is
**up to 176.5 px on a 1920-wide frame at 130°** (52.9 px at only 10° off-axis). Wrong law ⇒ the
cube walks off the road as it moves off-axis.
- Threaded through the snapshot as `"projection"` and persisted as `video_projection`, alongside
  the existing `video_fov_h_deg`. **Default `"pinhole"` ⇒ zero change for existing footage.**
- Exposed by **Shift+L** (toggle), matching how FOV is exposed — these pacer tunes have never had
  a settings-dialog control, only hotkeys + persistence.
- VERIFIED by pulling `_project` out of the file with `ast`: pinhole reproduces the ORIGINAL
  `f_px` formula to **3.4e-13 px**, and both laws put a point at the h-half-fov exactly on the
  frame edge. `ride_sim.py` imports cleanly.
- **Bonus, not yet exploited:** existing GoPro-Player exports are stereographic too, so setting
  `stereographic` should register them across the WHOLE frame instead of only within `r<0.45`,
  which is the limit the 118.8° pinhole fit has always had. Worth A/B-ing on old footage.

**(b) Chunked render — the terabyte problem, solved.** `--chunk SECONDS` (default **20**) expands
one window at a time, encodes it, deletes the scratch, then stream-copies the parts together.
Peak disk now depends on the window, not the ride: **~317 MB per second of ride ⇒ 10 s = 3.2 GB,
20 s = 6.3 GB, 30 s = 9.5 GB**, versus ~1.14 TB for a 1-hour ride expanded in one go. `--chunk 0`
= one pass (old behaviour). `--reuse-eqf` only applies when the whole clip fits in one chunk.
- VERIFIED: 6 s window at `--chunk 2` (3 chunks of 60/60/59) vs `--chunk 0` — both **179 frames
  at 30000/1001**, per-frame difference **0.96–3.26 mean** (pure H.264 encode noise; the colour
  path alone costs ~2.0), and **no discontinuity at the chunk boundaries** — frames 59/60 and
  119/120 match their neighbours. Scratch dirs removed afterwards.
- The concat is a stream copy, so it costs seconds, not a re-encode.

### Step 3.10 — Stabilizer: joint yaw/pitch/roll solve  ✅ DONE 2026-07-29 (UNCOMMITTED)
Replaces three independent per-axis hacks with one pose solve. **`pitch` was hard-coded `0`, and
that was the entire defect** — roll alone cannot level a horizon that is also pitched, so the
missing term leaked back out as residual tilt. "HF pitch" and "low-amplitude undamped roll" were
the same bug.

**Conventions — both solved EMPIRICALLY, do not re-derive:**
- **v360 rotation is `R = Ry(−roll)·Rx(−pitch)·Rz(+yaw)`.** Fitted by putting a dot grid in an
  equirect, rotating it, and solving Kabsch: **0.09° max error, runner-up convention 8.6° away**.
  Decompose with `as_euler('zxy')` → `[yaw, −pitch, −roll]`.
- **body → equirect axes: right = +body_x, forward = +body_z, up = −body_y.** Solved by requiring
  the new solve to reproduce the *old, known-good* GRAV roll (max 6.4° vs 28.3° for the runner-up);
  it then agrees to **0.045° mean**.

**GRAV owns TILT, CORI owns HEADING — not the other way round.** CORI's reference frame *drifts in
tilt*: ref-down moves **−4.28° over this 180 s clip**, i.e. tens of degrees per hour. A constant
"down in ref" is therefore only safe on short clips. GRAV is drift-free and already smooth (0.07°
rms of fast content at τ=1 s), so it sets the vertical; `--tau-level` (2 s) low-passes GRAV's
direction *inside the CORI frame* so the drift is tracked rather than baked in.
⚠ `~/gopro-max2/video_pose_track.py` still uses a constant `g_ref` via `.mean(0)` — fine for a
180 s clip, **wrong for a 1–2 h ride**. Port this fix when it next matters.

Also fixed: GRAV was sampled with `searchsorted` (nearest index) on a stream that is exactly one
sample per frame → +16.7 ms bias, sd 0.246°, max 1.9°. Now `np.interp`.

**Measured A/B** (identical source frames, cumulative sub-pixel phase correlation):

| window | pitch sd | OLD sd/ptp px | NEW sd/ptp px |
|---|---|---|---|
| t=163.5 s | 6.22° | 62.2 / 298 | **25.2 / 86** |
| t=100 s | 0.57° | 22.8 / 87 | 23.4 / 82 (neutral) |

Sign confirmed by inversion — `pitch=−new` gives 105.8 px, **2× worse than uncorrected**.

⚠ **Honest limit:** during normal riding, gravity-relative pitch is only ~0.57° sd, so this fix is
**neutral there**. The ~23 px of vertical motion remaining at mid-ride is *not* gravity-pitch
(0.57° = 3.9 px) and is still unexplained — scene flow, real body motion, or metric drift. If bob
is still visible on flat riding, investigate that, not pitch.

**Two measurement traps that cost a round each:**
- Inter-frame (differenced) `dy` is noise-dominated and reported the fix as *worse*. Use a
  cumulative sub-pixel trace, and choose a window where the signal exists (t=74–80 has pitch
  sd 0.414° — nothing to measure).
- A self-consistency check can be **tautological**: "residual tilt 0.0000°" proved nothing,
  because `R` was *constructed* with `up_e` as a row. Validate against ffmpeg's real output.

### Step 4 — Re-test overlay registration
With an owned pinhole + sim-yaw video, the existing `_project` / `_draw_cube` / `_draw_tangent_line`
should road-lock (same projection, same heading). Re-run the "R" road path over the new reframe and
confirm the old registration failures are gone.
⚠ **Written for the rectilinear era**, but no longer blocked: `_project()` learned the radial
lens law in Step 3.9, so this can be re-run against a `--proj sg` clip by pressing **Shift+L**
once (or against `--proj flat` with no change at all).

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
