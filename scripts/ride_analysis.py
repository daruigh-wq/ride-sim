#!/usr/bin/env python3
"""
ride_analysis.py — offline grade/mass/power/speed analysis for ride_sim.

Compares two TCX files recorded over the SAME course — typically:
    A) a Garmin-controlled trainer ride   (reference controller)
    B) a ride_sim-controlled trainer ride (our controller)

It derives grade from each file's altitude/distance profile, then checks how
well each ride obeys the cycling-power physics model ride_sim already uses
(sim_power_watts in ride_sim.py):

    P = (m*g*grade + m*g*Crr + 0.5*rho*CdA*v**2) * v

From that it reports, per file:
  1. Grade-vs-distance profile (overlaid, to compare smoothing/lookahead).
  2. Forward residual:  P_recorded - P_model(v, grade).
  3. Inverse residual:  v_recorded - v_solved(P, grade)  (trainer fidelity).
  4. Effective CdA / Crr / mass via least-squares (recovered coefficients).
  5. Power noise floor (RMS of forward residual) — the "is it smooth" number.
  6. Grade->power response lag via cross-correlation (the "latching" number).

Usage:
    python scripts/ride_analysis.py GARMIN.tcx RIDESIM.tcx
    python scripts/ride_analysis.py GARMIN.tcx RIDESIM.tcx --mass 80 --no-plots

Plots require matplotlib; without it the script still prints the full report.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

import numpy as np

# ── Physics constants — keep in sync with ride_sim.py ──────────────
GRAVITY = 9.81
DEFAULT_MASS_KG = 80.0
DEFAULT_CRR = 0.004
DEFAULT_CDA = 0.35
DEFAULT_RHO = 1.225

NS = {
    "tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
    "ae2": "http://www.garmin.com/xmlschemas/ActivityExtension/v2",
}


# ── TCX parsing ────────────────────────────────────────────────────
def parse_tcx(path):
    """Return a dict of numpy arrays: t, dist, elev, speed, power, cad, hr.

    Speed/power are read from the TPX activity extension; speed falls back to
    a distance/time finite difference when the Speed field is absent (common
    in raw Garmin exports). NaN marks genuinely missing samples.
    """
    root = ET.parse(path).getroot()
    tps = root.findall(".//tcx:Trackpoint", NS)
    if not tps:
        raise SystemExit(f"{path}: no Trackpoint elements found")

    t, dist, elev, speed, power, cad, hr = ([] for _ in range(7))
    t0 = None
    for tp in tps:
        time_el = tp.find("tcx:Time", NS)
        dist_el = tp.find("tcx:DistanceMeters", NS)
        if time_el is None or dist_el is None:
            continue
        ts = datetime.fromisoformat(time_el.text.strip().replace("Z", "+00:00"))
        if t0 is None:
            t0 = ts
        t.append((ts - t0).total_seconds())
        dist.append(float(dist_el.text))

        elev_el = tp.find("tcx:AltitudeMeters", NS)
        elev.append(float(elev_el.text) if elev_el is not None else np.nan)

        cad_el = tp.find("tcx:Cadence", NS)
        cad.append(float(cad_el.text) if cad_el is not None else np.nan)

        hr_el = tp.find("tcx:HeartRateBpm/tcx:Value", NS)
        hr.append(float(hr_el.text) if hr_el is not None else np.nan)

        # TPX extension lives under Extensions/TPX (ae2 namespace).
        spd_el = tp.find(".//ae2:Speed", NS)
        pwr_el = tp.find(".//ae2:Watts", NS)
        speed.append(float(spd_el.text) if spd_el is not None else np.nan)
        power.append(float(pwr_el.text) if pwr_el is not None else np.nan)

    t = np.array(t)
    dist = np.array(dist)
    speed = np.array(speed)

    # Fill missing speed from distance/time finite difference.
    if np.all(np.isnan(speed)):
        dt = np.gradient(t)
        dd = np.gradient(dist)
        with np.errstate(divide="ignore", invalid="ignore"):
            speed = np.where(dt > 1e-6, dd / dt, 0.0)

    return {
        "path": path,
        "t": t,
        "dist": dist,
        "elev": np.array(elev),
        "speed": speed,
        "power": np.array(power),
        "cad": np.array(cad),
        "hr": np.array(hr),
    }


# ── Grade derivation ───────────────────────────────────────────────
def derive_grade(dist, elev, window_m=20.0, smooth_m=30.0):
    """Centered finite-difference grade (%) over a distance window.

    Elevation is boxcar-smoothed over smooth_m first to suppress the
    sample-to-sample baro/GPS noise that otherwise dominates the derivative.
    """
    elev = elev.copy()
    nan = np.isnan(elev)
    if nan.any():  # linear-interpolate gaps so the smoother is well-defined
        elev[nan] = np.interp(np.flatnonzero(nan), np.flatnonzero(~nan), elev[~nan])

    # Distance-aware boxcar: average all samples within +/- smooth_m/2.
    sm = np.empty_like(elev)
    for i in range(len(elev)):
        lo = np.searchsorted(dist, dist[i] - smooth_m / 2)
        hi = np.searchsorted(dist, dist[i] + smooth_m / 2)
        sm[i] = elev[lo:hi + 1].mean() if hi >= lo else elev[i]

    grade = np.zeros_like(sm)
    for i in range(len(sm)):
        j0 = np.searchsorted(dist, dist[i] - window_m / 2)
        j1 = np.searchsorted(dist, dist[i] + window_m / 2)
        j0 = max(0, min(j0, len(sm) - 1))
        j1 = max(0, min(j1, len(sm) - 1))
        dd = dist[j1] - dist[j0]
        if dd > 1e-6:
            grade[i] = 100.0 * (sm[j1] - sm[j0]) / dd
    return grade


# ── Grade-pipeline tradeoff: elevation-smoothing vs lookahead ──────
def grade_tradeoff(dist, elev,
                   lookaheads=(10, 20, 40, 60),
                   elev_smooths=(0, 30, 60)):
    """Quantify the two-knob tradeoff that governs grade quality.

    Returns (rows, flat_mask) where each row is
        (lookahead_m, elev_smooth_m, flat_noise_pct, deepest_dip_pct).

    flat_noise = std of computed grade over genuinely-flat terrain
                 (the 'false +grade on flat ground' the user sees).
    deepest_dip = most-negative grade reached (descent depth preserved).

    Good config = LOW flat_noise AND deep (very negative) deepest_dip.
    Widening lookahead lowers BOTH (bad). Smoothing elevation lowers
    flat_noise while barely touching real dips (good) — that's the point.
    """
    # Low-frequency reference defines where the terrain is actually flat.
    lowfreq = derive_grade(dist, elev, window_m=80.0, smooth_m=80.0)
    flat = np.abs(lowfreq) < 1.2
    rows = []
    for la in lookaheads:
        for es in elev_smooths:
            g = derive_grade(dist, elev, window_m=la, smooth_m=es)
            noise = float(np.std(g[flat])) if flat.any() else np.nan
            rows.append((la, es, noise, float(g.min())))
    return rows, flat


# ── Physics ────────────────────────────────────────────────────────
def model_power(v, grade_pct, mass, crr, cda, rho, accel=None):
    """Steady-state power, optionally with the inertia term m*a*v.

    Without accel this is exactly ride_sim's sim_power_watts. With accel it
    adds the cost of spinning up rider+flywheel mass during surges, which is
    what makes the residual meaningful on a real (non-steady) ride.
    """
    g_force = mass * GRAVITY * (grade_pct / 100.0)
    rolling = mass * GRAVITY * crr
    aero = 0.5 * rho * cda * v ** 2
    inertia = mass * accel if accel is not None else 0.0
    return np.maximum(0.0, (g_force + rolling + aero + inertia) * v)


def solve_speed(power, grade_pct, mass, crr, cda, rho):
    """Invert P = (m g grade + m g Crr + 0.5 rho CdA v^2) v for v >= 0.

    Real positive root of  0.5 rho CdA v^3 + (m g (grade+Crr)) v - P = 0.
    """
    a = 0.5 * rho * cda
    b = mass * GRAVITY * (grade_pct / 100.0 + crr)
    out = np.zeros_like(power, dtype=float)
    for i in range(len(power)):
        P = power[i]
        if not np.isfinite(P) or P <= 0:
            continue
        roots = np.roots([a, 0.0, b[i], -P])
        real = roots[np.abs(roots.imag) < 1e-6].real
        real = real[real > 0]
        out[i] = real.min() if real.size else np.nan
    return out


def fit_coeffs(v, grade_pct, power, mass, rho, mask=None):
    """Least-squares recover effective (Crr, CdA) holding mass fixed.

    P/v = m*g*grade + m*g*Crr + 0.5*rho*CdA*v^2  is linear in [Crr, CdA]:
        y = m*g*Crr * 1  +  0.5*rho*CdA * v^2     where y = P/v - m*g*grade

    Pass a steady-state `mask` to exclude coasting/transient samples that
    otherwise corrupt the fit (P=0 descents pull CdA toward zero).
    """
    ok = np.isfinite(v) & np.isfinite(power) & (v > 1.0) & (power > 0)
    if mask is not None:
        ok &= mask
    v, g, P = v[ok], grade_pct[ok], power[ok]
    if ok.sum() < 10:
        return np.nan, np.nan, int(ok.sum())
    y = P / v - mass * GRAVITY * (g / 100.0)
    A = np.column_stack([np.full_like(v, mass * GRAVITY), 0.5 * rho * v ** 2])
    (crr, cda), *_ = np.linalg.lstsq(A, y, rcond=None)
    return crr, cda, int(ok.sum())


def response_lag(grade_pct, power, t):
    """Lag (s) maximizing cross-correlation of d/dt grade vs d/dt power.

    A positive lag means power changes AFTER grade changes — the trainer's
    response delay / 'latch at peaks' time, resampled to 1 Hz first.
    """
    if t[-1] <= t[0]:
        return np.nan
    grid = np.arange(t[0], t[-1], 1.0)
    gi = np.interp(grid, t, grade_pct)
    pi = np.interp(grid, t, np.nan_to_num(power))
    dg = np.diff(gi)
    dp = np.diff(pi)
    dg -= dg.mean()
    dp -= dp.mean()
    if dg.std() < 1e-9 or dp.std() < 1e-9:
        return np.nan
    n = len(dg)
    lags = np.arange(-15, 31)  # -15..+30 s
    best_lag, best_c = 0, -np.inf
    for L in lags:
        if L >= 0:
            a, b = dg[:n - L], dp[L:]
        else:
            a, b = dg[-L:], dp[:n + L]
        if len(a) < 10:
            continue
        c = np.corrcoef(a, b)[0, 1]
        if np.isfinite(c) and c > best_c:
            best_c, best_lag = c, L
    return best_lag, best_c


# ── Report ─────────────────────────────────────────────────────────
def analyze(d, mass, crr, cda, rho, window_m,
            min_speed=2.0, min_power=15.0, max_accel=0.2):
    grade = derive_grade(d["dist"], d["elev"], window_m=window_m)
    v = d["speed"]
    P = d["power"]

    # Acceleration from lightly-smoothed speed (3-sample mean) over real dt.
    t = d["t"]
    vs = np.convolve(np.nan_to_num(v), np.ones(3) / 3, mode="same")
    dt = np.gradient(t)
    accel = np.where(dt > 1e-6, np.gradient(vs) / dt, 0.0)

    has_power = np.isfinite(P).any() and np.nanmax(P) > 0

    # Quasi-steady, actively-pedaling mask: moving, on the gas, not surging.
    # This is where the steady-state physics is actually supposed to hold.
    steady = (
        np.isfinite(v) & (v > min_speed)
        & np.isfinite(P) & (P > min_power)
        & (np.abs(accel) < max_accel)
    )

    # Forward model includes inertia so surges that survive the mask are fair.
    P_model = model_power(v, grade, mass, crr, cda, rho, accel=accel)
    v_solved = solve_speed(P, grade, mass, crr, cda, rho) if has_power else None

    fwd = (P - P_model) if has_power else None
    inv = (v - v_solved) if has_power else None

    eff = fit_coeffs(v, grade, P, mass, rho, mask=steady) if has_power else None
    lag = response_lag(grade, P, t) if has_power else None

    return {
        "grade": grade, "accel": accel, "steady": steady,
        "P_model": P_model, "v_solved": v_solved,
        "fwd": fwd, "inv": inv, "eff": eff, "lag": lag,
        "has_power": has_power,
    }


def grade_bins(d, a, edges=(-12, -8, -4, -1, 1, 4, 8, 12)):
    """Median speed/power per grade bucket on steady-state samples."""
    grade, v, P, st = a["grade"], d["speed"], d["power"], a["steady"]
    rows = []
    edges = list(edges)
    bounds = [(-1e9, edges[0])] + list(zip(edges, edges[1:])) + [(edges[-1], 1e9)]
    for lo, hi in bounds:
        m = st & (grade >= lo) & (grade < hi)
        if m.sum() < 5:
            rows.append((lo, hi, m.sum(), np.nan, np.nan))
            continue
        rows.append((lo, hi, int(m.sum()),
                     np.median(v[m]) * 3.6, np.median(P[m])))
    return rows


def fmt(x, u="", n=2):
    return f"{x:.{n}f}{u}" if x is not None and np.isfinite(x) else "  n/a"


def print_report(d, a, mass):
    name = d["path"].split("/")[-1]
    t = d["t"]
    dur = (t[-1] - t[0]) / 60.0
    dist_km = (d["dist"][-1] - d["dist"][0]) / 1000.0
    print(f"\n=== {name} ===")
    print(f"  samples         {len(t)}")
    print(f"  duration        {fmt(dur, ' min', 1)}")
    print(f"  distance        {fmt(dist_km, ' km', 3)}")
    print(f"  mean sample dt  {fmt(np.median(np.diff(t)), ' s', 3)}")
    print(f"  grade range     {fmt(a['grade'].min(), '%')} .. {fmt(a['grade'].max(), '%')}")
    print(f"  grade RMS       {fmt(np.sqrt(np.mean(a['grade']**2)), '%')}")
    print(f"  mean speed      {fmt(np.nanmean(d['speed'])*3.6, ' km/h')}")
    if not a["has_power"]:
        print("  power           absent — physics checks skipped")
        return
    print(f"  mean power      {fmt(np.nanmean(d['power']), ' W')}")

    st = a["steady"]
    keep = st.sum()
    pct = 100.0 * keep / len(t)
    print(f"  steady samples  {keep} ({fmt(pct, '%', 0)} — pedaling, "
          f"v>2 m/s, |a|<0.2 m/s^2)")

    fwd, inv = a["fwd"], a["inv"]
    # All-sample stats kept for context, but the steady ones are the signal.
    print(f"  power resid ALL    bias {fmt(np.nanmean(fwd), ' W')}   "
          f"RMS {fmt(np.sqrt(np.nanmean(fwd**2)), ' W')}")
    print(f"  power resid STEADY bias {fmt(np.nanmean(fwd[st]), ' W')}   "
          f"RMS {fmt(np.sqrt(np.nanmean(fwd[st]**2)), ' W')}   (noise floor)")
    print(f"  speed resid STEADY bias {fmt(np.nanmean(inv[st])*3.6, ' km/h')}   "
          f"RMS {fmt(np.sqrt(np.nanmean(inv[st]**2))*3.6, ' km/h')}   "
          f"(trainer fidelity)")
    crr_e, cda_e, n = a["eff"]
    print(f"  effective Crr   {fmt(crr_e, '', 4)}   (configured {DEFAULT_CRR})  "
          f"[fit on {n} steady samples]")
    print(f"  effective CdA   {fmt(cda_e, ' m^2', 3)}   (configured {DEFAULT_CDA})")
    if a["lag"] is not None:
        L, c = a["lag"]
        print(f"  grade->power lag {fmt(L, ' s', 0)}   (xcorr {fmt(c, '', 2)})")

    print("  grade bin   n     med v      med P")
    for lo, hi, n, mv, mp in grade_bins(d, a):
        lbl = f"{lo:+.0f}..{hi:+.0f}%".replace("+1000000000", "+inf") \
                                       .replace("-1000000000", "-inf")
        print(f"    {lbl:>12} {n:5d}  {fmt(mv,' km/h')}  {fmt(mp,' W')}")


def make_plots(da, db, aa, ab):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n[plots skipped — pip install matplotlib to enable]")
        return
    fig, ax = plt.subplots(3, 1, figsize=(12, 11), sharex=False)

    # 1. grade vs distance
    ax[0].plot(da["dist"] / 1000, aa["grade"], label="Garmin", lw=1)
    ax[0].plot(db["dist"] / 1000, ab["grade"], label="ride_sim", lw=1, alpha=0.8)
    ax[0].set_ylabel("grade (%)"); ax[0].set_xlabel("distance (km)")
    ax[0].legend(); ax[0].set_title("Grade vs distance"); ax[0].grid(alpha=0.3)

    # 2. power resid vs time
    for d, a, lbl in ((da, aa, "Garmin"), (db, ab, "ride_sim")):
        if a["has_power"]:
            ax[1].plot(d["t"] / 60, a["fwd"], label=lbl, lw=0.8, alpha=0.8)
    ax[1].set_ylabel("P_rec - P_model (W)"); ax[1].set_xlabel("time (min)")
    ax[1].axhline(0, color="k", lw=0.5); ax[1].legend()
    ax[1].set_title("Power residual (noise floor)"); ax[1].grid(alpha=0.3)

    # 3. recorded vs solved speed
    for d, a, lbl in ((da, aa, "Garmin"), (db, ab, "ride_sim")):
        if a["has_power"]:
            ax[2].plot(d["t"] / 60, d["speed"] * 3.6, lw=0.8, label=f"{lbl} rec")
            ax[2].plot(d["t"] / 60, a["v_solved"] * 3.6, lw=0.8, alpha=0.6,
                       label=f"{lbl} model")
    ax[2].set_ylabel("speed (km/h)"); ax[2].set_xlabel("time (min)")
    ax[2].legend(fontsize=8); ax[2].set_title("Recorded vs physics-model speed")
    ax[2].grid(alpha=0.3)

    fig.tight_layout()
    out = "ride_analysis.png"
    fig.savefig(out, dpi=110)
    print(f"\n[plot written to {out}]")
    plt.close(fig)


def plot_tradeoff(d, label):
    """Overlay grade at several lookaheads to show dip washout visually."""
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    dist, elev = d["dist"], d["elev"]
    configs = [(10, 30), (20, 30), (40, 30), (60, 30)]
    grades = {la: derive_grade(dist, elev, window_m=la, smooth_m=es)
              for la, es in configs}

    # Find the deepest dip on the short-lookahead trace to zoom on.
    g_ref = grades[10]
    i_dip = int(np.argmin(g_ref))
    d_dip = dist[i_dip] / 1000.0

    fig, ax = plt.subplots(2, 1, figsize=(12, 8))
    for la, _ in configs:
        ax[0].plot(dist / 1000, grades[la], lw=0.8, label=f"lookahead {la} m")
    ax[0].set_title(f"{label}: grade vs distance (elev-smooth 30 m)")
    ax[0].set_ylabel("grade (%)"); ax[0].set_xlabel("distance (km)")
    ax[0].legend(fontsize=8); ax[0].grid(alpha=0.3)

    lo, hi = d_dip - 0.4, d_dip + 0.4
    for la, _ in configs:
        ax[1].plot(dist / 1000, grades[la], lw=1.4, label=f"lookahead {la} m")
    ax[1].set_xlim(lo, hi)
    ax[1].set_title(f"Zoom on deepest dip near {d_dip:.2f} km "
                    f"(note how wider lookahead fills it in)")
    ax[1].set_ylabel("grade (%)"); ax[1].set_xlabel("distance (km)")
    ax[1].legend(fontsize=8); ax[1].grid(alpha=0.3)

    fig.tight_layout()
    out = "grade_tradeoff.png"
    fig.savefig(out, dpi=110)
    print(f"[plot written to {out}]")
    plt.close(fig)


def print_tradeoff(d, label):
    rows, flat = grade_tradeoff(d["dist"], d["elev"])
    pct_flat = 100.0 * flat.mean()
    print(f"\n--- grade pipeline tradeoff: {label}  "
          f"({pct_flat:.0f}% of route is flat reference) ---")
    print("  lookahead  elev-smooth   flat-noise   deepest-dip")
    print("    (m)         (m)          (% RMS)       (%)")
    last_la = None
    for la, es, noise, dip in rows:
        if last_la is not None and la != last_la:
            print()
        last_la = la
        print(f"    {la:4.0f}       {es:5.0f}        {noise:7.2f}      {dip:7.2f}")
    print("  want: LOW flat-noise (no false grade on flats) + "
          "DEEP dip (real descents preserved)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("garmin", help="reference TCX (e.g. Garmin-controlled ride)")
    ap.add_argument("ridesim", help="ride_sim-controlled TCX")
    ap.add_argument("--mass", type=float, default=DEFAULT_MASS_KG)
    ap.add_argument("--crr", type=float, default=DEFAULT_CRR)
    ap.add_argument("--cda", type=float, default=DEFAULT_CDA)
    ap.add_argument("--rho", type=float, default=DEFAULT_RHO)
    ap.add_argument("--window", type=float, default=20.0,
                    help="grade derivation window (m), default 20")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args()

    da = parse_tcx(args.garmin)
    db = parse_tcx(args.ridesim)
    aa = analyze(da, args.mass, args.crr, args.cda, args.rho, args.window)
    ab = analyze(db, args.mass, args.crr, args.cda, args.rho, args.window)

    print(f"\nride_sim ride analysis  (mass={args.mass} kg, Crr={args.crr}, "
          f"CdA={args.cda}, rho={args.rho}, grade window={args.window} m)")
    print_report(da, aa, args.mass)
    print_report(db, ab, args.mass)
    print("\nReads:")
    print("  - power resid RMS  -> lower = smoother/more-consistent control")
    print("  - speed resid RMS  -> lower = trainer delivered the modeled speed")
    print("  - effective Crr/CdA near configured -> trainer matches the model")
    print("  - grade->power lag -> the 'latch at peaks' delay")

    # Grade-pipeline tradeoff — use the Garmin file's real baro as terrain.
    print_tradeoff(da, "Garmin (real baro terrain)")

    if not args.no_plots:
        make_plots(da, db, aa, ab)
        plot_tradeoff(da, "Garmin terrain")


if __name__ == "__main__":
    main()
