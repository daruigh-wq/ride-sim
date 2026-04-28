"""Headless render of the OverlayWidget cube. Saves PNG snapshots.

Run: PYTHONPATH=. .venv/bin/python scripts/cube_smoke.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore, QtGui, QtWidgets

import ride_sim


def render(state, w=1920, h=1080, name="cube.png") -> str:
    overlay = ride_sim.OverlayWidget(state, sim_mode=True)
    overlay.resize(w, h)
    pix = QtGui.QPixmap(w, h)
    pix.fill(QtGui.QColor(40, 40, 60))  # dark backdrop so the wireframe shows
    overlay.render(pix, QtCore.QPoint(0, 0), QtGui.QRegion(QtCore.QRect(0, 0, w, h)))
    out = Path("scripts") / "out"
    out.mkdir(parents=True, exist_ok=True)
    p = out / name
    pix.save(str(p))
    return str(p)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)

    state = ride_sim.SharedState()
    state.started = True
    state.ride_start_time = 0.0
    state.total_dist_m = 1000.0
    state.virtual_dist_m = 100.0
    state.speed_mps_smoothed = 5.0

    cases = [
        ("cube_default.png",  dict(pacer_gap_m=5.0,  video_fov_h_deg=115.0)),
        ("cube_close.png",    dict(pacer_gap_m=2.0,  video_fov_h_deg=115.0)),
        ("cube_far.png",      dict(pacer_gap_m=15.0, video_fov_h_deg=115.0)),
        ("cube_narrow.png",   dict(pacer_gap_m=5.0,  video_fov_h_deg=70.0)),
        ("cube_wide.png",     dict(pacer_gap_m=5.0,  video_fov_h_deg=150.0)),
        ("cube_lowcam.png",   dict(pacer_gap_m=5.0,  video_fov_h_deg=115.0, camera_height_m=0.5)),
    ]
    for name, cfg in cases:
        for k, v in cfg.items():
            setattr(state, k, v)
        path = render(state, name=name)
        print(f"  wrote {path}  (gap={cfg.get('pacer_gap_m')} fov={cfg.get('video_fov_h_deg')})")


if __name__ == "__main__":
    main()
