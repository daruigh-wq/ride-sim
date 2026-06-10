# ride_sim ↔ 3D engine interface (DRAFT / forward-looking)

> **Status: design sketch, not implemented.** Pins down the contract between
> ride_sim (the *producer* of rider position/telemetry) and a future 3D engine
> (the *consumer* that renders a Gaussian-splat world the rider moves through).
> Lives here because ride_sim is the producer; the engine work is a separate
> project track. See the `project-splat-world-track` memory for the why.

## Core idea

ride_sim is already an **on-rails** experience: telemetry → position along a
fixed route. The splat world is the same thing with a 3D camera instead of a
flat video. So the engine does **not** need physics/collision/free-roam — it
needs a **splat scene + the route centerline as a spline + a camera whose
position along that spline is driven by ride_sim**.

The single canonical drive signal is **distance along the route, in cumulative
metres from route start** (`virtual_dist_m`). Everything else is advisory or
for HUD/ghost rendering.

## The linchpin: one distance parameterization, two artifacts

Both artifacts must be built from the **same physical route** and parameterized
by the **same cumulative-distance metric**:

- ride_sim's route TCX → `route_dist_arr` (cumulative metres) — already exists.
- the engine's route spline → built from the capture's GoPro GPS track, also
  parameterized by cumulative metres.

Because GoPro telemetry gives **absolute scale + GPS world anchoring**, the
splat world, the spline, and ride_sim's distance metric all align. `distance_m`
from ride_sim then indexes directly into the engine spline → camera position.
Get this alignment right and the rest is plumbing.

## Transport

- **Prototype:** UDP datagrams on `localhost`, one **JSON line per packet**.
  Low-latency, fire-and-forget, trivial on both sides (Python `socket`, any UE
  JSON lib / a small UDP receiver actor). No handshake, no backpressure.
- Primarily **one-way**: ride_sim → engine.
- Default port: `varies` (config) — suggest `localhost:50505`.
- Later: binary packing or a websocket if JSON overhead ever matters (it won't
  at these rates). Don't optimize early.

## Rate & smoothing (important)

- ride_sim emits at its loop tick (currently ~4 Hz). **Do not** drive the
  camera directly at 4 Hz — it will judder.
- Send `distance_m` **and** `speed_mps` so the engine can **dead-reckon**
  between packets: `distance_render = distance_m + speed_mps * (t_now - t_pkt)`,
  rendered at the engine's native 60–90 fps. This mirrors how ride_sim already
  smooths internally, and makes send-rate and render-rate independent.

## Message schema (ride_sim → engine)

```jsonc
{
  "v": 1,                    // protocol version; consumers ignore unknown fields
  "t": 1234.567,             // monotonic seconds since ride start (packet stamp)
  "route_id": "alewife_loop",// which world/route this maps to
  "distance_m": 5421.3,      // PRIMARY drive signal — cumulative m along route
  "speed_mps": 7.85,         // for dead-reckoning between packets
  "grade_pct": -3.2,         // current grade (for camera pitch / effort cues)
  "heading_deg": 184.5,      // bearing along route at position (0=N, CW) — derived
  "elevation_m": 41.7,       // optional — camera height / world sanity
  "lat": 42.39512,           // optional — georegistered worlds
  "lon": -71.14233,          // optional
  "cadence_rpm": 88,         // optional — in-world HUD
  "power_w": 142,            // optional — in-world HUD
  "hr_bpm": 138,             // optional — in-world HUD
  "ghost": {                 // optional — place ghost rider in the world
    "active": true,
    "distance_m": 5470.1,    // ghost's position on the SAME spline
    "speed_mps": 8.10
  },
  "state": "riding"          // "idle" | "riding" | "paused" | "finished"
}
```

Rules: `v`, `t`, `route_id`, `distance_m`, `speed_mps`, `state` are **required**;
everything else is optional and may be absent. Consumers must ignore unknown
fields (forward-compat). Absent optional field = "not available this packet",
not zero.

## Optional reverse channel (engine → ride_sim) — future

Keep minimal and optional. Possible later uses: engine reports "world loaded /
ready" before ride start, or requests pause. Not needed for the prototype.

## What ride_sim must add to produce this (small, deferred)

All source fields already live in `SharedState`:
- `virtual_dist_m` → `distance_m` ✓
- `speed_mps_smoothed` → `speed_mps` ✓
- `grade_pct` ✓
- `route_lat_arr` / `route_lon_arr` / `route_dist_arr` → `lat`/`lon` by interp ✓
- `ghost_dist_m` / `ghost_speed_mps` ✓
- `cadence_rpm` / `power_w` / `hr_bpm` ✓

Only **`heading_deg` must be derived** — bearing between adjacent route points
around `route_index`. Then a ~30-line UDP emitter reads `SharedState` under the
lock and sends one JSON line per loop tick. It touches **no** core logic — a
clean bolt-on when the engine track is ready.
