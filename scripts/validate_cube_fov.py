"""Validate calibrated h_fov by compositing the cube/tangent overlay over real
Player export frames at multiple timestamps.

Output: scripts/out/cube_validation/frame_{t}s.png — full-res 3840x2160
PNGs of the Player export with the cube + tangent line drawn on top, plus a
1xN strip for quick comparison.

Run from ride-sim/:
    PYTHONPATH=. .venv/bin/python scripts/validate_cube_fov.py
"""
import math
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore, QtGui, QtWidgets

import ride_sim

PLAYER = Path.home() / "ride-sim/GS010004.mp4"
OUT_DIR = Path("scripts/out/cube_validation")
TIMESTAMPS_S = [0, 30, 60, 90, 120, 150]
GAPS_M = [5.0, 10.0, 20.0]  # multiple cube depths to see how it tracks
FRAME_W, FRAME_H = 3840, 2160


def extract_frame(t_s: float, out_path: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-ss", str(t_s),
        "-i", str(PLAYER),
        "-frames:v", "1",
        "-update", "1",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


def make_state(gap_m: float) -> ride_sim.SharedState:
    state = ride_sim.SharedState()
    state.started = True
    state.ride_start_time = 0.0
    state.total_dist_m = 1000.0
    state.virtual_dist_m = 100.0
    state.speed_mps_smoothed = 5.0
    # Calibrated FOV (the whole point of this validation)
    state.video_fov_h_deg = 118.8
    # Cube setup: on-axis, on the road plane
    state.pacer_visible = True
    state.tangent_visible = True
    state.pacer_gap_m = gap_m
    state.pacer_size_m = 1.0
    state.pacer_height_m = 0.0
    state.camera_height_m = 1.0
    state.cube_follows_ghost = False
    # Hide all HUD pills so they don't clutter the validation render
    for cfg in state.hud_pill_cfg:
        cfg["visible"] = False
    return state


def render_overlay_transparent(state: ride_sim.SharedState, w: int, h: int) -> QtGui.QImage:
    """Render OverlayWidget onto a transparent QImage (alpha-preserving)."""
    overlay = ride_sim.OverlayWidget(state, sim_mode=True)
    overlay.setAttribute(QtCore.Qt.WA_TranslucentBackground)
    overlay.resize(w, h)
    img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtCore.Qt.transparent)
    overlay.render(img, QtCore.QPoint(0, 0), QtGui.QRegion(QtCore.QRect(0, 0, w, h)))
    return img


def composite(bg_path: Path, overlay_img: QtGui.QImage, out_path: Path) -> None:
    bg = QtGui.QImage(str(bg_path))
    if bg.size() != overlay_img.size():
        bg = bg.scaled(overlay_img.size(), QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation)
    canvas = QtGui.QImage(bg)
    painter = QtGui.QPainter(canvas)
    painter.drawImage(0, 0, overlay_img)
    painter.end()
    canvas.save(str(out_path))


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rendered = []
    for t_s in TIMESTAMPS_S:
        frame_path = OUT_DIR / f"src_{t_s:03d}s.png"
        if not frame_path.exists():
            print(f"  extracting t={t_s}s...")
            extract_frame(t_s, frame_path)
        for gap in GAPS_M:
            state = make_state(gap)
            overlay_img = render_overlay_transparent(state, FRAME_W, FRAME_H)
            out_path = OUT_DIR / f"composite_t{t_s:03d}s_gap{int(gap):02d}m.png"
            composite(frame_path, overlay_img, out_path)
            rendered.append(out_path)
            print(f"  composited t={t_s:>3d}s gap={gap:>4.1f}m -> {out_path.name}")

    # Build a strip per gap: timestamp panels horizontally, half-res for file size
    for gap in GAPS_M:
        panels = []
        for t_s in TIMESTAMPS_S:
            p = OUT_DIR / f"composite_t{t_s:03d}s_gap{int(gap):02d}m.png"
            img = QtGui.QImage(str(p)).scaled(
                FRAME_W // 2, FRAME_H // 2,
                QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation,
            )
            panels.append(img)
        strip = QtGui.QImage(panels[0].width() * len(panels), panels[0].height(),
                             QtGui.QImage.Format_RGB32)
        strip.fill(QtCore.Qt.black)
        painter = QtGui.QPainter(strip)
        for i, img in enumerate(panels):
            painter.drawImage(i * panels[0].width(), 0, img)
        painter.end()
        strip_path = OUT_DIR / f"strip_gap{int(gap):02d}m.png"
        strip.save(str(strip_path))
        print(f"  strip -> {strip_path}")


if __name__ == "__main__":
    main()
