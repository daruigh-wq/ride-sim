#!/usr/bin/env python3
"""Headless sync-controller benchmark.

Drives the ride_sim sync controller against parameterized virtual riders
and reports objective metrics — RMS err, peak |err|, hard-seek count,
and rate-emit count (the audio-dropout proxy: each emit triggers a Qt
audio buffer flush, so minimizing this is as important as minimizing
err itself).

STATUS: SCAFFOLDING ONLY. Requires extracting `step_controller()` from
`ride_sim.run_ride_loop()` into a pure function callable from here AND
from production. Real work behind NotImplementedError stubs. `main()`
prints what it would sweep, then exits 1.

See `project-sync-bench-harness` and `project-cruise-audio-tradeoff` in
the memory store for full design context.
"""

import argparse
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# TODO: once step_controller is extracted from run_ride_loop, import it
# along with the route helpers:
#
# from ride_sim import (
#     step_controller,
#     load_tcx_route,
#     interp_time_from_distance,
#     find_index_for_distance,
#     CRUISE_STEP_PCT,
#     HARD_SEEK_SEC,
#     SEEK_COOLDOWN_SEC,
#     SOFT_ERR_SEC,
#     DT,
#     SPEED_ALPHA,
# )


# ─────────────────────────────────────────────────────────────
# Virtual rider profiles
# ─────────────────────────────────────────────────────────────

@dataclass
class RiderProfile:
    """A speed-generating function for the virtual rider.

    speed_fn(t_sim, recorded_speed_at_position_mps) -> rider_speed_mps
    """
    name: str
    speed_fn: Callable[[float, float], float]


def constant_pace(speed_mps: float) -> RiderProfile:
    """Rider holds a fixed speed regardless of what the recorded ride was doing."""
    return RiderProfile(
        name=f"constant_{speed_mps:.1f}mps",
        speed_fn=lambda t, rec: speed_mps,
    )


def relative_pace(ratio: float) -> RiderProfile:
    """Rider speed = ratio * recorded_speed at current position.

    ratio > 1.0 → faster rider than the recording (e.g. Cat3 on a hobbyist video)
    ratio < 1.0 → slower rider than the recording
    """
    return RiderProfile(
        name=f"relative_{ratio:.2f}x",
        speed_fn=lambda t, rec: ratio * rec,
    )


def sinusoidal_drift(amp: float, period_s: float, scale: float = 1.0) -> RiderProfile:
    """recorded_speed * scale * (1 + amp * sin(2π t / period_s)).

    Matches the current `worker_sim` SIM-mode generator's drift term.
    """
    return RiderProfile(
        name=f"sin_amp{amp:.2f}_T{period_s:.0f}s",
        speed_fn=lambda t, rec: rec * scale * (1.0 + amp * math.sin(2.0 * math.pi * t / period_s)),
    )


def interval(work_mps: float, rest_mps: float, period_s: float, duty: float = 0.5) -> RiderProfile:
    """Squarewave: work_mps for duty*period_s, then rest_mps for the rest of period_s.

    Mimics structured intervals or a route with frequent stop signs.
    """
    def fn(t_sim: float, _rec: float) -> float:
        phase = (t_sim % period_s) / period_s
        return work_mps if phase < duty else rest_mps
    return RiderProfile(
        name=f"interval_w{work_mps:.1f}_r{rest_mps:.1f}_T{period_s:.0f}s_d{duty:.2f}",
        speed_fn=fn,
    )


def with_noise(base: RiderProfile, sigma_pct: float, seed: int = 1234) -> RiderProfile:
    """Wrap any profile with multiplicative Gaussian noise."""
    rng = random.Random(seed)

    def fn(t_sim: float, rec: float) -> float:
        return base.speed_fn(t_sim, rec) * (1.0 + rng.gauss(0.0, sigma_pct))

    return RiderProfile(
        name=f"{base.name}_noise{sigma_pct:.2f}",
        speed_fn=fn,
    )


# ─────────────────────────────────────────────────────────────
# Controller configuration
# ─────────────────────────────────────────────────────────────

@dataclass
class ControllerConfig:
    name: str
    strategy: str                              # "cruise" | "proportional"
    base: float                  = 1.00
    kp: float                    = 0.08
    deadband: float              = 0.25
    min_rate: float              = 0.50
    max_rate: float              = 2.00
    # Cruise step parameters — interpretation depends on which cruise variant
    # is under test. The current production cruise uses the adaptive scheme:
    # ±step_pct when |err| <= aggressive_err_threshold, ±aggressive_step_pct
    # otherwise.
    step_pct: float              = 0.03
    aggressive_step_pct: float   = 0.10
    aggressive_err_threshold_s: float = 8.0


# ─────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────

@dataclass
class BenchMetrics:
    """Per-run summary. The pair to optimize is (rms_err_s, rate_emit_count)."""
    rms_err_s: float          = 0.0
    max_abs_err_s: float      = 0.0
    hard_seek_count: int      = 0
    rate_emit_count: int      = 0   # only emits that would survive _set_rate's 1% filter
    pct_outside_deadband: float = 0.0
    final_drift_s: float      = 0.0
    n_ticks: int              = 0


def compute_metrics(rows: list[dict]) -> BenchMetrics:
    """Roll per-tick rows into a BenchMetrics summary."""
    # TODO: implement once `run_bench` row schema is finalized.
    raise NotImplementedError("compute_metrics: pending run_bench implementation")


# ─────────────────────────────────────────────────────────────
# Bench loop
# ─────────────────────────────────────────────────────────────

def run_bench(
    route_dist_m: list[float],
    route_time_s: list[float],
    rider: RiderProfile,
    controller: ControllerConfig,
    duration_s: float,
    dt: float = 0.25,
) -> tuple[list[dict], BenchMetrics]:
    """Run one (rider × controller) combination headlessly.

    Returns the per-tick rows and the rolled-up BenchMetrics.

    TODO:
      1. Maintain virtual_dist (advanced by smoothed rider speed * dt).
      2. Compute target_video_t from interp_time_from_distance(virtual_dist).
      3. Advance synthetic video_t by current_rate * dt.
      4. err = target_video_t - video_t.
      5. Call step_controller(err, ...) for an (action, new_rate) decision.
      6. Apply action to synthetic video_t (a seek) or current_rate (rate change).
      7. Count rate emits where |new_rate - last_emitted_rate| > 0.01.
      8. Log a row matching the SYNC_DEBUG schema.
    """
    raise NotImplementedError("run_bench: pending step_controller refactor")


# ─────────────────────────────────────────────────────────────
# Sweep matrix
# ─────────────────────────────────────────────────────────────

def default_riders() -> list[RiderProfile]:
    """Riders that exercise the controller's interesting cases."""
    return [
        constant_pace(5.0),                                              # 18 km/h steady
        constant_pace(8.3),                                              # 30 km/h steady
        relative_pace(0.80),                                             # 20% slower than recorded
        relative_pace(1.20),                                             # 20% faster than recorded
        relative_pace(2.00),                                             # Cat3 on a hobbyist's video
        sinusoidal_drift(amp=0.10, period_s=120.0),                      # matches worker_sim default
        sinusoidal_drift(amp=0.15, period_s=60.0),                       # faster, larger oscillation
        with_noise(sinusoidal_drift(0.10, 120.0), sigma_pct=0.05),
        interval(work_mps=8.3, rest_mps=1.0, period_s=30.0, duty=0.70),  # work/rest
    ]


def default_controllers() -> list[ControllerConfig]:
    """Controller variants to benchmark. Extend with new candidates."""
    return [
        # Original non-iterative cruise (no adaptive bump)
        ControllerConfig(
            "cruise_orig",
            strategy="cruise",
            step_pct=0.03, aggressive_step_pct=0.03,
        ),
        # Current production cruise (adaptive 10% step at |err| > 8s)
        ControllerConfig(
            "cruise_adaptive",
            strategy="cruise",
            step_pct=0.03, aggressive_step_pct=0.10, aggressive_err_threshold_s=8.0,
        ),
        # Proportional, default tuning
        ControllerConfig(
            "prop_default",
            strategy="proportional",
            kp=0.08,
        ),
        # Proportional, gentler tuning
        ControllerConfig(
            "prop_gentle",
            strategy="proportional",
            kp=0.03,
        ),
    ]


# ─────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Headless sync-controller benchmark (scaffolding)",
    )
    parser.add_argument(
        "--tcx", type=Path, required=False,
        help="TCX file to use as route. If omitted, a synthetic flat route is generated.",
    )
    parser.add_argument(
        "--duration", type=float, default=600.0,
        help="Simulated run duration in seconds (default: 600)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("scripts/out"),
        help="Output directory for per-run CSVs (default: scripts/out, gitignored)",
    )
    args = parser.parse_args()

    riders      = default_riders()
    controllers = default_controllers()
    total_runs  = len(riders) * len(controllers)

    print("scripts/sync_bench.py: scaffolding only, not yet wired up.", file=sys.stderr)
    print("Blocked on: step_controller refactor out of ride_sim.run_ride_loop.", file=sys.stderr)
    print(file=sys.stderr)
    print(f"Configured TCX:       {args.tcx if args.tcx else '(synthetic flat route)'}")
    print(f"Configured duration:  {args.duration:.0f} s")
    print(f"Configured out-dir:   {args.out_dir}")
    print()
    print(f"Sweep would be {len(riders)} riders × {len(controllers)} controllers = {total_runs} runs:")
    print("  Riders:")
    for r in riders:
        print(f"    - {r.name}")
    print("  Controllers:")
    for c in controllers:
        print(f"    - {c.name}  (strategy={c.strategy})")

    return 1   # non-zero so CI / shell scripts know this isn't yet a real run


if __name__ == "__main__":
    sys.exit(main())
