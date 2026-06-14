#!/usr/bin/env python3
"""
Ride Simulator — v4.0
======================
Syncs a recorded ride video to live GPS/TCX data from a BLE FTMS smart trainer.

v4 changes:
  - QMediaPlayer replaces MPV: native embedding on macOS and Windows,
    no external DLL/dylib required, supports H.264 and HEVC via hardware decode
  - True immersive fullscreen: 100% display coverage, no title bar or dock
  - Escape / fullscreen button exits fullscreen cleanly
  - Red-ball / Cmd-Q quit works correctly
  - HUD pills configurable: visibility, size, position via Settings dialog
  - Elapsed time pill added
  - Map overlay size/corner configurable

Dependencies:
  pip install PySide6 PySide6-Addons bleak

Run:
  python ride_sim.py
"""

import asyncio
import csv
import json
import math
import os
import random
import socket
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ── Frozen-app base directory ──
if getattr(sys, 'frozen', False):
    _here = Path(sys._MEIPASS)
else:
    _here = Path(__file__).resolve().parent

# ── Platform detection ──
IS_WINDOWS = sys.platform == "win32"
IS_MACOS   = sys.platform == "darwin"

# ── Chromium sandbox fix for frozen apps (Windows only) ──
if getattr(sys, 'frozen', False) and IS_WINDOWS:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from bleak import BleakClient, BleakScanner

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWebEngineWidgets import QWebEngineView


# ─────────────────────────────────────────────────────────────
#  TUNING CONSTANTS
# ─────────────────────────────────────────────────────────────

APP_VERSION          = "0.1.0-beta"

# Beta expiration. Set to a date string "YYYY-MM-DD" at packaging time to
# disable the bundle after that date; leave as None during development. The
# check is a courtesy reminder for testers to update — not a security
# mechanism (any client-side check can be patched out).
BETA_EXPIRES         = "2026-09-01"  # e.g. "2026-09-01"

GATE_START_ON_SPEED  = True
START_SPEED_KMH      = 2.0
START_STABLE_SEC     = 1.0

CONTROL_HZ           = 4.0
DT                   = 1.0 / CONTROL_HZ

SPEED_ALPHA          = 0.20       # EMA smoothing for speed
HR_ALPHA             = 0.05       # EMA smoothing for simulated HR (slow lag)

GRADE_LOOKAHEAD_M    = 12.0
GRADE_CLAMP_PCT      = 15.0
GRADE_ALPHA          = 0.25       # EMA smoothing for grade sent to trainer.
                                  # At 4 Hz this is ~0.9 s time constant (~18 m
                                  # latch at 20 mph) vs ~2.4 s / ~50 m at 0.10.
                                  # Upstream elev-smoothing + lookahead already
                                  # kill grade noise, so the temporal EMA can be
                                  # fast without the trainer hunting. Cuts the
                                  # post-crest "still grinding" lag.
GRADE_SEND_INTERVAL  = 1.0        # seconds between grade writes
GRADE_SEND_THRESHOLD = 0.5        # only send if |Δgrade| > this (%)

# Stream position/speed to ride-sim-world (the Godot DEM world) over UDP so it
# can drive its on-rails camera live. Fire-and-forget localhost datagrams; if
# nothing is listening they're harmless. See ride-sim-world (engine_interface.md).
WORLD_UDP_ENABLED    = True
WORLD_UDP_ADDR       = ("127.0.0.1", 5005)   # ride_sim → world (distance/speed/ghost)
WORLD_UDP_RECV_ADDR  = ("127.0.0.1", 5006)   # world → ride_sim (commands, e.g. pause)

SOFT_ERR_SEC         = 4.0
HARD_SEEK_SEC        = 15.0
SEEK_COOLDOWN_SEC    = 5.0
CRUISE_STEP_PCT      = 0.03
CRUISE_STEP_AGGR_PCT = 0.10   # used when |err| > CRUISE_AGGRESSIVE_ERR_SEC
CRUISE_AGGRESSIVE_ERR_SEC = 8.0

# SYNC_DEBUG: when set in the environment, the ride loop writes a per-tick
# CSV of sync state to ~/ride_sim_sync_debug_<ts>.csv. Use it to diagnose
# why playback rate or seeks drift over time. Off by default; no cost when
# disabled.
SYNC_DEBUG_ENABLED = (
    os.environ.get("SYNC_DEBUG", "").strip().lower()
    not in ("", "0", "false", "no", "off")
)

# SIM speed generator
SIM_SEED             = 1234
SIM_SPEED_SCALE      = 1.00
SIM_NOISE_PCT        = 0.05
SIM_DRIFT_PCT        = 0.10
SIM_DRIFT_PERIOD_SEC = 120.0
SIM_MIN_SPEED_MPS    = 0.6

# Simulated rider physics (used for power/HR model in SIM mode)
RIDER_MASS_KG        = 83.0       # rider (~73) + gravel bike (~10)
CRR                  = 0.004      # rolling resistance coefficient
CD_A                 = 0.35       # drag coefficient × frontal area (m²)
RHO                  = 1.225      # air density (kg/m³)
GRAVITY              = 9.81


# ─────────────────────────────────────────────────────────────
#  BLE UUIDs
# ─────────────────────────────────────────────────────────────

FTMS_SERVICE_UUID                  = "00001826-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA_UUID              = "00002ad2-0000-1000-8000-00805f9b34fb"
FITNESS_MACHINE_CONTROL_POINT_UUID = "00002ad9-0000-1000-8000-00805f9b34fb"

HR_SERVICE_UUID                    = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID                = "00002a37-0000-1000-8000-00805f9b34fb"


# ─────────────────────────────────────────────────────────────
#  Leaflet map HTML  (dark CARTO tiles)
# ─────────────────────────────────────────────────────────────

LEAFLET_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <style>html,body,#map{height:100%;margin:0;padding:0;background:#0d0d14;}</style>
  <link rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin=""/>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin=""></script>
<script>
  const route = ROUTE_POINTS;
  const map = L.map('map',{zoomControl:false});
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
    maxZoom:19, attribution:'&copy; OpenStreetMap contributors &copy; CARTO'
  }).addTo(map);
  const full = L.polyline(route,{weight:3,color:'#444'}).addTo(map);
  map.fitBounds(full.getBounds(),{padding:[15,15]});
  const dot = L.circleMarker(route[0],{radius:8,color:'#ff4444',fillColor:'#ff4444',fillOpacity:1}).addTo(map);
  const ghost = L.circleMarker(route[0],{radius:7,color:'#ffd54f',fillColor:'#ffd54f',fillOpacity:1,weight:2}).addTo(map);
  ghost.setStyle({opacity:0,fillOpacity:0});
  let prog = L.polyline([route[0]],{weight:4,color:'#00e5ff',opacity:0.9}).addTo(map);
  window.setPos=function(a,o){dot.setLatLng([a,o]);}
  window.setProgress=function(j){try{prog.setLatLngs(JSON.parse(j));}catch(e){}}
  window.setGhost=function(a,o,vis){ghost.setLatLng([a,o]);ghost.setStyle({opacity:vis?1:0,fillOpacity:vis?1:0});}
  window._mapReady=true;
</script>
</body></html>
"""

# Overlay map HTML — no scrollbars, no controls, no attribution, square-friendly
OVERLAY_MAP_HTML = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <style>
    html,body,#map{height:100%;width:100%;margin:0;padding:0;
                   background:#0d0d14;overflow:hidden;}
    .leaflet-control-attribution{display:none!important;}
  </style>
  <link rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY="
        crossorigin=""/>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin=""></script>
<script>
  const route = ROUTE_POINTS;
  const map = L.map('map',{
    zoomControl:false,
    attributionControl:false,
    dragging:false,
    scrollWheelZoom:false,
    doubleClickZoom:false,
    boxZoom:false,
    keyboard:false,
    touchZoom:false
  });
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
    maxZoom:19
  }).addTo(map);
  const full = L.polyline(route,{weight:3,color:'#444'}).addTo(map);
  map.fitBounds(full.getBounds(),{padding:[10,10]});
  const dot = L.circleMarker(route[0],{radius:7,color:'#ff4444',fillColor:'#ff4444',fillOpacity:1}).addTo(map);
  const ghost = L.circleMarker(route[0],{radius:6,color:'#ffd54f',fillColor:'#ffd54f',fillOpacity:1,weight:2}).addTo(map);
  ghost.setStyle({opacity:0,fillOpacity:0});
  let prog = L.polyline([route[0]],{weight:3,color:'#00e5ff',opacity:0.9}).addTo(map);

  window.setPos=function(a,o){dot.setLatLng([a,o]);};
  window.setProgress=function(j){try{prog.setLatLngs(JSON.parse(j));}catch(e){}};
  window.setGhost=function(a,o,vis){ghost.setLatLng([a,o]);ghost.setStyle({opacity:vis?1:0,fillOpacity:vis?1:0});};
  window.showFullRoute=function(){
    document.getElementById('map').style.transform='none';
    map.invalidateSize();
    map.fitBounds(full.getBounds(),{padding:[10,10]});
  };
  window.trackPos=function(a,o,heading){
    map.setView([a,o],16,{animate:false});
    document.getElementById('map').style.transform='rotate('+ (-heading) +'deg)';
  };
  window._mapReady=true;
</script>
</body></html>
"""


# ─────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────

# Platform-aware UI font
if IS_MACOS:
    UI_FONT   = "Helvetica Neue"
    MONO_FONT = "Menlo"
else:
    UI_FONT   = "Segoe UI"
    MONO_FONT = "Consolas"

# CSS font-family strings
UI_FONT_CSS   = f'"{UI_FONT}", sans-serif'
MONO_FONT_CSS = f'"{MONO_FONT}", "Courier New", monospace'

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def u16(data, i):
    return int.from_bytes(data[i:i+2], "little", signed=False)


# ─────────────────────────────────────────────────────────────
#  BLE packet parsers
# ─────────────────────────────────────────────────────────────

def parse_ftms_indoor_bike(pkt: bytearray) -> dict:
    """
    Parse FTMS Indoor Bike Data (0x2AD2).
    Walks the flags word to extract whichever fields the trainer sends.
    Returns dict with any of: speed_mps, cadence_rpm, power_w
    """
    out = {}
    if len(pkt) < 4:
        return out
    flags  = u16(pkt, 0)
    offset = 2

    if not (flags & 0x0001):           # bit 0 clear → instant speed present
        if offset + 2 <= len(pkt):
            out["speed_mps"] = (u16(pkt, offset) / 100.0) / 3.6
        offset += 2
    if flags & 0x0002: offset += 2    # avg speed
    if flags & 0x0004:                 # instant cadence
        if offset + 2 <= len(pkt):
            out["cadence_rpm"] = u16(pkt, offset) / 2.0
        offset += 2
    if flags & 0x0008: offset += 2    # avg cadence
    if flags & 0x0010: offset += 3    # total distance (3 bytes)
    if flags & 0x0020: offset += 2    # resistance level
    if flags & 0x0040:                 # instant power
        if offset + 2 <= len(pkt):
            out["power_w"] = int.from_bytes(pkt[offset:offset+2], "little", signed=True)
        offset += 2
    return out


def parse_hr_measurement(pkt: bytearray) -> Optional[int]:
    """
    Parse BLE Heart Rate Measurement (0x2A37).
    Bit 0 of flags: 0 = HR value is uint8, 1 = uint16.
    """
    if len(pkt) < 2:
        return None
    flags = pkt[0]
    if flags & 0x01:
        return u16(pkt, 1) if len(pkt) >= 3 else None
    return pkt[1]


# ─────────────────────────────────────────────────────────────
#  TCX loader
# ─────────────────────────────────────────────────────────────

def load_tcx_route(tcx_path: str):
    tree = ET.parse(tcx_path)
    root = tree.getroot()
    ns   = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
    tps  = root.findall(".//tcx:Trackpoint", ns)
    if not tps:
        raise RuntimeError("No Trackpoint elements found in TCX.")

    time_s: List[float]          = []
    dist_m: List[float]          = []
    elev_m: List[float]          = []
    lat:    List[Optional[float]] = []
    lon:    List[Optional[float]] = []
    t0: Optional[datetime]       = None

    for tp in tps:
        time_el = tp.find("tcx:Time", ns)
        dist_el = tp.find("tcx:DistanceMeters", ns)
        elev_el = tp.find("tcx:AltitudeMeters", ns)
        if time_el is None or dist_el is None:
            continue
        t = datetime.fromisoformat(time_el.text.replace("Z", "+00:00"))
        if t0 is None:
            t0 = t
        time_s.append((t - t0).total_seconds())
        dist_m.append(float(dist_el.text))
        elev_m.append(float(elev_el.text) if elev_el is not None else 0.0)
        lat_el = tp.find("tcx:Position/tcx:LatitudeDegrees", ns)
        lon_el = tp.find("tcx:Position/tcx:LongitudeDegrees", ns)
        lat.append(float(lat_el.text) if lat_el is not None else None)
        lon.append(float(lon_el.text) if lon_el is not None else None)

    if len(time_s) < 10:
        raise RuntimeError("Too few trackpoints in TCX file.")
    d0     = dist_m[0]
    dist_m = [d - d0 for d in dist_m]
    elev_m = _smooth_elev(elev_m, dist_m, half_m=30.0)
    return time_s, dist_m, elev_m, lat, lon


def _smooth_elev(elev_m: List[float], dist_m: List[float],
                 half_m: float = 30.0) -> List[float]:
    """Distance-aware centered boxcar — averages all samples within ±half_m.

    GPS/baro elevation has ±0.5-1 m sample-to-sample noise that becomes
    false grade on flat ground when compute_grade_pct takes a finite
    difference. Smoothing in *metres* (not sample count) removes it
    independent of how densely the TCX is sampled, and a wide window
    here lets compute_grade_pct keep a short lookahead — so real dips and
    climbs (tens of metres wide) survive at full depth while sensor noise
    is averaged out. See scripts/ride_analysis.py for the tradeoff data.
    """
    n = len(elev_m)
    if n < 3:
        return elev_m
    out = [0.0] * n
    for i in range(n):
        lo = i
        while lo > 0 and dist_m[i] - dist_m[lo - 1] <= half_m:
            lo -= 1
        hi = i
        while hi < n - 1 and dist_m[hi + 1] - dist_m[i] <= half_m:
            hi += 1
        out[i] = sum(elev_m[lo:hi + 1]) / (hi - lo + 1)
    return out


# ─────────────────────────────────────────────────────────────
#  Ghost TCX loader  (lightweight — only needs time and distance)
# ─────────────────────────────────────────────────────────────

def load_ghost_tcx(tcx_path: str):
    """
    Load a TCX as a ghost rider.
    Returns (time_s, dist_m) arrays suitable for interp_dist_from_time().
    The ghost rides at the pace recorded in this file.
    """
    time_s, dist_m, _, _, _ = load_tcx_route(tcx_path)
    return time_s, dist_m


def interp_dist_from_time(time_s, dist_m, t: float) -> float:
    """Given elapsed seconds, return how far the ghost has ridden (metres)."""
    if t <= time_s[0]:   return dist_m[0]
    if t >= time_s[-1]:  return dist_m[-1]
    lo, hi = 0, len(time_s) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if time_s[mid] <= t:
            lo = mid
        else:
            hi = mid
    t0, t1 = time_s[lo], time_s[hi]
    d0, d1 = dist_m[lo], dist_m[hi]
    if t1 == t0: return d0
    return d0 + (t - t0) / (t1 - t0) * (d1 - d0)


def ghost_speed_at_time(time_s, dist_m, t: float) -> float:
    """Instantaneous speed of the ghost at elapsed time t (m/s)."""
    lo, hi = 0, len(time_s) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if time_s[mid] <= t:
            lo = mid
        else:
            hi = mid
    dt = time_s[hi] - time_s[lo]
    if dt < 0.01: return 0.0
    return (dist_m[hi] - dist_m[lo]) / dt


# ─────────────────────────────────────────────────────────────
#  Route math
# ─────────────────────────────────────────────────────────────

def _bisect(arr, x: float) -> int:
    lo, hi = 0, len(arr) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if arr[mid] <= x:
            lo = mid
        else:
            hi = mid
    return lo

def interp_time_from_distance(dist_m, time_s, x_m: float) -> float:
    if x_m <= 0:           return time_s[0]
    if x_m >= dist_m[-1]:  return time_s[-1]
    i  = _bisect(dist_m, x_m)
    x0, x1 = dist_m[i], dist_m[i+1]
    t0, t1 = time_s[i],  time_s[i+1]
    if x1 == x0: return t0
    return t0 + (x_m - x0) / (x1 - x0) * (t1 - t0)

def find_index_for_distance(dist_m, x_m: float) -> int:
    if x_m <= 0:           return 0
    if x_m >= dist_m[-1]:  return len(dist_m) - 1
    return _bisect(dist_m, x_m)

def compute_grade_pct(dist_m, elev_m, x_m: float, lookahead: float) -> float:
    i  = find_index_for_distance(dist_m, x_m)
    j  = find_index_for_distance(dist_m, dist_m[i] + lookahead)
    dx = dist_m[j] - dist_m[i]
    return 0.0 if dx < 1e-6 else 100.0 * (elev_m[j] - elev_m[i]) / dx


# ─────────────────────────────────────────────────────────────
#  Activity recorder (TCX export)
# ─────────────────────────────────────────────────────────────

NS_TCX = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
NS_AE2 = "http://www.garmin.com/xmlschemas/ActivityExtension/v2"

class ActivityRecorder:
    """
    Accumulates telemetry samples during a ride and writes a valid
    TCX file on completion.  Samples are taken every ~1 second.
    """
    def __init__(self):
        self._samples: List[dict] = []
        self._start_time: Optional[datetime] = None
        self._last_sample_t: float = 0.0
        self.enabled = False

    def start(self):
        self._samples.clear()
        self._start_time = datetime.now(timezone.utc)
        self._last_sample_t = 0.0

    def record(self, t_sim: float, lat: Optional[float], lon: Optional[float],
               elev_m: float, dist_m: float, speed_mps: float,
               cadence_rpm: float, power_w: float, hr_bpm: float):
        """Called every tick (~4 Hz).  Only records every ~1 second."""
        if not self.enabled or self._start_time is None:
            return
        if t_sim - self._last_sample_t < 1.0:
            return
        self._last_sample_t = t_sim
        self._samples.append({
            "t_sim": t_sim, "lat": lat, "lon": lon,
            "elev_m": elev_m, "dist_m": dist_m, "speed_mps": speed_mps,
            "cadence": int(round(cadence_rpm)),
            "power": int(round(power_w)),
            "hr": int(round(hr_bpm)),
        })

    def save(self, out_path: str) -> bool:
        """Write accumulated samples as a TCX file.  Returns True on success."""
        if not self._samples or self._start_time is None:
            return False
        ET.register_namespace("", NS_TCX)
        ET.register_namespace("ae", NS_AE2)
        root = ET.Element(f"{{{NS_TCX}}}TrainingCenterDatabase")
        acts = ET.SubElement(root, f"{{{NS_TCX}}}Activities")
        act  = ET.SubElement(acts, f"{{{NS_TCX}}}Activity", Sport="Biking")
        ET.SubElement(act, f"{{{NS_TCX}}}Id").text = \
            self._start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        lap = ET.SubElement(act, f"{{{NS_TCX}}}Lap",
                            StartTime=self._start_time.strftime("%Y-%m-%dT%H:%M:%SZ"))
        total_time = self._samples[-1]["t_sim"] - self._samples[0]["t_sim"]
        total_dist = self._samples[-1]["dist_m"] - self._samples[0]["dist_m"]
        ET.SubElement(lap, f"{{{NS_TCX}}}TotalTimeSeconds").text = f"{total_time:.1f}"
        ET.SubElement(lap, f"{{{NS_TCX}}}DistanceMeters").text = f"{total_dist:.1f}"
        track = ET.SubElement(lap, f"{{{NS_TCX}}}Track")
        from datetime import timedelta
        for s in self._samples:
            tp = ET.SubElement(track, f"{{{NS_TCX}}}Trackpoint")
            ts = self._start_time + timedelta(seconds=s["t_sim"])
            ET.SubElement(tp, f"{{{NS_TCX}}}Time").text = \
                ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-4] + "Z"
            if s["lat"] is not None and s["lon"] is not None:
                pos = ET.SubElement(tp, f"{{{NS_TCX}}}Position")
                ET.SubElement(pos, f"{{{NS_TCX}}}LatitudeDegrees").text = f"{s['lat']:.7f}"
                ET.SubElement(pos, f"{{{NS_TCX}}}LongitudeDegrees").text = f"{s['lon']:.7f}"
            ET.SubElement(tp, f"{{{NS_TCX}}}AltitudeMeters").text = f"{s['elev_m']:.1f}"
            ET.SubElement(tp, f"{{{NS_TCX}}}DistanceMeters").text = f"{s['dist_m']:.1f}"
            if s["hr"] > 0:
                hr_el = ET.SubElement(tp, f"{{{NS_TCX}}}HeartRateBpm")
                ET.SubElement(hr_el, f"{{{NS_TCX}}}Value").text = str(s["hr"])
            if s["cadence"] > 0:
                ET.SubElement(tp, f"{{{NS_TCX}}}Cadence").text = str(s["cadence"])
            if s["speed_mps"] > 0 or s["power"] > 0:
                ext = ET.SubElement(tp, f"{{{NS_TCX}}}Extensions")
                tpx = ET.SubElement(ext, f"{{{NS_AE2}}}TPX")
                if s["speed_mps"] > 0:
                    ET.SubElement(tpx, f"{{{NS_AE2}}}Speed").text = \
                        f"{s['speed_mps']:.3f}"
                if s["power"] > 0:
                    ET.SubElement(tpx, f"{{{NS_AE2}}}Watts").text = str(s["power"])
        try:
            ET.indent(root, space="  ")
            ET.ElementTree(root).write(out_path, encoding="unicode",
                                       xml_declaration=True)
            print(f"Activity saved: {out_path}  ({len(self._samples)} points, "
                  f"{total_dist/1000:.2f} km, {total_time/60:.1f} min)")
            return True
        except Exception as e:
            print(f"Failed to save activity: {e}")
            return False


# ─────────────────────────────────────────────────────────────
#  SIM telemetry generator
# ─────────────────────────────────────────────────────────────

def robust_local_speed(dist_m, time_s, idx: int, window: int = 30) -> float:
    n = len(dist_m)
    for r in range(1, 4):
        i0, i1 = max(0, idx - r), min(n - 1, idx + r)
        dx, dt = dist_m[i1] - dist_m[i0], time_s[i1] - time_s[i0]
        if dt > 1e-6 and dx > 1e-3:
            return dx / dt
    for k in range(1, window + 1):
        j = min(n - 1, idx + k)
        dx, dt = dist_m[j] - dist_m[idx], time_s[j] - time_s[idx]
        if dt > 1e-6 and dx > 1e-3:
            return dx / dt
    return 0.0


def sim_power_watts(speed_mps: float, grade_pct: float) -> float:
    """Simple power model: gravity + rolling + aero."""
    if speed_mps < 0.5:
        return 0.0
    g_force  = RIDER_MASS_KG * GRAVITY * (grade_pct / 100.0)
    rolling  = RIDER_MASS_KG * GRAVITY * CRR
    aero     = 0.5 * RHO * CD_A * speed_mps ** 2
    return max(0.0, (g_force + rolling + aero) * speed_mps)


def sim_cadence_rpm(speed_mps: float, grade_pct: float) -> float:
    """
    Rough cadence model: typical riders spin 85-95 rpm on flat,
    drop ~5 rpm per 2% grade as they push harder gears uphill.
    """
    if speed_mps < 0.5:
        return 0.0
    base = 90.0 - clamp(grade_pct, -5.0, 10.0) * 0.8
    return clamp(base + random.uniform(-2.0, 2.0), 60.0, 110.0)


def make_sim_speed_fn(dist_m, time_s, state=None):
    rng = random.Random(SIM_SEED)
    def fn(idx: int, t_sim: float) -> float:
        base  = robust_local_speed(dist_m, time_s, idx)
        drift = SIM_DRIFT_PCT * math.sin(2.0 * math.pi * t_sim / SIM_DRIFT_PERIOD_SEC)
        noise = rng.uniform(-SIM_NOISE_PCT, SIM_NOISE_PCT)
        scale = SIM_SPEED_SCALE
        if state is not None:
            with state.lock:
                scale *= state.sim_speed_scale
        return max(SIM_MIN_SPEED_MPS, base * scale * (1.0 + drift + noise))
    return fn


# ─────────────────────────────────────────────────────────────
#  FTMS grade control
# ─────────────────────────────────────────────────────────────

async def try_request_control(client: BleakClient) -> bool:
    try:
        await client.write_gatt_char(
            FITNESS_MACHINE_CONTROL_POINT_UUID, bytes([0x00]), response=True)
        return True
    except Exception:
        return False

async def try_set_sim_grade(client: BleakClient, grade_pct: float) -> bool:
    grade_pct  = clamp(grade_pct, -GRADE_CLAMP_PCT, GRADE_CLAMP_PCT)
    grade_001  = int(round(grade_pct * 100))
    payload    = bytearray([0x11])
    payload   += int(0).to_bytes(2, "little", signed=True)
    payload   += grade_001.to_bytes(2, "little", signed=True)
    payload   += bytes([0, 0])
    try:
        await client.write_gatt_char(
            FITNESS_MACHINE_CONTROL_POINT_UUID, payload, response=True)
        return True
    except Exception:
        return False

async def find_ble_device(service_uuid: str, timeout: float = 6.0):
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    best, best_rssi = None, -999
    for device, adv in devices.values():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if service_uuid.lower() in uuids and adv.rssi > best_rssi:
            best_rssi = adv.rssi
            best      = device
    return best


# ─────────────────────────────────────────────────────────────
#  Shared state
# ─────────────────────────────────────────────────────────────

class SharedState:
    def __init__(self):
        self.lock      = threading.Lock()
        self.stop_event = threading.Event()

        # ── Controls (GUI → worker) ──
        self.base_rate            = 1.00
        self.strategy             = "cruise"
        self.kp                   = 0.08
        self.deadband             = 0.25
        self.min_rate             = 0.50
        self.max_rate             = 2.00
        self.send_grade_to_trainer = False
        self.grade_lookahead_m    = GRADE_LOOKAHEAD_M
        self.video_offset_sec     = 0.0
        self.position_bump_m      = 0.0
        self.video_offset_adj     = 0.0
        self.imperial             = False
        self.video_lock           = False  # world/map mode: stop the video-rate
                                            # controller so position drives map +
                                            # UDP world directly (no sync hunting)
        self.sim_speed_scale      = 1.0    # debug: scale SIM speed (0.25–16×)

        # ── Telemetry (worker → GUI) ──
        self.virtual_dist_m       = 0.0
        self.total_dist_m         = 0.0
        self.speed_mps_smoothed   = 0.0
        self.cadence_rpm          = 0.0
        self.power_w              = 0.0
        self.hr_bpm               = 0.0
        self.grade_pct            = 0.0

        # ── Sync state (worker → GUI) ──
        self.video_t              = 0.0
        self.target_video_t       = 0.0
        self.err_s                = 0.0
        self.rate                 = 1.0
        self.route_index          = 0
        self.started              = False
        self.status               = "Initialising…"
        self.ble_status           = ""
        self.hr_status            = ""

        # ── User scrubber (GUI → worker) ──
        self.seek_to_dist_m       = -1.0

        # ── HR source flag ──
        self.hr_from_ble          = False

        # ── Ghost / virtual partner ──
        self.ghost_dist_m         = 0.0
        self.ghost_speed_mps      = 0.0
        self.ghost_gap_m          = 0.0
        self.ghost_name           = ""
        self.ghost_active         = False
        self.ghost_time_s         = None
        self.ghost_dist_m_arr     = None

        # ── Activity recorder ──
        self.activity_recorder    = ActivityRecorder()

        # ── Ride timing ──
        self.ride_start_time      = None   # set when ride starts (time.time())

        # ── Map overlay mode: 0=bottom panel, 1=overlay full, 2=overlay tracking ──
        self.map_mode             = 0
        self.map_opacity          = 0.6    # 0.0–1.0

        # ── HUD configuration (persisted in settings) ──
        # pill_cfg: list of dicts {key, visible, size}  size: 0=S 1=M 2=L
        self.hud_pill_cfg = [
            {"key": "SPEED",    "visible": True,  "size": 1},
            {"key": "CADENCE",  "visible": True,  "size": 1},
            {"key": "POWER",    "visible": True,  "size": 1},
            {"key": "HR",       "visible": True,  "size": 1},
            {"key": "GRADE",    "visible": True,  "size": 1},
            {"key": "DISTANCE", "visible": True,  "size": 1},
            {"key": "ELAPSED",  "visible": True,  "size": 1},
            {"key": "SYNC",     "visible": False, "size": 0},
        ]
        # Map overlay: corner 0=TR 1=TL 2=BR 3=BL, size_pct=% of video height
        self.map_corner   = 0
        self.map_size_pct = 28
        # Pill layout: 0 = bottom row (default), 1 = stacked right, 2 = stacked left
        self.pill_layout  = 0

        # ── Pacer cube overlay (cube-overlay branch) ──
        # Wireframe cube drawn on the optical axis at depth = pacer_gap_m metres.
        # Forward-locked video assumed: cube is always straight ahead.
        # World convention here: +y up, camera at origin, road plane at y = -camera_height_m.
        # pacer_height_m is the vertical *offset* from ground-resting (0 = cube on road).
        self.pacer_visible     = True
        self.pacer_gap_m       = 5.0    # depth ahead of camera, metres
        self.pacer_size_m      = 1.0    # cube edge length, metres
        self.pacer_height_m    = 0.0    # 0 = bottom on road, +Δ lifts cube up
        self.camera_height_m   = 1.0    # bar-mounted GoPro: ~1 m above road
        self.tangent_visible   = True   # red wireframe line down the road centre
        # When True AND ghost_active, cube is drawn at ghost_gap_m instead of
        # pacer_gap_m — turning the cube into a visual proxy for the ghost rider
        # at the actual gap distance (clamped to [0.5, 100] m).
        self.cube_follows_ghost = False
        # Calibrated 2026-05-10 against GS010004 GoPro Player export (Max 2 360 reframe):
        # central-region effective rectilinear h_fov is 118.81° (angular residual
        # ~0.5° med within r<0.45 of half-width). Player's POLY projection diverges
        # past r≈0.45, so cube/tangent geometry should stay near the optical axis.
        self.video_fov_h_deg   = 118.8
        self._tune_message     = ""     # transient HUD readout after a hotkey adjust
        self._tune_message_t   = 0.0    # time.monotonic() of last update

        # ── User pause + ghost gap preservation ──
        # user_paused: Space-toggle. When True (or auto-paused for low speed),
        # the video pauses and the ghost stops advancing. Resume continues both
        # without ghost having drifted forward.
        # ghost_t_offset_s: shifts the ghost's effective time so the gap to the
        # rider is preserved across pauses and timeline scrubs. Effective
        # ghost time = t_sim - ghost_t_offset_s.
        # elapsed_frozen_s: when not None, OverlayWidget uses this for the
        # elapsed pill instead of (now - ride_start_time). Set on pause-start
        # so the displayed timer freezes during pause without per-tick aliasing
        # against the worker's 4 Hz update rate.
        self.user_paused       = False
        self.ghost_t_offset_s  = 0.0
        self.elapsed_frozen_s  = None

        # ── Route geometry (for curve-following tangent dashes) ──
        # lat/lon arrays match dist_m by index. None until main() populates after
        # load_tcx_route. OverlayWidget reads these to render the road centerline
        # ahead of the rider as a dashed curve through space, not a straight line.
        self.route_lat_arr     = None    # numpy float array, NaN where missing
        self.route_lon_arr     = None    # numpy float array, NaN where missing
        self.route_dist_arr    = None    # numpy float array, cumulative metres


# ─────────────────────────────────────────────────────────────
#  Qt signal bridge
# ─────────────────────────────────────────────────────────────

class WorkerSignals(QObject):
    request_play        = Signal()
    request_pause       = Signal()
    request_seek        = Signal(float)
    request_rate        = Signal(float)
    request_video_seek  = Signal(float)


# ─────────────────────────────────────────────────────────────
#  Core ride loop
# ─────────────────────────────────────────────────────────────

async def run_ride_loop(
    state: SharedState,
    signals: WorkerSignals,
    time_s, dist_m, elev_m,
    get_telemetry,
    ble_client=None,
):
    total_dist = dist_m[-1]
    with state.lock:
        state.total_dist_m = total_dist

    virtual_dist     = 0.0
    smoothed         = 0.0
    smoothed_hr      = 70.0
    start_ok_since   = None
    last_seek        = 0.0
    last_grade_send  = 0.0
    last_grade_value = None
    smoothed_grade   = None
    # Pause state: True iff video is currently paused (auto for low speed OR
    # user-pause via Space). pause_started_t_sim records when the pause began,
    # so on resume we can roll ghost_t_offset_s forward by the pause duration
    # and the ghost picks up exactly where it left off.
    is_paused             = False
    pause_started_t_sim   = None

    signals.request_pause.emit()

    with state.lock:
        state.started = not GATE_START_ON_SPEED
        state.status  = "Running (SIM)" if not GATE_START_ON_SPEED else "Waiting for speed…"

    t0       = time.time()
    last_now = t0

    sync_dbg_file = None
    sync_dbg_csv  = None
    if SYNC_DEBUG_ENABLED:
        ts_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        sync_dbg_path = Path.home() / f"ride_sim_sync_debug_{ts_tag}.csv"
        sync_dbg_file = open(sync_dbg_path, "w", newline="")
        sync_dbg_csv  = csv.writer(sync_dbg_file)
        sync_dbg_csv.writerow([
            "t_sim", "started", "paused",
            "video_t", "target_video_t", "err_s",
            "smoothed_mps", "virtual_dist_m",
            "strategy", "action", "rate",
        ])
        print(f"[SYNC_DEBUG] writing trace to {sync_dbg_path}", flush=True)

    # Optional live feed to ride-sim-world. Non-blocking + swallowed errors so a
    # missing/closed socket can never disturb the ride loop.
    world_sock = None
    world_recv = None
    if WORLD_UDP_ENABLED:
        try:
            world_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            world_sock.setblocking(False)
        except OSError:
            world_sock = None
        try:
            # Back-channel: the world sends commands here (e.g. spacebar pause
            # from the Godot window). Best-effort — a bind clash just disables it.
            world_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            world_recv.setblocking(False)
            world_recv.bind(WORLD_UDP_RECV_ADDR)
        except OSError:
            world_recv = None

    def _emit_world(dist_val, speed_val):
        if world_sock is None:
            return
        msg = {"distance_m": round(dist_val, 2), "speed_mps": round(speed_val, 3)}
        with state.lock:
            if state.ghost_active and state.ghost_dist_m is not None:
                msg["ghost_distance_m"] = round(state.ghost_dist_m, 2)
        try:
            world_sock.sendto(json.dumps(msg).encode(), WORLD_UDP_ADDR)
        except OSError:
            pass

    def _poll_world_commands():
        # Drain back-channel packets; a "toggle_pause" flips user_paused exactly
        # like the in-app Space hotkey, so Space works from the Godot window too.
        if world_recv is None:
            return
        while True:
            try:
                data, _ = world_recv.recvfrom(512)
            except OSError:
                return
            try:
                cmd = json.loads(data.decode()).get("cmd")
            except (ValueError, UnicodeDecodeError):
                continue
            if cmd == "toggle_pause":
                with state.lock:
                    state.user_paused = not state.user_paused

    while True:
        await asyncio.sleep(DT)

        with state.lock:
            if state.stop_event.is_set():
                break

        _poll_world_commands()

        now      = time.time()
        dt_real  = now - last_now
        last_now = now
        t_sim    = now - t0

        idx = find_index_for_distance(dist_m, virtual_dist)
        tel = await get_telemetry(t_sim, idx)

        raw_speed = tel.get("speed_mps", 0.0)
        smoothed  = (1.0 - SPEED_ALPHA) * smoothed + SPEED_ALPHA * raw_speed

        with state.lock:
            hr_from_ble = state.hr_from_ble
        if not hr_from_ble and "hr_bpm" in tel:
            raw_hr      = tel["hr_bpm"]
            smoothed_hr = (1.0 - HR_ALPHA) * smoothed_hr + HR_ALPHA * raw_hr

        with state.lock:
            started = state.started

        if GATE_START_ON_SPEED and not started:
            if smoothed * 3.6 >= START_SPEED_KMH:
                if start_ok_since is None:
                    start_ok_since = now
                elif (now - start_ok_since) >= START_STABLE_SEC:
                    with state.lock:
                        state.started = True
                        state.status  = "Running"
                        state.ride_start_time = time.time()
                    signals.request_play.emit()
                    started = True
            else:
                start_ok_since = None

        with state.lock:
            lookahead = state.grade_lookahead_m
        grade = clamp(
            compute_grade_pct(dist_m, elev_m, virtual_dist, lookahead),
            -GRADE_CLAMP_PCT, GRADE_CLAMP_PCT
        )
        with state.lock:
            state.speed_mps_smoothed = smoothed
            state.cadence_rpm        = tel.get("cadence_rpm", 0.0)
            state.power_w            = tel.get("power_w",     0.0)
            if not state.hr_from_ble:
                state.hr_bpm         = smoothed_hr
            state.grade_pct          = grade

        if not started:
            continue

        STOP_THRESHOLD_MPS = 0.3
        with state.lock:
            user_paused = state.user_paused
        should_pause = user_paused or (smoothed < STOP_THRESHOLD_MPS)

        if should_pause and not is_paused:
            signals.request_pause.emit()
            is_paused = True
            pause_started_t_sim = t_sim
            with state.lock:
                # Snapshot elapsed at pause start. OverlayWidget reads this
                # preferentially while non-None, so the displayed timer
                # freezes immediately rather than walking with wall clock
                # until resume.
                if state.ride_start_time is not None:
                    state.elapsed_frozen_s = time.time() - state.ride_start_time
                else:
                    state.elapsed_frozen_s = 0.0
        elif not should_pause and is_paused:
            signals.request_play.emit()
            is_paused = False
            if pause_started_t_sim is not None:
                pause_dur = t_sim - pause_started_t_sim
                with state.lock:
                    state.ghost_t_offset_s += pause_dur
                    # Advance ride_start_time so post-resume elapsed picks up
                    # at the frozen value rather than jumping forward by the
                    # pause duration.
                    if state.ride_start_time is not None:
                        state.ride_start_time += pause_dur
                    state.elapsed_frozen_s = None
                pause_started_t_sim = None

        with state.lock:
            req = state.seek_to_dist_m
        if req >= 0:
            new_virtual = clamp(req, 0.0, total_dist)
            # Preserve ghost gap across the seek: find the ghost timestamp that
            # places the ghost at (new_virtual + previous_gap), and update the
            # ghost-time offset so subsequent ghost lookups produce that point.
            with state.lock:
                if (state.ghost_active and state.ghost_time_s is not None
                        and state.ghost_dist_m_arr is not None):
                    saved_gap = state.ghost_dist_m - virtual_dist
                    g_dist_arr = state.ghost_dist_m_arr
                    g_time_arr = state.ghost_time_s
                    target_g_dist = clamp(
                        new_virtual + saved_gap,
                        float(g_dist_arr[0]),
                        float(g_dist_arr[-1]),
                    )
                    t_ghost_target = interp_time_from_distance(
                        g_dist_arr, g_time_arr, target_g_dist)
                    # We want ghost(t_sim - offset) ≈ target_g_dist.
                    # If currently paused, ghost is frozen at (pause_started_t_sim
                    # - offset_at_pause_start); use pause_started_t_sim instead
                    # of t_sim so the ghost stays at the new position when the
                    # user resumes.
                    t_ref = (pause_started_t_sim
                             if (is_paused and pause_started_t_sim is not None)
                             else t_sim)
                    state.ghost_t_offset_s = t_ref - t_ghost_target
            virtual_dist = new_virtual
            # Re-arm after a user scrub: lift the low-speed auto-pause so seeking
            # away from the finish (or any stopped tail) starts riding again.
            # Only the speed term is bumped — an explicit Space pause still holds,
            # and telemetry takes back over within a tick if the spot is slow.
            smoothed = max(smoothed, STOP_THRESHOLD_MPS * 3.0)
            target_route_t = interp_time_from_distance(dist_m, time_s, virtual_dist)
            with state.lock:
                offset = state.video_offset_sec
                # Publish the scrubbed position now (and push it to the world) so
                # the map marker / scrubber don't rubber-band back to the old
                # end value while auto-paused — the seek block returns early,
                # before the normal state update at the bottom of the loop.
                state.virtual_dist_m = virtual_dist
            _emit_world(virtual_dist, 0.0)
            signals.request_video_seek.emit(offset + target_route_t)
            with state.lock:
                state.seek_to_dist_m = -1.0
                # Bound the trackpoint discontinuity around a scrub: skip
                # recording for ~2 s past this seek so the TCX has a clear
                # gap rather than a smooth-looking teleport. Strava/Garmin
                # handle gaps without crediting the missing distance.
                state.activity_recorder._last_sample_t = t_sim + 2.0
            last_seek = time.time()
            continue

        # While paused, do not advance virtual_dist or send rate updates.
        # Zero out live telemetry pills (speed/cadence/power) so the HUD
        # shows the rider isn't pedaling — without this, SIM mode keeps
        # spinning out non-zero values from the simulated profile and BLE
        # mode would briefly hold its last-reported values.
        # The ghost worker block below also skips its update so ghost stays
        # frozen at its last position — its effective time is t_sim - offset
        # which keeps drifting, but on resume we'll add the pause duration to
        # the offset so ghost picks up exactly where it left off.
        if is_paused:
            with state.lock:
                state.speed_mps_smoothed = 0.0
                state.cadence_rpm        = 0.0
                state.power_w            = 0.0
            _emit_world(virtual_dist, 0.0)
            continue

        with state.lock:
            bump = state.position_bump_m
            if bump != 0.0:
                state.position_bump_m = 0.0
        virtual_dist = clamp(virtual_dist + smoothed * dt_real + bump, 0.0, total_dist)

        target_route_t = interp_time_from_distance(dist_m, time_s, virtual_dist)
        with state.lock:
            offset = state.video_offset_sec + state.video_offset_adj
        target_video_t = offset + target_route_t

        with state.lock:
            base       = state.base_rate
            strategy   = state.strategy
            kp         = state.kp
            deadband   = state.deadband
            min_r      = state.min_rate
            max_r      = state.max_rate
            send_grade = state.send_grade_to_trainer
            video_lock = state.video_lock
            video_t    = state.video_t

        err = target_video_t - video_t

        if video_lock:
            # World/map mode: position already drives the map and the UDP world
            # directly (virtual_dist integration above). Don't run the video-rate
            # controller — with no route-matched video to sync, it only hunts and
            # thrashes the screen. Hold the video at base rate; never seek.
            signals.request_rate.emit(base)
            _dbg_act  = "locked"
            _dbg_rate = base
        elif video_t < 0.1:
            continue
        elif abs(err) > HARD_SEEK_SEC and (now - last_seek) > SEEK_COOLDOWN_SEC:
            # Symmetric hard-seek: previously this branch only fired when err
            # was positive (video lagging), so cruise mode (with its gentle
            # 3% step) could never recover from a video-ahead drift. Negative
            # err now seeks the video backward to target_video_t.
            signals.request_seek.emit(target_video_t)
            signals.request_rate.emit(base)
            last_seek = now
            _dbg_act  = "seek-back" if err < 0 else "seek-fwd"
            _dbg_rate = base
        else:
            if abs(err) < deadband:
                new_rate = clamp(base, min_r, max_r)
            elif strategy == "proportional":
                new_rate = clamp(base + kp * err, min_r, max_r)
            else:
                # Adaptive cruise step: when |err| is large, take bigger
                # rate jumps so cruise converges fast enough to avoid the
                # 15s hard-seek (and its visible backward jump in cruise
                # mode). Inside CRUISE_AGGRESSIVE_ERR_SEC the gentle 3%
                # step still wins so steady-state is calm.
                step     = (CRUISE_STEP_AGGR_PCT
                            if abs(err) > CRUISE_AGGRESSIVE_ERR_SEC
                            else CRUISE_STEP_PCT)
                new_rate = clamp(
                    base * (1.0 + step if err > 0 else 1.0 - step), min_r, max_r)
            signals.request_rate.emit(new_rate)
            _dbg_act  = "rate"
            _dbg_rate = new_rate

        if sync_dbg_csv is not None:
            sync_dbg_csv.writerow([
                f"{t_sim:.3f}", started, is_paused,
                f"{video_t:.3f}", f"{target_video_t:.3f}", f"{err:+.3f}",
                f"{smoothed:.3f}", f"{virtual_dist:.2f}",
                strategy, _dbg_act, f"{_dbg_rate:.4f}",
            ])
            sync_dbg_file.flush()

        if smoothed_grade is None:
            smoothed_grade = grade
        else:
            smoothed_grade += GRADE_ALPHA * (grade - smoothed_grade)

        if ble_client and send_grade:
            if (now - last_grade_send) > GRADE_SEND_INTERVAL and (
                last_grade_value is None
                or abs(smoothed_grade - last_grade_value) > GRADE_SEND_THRESHOLD
            ):
                await try_set_sim_grade(ble_client, smoothed_grade)
                last_grade_send  = now
                last_grade_value = smoothed_grade

        ridx = find_index_for_distance(dist_m, virtual_dist)
        with state.lock:
            state.virtual_dist_m   = virtual_dist
            state.target_video_t   = target_video_t
            state.err_s            = err
            state.route_index      = ridx

            if state.ghost_active and state.ghost_time_s is not None:
                t_ghost = t_sim - state.ghost_t_offset_s
                g_dist = interp_dist_from_time(
                    state.ghost_time_s, state.ghost_dist_m_arr, t_ghost)
                g_spd  = ghost_speed_at_time(
                    state.ghost_time_s, state.ghost_dist_m_arr, t_ghost)
                state.ghost_dist_m    = g_dist
                state.ghost_speed_mps = g_spd
                state.ghost_gap_m     = g_dist - virtual_dist

        # At the finish virtual_dist is clamped at total_dist but `smoothed` is
        # still non-zero (SIM keeps generating speed); emitting that would make
        # the world dead-reckon past the end each frame and snap back on every
        # packet (a 1–2 m forward/back jitter). Emit 0 so the world holds still.
        _emit_world(virtual_dist, 0.0 if virtual_dist >= total_dist - 0.01 else smoothed)

        # ── Activity recording ──
        # Interpolate lat/lon from route for current position
        rec_lat, rec_lon = None, None
        if ridx < len(elev_m):
            # Find the lat/lon arrays via state (they're not passed to the loop)
            # We use the route arrays passed to the function instead
            pass   # lat/lon recorded via signals below

        # Skip recording while paused: the recorded TCX should reflect only
        # real pedaling time/distance — paused intervals would inflate total
        # time and produce flat trackpoints. Combined with the post-seek
        # _last_sample_t bump in the seek handler, this keeps the recorded
        # ride honest across both pauses and timeline scrubs.
        if not is_paused:
            with state.lock:
                rec = state.activity_recorder
            if rec.enabled:
                _elev = elev_m[ridx] if ridx < len(elev_m) else 0.0
                rec.record(t_sim, None, None, _elev, virtual_dist,
                           smoothed, tel.get("cadence_rpm", 0.0),
                           tel.get("power_w", 0.0),
                           state.hr_bpm if hr_from_ble else smoothed_hr)

        if virtual_dist >= total_dist:
            with state.lock:
                state.status = "Ride complete! 🎉"
                # Freeze the elapsed pill and zero the live pills at end-of-ride.
                if state.ride_start_time is not None and state.elapsed_frozen_s is None:
                    state.elapsed_frozen_s = time.time() - state.ride_start_time
                state.speed_mps_smoothed = 0.0
                state.cadence_rpm        = 0.0
                state.power_w            = 0.0
            signals.request_rate.emit(base)
            # Don't break out of the loop — keep it alive so a scrub back from the
            # finish is still processed (the seek block at the top re-arms the
            # ride). Just hold here without advancing.
            continue

    if world_sock is not None:
        world_sock.close()
    if world_recv is not None:
        world_recv.close()


# ─────────────────────────────────────────────────────────────
#  SIM worker
# ─────────────────────────────────────────────────────────────

async def worker_sim(state, signals, time_s, dist_m, elev_m):
    sim_speed = make_sim_speed_fn(dist_m, time_s, state)

    async def get_telemetry(t_sim, idx):
        spd = sim_speed(idx, t_sim)
        with state.lock:
            grd = state.grade_pct
        pwr = sim_power_watts(spd, grd)
        cad = sim_cadence_rpm(spd, grd)
        target_hr = 60 + clamp(pwr / 4.0, 0, 110)
        return {"speed_mps": spd, "power_w": pwr, "cadence_rpm": cad, "hr_bpm": target_hr}

    with state.lock:
        state.ble_status = "SIM MODE"
        state.hr_status  = "SIM HR"
        state.status     = "SIM MODE — waiting for speed gate…"

    await run_ride_loop(state, signals, time_s, dist_m, elev_m, get_telemetry)


# ─────────────────────────────────────────────────────────────
#  BLE HR worker
# ─────────────────────────────────────────────────────────────

async def worker_hr(state: SharedState):
    with state.lock:
        state.hr_status = "Scanning for HR monitor…"

    dev = await find_ble_device(HR_SERVICE_UUID, timeout=8.0)
    if not dev:
        with state.lock:
            state.hr_status = "No HR monitor found"
        return

    with state.lock:
        state.hr_status = f"HR: {dev.name}"

    def on_hr(sender, data: bytearray):
        bpm = parse_hr_measurement(data)
        if bpm is not None:
            with state.lock:
                state.hr_bpm     = float(bpm)
                state.hr_from_ble = True

    try:
        async with BleakClient(dev.address) as client:
            await client.start_notify(HR_MEASUREMENT_UUID, on_hr)
            while True:
                await asyncio.sleep(1.0)
                with state.lock:
                    if state.status.startswith("Ride complete"):
                        break
    except Exception as e:
        with state.lock:
            state.hr_status = f"HR disconnected: {e}"


# ─────────────────────────────────────────────────────────────
#  BLE FTMS worker
# ─────────────────────────────────────────────────────────────

async def worker_ble(state, signals, time_s, dist_m, elev_m):
    with state.lock:
        state.ble_status = "Scanning for trainer…"

    ftms_dev, _ = await asyncio.gather(
        find_ble_device(FTMS_SERVICE_UUID, timeout=6.0),
        asyncio.sleep(0),
    )

    if not ftms_dev:
        with state.lock:
            state.ble_status = "No FTMS trainer found"
            state.status     = "No trainer found — wake trainer and retry"
        return

    with state.lock:
        state.ble_status = f"Connecting to {ftms_dev.name}…"

    last_data: dict       = {}
    last_packet_t         = {"t": None}

    def on_notify(sender, data: bytearray):
        parsed = parse_ftms_indoor_bike(data)
        if parsed:
            last_data.update(parsed)
            last_packet_t["t"] = time.time()

    async def get_telemetry(t_sim, idx):
        stale = last_packet_t["t"] is None or (time.time() - last_packet_t["t"] > 2.0)
        if stale:
            return {}
        return {k: v for k, v in last_data.items()}

    async with BleakClient(ftms_dev.address) as client:
        await client.start_notify(INDOOR_BIKE_DATA_UUID, on_notify)

        cp_ack = {"last": None}
        def on_cp(sender, data: bytearray):
            if len(data) >= 3 and data[0] == 0x80:
                cp_ack["last"] = data[2]

        try:
            await client.start_notify(FITNESS_MACHINE_CONTROL_POINT_UUID, on_cp)
        except Exception:
            pass

        for attempt in range(2):
            try:
                await client.write_gatt_char(
                    FITNESS_MACHINE_CONTROL_POINT_UUID, bytes([0x00]), response=True)
                await asyncio.sleep(0.3)
                break
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(0.2)

        with state.lock:
            state.ble_status = f"● {ftms_dev.name}"
            state.status     = "BLE connected — waiting for speed…"

        await run_ride_loop(
            state, signals, time_s, dist_m, elev_m,
            get_telemetry, ble_client=client
        )


# ─────────────────────────────────────────────────────────────
#  Worker thread entry
# ─────────────────────────────────────────────────────────────

async def worker_main(state, signals, time_s, dist_m, elev_m, sim_mode: bool):
    if sim_mode:
        await worker_sim(state, signals, time_s, dist_m, elev_m)
    else:
        await asyncio.gather(
            worker_ble(state, signals, time_s, dist_m, elev_m),
            worker_hr(state),
        )

def start_worker_thread(state, signals, time_s, dist_m, elev_m, sim_mode: bool):
    def run():
        asyncio.run(worker_main(state, signals, time_s, dist_m, elev_m, sim_mode))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


# ─────────────────────────────────────────────────────────────
#  Overlay widget
# ─────────────────────────────────────────────────────────────

class OverlayWidget(QtWidgets.QWidget):
    """
    Transparent HUD overlay painted directly over the video widget.
    Pills are configurable: visibility, size (S/M/L), and order.
    Map thumbnail is painted in the chosen corner at configurable size.
    """
    MARGIN   = 12
    GAP      = 6

    # Per-size pill dimensions  [S, M, L]
    PILL_SIZES = [
        {"w": 72,  "h": 62,  "r": 8,  "lbl": 8,  "val": 16, "unit": 8},
        {"w": 96,  "h": 76,  "r": 10, "lbl": 9,  "val": 22, "unit": 9},
        {"w": 120, "h": 90,  "r": 12, "lbl": 10, "val": 28, "unit": 10},
    ]

    BG      = QtGui.QColor(0,   0,   0,   160)
    ACCENT  = QtGui.QColor(0,   229, 255)
    BRIGHT  = QtGui.QColor(255, 255, 255)
    DIM     = QtGui.QColor(140, 140, 160)
    WARN    = QtGui.QColor(255, 180, 0)
    RED     = QtGui.QColor(255, 80,  80)

    GHOST_BAR_W  = 600
    GHOST_BAR_H  = 36
    GHOST_DOT_R  = 10
    GHOST_MAX_M  = 300
    GHOST_COLOR  = QtGui.QColor(255, 140, 0)
    RIDER_COLOR  = QtGui.QColor(0,   229, 255)

    def __init__(self, state: SharedState, sim_mode: bool, parent=None):
        super().__init__(parent)
        self.state    = state
        self.sim_mode = sim_mode
        self.map_pixmap = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def refresh(self):
        self.update()

    def _pill_value(self, key: str, snap: dict) -> tuple:
        """Return (label, value_str, unit_str) for a given pill key."""
        imp = snap["imperial"]
        k   = key
        if k == "SPEED":
            v = snap["speed"] * (2.23694 if imp else 3.6)
            return "SPEED", f"{v:.1f}", "mph" if imp else "km/h"
        if k == "CADENCE":
            return "CADENCE", f"{snap['cadence']:.0f}", "rpm"
        if k == "POWER":
            return "POWER", f"{snap['power']:.0f}", "W"
        if k == "HR":
            return "HR", f"{snap['hr']:.0f}", "bpm"
        if k == "GRADE":
            return "GRADE", f"{snap['grade']:+.1f}", "%"
        if k == "DISTANCE":
            pct = (snap["dist"] / snap["total"] * 100) if snap["total"] > 0 else 0
            if imp:
                d = snap["dist"] / 1609.344
                t = snap["total"] / 1609.344
                return "DIST", f"{d:.2f}", f"/{t:.1f}mi {pct:.0f}%"
            else:
                d = snap["dist"] / 1000.0
                t = snap["total"] / 1000.0
                return "DIST", f"{d:.2f}", f"/{t:.1f}km {pct:.0f}%"
        if k == "ELAPSED":
            secs = int(snap["elapsed"])
            h, rem = divmod(secs, 3600)
            m, s   = divmod(rem, 60)
            if h > 0:
                return "ELAPSED", f"{h}:{m:02d}:{s:02d}", "h:m:s"
            else:
                return "ELAPSED", f"{m}:{s:02d}", "min:s"
        if k == "SYNC":
            return "SYNC", f"{snap['err']:+.2f}", "s err"
        return key, "—", ""

    def _pill_accent(self, key: str, snap: dict) -> QtGui.QColor:
        if key == "HR" and snap["hr"] > 160:
            return self.RED
        if key == "GRADE" and abs(snap["grade"]) > 5:
            return self.WARN
        if key == "SYNC" and abs(snap["err"]) > 2:
            return self.WARN
        return self.ACCENT

    def paintEvent(self, event):
        with self.state.lock:
            snap = {
                "speed":    self.state.speed_mps_smoothed,
                "cadence":  self.state.cadence_rpm,
                "power":    self.state.power_w,
                "hr":       self.state.hr_bpm,
                "grade":    self.state.grade_pct,
                "dist":     self.state.virtual_dist_m,
                "total":    self.state.total_dist_m,
                "err":      self.state.err_s,
                "started":  self.state.started,
                "status":   self.state.status,
                "ble":      self.state.ble_status,
                "hr_st":    self.state.hr_status,
                "ghost_active": self.state.ghost_active,
                "ghost_gap_m":  self.state.ghost_gap_m,
                "ghost_spd":    self.state.ghost_speed_mps,
                "ghost_name":   self.state.ghost_name,
                "imperial":     self.state.imperial,
                "pill_cfg":     list(self.state.hud_pill_cfg),
                "pill_layout":  self.state.pill_layout,
                "map_mode":     self.state.map_mode,
                "map_opacity":  self.state.map_opacity,
                "map_corner":   self.state.map_corner,
                "map_size_pct": self.state.map_size_pct,
                "ride_start":   self.state.ride_start_time,
                "elapsed_frozen_s": self.state.elapsed_frozen_s,
                "pacer_visible":   self.state.pacer_visible,
                "pacer_gap_m":     self.state.pacer_gap_m,
                "pacer_size_m":    self.state.pacer_size_m,
                "pacer_height_m":  self.state.pacer_height_m,
                "camera_height_m": self.state.camera_height_m,
                "tangent_visible": self.state.tangent_visible,
                "cube_follows_ghost": self.state.cube_follows_ghost,
                "route_lat":       self.state.route_lat_arr,
                "route_lon":       self.state.route_lon_arr,
                "route_dist":      self.state.route_dist_arr,
                "fov_h_deg":       self.state.video_fov_h_deg,
                "tune_msg":        self.state._tune_message,
                "tune_t":          self.state._tune_message_t,
                "video_t":         self.state.video_t,
            }

        # Elapsed time. While paused, the worker stashes a frozen snapshot
        # in state.elapsed_frozen_s so this pill stops advancing immediately
        # rather than walking with wall clock until resume.
        if snap["elapsed_frozen_s"] is not None:
            snap["elapsed"] = snap["elapsed_frozen_s"]
        elif snap["started"] and snap["ride_start"] is not None:
            snap["elapsed"] = time.time() - snap["ride_start"]
        else:
            snap["elapsed"] = 0.0

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # BLE / HR status line (top-left, small)
        parts = [x for x in [snap["ble"], snap["hr_st"]] if x]
        if parts:
            p.setFont(QtGui.QFont(UI_FONT, 10))
            p.setPen(self.DIM)
            p.drawText(self.MARGIN, self.MARGIN + 14, "  |  ".join(parts))

        if not snap["started"]:
            txt = "Waiting for speed…"
            f   = QtGui.QFont(UI_FONT, 15, QtGui.QFont.Bold)
            p.setFont(f)
            p.setPen(self.WARN)
            fm = QtGui.QFontMetrics(f)
            p.drawText((w - fm.horizontalAdvance(txt)) // 2, h // 2, txt)
            p.end()
            return

        pills_cfg = [c for c in snap["pill_cfg"] if c["visible"]]
        layout    = snap["pill_layout"]   # 0=bottom row, 1=stacked right, 2=stacked left

        # ── Pill positions ──
        # Compute (px, py, sz) for each visible pill based on layout mode.
        positions = []
        if pills_cfg:
            if layout == 0:
                total_pw = sum(self.PILL_SIZES[c["size"]]["w"] for c in pills_cfg)
                total_pw += self.GAP * (len(pills_cfg) - 1)
                x = (w - total_pw) // 2
                base_y = h - self.MARGIN
                for cfg in pills_cfg:
                    sz = self.PILL_SIZES[cfg["size"]]
                    positions.append((cfg, x, base_y - sz["h"], sz))
                    x += sz["w"] + self.GAP
            else:
                max_pw = max(self.PILL_SIZES[c["size"]]["w"] for c in pills_cfg)
                if layout == 1:
                    x0 = w - self.MARGIN - max_pw
                else:
                    x0 = self.MARGIN
                y = self.MARGIN + 30   # leave room for status / map corner
                for cfg in pills_cfg:
                    sz = self.PILL_SIZES[cfg["size"]]
                    px = x0 + (max_pw - sz["w"])   # right-align in column for layout 1
                    if layout == 2:
                        px = x0                    # left-align for layout 2
                    positions.append((cfg, px, y, sz))
                    y += sz["h"] + self.GAP

        # ── Progress bar ──
        if snap["total"] > 0:
            pct   = snap["dist"] / snap["total"]
            bar_h = 4
            if layout == 0 and pills_cfg:
                max_ph = max(self.PILL_SIZES[c["size"]]["h"] for c in pills_cfg)
                bar_y  = h - max_ph - self.MARGIN - bar_h - 2
            else:
                bar_y  = h - self.MARGIN - bar_h
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QBrush(QtGui.QColor(40, 40, 60)))
            p.drawRect(0, bar_y, w, bar_h)
            p.setBrush(QtGui.QBrush(self.ACCENT))
            p.drawRect(0, bar_y, int(w * pct), bar_h)

        # ── Pills ──
        for cfg, px, py, sz in positions:
            pw, ph = sz["w"], sz["h"]
            rect = QtCore.QRectF(px, py, pw, ph)

            lbl_str, val_str, unit_str = self._pill_value(cfg["key"], snap)
            accent = self._pill_accent(cfg["key"], snap)

            p.setBrush(QtGui.QBrush(self.BG))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(rect, sz["r"], sz["r"])

            p.setBrush(QtGui.QBrush(accent))
            p.drawRect(QtCore.QRectF(px, py, pw, 3))

            p.setFont(QtGui.QFont(UI_FONT, sz["lbl"]))
            p.setPen(self.DIM)
            p.drawText(QtCore.QRectF(px, py + 4, pw, sz["lbl"] + 4),
                       Qt.AlignCenter, lbl_str)

            p.setFont(QtGui.QFont(UI_FONT, sz["val"], QtGui.QFont.Bold))
            p.setPen(self.BRIGHT)
            p.drawText(QtCore.QRectF(px, py + sz["lbl"] + 6, pw, sz["val"] + 4),
                       Qt.AlignCenter, val_str)

            p.setFont(QtGui.QFont(UI_FONT, sz["unit"]))
            p.setPen(self.DIM)
            p.drawText(QtCore.QRectF(px, py + ph - sz["unit"] - 6, pw, sz["unit"] + 4),
                       Qt.AlignCenter, unit_str)

        # ── Ghost bar ──
        if snap["ghost_active"]:
            self._draw_ghost_bar(p, w, h, snap)

        # ── Map overlay ──
        if snap["map_mode"] > 0 and self.map_pixmap is not None:
            map_sz  = int(min(h, w) * snap["map_size_pct"] / 100.0)
            corner  = snap["map_corner"]
            mx = (w - map_sz - self.MARGIN) if corner in (0, 2) else self.MARGIN
            ghost_offset = 55 if snap["ghost_active"] else 20
            my = (self.MARGIN + ghost_offset) if corner in (0, 1) else (h - map_sz - self.MARGIN)

            p.setOpacity(snap["map_opacity"])
            p.drawPixmap(mx, my, map_sz, map_sz, self.map_pixmap)
            p.setOpacity(1.0)
            p.setPen(QtGui.QPen(QtGui.QColor(100, 100, 100, 120), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRect(mx, my, map_sz, map_sz)
            # OSM/CARTO attribution — required by ODbL §4.3 and CARTO ToS.
            # The Leaflet attribution control is suppressed on the overlay map
            # (it would clash with the dark HUD), so render it here in HUD style.
            attr_font = QtGui.QFont(self.font())
            attr_font.setPointSize(8)
            p.setFont(attr_font)
            p.setPen(QtGui.QColor(200, 200, 200, 200))
            p.drawText(mx + 4, my + map_sz - 4, "© OpenStreetMap contributors © CARTO")

        # ── Tangent line + pacer cube ──
        # Draw the road-tangent reference first so the cube sits visually on top of it.
        if snap["tangent_visible"]:
            self._draw_tangent_line(p, w, h, snap)
        if snap["pacer_visible"]:
            self._draw_cube(p, w, h, snap)
        self._draw_tune_msg(p, w, h, snap)

        p.end()

    def _draw_ghost_bar(self, p, w, h, snap):
        gap_m = snap["ghost_gap_m"]
        imp   = snap["imperial"]
        g_spd  = snap["ghost_spd"] * (2.23694 if imp else 3.6)
        my_spd = snap["speed"]     * (2.23694 if imp else 3.6)
        spd_u  = "mph" if imp else "km/h"
        name   = snap["ghost_name"] or "Ghost"

        bw, bh = self.GHOST_BAR_W, self.GHOST_BAR_H
        x0 = (w - bw) // 2
        y0 = self.MARGIN

        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 140)))
        p.drawRoundedRect(QtCore.QRectF(x0, y0, bw, bh), 8, 8)

        cx = x0 + bw // 2
        p.setBrush(QtGui.QBrush(self.RIDER_COLOR))
        p.drawEllipse(QtCore.QPointF(cx, y0 + bh / 2), self.GHOST_DOT_R, self.GHOST_DOT_R)

        scale  = (bw / 2 - self.GHOST_DOT_R - 4) / self.GHOST_MAX_M
        offset = clamp(gap_m * scale,
                       -(bw/2 - self.GHOST_DOT_R - 4), (bw/2 - self.GHOST_DOT_R - 4))
        gx = cx + offset
        p.setBrush(QtGui.QBrush(self.GHOST_COLOR))
        p.drawEllipse(QtCore.QPointF(gx, y0 + bh / 2), self.GHOST_DOT_R, self.GHOST_DOT_R)

        p.setPen(QtGui.QPen(QtGui.QColor(120, 120, 120, 160), 2))
        p.drawLine(QtCore.QPointF(min(cx,gx)+self.GHOST_DOT_R, y0+bh/2),
                   QtCore.QPointF(max(cx,gx)-self.GHOST_DOT_R, y0+bh/2))

        f = QtGui.QFont(UI_FONT, 16, QtGui.QFont.Bold)
        p.setFont(f)
        if imp:
            gv = gap_m / 1609.344
            gt = f"{gv:+.2f} mi" if abs(gv) >= 0.1 else f"{gap_m * 3.28084:+.0f} ft"
        else:
            gt = f"{gap_m/1000:+.2f} km" if abs(gap_m) >= 1000 else f"{gap_m:+.0f} m"
        direction = "ahead" if gap_m > 0 else "behind"
        label = f"{name}  {gt} {direction}   {g_spd:.1f} vs {my_spd:.1f} {spd_u}"
        p.setPen(self.GHOST_COLOR if gap_m > 5 else
                 self.RIDER_COLOR if gap_m < -5 else self.DIM)
        fm = QtGui.QFontMetrics(f)
        p.drawText(int(x0 + (bw - fm.horizontalAdvance(label))//2), int(y0 + bh + 14), label)

    # ── Pacer cube (cube-overlay branch) ──
    # Camera convention: +x right, +y up, +z toward viewer (right-handed).
    # Visible points have z < 0. Forward-locked video = cube on the -z axis.

    PACER_COLOR   = QtGui.QColor(0, 229, 255, 220)
    TANGENT_COLOR = QtGui.QColor(255, 80,  80, 230)

    def _project(self, x, y, z, w, h, fov_h_deg):
        """Pinhole projection. Returns (u, v, behind)."""
        if z >= -0.01:
            return 0.0, 0.0, True
        f_px = (w / 2.0) / math.tan(math.radians(fov_h_deg) / 2.0)
        u = w / 2.0 + f_px * (x / -z)
        v = h / 2.0 - f_px * (y / -z)
        return u, v, False

    def _draw_cube(self, p, w, h, snap):
        gap   = snap["pacer_gap_m"]
        # In ghost-follow mode, swap in the ghost's gap so the cube *is* the
        # ghost. Hide if the ghost is alongside or behind the rider
        # (negative/None gap), and cap the far end at 100 m to avoid sub-pixel
        # specks.
        if snap.get("cube_follows_ghost") and snap.get("ghost_active"):
            g = snap.get("ghost_gap_m")
            if g is None or g < 0.5:
                return
            gap = min(g, 100.0)
        size  = snap["pacer_size_m"]
        cam_h = snap["camera_height_m"]
        cy    = -cam_h + size / 2.0 + snap["pacer_height_m"]
        fov   = snap["fov_h_deg"]
        if gap < 0.5:
            return
        s = size / 2.0
        cx, cz = 0.0, -gap
        verts = []
        for dx in (-s, s):
            for dy in (-s, s):
                for dz in (-s, s):
                    verts.append((cx + dx, cy + dy, cz + dz))
        edges = [(0,1),(0,2),(0,4),(1,3),(1,5),(2,3),(2,6),(3,7),
                 (4,5),(4,6),(5,7),(6,7)]
        pts = [self._project(vx, vy, vz, w, h, fov) for (vx, vy, vz) in verts]
        if all(b for *_, b in pts):
            return
        pen = QtGui.QPen(self.PACER_COLOR, 2.0)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for i, j in edges:
            ui, vi, bi = pts[i]
            uj, vj, bj = pts[j]
            if bi or bj:
                continue
            p.drawLine(QtCore.QPointF(ui, vi), QtCore.QPointF(uj, vj))

    # Dashed centreline: dashes are anchored to fixed positions along the
    # *route* (TCX trackpoints), so they scroll toward the camera as the rider
    # advances and bend through curves the way the road itself does. If route
    # geometry isn't loaded, falls back to straight-ahead dashes along the
    # camera optical axis.
    DASH_PERIOD_M = 0.6     # distance between dash starts (along route)
    DASH_LEN_M    = 0.3     # length of each dash (along route)
    DASH_NEAR_M   = 2.0     # closest dash start, metres ahead of rider
    DASH_FAR_M    = 50.0    # farthest dash start, metres ahead of rider

    @staticmethod
    def _route_xy_at(d_along, route_dist, route_lat, route_lon, lat0, mpd_lat, mpd_lon):
        """Interpolate (east_m, north_m) on the route at distance d_along (metres
        along route from start). Returns (east_m, north_m) or (nan, nan) if the
        endpoints have no fix.
        """
        import numpy as _np
        if d_along <= route_dist[0]:
            lat = route_lat[0]
            lon = route_lon[0]
        elif d_along >= route_dist[-1]:
            lat = route_lat[-1]
            lon = route_lon[-1]
        else:
            i = int(_np.searchsorted(route_dist, d_along, side="right")) - 1
            i = max(0, min(i, len(route_dist) - 2))
            d0, d1 = route_dist[i], route_dist[i + 1]
            la0, la1 = route_lat[i], route_lat[i + 1]
            lo0, lo1 = route_lon[i], route_lon[i + 1]
            if not (math.isfinite(la0) and math.isfinite(la1)
                    and math.isfinite(lo0) and math.isfinite(lo1)):
                return float("nan"), float("nan")
            f = (d_along - d0) / max(d1 - d0, 1e-9)
            lat = la0 + f * (la1 - la0)
            lon = lo0 + f * (lo1 - lo0)
        if not (math.isfinite(lat) and math.isfinite(lon)):
            return float("nan"), float("nan")
        return (lon * mpd_lon), (lat * mpd_lat)  # caller subtracts origin

    def _draw_tangent_line(self, p, w, h, snap):
        """Dashed red centreline traced along the recorded TCX route, ahead of
        the rider's current position. Camera-locked rendering — assumes the
        video stream is forward-stabilized (e.g. GoPro Player export).
        """
        fov   = snap["fov_h_deg"]
        cam_h = snap["camera_height_m"]
        y     = -cam_h
        s_world = float(snap.get("dist") or 0.0)

        pen = QtGui.QPen(self.TANGENT_COLOR, 2.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)

        # Static perpendicular ticks at fixed camera-relative depths — a depth
        # ruler that stays put while the dashes flow past.
        def proj_camera(wx, wz):
            return self._project(wx, y, wz, w, h, fov)

        tick_half = 0.5
        for dist in (5, 10, 15, 20, 30, 50):
            u_l, v_l, b_l = proj_camera(-tick_half, -dist)
            u_r, v_r, b_r = proj_camera(+tick_half, -dist)
            if not (b_l or b_r):
                p.drawLine(QtCore.QPointF(u_l, v_l), QtCore.QPointF(u_r, v_r))

        route_dist = snap.get("route_dist")
        route_lat  = snap.get("route_lat")
        route_lon  = snap.get("route_lon")
        if route_dist is None or route_lat is None or route_lon is None:
            self._draw_tangent_dashes_straight(p, w, h, proj_camera, s_world)
            return
        if len(route_dist) < 2:
            self._draw_tangent_dashes_straight(p, w, h, proj_camera, s_world)
            return

        import numpy as _np
        # Rider position + heading via interpolation.
        s_rider = max(route_dist[0], min(s_world, route_dist[-1]))
        i_r = int(_np.searchsorted(route_dist, s_rider, side="right")) - 1
        i_r = max(0, min(i_r, len(route_dist) - 2))
        f_r = (s_rider - route_dist[i_r]) / max(
            route_dist[i_r + 1] - route_dist[i_r], 1e-9)
        lat_r = route_lat[i_r] + f_r * (route_lat[i_r + 1] - route_lat[i_r])
        lon_r = route_lon[i_r] + f_r * (route_lon[i_r + 1] - route_lon[i_r])
        if not (math.isfinite(lat_r) and math.isfinite(lon_r)):
            self._draw_tangent_dashes_straight(p, w, h, proj_camera, s_world)
            return

        # Heading at rider: bearing computed over a ±2 m window to smooth jitter
        # from individual GPS samples.
        s_back = max(route_dist[0], s_rider - 2.0)
        s_fwd  = min(route_dist[-1], s_rider + 2.0)

        def lat_lon_at(s):
            i = int(_np.searchsorted(route_dist, s, side="right")) - 1
            i = max(0, min(i, len(route_dist) - 2))
            ff = (s - route_dist[i]) / max(route_dist[i + 1] - route_dist[i], 1e-9)
            return (route_lat[i] + ff * (route_lat[i + 1] - route_lat[i]),
                    route_lon[i] + ff * (route_lon[i + 1] - route_lon[i]))

        la_b, lo_b = lat_lon_at(s_back)
        la_f, lo_f = lat_lon_at(s_fwd)
        if not all(math.isfinite(v) for v in (la_b, lo_b, la_f, lo_f)):
            self._draw_tangent_dashes_straight(p, w, h, proj_camera, s_world)
            return
        lat1 = math.radians(la_b)
        lat2 = math.radians(la_f)
        dlon = math.radians(lo_f - lo_b)
        by = math.sin(dlon) * math.cos(lat2)
        bx = (math.cos(lat1) * math.sin(lat2)
              - math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
        heading = math.atan2(by, bx)  # bearing from north, clockwise

        # Flat-earth metres-per-degree at rider latitude.
        mpd_lat = 111320.0
        mpd_lon = 111320.0 * math.cos(math.radians(lat_r))

        def world_to_cam_xz(d_along):
            la, lo = lat_lon_at(d_along)
            if not (math.isfinite(la) and math.isfinite(lo)):
                return None
            de = (lo - lon_r) * mpd_lon
            dn = (la - lat_r) * mpd_lat
            forward = de * math.sin(heading) + dn * math.cos(heading)
            right   = de * math.cos(heading) - dn * math.sin(heading)
            return right, -forward  # camera frame: x=right, z=-forward

        # Dashes scroll because their world-distance is fixed; rider's s_rider
        # advances. Phase the dash grid so it's stable in route coordinates.
        period = self.DASH_PERIOD_M
        phase  = s_rider % period
        d_local = self.DASH_NEAR_M + ((-phase) % period)
        while d_local <= self.DASH_FAR_M:
            d_along_a = s_rider + d_local
            d_along_b = s_rider + d_local + self.DASH_LEN_M
            if d_along_b > route_dist[-1]:
                break
            xz_a = world_to_cam_xz(d_along_a)
            xz_b = world_to_cam_xz(d_along_b)
            if xz_a is None or xz_b is None:
                d_local += period
                continue
            ux, uz = xz_a
            vx, vz = xz_b
            u0, v0, b0 = self._project(ux, y, uz, w, h, fov)
            u1, v1, b1 = self._project(vx, y, vz, w, h, fov)
            if not (b0 or b1):
                p.drawLine(QtCore.QPointF(u0, v0), QtCore.QPointF(u1, v1))
            d_local += period

    def _draw_tangent_dashes_straight(self, p, w, h, proj_camera, s_world):
        """Fallback: straight-ahead dashes if route data isn't available."""
        period = self.DASH_PERIOD_M
        phase  = s_world % period
        d = self.DASH_NEAR_M + ((-phase) % period)
        while d <= self.DASH_FAR_M:
            d_far = d + self.DASH_LEN_M
            u0, v0, b0 = proj_camera(0.0, -d)
            u1, v1, b1 = proj_camera(0.0, -d_far)
            if not (b0 or b1):
                p.drawLine(QtCore.QPointF(u0, v0), QtCore.QPointF(u1, v1))
            d += period

    def _draw_tune_msg(self, p, w, h, snap):
        msg = snap.get("tune_msg") or ""
        t   = snap.get("tune_t") or 0.0
        if not msg or t <= 0.0:
            return
        age = time.monotonic() - t
        if age > 2.0:
            return
        # Solid for 1.5 s, fade out over the next 0.5 s.
        alpha = 255 if age < 1.5 else int(255 * max(0.0, 1.0 - (age - 1.5) / 0.5))
        f = QtGui.QFont(UI_FONT, 12, QtGui.QFont.Bold)
        p.setFont(f)
        fm = QtGui.QFontMetrics(f)
        tw = fm.horizontalAdvance(msg)
        bg = QtGui.QColor(0, 0, 0, min(160, alpha))
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        pad = 6
        rx = w - tw - 2 * pad - self.MARGIN
        ry = self.MARGIN + 30
        p.drawRoundedRect(QtCore.QRectF(rx, ry, tw + 2 * pad, fm.height() + 4), 6, 6)
        p.setPen(QtGui.QColor(0, 229, 255, alpha))
        p.drawText(int(rx + pad), int(ry + fm.ascent() + 2), msg)




# ─────────────────────────────────────────────────────────────
#  Video panel  (QMediaPlayer + QVideoWidget — native embedding)
# ─────────────────────────────────────────────────────────────

class VideoPanel(QtWidgets.QWidget):
    """
    Hosts QVideoWidget (hardware-decoded, natively embedded on macOS + Windows)
    with a transparent OverlayWidget painted on top.

    Sync signals from the worker thread drive seek() and setPlaybackRate().
    Rate changes are clamped and smoothed to avoid audio artifacts.
    """

    # Largest rate deviation from 1× before we just seek instead
    MAX_RATE_SEEK_THRESHOLD = 8.0

    def __init__(self, state: SharedState, sim_mode: bool,
                 video_path: str, signals: WorkerSignals, parent=None):
        super().__init__(parent)
        self.state       = state
        self._video_path = video_path

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Video widget / media player, OR a placeholder for a virtual-world ride.
        # In virtual mode there's no footage — the Godot window is the view — so we
        # skip the player entirely. _player is None and every player method guards
        # on it, so the sync signals (which still fire) are harmless no-ops.
        if video_path:
            self.video_widget = QVideoWidget(self)
            self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
            layout.addWidget(self.video_widget)

            self._player = QMediaPlayer(self)
            self._audio  = QAudioOutput(self)
            self._audio.setVolume(1.0)
            self._player.setAudioOutput(self._audio)
            self._player.setVideoOutput(self.video_widget)
            # Larger buffer reduces audio underruns with Qt Multimedia FFmpeg backend
            self._player.setSource(QtCore.QUrl.fromLocalFile(str(Path(video_path).resolve())))
            self._player.pause()
            self._player.positionChanged.connect(self._on_position)
            self._player.errorOccurred.connect(
                lambda err, msg: print(f"[player] {err}: {msg}"))
        else:
            self._player = None
            placeholder = QtWidgets.QLabel(
                "🌐  Virtual world\n\nThe view is in the Godot window.\n"
                "HUD and map here drive from your position.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(
                "color:#39a07a; font-size:15px; background:#08080d;")
            layout.addWidget(placeholder)

        # ── Overlay: must be a top-level Tool window, not a child widget.
        # QVideoWidget uses a native surface (AVSampleBufferDisplayLayer /
        # Direct3D swap chain) that renders above all Qt child widgets,
        # making child-based overlays invisible. A frameless Tool window
        # positioned over the video panel is the reliable cross-platform fix.
        self.overlay = OverlayWidget(state, sim_mode, parent=None)
        self.overlay.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.overlay.setAttribute(Qt.WA_TranslucentBackground)
        self.overlay.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.overlay.setAttribute(Qt.WA_ShowWithoutActivating)

        # Connect worker signals
        signals.request_play.connect(self._play)
        signals.request_pause.connect(self._pause)
        signals.request_seek.connect(self._seek)
        signals.request_video_seek.connect(self._seek)
        signals.request_rate.connect(self._set_rate)

    def _on_position(self, pos_ms: int):
        with self.state.lock:
            self.state.video_t = pos_ms / 1000.0

    def _play(self):
        if self._player is None:
            return
        self._player.play()

    def _pause(self):
        if self._player is None:
            return
        self._player.pause()

    def _seek(self, t_sec: float):
        if self._player is None:
            return
        self._player.setPosition(int(t_sec * 1000))

    def _set_rate(self, rate: float):
        if self._player is None:
            return
        rate = clamp(rate, 0.5, 2.0)
        # Suppress tiny rate changes — Qt Multimedia flushes audio buffers
        # on every setPlaybackRate call, causing ~500ms stutter each time.
        # Only apply if the change is meaningful (>1% from current).
        current = self._player.playbackRate()
        if abs(rate - current) > 0.01:
            self._player.setPlaybackRate(rate)

    def _sync_overlay(self):
        if not self.isVisible():
            return
        tl  = self.mapToGlobal(QtCore.QPoint(0, 0))
        geo = QtCore.QRect(tl.x(), tl.y(), self.width(), self.height())
        self.overlay.setGeometry(geo)
        self.overlay.show()
        self.overlay.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_overlay()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_overlay()

    def showEvent(self, event):
        super().showEvent(event)
        QtCore.QTimer.singleShot(50, self._sync_overlay)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.overlay.hide()

    def set_aspect_fill(self, fill: bool):
        """fill=True for fullscreen (stretch to 100%), False for windowed (letterbox)."""
        if self._player is None:
            return
        mode = Qt.IgnoreAspectRatio if fill else Qt.KeepAspectRatio
        self.video_widget.setAspectRatioMode(mode)

    def terminate(self):
        """Clean shutdown."""
        try:
            self._player.stop()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
#  Map widget
# ─────────────────────────────────────────────────────────────

class MapWidget(QWebEngineView):
    def __init__(self, lat_list, lon_list):
        super().__init__()
        route = [[la, lo] for la, lo in zip(lat_list, lon_list)
                 if la is not None and lo is not None]
        if len(route) < 2:
            raise RuntimeError("Not enough GPS points for map.")
        self._ready = False
        self.setHtml(LEAFLET_HTML.replace("ROUTE_POINTS", str(route)))
        self.loadFinished.connect(lambda ok: setattr(self, "_ready", bool(ok)))

    def set_position(self, lat, lon):
        if self._ready:
            self.page().runJavaScript(
                f"if(window._mapReady)setPos({lat},{lon});")

    def set_progress(self, pts):
        if self._ready:
            self.page().runJavaScript(
                f"if(window._mapReady)setProgress({json.dumps(pts)});")

    def set_ghost_position(self, lat, lon, visible: bool):
        if self._ready:
            self.page().runJavaScript(
                f"if(window._mapReady)setGhost({lat},{lon},{'true' if visible else 'false'});")


class OverlayMapWidget(QWebEngineView):
    """
    Dedicated offscreen map for overlay grabs.
    Sized to a fixed square (e.g. 300×300), never shown in any layout.
    Avoids flashing/scrollbar issues caused by grabbing the visible map.
    """
    OVERLAY_SIZE = 300

    def __init__(self, lat_list, lon_list, parent=None):
        super().__init__(parent)
        route = [[la, lo] for la, lo in zip(lat_list, lon_list)
                 if la is not None and lo is not None]
        if len(route) < 2:
            route = [[0, 0], [0, 0]]
        self._ready = False
        self._route_json = json.dumps(route)
        sz = self.OVERLAY_SIZE
        self.setFixedSize(sz, sz)
        # Position offscreen so it never flashes
        self.move(-sz - 100, -sz - 100)
        self.setHtml(
            OVERLAY_MAP_HTML.replace("ROUTE_POINTS", self._route_json))
        self.loadFinished.connect(self._on_loaded)
        # Must be shown (even offscreen) for .grab() to render
        self.show()

    def _on_loaded(self, ok: bool):
        self._ready = True

    def _js(self, js: str):
        if self._ready:
            self.page().runJavaScript(js)

    def set_position(self, lat, lon):
        self._js(f"setPos({lat},{lon});")

    def set_progress(self, pts):
        self._js(f"setProgress({json.dumps(pts)});")

    def set_ghost_position(self, lat, lon, visible: bool):
        self._js(f"setGhost({lat},{lon},{'true' if visible else 'false'});")

    def show_full_route(self):
        self._js("showFullRoute();")

    def track_position(self, lat, lon, heading):
        self._js(f"trackPos({lat},{lon},{heading:.1f});")

    def grab_pixmap(self):
        """Grab the current rendering as a QPixmap."""
        if self._ready:
            return self.grab()
        return None


# ─────────────────────────────────────────────────────────────
#  Settings dialog (PID / sync tuning — rarely changed)
# ─────────────────────────────────────────────────────────────

class SettingsDialog(QtWidgets.QDialog):
    """Settings: HUD pills, map overlay, sync/PID."""
    SIZE_LABELS   = ["S", "M", "L"]
    CORNER_LABELS = ["Top-Right", "Top-Left", "Bottom-Right", "Bottom-Left"]

    def __init__(self, state: SharedState, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setStyleSheet(DARK_STYLESHEET)
        self.state = state

        with state.lock:
            pill_cfg    = [dict(c) for c in state.hud_pill_cfg]
            pill_layout = state.pill_layout
            map_corner  = state.map_corner
            map_size    = state.map_size_pct
            map_opacity = state.map_opacity
            kp_i        = int(state.kp * 100)
            db_i        = int(state.deadband * 100)
            mnr_i       = int(state.min_rate * 100)
            mxr_i       = int(state.max_rate * 100)
            strategy    = state.strategy
            lookahead_m = int(state.grade_lookahead_m)

        tabs = QtWidgets.QTabWidget()
        lay  = QtWidgets.QVBoxLayout(self)
        lay.addWidget(tabs)

        # ── Tab 1: HUD Pills ──
        hud_tab = QtWidgets.QWidget()
        hud_lay = QtWidgets.QVBoxLayout(hud_tab)
        hint = QtWidgets.QLabel(
            "Toggle pills on/off and choose size. "
            "Drag rows to reorder (top of list = first on screen).")
        hint.setWordWrap(True)
        hud_lay.addWidget(hint)

        layout_row = QtWidgets.QHBoxLayout()
        layout_row.addWidget(QtWidgets.QLabel("Layout:"))
        self._pill_layout_combo = QtWidgets.QComboBox()
        self._pill_layout_combo.addItems(["Bottom row", "Stacked right", "Stacked left"])
        self._pill_layout_combo.setCurrentIndex(pill_layout)
        layout_row.addWidget(self._pill_layout_combo)
        layout_row.addStretch()
        hud_lay.addLayout(layout_row)

        self._pill_list = QtWidgets.QListWidget()
        self._pill_list.setDragDropMode(QtWidgets.QListWidget.InternalMove)
        self._pill_list.setDefaultDropAction(Qt.MoveAction)
        self._pill_list.setSpacing(2)

        for cfg in pill_cfg:
            item = QtWidgets.QListWidgetItem()
            item.setData(Qt.UserRole, cfg["key"])
            self._pill_list.addItem(item)
            row_w = QtWidgets.QWidget()
            row_l = QtWidgets.QHBoxLayout(row_w)
            row_l.setContentsMargins(4, 2, 4, 2)
            vis_cb = QtWidgets.QCheckBox(cfg["key"])
            vis_cb.setChecked(cfg["visible"])
            vis_cb.setFixedWidth(110)
            row_l.addWidget(vis_cb)
            size_cb = QtWidgets.QComboBox()
            size_cb.addItems(self.SIZE_LABELS)
            size_cb.setCurrentIndex(cfg["size"])
            size_cb.setFixedWidth(60)
            row_l.addWidget(size_cb)
            row_l.addStretch()
            drag_lbl = QtWidgets.QLabel("= drag")
            drag_lbl.setStyleSheet("color:#555; font-size:10px;")
            row_l.addWidget(drag_lbl)
            item.setSizeHint(row_w.sizeHint())
            self._pill_list.setItemWidget(item, row_w)

        hud_lay.addWidget(self._pill_list)
        tabs.addTab(hud_tab, "HUD Pills")

        # ── Tab 2: Map Overlay ──
        map_tab = QtWidgets.QWidget()
        map_lay = QtWidgets.QFormLayout(map_tab)
        self._corner_combo = QtWidgets.QComboBox()
        self._corner_combo.addItems(self.CORNER_LABELS)
        self._corner_combo.setCurrentIndex(map_corner)
        map_lay.addRow("Corner:", self._corner_combo)

        self._map_size_s = QtWidgets.QSlider(Qt.Horizontal)
        self._map_size_s.setRange(15, 50)
        self._map_size_s.setValue(map_size)
        self._map_size_v = QtWidgets.QLabel(f"{map_size}%")
        self._map_size_s.valueChanged.connect(lambda v: self._map_size_v.setText(f"{v}%"))
        sz_row = QtWidgets.QHBoxLayout()
        sz_row.addWidget(self._map_size_s, 1)
        sz_row.addWidget(self._map_size_v)
        map_lay.addRow("Size (% of screen):", sz_row)

        self._opa_s = QtWidgets.QSlider(Qt.Horizontal)
        self._opa_s.setRange(10, 100)
        self._opa_s.setValue(int(map_opacity * 100))
        self._opa_v = QtWidgets.QLabel(f"{int(map_opacity*100)}%")
        self._opa_s.valueChanged.connect(lambda v: self._opa_v.setText(f"{v}%"))
        op_row = QtWidgets.QHBoxLayout()
        op_row.addWidget(self._opa_s, 1)
        op_row.addWidget(self._opa_v)
        map_lay.addRow("Opacity:", op_row)
        tabs.addTab(map_tab, "Map Overlay")

        # ── Tab 3: Sync / PID ──
        sync_tab = QtWidgets.QWidget()
        sync_lay = QtWidgets.QVBoxLayout(sync_tab)
        sync_hint = QtWidgets.QLabel(
            "Video sync loop tuning. Defaults work well.")
        sync_lay.addWidget(sync_hint)

        strat_row = QtWidgets.QHBoxLayout()
        strat_row.addWidget(QtWidgets.QLabel("Strategy:"))
        self._strat = QtWidgets.QComboBox()
        self._strat.addItems(["cruise", "proportional"])
        self._strat.setCurrentIndex(1 if strategy == "proportional" else 0)
        strat_row.addWidget(self._strat)
        strat_row.addStretch()
        sync_lay.addLayout(strat_row)

        def srow(label, lo, hi, init, fmt):
            r = QtWidgets.QHBoxLayout()
            lb = QtWidgets.QLabel(label)
            lb.setFixedWidth(180)
            s = QtWidgets.QSlider(Qt.Horizontal)
            s.setRange(lo, hi)
            s.setValue(init)
            v = QtWidgets.QLabel(fmt(init))
            v.setFixedWidth(70)
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            s.valueChanged.connect(lambda x, v=v, f=fmt: v.setText(f(x)))
            r.addWidget(lb)
            r.addWidget(s, 1)
            r.addWidget(v)
            sync_lay.addLayout(r)
            return s

        self._kp_s   = srow("Kp (proportional)",  0, 300, kp_i,  lambda x: f"{x/100:.2f}")
        self._dead_s = srow("Deadband (s)",        0, 200, db_i,  lambda x: f"{x/100:.2f} s")
        self._minr_s = srow("Min rate",           50, 200, mnr_i, lambda x: f"{x/100:.2f}x")
        self._maxr_s = srow("Max rate",           50, 200, mxr_i, lambda x: f"{x/100:.2f}x")
        sync_lay.addStretch()
        tabs.addTab(sync_tab, "Sync / PID")

        # ── Tab 4: Trainer (grade computation) ──
        tr_tab = QtWidgets.QWidget()
        tr_lay = QtWidgets.QVBoxLayout(tr_tab)
        tr_hint = QtWidgets.QLabel(
            "Grade lookahead is the distance ahead of your current "
            "position used to compute slope (Δelev / Δdistance). "
            "Smaller = more responsive but noisier; larger = smoother "
            "but feels delayed on sharp climb starts.")
        tr_hint.setWordWrap(True)
        tr_lay.addWidget(tr_hint)

        la_row = QtWidgets.QHBoxLayout()
        la_lbl = QtWidgets.QLabel("Grade lookahead (m)")
        la_lbl.setFixedWidth(180)
        self._la_s = QtWidgets.QSlider(Qt.Horizontal)
        self._la_s.setRange(5, 100)
        self._la_s.setValue(lookahead_m)
        self._la_v = QtWidgets.QLabel(f"{lookahead_m} m")
        self._la_v.setFixedWidth(70)
        self._la_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._la_s.valueChanged.connect(lambda v: self._la_v.setText(f"{v} m"))
        la_row.addWidget(la_lbl)
        la_row.addWidget(self._la_s, 1)
        la_row.addWidget(self._la_v)
        tr_lay.addLayout(la_row)
        tr_lay.addStretch()
        tabs.addTab(tr_tab, "Trainer")

        # ── Buttons ──
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()
        can = QtWidgets.QPushButton("Cancel")
        can.clicked.connect(self.reject)
        ok  = QtWidgets.QPushButton("OK")
        ok.clicked.connect(self.accept)
        btns.addWidget(can)
        btns.addWidget(ok)
        lay.addLayout(btns)

    def apply_to_state(self):
        new_cfg = []
        for i in range(self._pill_list.count()):
            item  = self._pill_list.item(i)
            key   = item.data(Qt.UserRole)
            row_w = self._pill_list.itemWidget(item)
            vis   = row_w.findChild(QtWidgets.QCheckBox)
            siz   = row_w.findChild(QtWidgets.QComboBox)
            new_cfg.append({"key": key, "visible": vis.isChecked(), "size": siz.currentIndex()})
        with self.state.lock:
            self.state.hud_pill_cfg  = new_cfg
            self.state.pill_layout   = self._pill_layout_combo.currentIndex()
            self.state.map_corner    = self._corner_combo.currentIndex()
            self.state.map_size_pct  = self._map_size_s.value()
            self.state.map_opacity   = self._opa_s.value() / 100.0
            self.state.strategy      = self._strat.currentText()
            self.state.kp            = self._kp_s.value()   / 100.0
            self.state.deadband      = self._dead_s.value()  / 100.0
            self.state.min_rate      = self._minr_s.value()  / 100.0
            self.state.max_rate      = self._maxr_s.value()  / 100.0
            self.state.grade_lookahead_m = float(self._la_s.value())


class ControlsPanel(QtWidgets.QWidget):
    def __init__(self, state: SharedState, sim_mode: bool, virtual: bool = False, parent=None):
        super().__init__(parent)
        self.state    = state
        self.sim_mode = sim_mode
        self.virtual  = virtual
        self._build()

    def _build(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)

        units_row = QtWidgets.QHBoxLayout()
        self.imperial_cb = QtWidgets.QCheckBox("Imperial units  (mph / miles)")
        self.imperial_cb.setChecked(False)
        units_row.addWidget(self.imperial_cb)
        units_row.addStretch()
        root.addLayout(units_row)

        badge = QtWidgets.QLabel("⚡ SIM MODE" if self.sim_mode else "📡 BLE MODE")
        badge.setStyleSheet(
            "color: #ffcc00; font-weight: bold; font-size: 13px;"
            if self.sim_mode else
            "color: #00e5ff; font-weight: bold; font-size: 13px;"
        )
        root.addWidget(badge)

        self.send_grade_cb = QtWidgets.QCheckBox("Send grade → trainer")
        self.send_grade_cb.setEnabled(not self.sim_mode)
        root.addWidget(self.send_grade_cb)

        # Playback pace slider (kept here for quick access)
        pace_row = QtWidgets.QHBoxLayout()
        pace_lbl = QtWidgets.QLabel("Playback pace")
        pace_lbl.setFixedWidth(120)
        self.base_s = QtWidgets.QSlider(Qt.Horizontal)
        self.base_s.setRange(50, 200); self.base_s.setValue(100)
        self.base_v = QtWidgets.QLabel("1.00×")
        self.base_v.setFixedWidth(55)
        self.base_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        pace_row.addWidget(pace_lbl); pace_row.addWidget(self.base_s, 1)
        pace_row.addWidget(self.base_v)
        root.addLayout(pace_row)

        # Lock to map (world / virtual-world mode): stop the video-rate controller
        # so position drives the map + UDP world without the sync hunting.
        self.video_lock_cb = QtWidgets.QCheckBox("Lock to map  (no video sync)")
        self.video_lock_cb.setToolTip(
            "World mode: drive the GPS map and the virtual world straight from "
            "position. The video holds at the pace rate and stops hunting/seeking.")
        if self.virtual:
            # Virtual ride: map-drive is required (it's what feeds the world over
            # UDP), so force it on and lock the toggle.
            self.video_lock_cb.setChecked(True)
            self.video_lock_cb.setEnabled(False)
        root.addWidget(self.video_lock_cb)

        # Debug SIM-speed slider (0.25–16×), log-mapped. SIM mode only.
        sim_row = QtWidgets.QHBoxLayout()
        sim_lbl = QtWidgets.QLabel("SIM speed")
        sim_lbl.setFixedWidth(120)
        self.sim_speed_s = QtWidgets.QSlider(Qt.Horizontal)
        self.sim_speed_s.setRange(0, 100); self.sim_speed_s.setValue(50)  # 50 → 1.0×
        self.sim_speed_v = QtWidgets.QLabel("1.00×")
        self.sim_speed_v.setFixedWidth(55)
        self.sim_speed_v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.sim_speed_s.valueChanged.connect(
            lambda v: self.sim_speed_v.setText(f"{self._sim_scale_from_slider(v):.2f}×"))
        self.sim_speed_s.setEnabled(self.sim_mode)
        sim_lbl.setEnabled(self.sim_mode)
        sim_row.addWidget(sim_lbl); sim_row.addWidget(self.sim_speed_s, 1)
        sim_row.addWidget(self.sim_speed_v)
        root.addLayout(sim_row)

        # Map mode cycle button
        map_row = QtWidgets.QHBoxLayout()
        self.map_mode_btn = QtWidgets.QPushButton("🗺 Map: Bottom Panel  (M)")
        self.map_mode_btn.setToolTip(
            "Cycle: Bottom Panel → Overlay (Full) → Overlay (Tracking)")
        self.map_mode_btn.clicked.connect(self._cycle_map_mode)
        map_row.addWidget(self.map_mode_btn)
        map_row.addStretch()
        root.addLayout(map_row)

        # ── A: Video Offset (video only) — now first ──
        vid_grp = QtWidgets.QGroupBox("A: Video Offset  (video only)")
        vg = QtWidgets.QVBoxLayout(vid_grp)
        _vh = QtWidgets.QLabel("Video out of sync with GPS map")
        _vh.setStyleSheet("color:#888;font-size:10px;")
        vg.addWidget(_vh)
        self._vid_offset_lbl = QtWidgets.QLabel("Offset: +0.0 s")
        self._vid_offset_lbl.setStyleSheet(
            f"font-family:'{MONO_FONT}'; color:#00e5ff; font-size:11px;")
        self._vid_offset_lbl.setAlignment(Qt.AlignCenter)
        vg.addWidget(self._vid_offset_lbl)
        vid_row = QtWidgets.QHBoxLayout()
        vid_row.setSpacing(3)
        for label, delta_s in [("-30s", -30), ("-5s", -5), ("-1s", -1),
                                ("+1s", 1), ("+5s", 5), ("+30s", 30)]:
            b = QtWidgets.QPushButton(label)
            b.setFixedHeight(26)
            b.clicked.connect(lambda _, d=delta_s: self._bump_video_offset(d))
            vid_row.addWidget(b)
        vg.addLayout(vid_row)
        vid_reset = QtWidgets.QPushButton("Reset video offset")
        vid_reset.setStyleSheet("color:#ff8080; font-size:10px;")
        vid_reset.clicked.connect(self._reset_video_offset)
        vg.addWidget(vid_reset)
        root.addWidget(vid_grp)

        # ── B: Position Sync (map + video) — now second ──
        pos_grp = QtWidgets.QGroupBox("B: Position Sync  (map + video)")
        pg = QtWidgets.QVBoxLayout(pos_grp)
        _ph = QtWidgets.QLabel("App map drifts from Garmin map")
        _ph.setStyleSheet("color:#888;font-size:10px;")
        pg.addWidget(_ph)
        pos_row = QtWidgets.QHBoxLayout()
        pos_row.setSpacing(3)
        for label, delta_m in [("-500", -500), ("-100", -100), ("-20", -20),
                                ("+20", 20), ("+100", 100), ("+500", 500)]:
            b = QtWidgets.QPushButton(label)
            b.setFixedHeight(26)
            b.clicked.connect(lambda _, d=delta_m: self._bump_pos(d))
            pos_row.addWidget(b)
        pg.addLayout(pos_row)
        root.addWidget(pos_grp)
        root.addStretch()

    @staticmethod
    def _hint_widget(text):
        l = QtWidgets.QLabel(text)
        l.setStyleSheet("color:#888; font-size:10px;")
        return l

    MAP_MODE_LABELS = [
        "🗺 Map: Bottom Panel  (M)",
        "🗺 Map: Overlay Full  (M)",
        "🗺 Map: Overlay Tracking  (M)",
    ]

    def _cycle_map_mode(self):
        with self.state.lock:
            mode = (self.state.map_mode + 1) % 3
            self.state.map_mode = mode
        self.map_mode_btn.setText(self.MAP_MODE_LABELS[mode])

    def _bump_pos(self, delta_m: float):
        with self.state.lock:
            self.state.position_bump_m += delta_m

    def _bump_video_offset(self, delta_s: float):
        with self.state.lock:
            self.state.video_offset_adj += delta_s
            adj = self.state.video_offset_adj
        self._vid_offset_lbl.setText(f"Offset: {adj:+.1f} s")

    def _reset_video_offset(self):
        with self.state.lock:
            self.state.video_offset_adj = 0.0
        self._vid_offset_lbl.setText("Offset: +0.0 s")

    @staticmethod
    def _sim_scale_from_slider(v: int) -> float:
        # Piecewise log map, centered so the default (50) is exactly 1.0×:
        #   0→0.25×, 50→1.0×, 100→16×.  Lower half spans 0.25–1, upper 1–16.
        if v <= 50:
            return 0.25 * (4.0 ** (v / 50.0))
        return 16.0 ** ((v - 50.0) / 50.0)

    def read_into_state(self):
        with self.state.lock:
            self.state.imperial              = self.imperial_cb.isChecked()
            self.state.send_grade_to_trainer = self.send_grade_cb.isChecked()
            self.state.base_rate             = self.base_s.value() / 100.0
            self.state.video_lock            = self.video_lock_cb.isChecked()
            self.state.sim_speed_scale       = self._sim_scale_from_slider(
                self.sim_speed_s.value())
        self.base_v.setText(f"{self.base_s.value()/100:.2f}×")


# ─────────────────────────────────────────────────────────────
#  Main window
# ─────────────────────────────────────────────────────────────

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, state, signals, lat, lon, sim_mode, video_path, total_dist_m):
        super().__init__()
        self.state        = state
        self.signals      = signals
        self.lat          = lat
        self.lon          = lon
        self._last_ridx   = -1
        self._total_dist  = total_dist_m
        self._fullscreen  = False
        self._sim_mode    = sim_mode
        self._video_path  = video_path
        self._last_map_mode = 0
        self._map_pixmap    = None
        self._prev_lat      = None
        self._prev_lon      = None

        self.setWindowTitle("Ride Simulator")
        self.setStyleSheet(DARK_STYLESHEET)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        self.video_panel = VideoPanel(state, sim_mode, video_path, signals)
        root.addWidget(self.video_panel, stretch=3)

        # Scrubber row — wrapped in a widget so we can hide it in fullscreen
        self._scrub_row_widget = QtWidgets.QWidget()
        scrub_row = QtWidgets.QHBoxLayout(self._scrub_row_widget)
        scrub_row.setContentsMargins(0, 0, 0, 0)
        scrub_lbl = QtWidgets.QLabel("⏩")
        scrub_lbl.setFixedWidth(22)
        self.scrubber = QtWidgets.QSlider(Qt.Horizontal)
        self.scrubber.setRange(0, 1000)
        self._scrubber_dragging = False
        self.scrubber.sliderPressed.connect(self._scrub_pressed)
        self.scrubber.sliderReleased.connect(self._scrub_released)
        scrub_row.addWidget(scrub_lbl)
        scrub_row.addWidget(self.scrubber, 1)
        self._scrub_dist_lbl = QtWidgets.QLabel("0.00 km")
        self._scrub_dist_lbl.setFixedWidth(70)
        scrub_row.addWidget(self._scrub_dist_lbl)

        settings_btn = QtWidgets.QPushButton("⚙ Settings")
        settings_btn.setFixedWidth(90)
        settings_btn.setToolTip("Settings (HUD, sync, map)")
        settings_btn.clicked.connect(self._open_settings)
        scrub_row.addWidget(settings_btn)

        self._fs_btn = QtWidgets.QPushButton("⛶ Full")
        self._fs_btn.setFixedWidth(70)
        self._fs_btn.clicked.connect(self._toggle_fullscreen)
        scrub_row.addWidget(self._fs_btn)
        root.addWidget(self._scrub_row_widget)

        self.bottom_widget = QtWidgets.QWidget()
        brow = QtWidgets.QHBoxLayout(self.bottom_widget)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(4)

        self.map_widget = MapWidget(lat, lon)
        brow.addWidget(self.map_widget, stretch=2)

        self.controls = ControlsPanel(state, sim_mode, virtual=not video_path)
        self.controls.setFixedWidth(360)
        brow.addWidget(self.controls)

        root.addWidget(self.bottom_widget, stretch=1)

        # Deferred overlay map
        self._overlay_map = None
        self._overlay_lat = lat
        self._overlay_lon = lon
        QtCore.QTimer.singleShot(3000, self._create_overlay_map)

        # Shortcuts
        QtGui.QShortcut(QtGui.QKeySequence("F11"), self).activated.connect(
            self._toggle_fullscreen)
        QtGui.QShortcut(QtGui.QKeySequence("Escape"), self).activated.connect(
            self._exit_fullscreen)
        QtGui.QShortcut(QtGui.QKeySequence("M"), self).activated.connect(
            self.controls._cycle_map_mode)
        QtGui.QShortcut(QtGui.QKeySequence("F1"), self).activated.connect(
            lambda: AboutDialog(self).exec())

        # Pacer cube tuning (cube-overlay branch)
        QtGui.QShortcut(QtGui.QKeySequence(","), self).activated.connect(
            lambda: self._tune_pacer("fov", -1.0))
        QtGui.QShortcut(QtGui.QKeySequence("."), self).activated.connect(
            lambda: self._tune_pacer("fov", +1.0))
        QtGui.QShortcut(QtGui.QKeySequence("Shift+,"), self).activated.connect(
            lambda: self._tune_pacer("fov", -0.1))
        QtGui.QShortcut(QtGui.QKeySequence("Shift+."), self).activated.connect(
            lambda: self._tune_pacer("fov", +0.1))
        QtGui.QShortcut(QtGui.QKeySequence(";"), self).activated.connect(
            lambda: self._tune_pacer("gap", -0.5))
        QtGui.QShortcut(QtGui.QKeySequence("'"), self).activated.connect(
            lambda: self._tune_pacer("gap", +0.5))
        QtGui.QShortcut(QtGui.QKeySequence("C"), self).activated.connect(
            self._toggle_pacer)
        QtGui.QShortcut(QtGui.QKeySequence("R"), self).activated.connect(
            self._toggle_tangent)
        QtGui.QShortcut(QtGui.QKeySequence("G"), self).activated.connect(
            self._toggle_cube_follows_ghost)
        # User pause toggle: pauses video AND ghost (gap preserved on resume).
        QtGui.QShortcut(QtGui.QKeySequence("Space"), self).activated.connect(
            self._toggle_user_pause)

        self.statusBar().showMessage("Loading…")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)

        self._map_snap_timer = QTimer(self)
        self._map_snap_timer.timeout.connect(self._grab_map_snapshot)
        self._map_snap_timer.start(500)

    def _open_settings(self):
        dlg = SettingsDialog(self.state, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            dlg.apply_to_state()

    def _tune_pacer(self, axis: str, delta: float):
        with self.state.lock:
            if axis == "fov":
                v = clamp(self.state.video_fov_h_deg + delta, 30.0, 170.0)
                self.state.video_fov_h_deg = v
                msg = f"FOV: {v:.1f}°"
            elif axis == "gap":
                v = clamp(self.state.pacer_gap_m + delta, 0.5, 100.0)
                self.state.pacer_gap_m = v
                msg = f"Gap: {v:.1f} m"
            else:
                return
            self.state._tune_message   = msg
            self.state._tune_message_t = time.monotonic()

    def _toggle_user_pause(self):
        """Space hotkey: toggle user pause. Pauses video AND ghost so the gap
        is preserved on resume. Worker reads state.user_paused each tick and
        stops advancing virtual_dist + accumulates ghost_t_offset_s during the
        pause window."""
        with self.state.lock:
            self.state.user_paused = not self.state.user_paused
            self.state._tune_message   = f"Paused" if self.state.user_paused else "Resumed"
            self.state._tune_message_t = time.monotonic()

    def _toggle_pacer(self):
        with self.state.lock:
            self.state.pacer_visible   = not self.state.pacer_visible
            self.state._tune_message   = f"Cube: {'ON' if self.state.pacer_visible else 'OFF'}"
            self.state._tune_message_t = time.monotonic()

    def _toggle_tangent(self):
        with self.state.lock:
            self.state.tangent_visible = not self.state.tangent_visible
            self.state._tune_message   = f"Tangent: {'ON' if self.state.tangent_visible else 'OFF'}"
            self.state._tune_message_t = time.monotonic()

    def _toggle_cube_follows_ghost(self):
        with self.state.lock:
            self.state.cube_follows_ghost = not self.state.cube_follows_ghost
            self.state._tune_message   = (
                f"Cube follows ghost: "
                f"{'ON' if self.state.cube_follows_ghost else 'OFF'}")
            self.state._tune_message_t = time.monotonic()

    def _create_overlay_map(self):
        if self._overlay_map is not None:
            return
        self._overlay_map = OverlayMapWidget(
            self._overlay_lat, self._overlay_lon, parent=self)

    def _scrub_pressed(self):
        self._scrubber_dragging = True

    def _scrub_released(self):
        self._scrubber_dragging = False
        pct  = self.scrubber.value() / 1000.0
        dist = pct * self._total_dist
        with self.state.lock:
            self.state.seek_to_dist_m = dist

    def _toggle_fullscreen(self):
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            self.bottom_widget.hide()
            self._scrub_row_widget.hide()
            self.statusBar().hide()
            self.video_panel.set_aspect_fill(True)
            # Never change windowFlags on a visible window on macOS —
            # it causes the window to lose event handling entirely.
            # showFullScreen() alone hides the title bar and covers the dock.
            self.showFullScreen()
        else:
            self.bottom_widget.show()
            self._scrub_row_widget.show()
            self.statusBar().show()
            self.video_panel.set_aspect_fill(False)
            self.showNormal()
        QtCore.QTimer.singleShot(80, self._sync_overlay_geometry)

    def _exit_fullscreen(self):
        if self._fullscreen:
            self._toggle_fullscreen()

    def _sync_overlay_geometry(self):
        self.video_panel._sync_overlay()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.video_panel._sync_overlay()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.video_panel._sync_overlay()

    def _tick(self):
        self.controls.read_into_state()

        with self.state.lock:
            status   = self.state.status
            ble      = self.state.ble_status
            hr_st    = self.state.hr_status
            ridx     = self.state.route_index
            vdist    = self.state.virtual_dist_m
            map_mode = self.state.map_mode
            imp      = self.state.imperial
            ghost_active = self.state.ghost_active
            ghost_gap_m  = self.state.ghost_gap_m
            route_dist_arr = self.state.route_dist_arr
            route_lat_arr  = self.state.route_lat_arr
            route_lon_arr  = self.state.route_lon_arr

        parts = [x for x in [ble, hr_st, status] if x]
        self.statusBar().showMessage("   |   ".join(parts))

        if not self._scrubber_dragging and self._total_dist > 0:
            pct = vdist / self._total_dist
            self.scrubber.setValue(int(pct * 1000))
            if imp:
                self._scrub_dist_lbl.setText(f"{vdist/1609.344:.2f} mi")
            else:
                self._scrub_dist_lbl.setText(f"{vdist/1000:.2f} km")

        i = min(max(ridx, 0), len(self.lat) - 1)
        while i > 0 and (self.lat[i] is None or self.lon[i] is None):
            i -= 1
        cur_lat = self.lat[i] if self.lat[i] is not None else None
        cur_lon = self.lon[i] if self.lon[i] is not None else None

        if cur_lat is not None:
            self.map_widget.set_position(cur_lat, cur_lon)
            if self._overlay_map:
                self._overlay_map.set_position(cur_lat, cur_lon)

        # Ghost rider on the map: interpolate route lat/lon at ghost position.
        ghost_lat = ghost_lon = None
        if (ghost_active and route_dist_arr is not None
                and route_lat_arr is not None and route_lon_arr is not None
                and len(route_dist_arr) >= 2):
            import numpy as _np
            ghost_dist = max(float(route_dist_arr[0]),
                             min(vdist + ghost_gap_m,
                                 float(route_dist_arr[-1])))
            j = int(_np.searchsorted(route_dist_arr, ghost_dist, side="right")) - 1
            j = max(0, min(j, len(route_dist_arr) - 2))
            d0 = float(route_dist_arr[j]); d1 = float(route_dist_arr[j + 1])
            la0 = float(route_lat_arr[j]); la1 = float(route_lat_arr[j + 1])
            lo0 = float(route_lon_arr[j]); lo1 = float(route_lon_arr[j + 1])
            if all(math.isfinite(v) for v in (la0, la1, lo0, lo1)):
                f = (ghost_dist - d0) / max(d1 - d0, 1e-9)
                ghost_lat = la0 + f * (la1 - la0)
                ghost_lon = lo0 + f * (lo1 - lo0)
        if ghost_lat is not None and ghost_lon is not None:
            self.map_widget.set_ghost_position(ghost_lat, ghost_lon, True)
            if self._overlay_map:
                self._overlay_map.set_ghost_position(ghost_lat, ghost_lon, True)
        else:
            self.map_widget.set_ghost_position(0.0, 0.0, False)
            if self._overlay_map:
                self._overlay_map.set_ghost_position(0.0, 0.0, False)

        if ridx - self._last_ridx >= 15 and ridx > 0:
            pts = [[self.lat[k], self.lon[k]]
                   for k in range(ridx + 1)
                   if self.lat[k] is not None and self.lon[k] is not None]
            if len(pts) >= 2:
                self.map_widget.set_progress(pts)
                if self._overlay_map:
                    self._overlay_map.set_progress(pts)
            self._last_ridx = ridx

        # Backfill lat/lon into activity recorder
        with self.state.lock:
            rec = self.state.activity_recorder
        if rec.enabled and rec._samples and cur_lat is not None:
            for s in reversed(rec._samples):
                if s["lat"] is not None:
                    break
                s["lat"] = cur_lat
                s["lon"] = cur_lon

        # Map mode management
        if map_mode != self._last_map_mode:
            self._last_map_mode = map_mode
            if map_mode == 0:
                self.bottom_widget.show()
            elif not self._fullscreen:
                self.bottom_widget.hide()
            if map_mode == 1 and self._overlay_map:
                self._overlay_map.show_full_route()
            elif map_mode == 2 and cur_lat is not None and self._overlay_map:
                self._overlay_map.track_position(cur_lat, cur_lon,
                                                  self._compute_heading(cur_lat, cur_lon))

        if map_mode == 2 and cur_lat is not None and self._overlay_map:
            self._overlay_map.track_position(cur_lat, cur_lon,
                                              self._compute_heading(cur_lat, cur_lon))

        self.video_panel.overlay.map_pixmap = self._map_pixmap
        self.video_panel.overlay.refresh()

        if status.startswith("Ride complete"):
            self._save_activity()

    def _compute_heading(self, lat, lon):
        if self._prev_lat is None:
            self._prev_lat, self._prev_lon = lat, lon
            return 0.0
        dlat = lat - self._prev_lat
        dlon = (lon - self._prev_lon) * math.cos(math.radians(lat))
        self._prev_lat, self._prev_lon = lat, lon
        if abs(dlat) < 1e-8 and abs(dlon) < 1e-8:
            return getattr(self, "_last_heading", 0.0)
        heading = math.degrees(math.atan2(dlon, dlat)) % 360
        self._last_heading = heading
        return heading

    def _grab_map_snapshot(self):
        with self.state.lock:
            mode = self.state.map_mode
        if mode == 0 or self._overlay_map is None:
            self._map_pixmap = None
            return
        px = self._overlay_map.grab_pixmap()
        if px is not None:
            self._map_pixmap = px

    def _save_activity(self):
        if hasattr(self, "_activity_saved"):
            return
        self._activity_saved = True
        with self.state.lock:
            rec = self.state.activity_recorder
        if not rec.enabled or not rec._samples:
            return
        ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
        out = str(Path(self._video_path).parent / f"ride_{ts}.tcx")
        if rec.save(out):
            self.statusBar().showMessage(f"Activity saved: {out}")

    def closeEvent(self, event):
        with self.state.lock:
            self.state.stop_event.set()
        self._save_activity()
        self.timer.stop()
        self._map_snap_timer.stop()
        try:
            self.video_panel.terminate()
        except Exception:
            pass
        event.accept()


# ─────────────────────────────────────────────────────────────
#  Dark stylesheet
# ─────────────────────────────────────────────────────────────

DARK_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: #0d0d14;
    color: #e0e0e0;
    font-family: "{UI_FONT}", sans-serif;
    font-size: 12px;
}}
QSlider::groove:horizontal {{
    height: 4px; background: #2a2a40; border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: #00e5ff; width: 14px; height: 14px;
    margin: -5px 0; border-radius: 7px;
}}
QSlider::sub-page:horizontal {{
    background: #007a99; border-radius: 2px;
}}
QComboBox, QCheckBox, QDoubleSpinBox, QLineEdit {{
    background: #1a1a2e; border: 1px solid #2a2a40;
    border-radius: 4px; padding: 3px 6px; color: #e0e0e0;
}}
QPushButton {{
    background: #1a1a2e; border: 1px solid #2a2a40;
    border-radius: 4px; padding: 4px 12px; color: #e0e0e0;
}}
QPushButton:hover {{ background: #00a0bb; color: white; }}
QStatusBar {{ background: #080810; color: #666; font-size: 11px; }}
QTabWidget::pane {{ border: 1px solid #2a2a40; }}
QTabBar::tab {{
    background: #1a1a2e; border: 1px solid #2a2a40;
    padding: 4px 12px; color: #888;
}}
QTabBar::tab:selected {{ background: #0d0d14; color: #e0e0e0; }}
QListWidget {{
    background: #1a1a2e; border: 1px solid #2a2a40;
}}
QListWidget::item:selected {{ background: #00a0bb; color: white; }}
"""



class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Ride Simulator")
        self.setMinimumWidth(500)
        self.setStyleSheet(DARK_STYLESHEET)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(10)

        title = QtWidgets.QLabel("🚴  Ride Simulator")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #00e5ff;")
        lay.addWidget(title)

        ver = QtWidgets.QLabel(f"Version {APP_VERSION}")
        ver.setStyleSheet("color: #aaa;")
        lay.addWidget(ver)

        body = QtWidgets.QLabel(
            "Syncs a recorded cycling video to live telemetry from a "
            "BLE FTMS smart trainer.\n\n"
            "Built collaboratively with Claude Code (Anthropic).\n\n"
            "Map data © OpenStreetMap contributors. Tiles © CARTO.\n\n"
            "GoPro and Max are trademarks of GoPro, Inc., used "
            "descriptively. This product is not affiliated with or "
            "endorsed by GoPro, Inc."
        )
        body.setWordWrap(True)
        body.setStyleSheet("color: #ddd; font-size: 12px;")
        lay.addWidget(body)

        # Feedback row
        frow = QtWidgets.QHBoxLayout()
        issue_btn = QtWidgets.QPushButton("Report an Issue")
        issue_btn.setToolTip("Open the GitHub issue tracker (requires a GitHub account)")
        issue_btn.clicked.connect(self._report_issue)
        frow.addWidget(issue_btn)
        contact_btn = QtWidgets.QPushButton("Discuss / Ask a Question")
        contact_btn.setToolTip("Open GitHub Discussions for questions and general feedback")
        contact_btn.clicked.connect(self._contact)
        frow.addWidget(contact_btn)
        frow.addStretch()
        lay.addLayout(frow)

        # License row
        brow = QtWidgets.QHBoxLayout()
        lic_btn = QtWidgets.QPushButton("View Third-Party Licenses")
        lic_btn.clicked.connect(self._open_licenses)
        brow.addWidget(lic_btn)
        brow.addStretch()
        ok = QtWidgets.QPushButton("Close")
        ok.clicked.connect(self.accept)
        brow.addWidget(ok)
        lay.addLayout(brow)

    REPO_URL        = "https://github.com/daruigh-wq/ride-sim"
    DISCUSSIONS_URL = "https://github.com/daruigh-wq/ride-sim/discussions"

    def _report_issue(self):
        from urllib.parse import quote
        title = quote(f"Bug in Ride Sim {APP_VERSION}: ")
        body  = quote(
            f"Version: {APP_VERSION}\n"
            f"OS: \n"
            f"Mode: BLE / SIM\n\n"
            f"What happened:\n\n"
            f"What I expected:\n\n"
            f"Steps to reproduce:\n"
        )
        url = f"{self.REPO_URL}/issues/new?labels=bug&title={title}&body={body}"
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    def _contact(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(self.DISCUSSIONS_URL))

    def _open_licenses(self):
        # Locate the licenses file. In a PyInstaller bundle it sits next to
        # the executable (or in Contents/Resources on macOS). In a dev run
        # it sits next to this source file.
        candidates = []
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
            candidates.extend([
                base / "THIRD_PARTY_LICENSES.txt",
                base.parent / "Resources" / "THIRD_PARTY_LICENSES.txt",
            ])
        candidates.append(Path(__file__).resolve().parent / "THIRD_PARTY_LICENSES.txt")
        for p in candidates:
            if p.exists():
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(p)))
                return
        QtWidgets.QMessageBox.information(
            self, "Licenses",
            "THIRD_PARTY_LICENSES.txt not found alongside the application.",
        )


class StartupDialog(QtWidgets.QDialog):
    def __init__(self, last: dict):
        super().__init__()
        self.setWindowTitle("Ride Simulator")
        self.setMinimumWidth(560)
        self.setStyleSheet(DARK_STYLESHEET)
        self.result_data = {}
        self._last = last
        self._build()

    def _build(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(12)

        title = QtWidgets.QLabel("🚴  Ride Simulator")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #00e5ff;")
        lay.addWidget(title)

        # Ride type — your own footage (video) or the generated Godot world.
        wt_row = QtWidgets.QHBoxLayout()
        wt_lbl = QtWidgets.QLabel("Ride type:")
        wt_lbl.setFixedWidth(90)
        wt_row.addWidget(wt_lbl)
        self.world_combo = QtWidgets.QComboBox()
        self.world_combo.addItems(["Video ride (your footage)",
                                   "Virtual world (Godot)"])
        self.world_combo.setCurrentIndex(int(self._last.get("world_type", 0)))
        self.world_combo.currentIndexChanged.connect(self._update_world_mode)
        wt_row.addWidget(self.world_combo, 1)
        lay.addLayout(wt_row)

        lay.addWidget(self._file_row("TCX file:", "*.tcx", "tcx"))
        self._video_row = self._file_row("Video file:", "*.mp4 *.mkv *.avi *.mov", "video")
        lay.addWidget(self._video_row)

        # Virtual-world launch (shown only for a Virtual ride). Both optional:
        # if set, ride_sim launches Godot for you; if blank, start Godot yourself.
        self._world_app_row = self._file_row("World app:", "*", "world_app")
        self._world_data_row = self._file_row("World data:", "*", "world_data")
        lay.addWidget(self._world_app_row)
        lay.addWidget(self._world_data_row)
        self._virtual_hint = QtWidgets.QLabel(
            "World app = the RideSimWorld renderer (e.g. build/RideSimWorld.app). "
            "World data = a baked world folder from tools/bake_world.py (contains "
            "world.json). Leave blank to launch the world yourself — ride_sim still "
            "drives it over UDP.")
        self._virtual_hint.setStyleSheet("color:#555; font-size:10px; margin-left:95px;")
        self._virtual_hint.setWordWrap(True)
        lay.addWidget(self._virtual_hint)

        ghost_row = self._file_row("Ghost TCX:", "*.tcx", "ghost_tcx")
        ghost_hint = QtWidgets.QLabel(
            "Optional — race yourself or a friend. Leave blank for no ghost.")
        ghost_hint.setStyleSheet("color:#555; font-size:10px; margin-left:95px;")
        lay.addWidget(ghost_row)
        lay.addWidget(ghost_hint)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Video offset (s):"))
        self.offset_spin = QtWidgets.QDoubleSpinBox()
        self.offset_spin.setRange(-3600, 3600)
        self.offset_spin.setValue(self._last.get("offset", 0.0))
        self.offset_spin.setSingleStep(0.5)
        self.offset_spin.setFixedWidth(90)
        row.addWidget(self.offset_spin)
        row.addSpacing(20)
        row.addWidget(QtWidgets.QLabel("Mode:"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["BLE FTMS (real trainer)", "SIM (no trainer)"])
        self.mode_combo.setCurrentIndex(self._last.get("mode_idx", 0))
        row.addWidget(self.mode_combo)
        row.addStretch()
        lay.addLayout(row)

        # Activity recording checkbox
        self.record_cb = QtWidgets.QCheckBox("Record activity (TCX) — upload to Strava / Garmin after ride")
        self.record_cb.setChecked(True)
        self.record_cb.setToolTip(
            "Records speed, power, cadence, HR, and GPS position during the ride.\n"
            "Saves a TCX file you can upload to Strava, Garmin Connect, etc.\n"
            "Default: ON for BLE trainer, OFF for SIM mode.")
        lay.addWidget(self.record_cb)

        # Auto-toggle record based on mode selection
        def _mode_changed(idx):
            self.record_cb.setChecked(idx == 0)  # ON for BLE, OFF for SIM
        self.mode_combo.currentIndexChanged.connect(_mode_changed)

        brow = QtWidgets.QHBoxLayout()
        about = QtWidgets.QPushButton("About")
        about.setFlat(True)
        about.setStyleSheet("color:#888; padding:6px 12px;")
        about.clicked.connect(lambda: AboutDialog(self).exec())
        brow.addWidget(about)
        brow.addStretch()
        go = QtWidgets.QPushButton("▶  Start Ride")
        go.setStyleSheet(
            "background:#00a0bb; color:white; font-weight:bold;"
            "padding:10px 30px; border-radius:6px; font-size:14px;"
        )
        go.clicked.connect(self._accept)
        brow.addWidget(go)
        lay.addLayout(brow)

        self._update_world_mode()

    def _update_world_mode(self):
        virtual = self.world_combo.currentIndex() == 1
        self._video_row.setVisible(not virtual)
        self._world_app_row.setVisible(virtual)
        self._world_data_row.setVisible(virtual)
        self._virtual_hint.setVisible(virtual)

    def _file_row(self, label, filt, key):
        w   = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QtWidgets.QLabel(label)
        lbl.setFixedWidth(90)
        edit = QtWidgets.QLineEdit()
        edit.setText(self._last.get(key, ""))
        edit.setPlaceholderText("Browse or paste path…")
        setattr(self, f"_{key}_edit", edit)
        btn = QtWidgets.QPushButton("Browse…")
        btn.setFixedWidth(80)

        def browse():
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, f"Select {label}", "", filt)
            if path:
                edit.setText(path)

        btn.clicked.connect(browse)
        row.addWidget(lbl)
        row.addWidget(edit, 1)
        row.addWidget(btn)
        return w

    def _accept(self):
        virtual = self.world_combo.currentIndex() == 1
        tcx   = self._tcx_edit.text().strip()
        video = self._video_edit.text().strip()
        if not tcx or not Path(tcx).exists():
            QtWidgets.QMessageBox.warning(self, "Missing", "Please select a valid TCX file.")
            return
        if not virtual and (not video or not Path(video).exists()):
            QtWidgets.QMessageBox.warning(self, "Missing", "Please select a valid video file.")
            return
        world_app  = self._world_app_edit.text().strip()
        world_data = self._world_data_edit.text().strip()
        if virtual and world_app and not Path(world_app).exists():
            QtWidgets.QMessageBox.warning(
                self, "Missing", "World app not found — clear it or fix the path.")
            return
        if virtual and world_data and not Path(world_data).exists():
            QtWidgets.QMessageBox.warning(
                self, "Missing", "World data folder not found — clear it or fix the path.")
            return
        ghost = self._ghost_tcx_edit.text().strip()
        if ghost and not Path(ghost).exists():
            QtWidgets.QMessageBox.warning(
                self, "Missing", "Ghost TCX file not found — clear it or fix the path.")
            return
        self.result_data = {
            "tcx":        tcx,
            "video":      "" if virtual else video,
            "virtual":    virtual,
            "world_app":  world_app,
            "world_data": world_data,
            "offset":     self.offset_spin.value(),
            "sim_mode":   self.mode_combo.currentIndex() == 1,
            "mode_idx":   self.mode_combo.currentIndex(),
            "ghost_tcx":  ghost,
            "record":     self.record_cb.isChecked(),
        }
        self.accept()


# ─────────────────────────────────────────────────────────────
#  Persistent settings
# ─────────────────────────────────────────────────────────────

SETTINGS_FILE = Path.home() / ".ride_sim_settings.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}

def save_settings(d: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps(d, indent=2))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────

def _resolve_app_binary(path: Path) -> Optional[Path]:
    """Resolve a launchable executable from a path that may be a macOS .app
    bundle (pick the single executable in Contents/MacOS) or a direct binary."""
    if path.is_file() and os.access(path, os.X_OK):
        return path
    macos = path / "Contents" / "MacOS"
    if macos.is_dir():
        for p in macos.iterdir():
            if p.is_file() and os.access(p, os.X_OK):
                return p
    return None


def launch_world_renderer(world_app: str, world_data: str):
    """
    Launch the exported RideSimWorld renderer for a virtual ride and point it at
    the baked world via the RIDESIM_WORLD_DIR env var (the renderer reads its data
    from there; see ride-sim-world Main.gd). world_app may be the .app bundle or
    its inner binary. Returns the Popen, or None if not launched (blank path or
    failure — ride_sim still drives any renderer the user starts themselves).
    """
    if not world_app:
        return None
    binp = _resolve_app_binary(Path(world_app))
    if binp is None:
        print(f"Could not find an executable in {world_app}; start the world "
              "yourself — UDP still drives it.")
        return None
    env = dict(os.environ)
    if world_data:
        env["RIDESIM_WORLD_DIR"] = str(Path(world_data).resolve())
    try:
        proc = subprocess.Popen([str(binp)], env=env)
        print(f"Launched world renderer: {binp}  RIDESIM_WORLD_DIR={env.get('RIDESIM_WORLD_DIR','(bundled)')}")
        return proc
    except Exception as e:
        print(f"Could not launch world renderer ({e}); start it yourself — UDP still drives it.")
        return None


def main():
    QtWidgets.QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    if BETA_EXPIRES is not None:
        try:
            expires = datetime.strptime(BETA_EXPIRES, "%Y-%m-%d").date()
            if datetime.now().date() > expires:
                QtWidgets.QMessageBox.critical(
                    None,
                    "Beta expired",
                    f"This beta build expired on {expires.isoformat()}.\n\n"
                    f"Please download the latest version.",
                )
                return
        except ValueError:
            pass  # malformed date — skip check rather than block startup

    last     = load_settings()
    dlg      = StartupDialog(last)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    cfg = dlg.result_data
    save_settings({
        "tcx":        cfg["tcx"],
        "video":      cfg["video"],
        "offset":     cfg["offset"],
        "mode_idx":   cfg["mode_idx"],
        "ghost_tcx":  cfg.get("ghost_tcx", ""),
        "world_type": 1 if cfg.get("virtual") else 0,
        "world_app":  cfg.get("world_app", ""),
        "world_data": cfg.get("world_data", ""),
    })

    try:
        time_s, dist_m, elev_m, lat, lon = load_tcx_route(cfg["tcx"])
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "TCX Error", str(e))
        return

    print(f"TCX: {len(time_s)} pts | {dist_m[-1]/1000:.2f} km | "
          f"{time_s[-1]/60:.1f} min | elev {min(elev_m):.0f}–{max(elev_m):.0f} m")

    import numpy as _np
    state                  = SharedState()
    state.video_offset_sec = cfg["offset"]
    # Virtual-world ride: the Godot world is the view. Force map-drive (no video
    # controller to hunt) and launch Godot if the user configured its paths.
    godot_proc = None
    if cfg.get("virtual"):
        state.video_lock = True
        godot_proc = launch_world_renderer(cfg.get("world_app", ""), cfg.get("world_data", ""))
    # Stash route geometry for the curved-centerline tangent renderer. NaN-fill
    # missing lat/lon entries so downstream code can use np.isfinite() masks.
    _lat_arr = _np.asarray([(v if v is not None else math.nan) for v in lat], dtype=float)
    _lon_arr = _np.asarray([(v if v is not None else math.nan) for v in lon], dtype=float)
    state.route_lat_arr  = _lat_arr
    state.route_lon_arr  = _lon_arr
    state.route_dist_arr = _np.asarray(dist_m, dtype=float)
    # Restore persisted pacer cube tunes (cube-overlay branch)
    state.video_fov_h_deg  = float(last.get("video_fov_h_deg",  state.video_fov_h_deg))
    state.pacer_gap_m      = float(last.get("pacer_gap_m",      state.pacer_gap_m))
    state.pacer_visible    = bool(last.get("pacer_visible",     state.pacer_visible))
    state.camera_height_m  = float(last.get("camera_height_m",  state.camera_height_m))
    state.tangent_visible  = bool(last.get("tangent_visible",   state.tangent_visible))
    state.cube_follows_ghost = bool(last.get("cube_follows_ghost",
                                             state.cube_follows_ghost))
    signals                = WorkerSignals()

    # ── Activity recording ──
    if cfg.get("record", False):
        state.activity_recorder.enabled = True
        state.activity_recorder.start()
        print("Activity recording: ON")
    else:
        print("Activity recording: OFF")

    ghost_path = cfg.get("ghost_tcx", "").strip()
    if ghost_path and Path(ghost_path).exists():
        try:
            g_time_s, g_dist_m = load_ghost_tcx(ghost_path)
            state.ghost_time_s    = g_time_s
            state.ghost_dist_m_arr = g_dist_m
            state.ghost_active    = True
            state.ghost_name      = Path(ghost_path).stem
            print(f"Ghost: {state.ghost_name} | "
                  f"{g_dist_m[-1]/1000:.2f} km | {g_time_s[-1]/60:.1f} min")
        except Exception as e:
            print(f"Ghost TCX load failed: {e}")
    start_worker_thread(state, signals, time_s, dist_m, elev_m, cfg["sim_mode"])
    win = MainWindow(state, signals, lat, lon, cfg["sim_mode"], cfg["video"],
                     total_dist_m=dist_m[-1])
    win.resize(1440, 900)
    win.show()
    app.exec()

    # Close the Godot world we launched (if any) when the ride window exits.
    if godot_proc is not None and godot_proc.poll() is None:
        godot_proc.terminate()

    # Persist runtime tunes (cube-overlay branch)
    try:
        runtime = load_settings()
        runtime["video_fov_h_deg"]         = state.video_fov_h_deg
        runtime["pacer_gap_m"]             = state.pacer_gap_m
        runtime["pacer_visible"]           = state.pacer_visible
        runtime["camera_height_m"]         = state.camera_height_m
        runtime["tangent_visible"]         = state.tangent_visible
        runtime["cube_follows_ghost"]      = state.cube_follows_ghost
        save_settings(runtime)
    except Exception:
        pass


if __name__ == "__main__":
    main()
