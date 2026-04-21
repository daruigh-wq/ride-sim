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
import json
import math
import os
import random
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

GATE_START_ON_SPEED  = True
START_SPEED_KMH      = 2.0
START_STABLE_SEC     = 1.0

CONTROL_HZ           = 4.0
DT                   = 1.0 / CONTROL_HZ

SPEED_ALPHA          = 0.20       # EMA smoothing for speed
HR_ALPHA             = 0.05       # EMA smoothing for simulated HR (slow lag)

GRADE_LOOKAHEAD_M    = 20.0
GRADE_CLAMP_PCT      = 15.0

SOFT_ERR_SEC         = 4.0
HARD_SEEK_SEC        = 15.0
SEEK_COOLDOWN_SEC    = 5.0
CRUISE_STEP_PCT      = 0.03

# SIM speed generator
SIM_SEED             = 1234
SIM_SPEED_SCALE      = 1.00
SIM_NOISE_PCT        = 0.05
SIM_DRIFT_PCT        = 0.10
SIM_DRIFT_PERIOD_SEC = 120.0
SIM_MIN_SPEED_MPS    = 0.6

# Simulated rider physics (used for power/HR model in SIM mode)
RIDER_MASS_KG        = 80.0       # rider + bike
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
    maxZoom:19, attribution:'&copy; OSM &copy; CARTO'
  }).addTo(map);
  const full = L.polyline(route,{weight:3,color:'#444'}).addTo(map);
  map.fitBounds(full.getBounds(),{padding:[15,15]});
  const dot = L.circleMarker(route[0],{radius:8,color:'#ff4444',fillColor:'#ff4444',fillOpacity:1}).addTo(map);
  let prog = L.polyline([route[0]],{weight:4,color:'#00e5ff',opacity:0.9}).addTo(map);
  window.setPos=function(a,o){dot.setLatLng([a,o]);}
  window.setProgress=function(j){try{prog.setLatLngs(JSON.parse(j));}catch(e){}}
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
  let prog = L.polyline([route[0]],{weight:3,color:'#00e5ff',opacity:0.9}).addTo(map);

  window.setPos=function(a,o){dot.setLatLng([a,o]);};
  window.setProgress=function(j){try{prog.setLatLngs(JSON.parse(j));}catch(e){}};
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
    return time_s, dist_m, elev_m, lat, lon


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
                ts.strftime("%Y-%m-%dT%H:%M:%SZ")
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
            if s["power"] > 0:
                ext = ET.SubElement(tp, f"{{{NS_TCX}}}Extensions")
                tpx = ET.SubElement(ext, f"{{{NS_AE2}}}TPX")
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


def make_sim_speed_fn(dist_m, time_s):
    rng = random.Random(SIM_SEED)
    def fn(idx: int, t_sim: float) -> float:
        base  = robust_local_speed(dist_m, time_s, idx)
        drift = SIM_DRIFT_PCT * math.sin(2.0 * math.pi * t_sim / SIM_DRIFT_PERIOD_SEC)
        noise = rng.uniform(-SIM_NOISE_PCT, SIM_NOISE_PCT)
        return max(SIM_MIN_SPEED_MPS, base * SIM_SPEED_SCALE * (1.0 + drift + noise))
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
        try:
            await client.write_gatt_char(
                FITNESS_MACHINE_CONTROL_POINT_UUID, bytes([0x00]), response=True)
            await asyncio.sleep(0.05)
        except Exception:
            pass
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
        self.video_offset_sec     = 0.0
        self.position_bump_m      = 0.0
        self.video_offset_adj     = 0.0
        self.imperial             = False

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
    video_paused_for_speed = False

    signals.request_pause.emit()

    with state.lock:
        state.started = not GATE_START_ON_SPEED
        state.status  = "Running (SIM)" if not GATE_START_ON_SPEED else "Waiting for speed…"

    t0 = time.time()

    while True:
        await asyncio.sleep(DT)

        with state.lock:
            if state.stop_event.is_set():
                break

        now   = time.time()
        t_sim = now - t0

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

        grade = clamp(
            compute_grade_pct(dist_m, elev_m, virtual_dist, GRADE_LOOKAHEAD_M),
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
        if smoothed < STOP_THRESHOLD_MPS:
            if not video_paused_for_speed:
                signals.request_pause.emit()
                video_paused_for_speed = True
        else:
            if video_paused_for_speed:
                signals.request_play.emit()
                video_paused_for_speed = False

        with state.lock:
            req = state.seek_to_dist_m
        if req >= 0:
            virtual_dist = clamp(req, 0.0, total_dist)
            target_route_t = interp_time_from_distance(dist_m, time_s, virtual_dist)
            with state.lock:
                offset = state.video_offset_sec
            signals.request_video_seek.emit(offset + target_route_t)
            with state.lock:
                state.seek_to_dist_m = -1.0
            last_seek = time.time()
            continue

        with state.lock:
            bump = state.position_bump_m
            if bump != 0.0:
                state.position_bump_m = 0.0
        virtual_dist = clamp(virtual_dist + smoothed * DT + bump, 0.0, total_dist)

        target_route_t = interp_time_from_distance(dist_m, time_s, virtual_dist)
        with state.lock:
            offset = state.video_offset_sec + state.video_offset_adj
        target_video_t = offset + target_route_t

        with state.lock:
            video_t = state.video_t

        if video_t < 0.1:
            continue

        err = target_video_t - video_t

        with state.lock:
            base       = state.base_rate
            strategy   = state.strategy
            kp         = state.kp
            deadband   = state.deadband
            min_r      = state.min_rate
            max_r      = state.max_rate
            send_grade = state.send_grade_to_trainer

        if err > HARD_SEEK_SEC and (now - last_seek) > SEEK_COOLDOWN_SEC:
            signals.request_seek.emit(target_video_t)
            signals.request_rate.emit(base)
            last_seek = now
        else:
            if abs(err) < deadband:
                new_rate = clamp(base, min_r, max_r)
            elif strategy == "proportional":
                new_rate = clamp(base + kp * err, min_r, max_r)
            else:
                step     = CRUISE_STEP_PCT
                new_rate = clamp(
                    base * (1.0 + step if err > 0 else 1.0 - step), min_r, max_r)
            signals.request_rate.emit(new_rate)

        if ble_client and send_grade:
            if (now - last_grade_send) > 0.5 and (
                last_grade_value is None or abs(grade - last_grade_value) > 0.2
            ):
                await try_set_sim_grade(ble_client, grade)
                last_grade_send  = now
                last_grade_value = grade

        ridx = find_index_for_distance(dist_m, virtual_dist)
        with state.lock:
            state.virtual_dist_m   = virtual_dist
            state.target_video_t   = target_video_t
            state.err_s            = err
            state.route_index      = ridx

            if state.ghost_active and state.ghost_time_s is not None:
                g_dist = interp_dist_from_time(
                    state.ghost_time_s, state.ghost_dist_m_arr, t_sim)
                g_spd  = ghost_speed_at_time(
                    state.ghost_time_s, state.ghost_dist_m_arr, t_sim)
                state.ghost_dist_m    = g_dist
                state.ghost_speed_mps = g_spd
                state.ghost_gap_m     = g_dist - virtual_dist

        # ── Activity recording ──
        # Interpolate lat/lon from route for current position
        rec_lat, rec_lon = None, None
        if ridx < len(elev_m):
            # Find the lat/lon arrays via state (they're not passed to the loop)
            # We use the route arrays passed to the function instead
            pass   # lat/lon recorded via signals below

        with state.lock:
            rec = state.activity_recorder
        if rec.enabled:
            # Interpolate position from dist_m/elev_m arrays
            _elev = elev_m[ridx] if ridx < len(elev_m) else 0.0
            rec.record(t_sim, None, None, _elev, virtual_dist,
                       smoothed, tel.get("cadence_rpm", 0.0),
                       tel.get("power_w", 0.0),
                       state.hr_bpm if hr_from_ble else smoothed_hr)

        if virtual_dist >= total_dist:
            with state.lock:
                state.status = "Ride complete! 🎉"
            signals.request_rate.emit(base)
            break


# ─────────────────────────────────────────────────────────────
#  SIM worker
# ─────────────────────────────────────────────────────────────

async def worker_sim(state, signals, time_s, dist_m, elev_m):
    sim_speed = make_sim_speed_fn(dist_m, time_s)

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
                "map_mode":     self.state.map_mode,
                "map_opacity":  self.state.map_opacity,
                "map_corner":   self.state.map_corner,
                "map_size_pct": self.state.map_size_pct,
                "ride_start":   self.state.ride_start_time,
            }

        # Elapsed time
        if snap["started"] and snap["ride_start"] is not None:
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

        # ── Progress bar ──
        if snap["total"] > 0:
            pct   = snap["dist"] / snap["total"]
            bar_h = 4
            pills_cfg = [c for c in snap["pill_cfg"] if c["visible"]]
            # place it just above pills — compute max pill height first
            max_ph = max((self.PILL_SIZES[c["size"]]["h"] for c in pills_cfg),
                         default=76)
            bar_y  = h - max_ph - self.MARGIN - bar_h - 2
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QBrush(QtGui.QColor(40, 40, 60)))
            p.drawRect(0, bar_y, w, bar_h)
            p.setBrush(QtGui.QBrush(self.ACCENT))
            p.drawRect(0, bar_y, int(w * pct), bar_h)

        # ── Pills ──
        pills_cfg = [c for c in snap["pill_cfg"] if c["visible"]]
        if pills_cfg:
            total_pw = sum(self.PILL_SIZES[c["size"]]["w"] for c in pills_cfg)
            total_pw += self.GAP * (len(pills_cfg) - 1)
            x0 = (w - total_pw) // 2
            # align bottoms to MARGIN from bottom
            max_ph = max(self.PILL_SIZES[c["size"]]["h"] for c in pills_cfg)
            base_y = h - self.MARGIN

            px = x0
            for cfg in pills_cfg:
                sz   = self.PILL_SIZES[cfg["size"]]
                pw, ph = sz["w"], sz["h"]
                py   = base_y - ph
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

                px += pw + self.GAP

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
            gt = f"{gv:+.2f} mi" if abs(gv) >= 0.1 else f"{gap_m:+.0f} m"
        else:
            gt = f"{gap_m/1000:+.2f} km" if abs(gap_m) >= 1000 else f"{gap_m:+.0f} m"
        direction = "ahead" if gap_m > 0 else "behind"
        label = f"{name}  {gt} {direction}   {g_spd:.1f} vs {my_spd:.1f} {spd_u}"
        p.setPen(self.GHOST_COLOR if gap_m > 5 else
                 self.RIDER_COLOR if gap_m < -5 else self.DIM)
        fm = QtGui.QFontMetrics(f)
        p.drawText(int(x0 + (bw - fm.horizontalAdvance(label))//2), int(y0 + bh + 14), label)




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

        # ── Video widget ──
        self.video_widget = QVideoWidget(self)
        self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
        layout.addWidget(self.video_widget)

        # ── Media player ──
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
        self._player.play()

    def _pause(self):
        self._player.pause()

    def _seek(self, t_sec: float):
        self._player.setPosition(int(t_sec * 1000))

    def _set_rate(self, rate: float):
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
            map_corner  = state.map_corner
            map_size    = state.map_size_pct
            map_opacity = state.map_opacity
            kp_i        = int(state.kp * 100)
            db_i        = int(state.deadband * 100)
            mnr_i       = int(state.min_rate * 100)
            mxr_i       = int(state.max_rate * 100)
            strategy    = state.strategy

        tabs = QtWidgets.QTabWidget()
        lay  = QtWidgets.QVBoxLayout(self)
        lay.addWidget(tabs)

        # ── Tab 1: HUD Pills ──
        hud_tab = QtWidgets.QWidget()
        hud_lay = QtWidgets.QVBoxLayout(hud_tab)
        hint = QtWidgets.QLabel(
            "Toggle pills on/off and choose size. "
            "Drag rows to reorder (left to right on screen).")
        hint.setWordWrap(True)
        hud_lay.addWidget(hint)

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
            self.state.map_corner    = self._corner_combo.currentIndex()
            self.state.map_size_pct  = self._map_size_s.value()
            self.state.map_opacity   = self._opa_s.value() / 100.0
            self.state.strategy      = self._strat.currentText()
            self.state.kp            = self._kp_s.value()   / 100.0
            self.state.deadband      = self._dead_s.value()  / 100.0
            self.state.min_rate      = self._minr_s.value()  / 100.0
            self.state.max_rate      = self._maxr_s.value()  / 100.0


class ControlsPanel(QtWidgets.QWidget):
    def __init__(self, state: SharedState, sim_mode: bool, parent=None):
        super().__init__(parent)
        self.state    = state
        self.sim_mode = sim_mode
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

    def read_into_state(self):
        with self.state.lock:
            self.state.imperial              = self.imperial_cb.isChecked()
            self.state.send_grade_to_trainer = self.send_grade_cb.isChecked()
            self.state.base_rate             = self.base_s.value() / 100.0
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

        settings_btn = QtWidgets.QPushButton("⚙")
        settings_btn.setFixedWidth(32)
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

        self.controls = ControlsPanel(state, sim_mode)
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

        lay.addWidget(self._file_row("TCX file:",   "*.tcx",                     "tcx"))
        lay.addWidget(self._file_row("Video file:", "*.mp4 *.mkv *.avi *.mov", "video"))

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
        brow.addStretch()
        go = QtWidgets.QPushButton("▶  Start Ride")
        go.setStyleSheet(
            "background:#00a0bb; color:white; font-weight:bold;"
            "padding:10px 30px; border-radius:6px; font-size:14px;"
        )
        go.clicked.connect(self._accept)
        brow.addWidget(go)
        lay.addLayout(brow)

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
        tcx   = self._tcx_edit.text().strip()
        video = self._video_edit.text().strip()
        if not tcx or not Path(tcx).exists():
            QtWidgets.QMessageBox.warning(self, "Missing", "Please select a valid TCX file.")
            return
        if not video or not Path(video).exists():
            QtWidgets.QMessageBox.warning(self, "Missing", "Please select a valid video file.")
            return
        ghost = self._ghost_tcx_edit.text().strip()
        if ghost and not Path(ghost).exists():
            QtWidgets.QMessageBox.warning(
                self, "Missing", "Ghost TCX file not found — clear it or fix the path.")
            return
        self.result_data = {
            "tcx":       tcx,
            "video":     video,
            "offset":    self.offset_spin.value(),
            "sim_mode":  self.mode_combo.currentIndex() == 1,
            "mode_idx":  self.mode_combo.currentIndex(),
            "ghost_tcx": ghost,
            "record":    self.record_cb.isChecked(),
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

def main():
    QtWidgets.QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    last     = load_settings()
    dlg      = StartupDialog(last)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return
    cfg = dlg.result_data
    save_settings({
        "tcx":       cfg["tcx"],
        "video":     cfg["video"],
        "offset":    cfg["offset"],
        "mode_idx":  cfg["mode_idx"],
        "ghost_tcx": cfg.get("ghost_tcx", ""),
    })

    try:
        time_s, dist_m, elev_m, lat, lon = load_tcx_route(cfg["tcx"])
    except Exception as e:
        QtWidgets.QMessageBox.critical(None, "TCX Error", str(e))
        return

    print(f"TCX: {len(time_s)} pts | {dist_m[-1]/1000:.2f} km | "
          f"{time_s[-1]/60:.1f} min | elev {min(elev_m):.0f}–{max(elev_m):.0f} m")

    state                  = SharedState()
    state.video_offset_sec = cfg["offset"]
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


if __name__ == "__main__":
    main()
