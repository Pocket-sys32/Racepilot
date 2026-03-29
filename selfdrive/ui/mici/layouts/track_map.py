import json
from pathlib import Path

import numpy as np
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets.nav_widget import NavWidget

PADDING = 40
MS_TO_MPH = 2.23694
MS_TO_KPH = 3.6


def _speed_color(t: float, alpha: int = 220) -> rl.Color:
  """t=0 slow (orange), t=1 fast (cyan)."""
  r = int(255 * (1.0 - t))
  g = int(140 + 115 * t)
  b = int(255 * t)
  return rl.Color(r, g, b, alpha)


def _map_transform(xs: np.ndarray, ys: np.ndarray, rect: rl.Rectangle, margin: float = 30):
  """Return (cx, cy, track_cx, track_cy, scale) to map local coords → screen."""
  min_x, max_x = float(xs.min()), float(xs.max())
  min_y, max_y = float(ys.min()), float(ys.max())
  span_x = max(max_x - min_x, 1.0)
  span_y = max(max_y - min_y, 1.0)
  scale = min((rect.width - margin * 2) / span_x,
              (rect.height - margin * 2) / span_y)
  return (rect.x + rect.width / 2, rect.y + rect.height / 2,
          (min_x + max_x) / 2.0, (min_y + max_y) / 2.0, scale)


def _apply_transform(lx: float, ly: float, cx, cy, tcx, tcy, scale):
  return cx + (lx - tcx) * scale, cy - (ly - tcy) * scale  # flip Y


class TrackMapLayout(NavWidget):
  def __init__(self):
    super().__init__()
    self._reference: dict | None = None
    self._track_name: str = ""
    self._completed_laps: int = 0
    self._error: str = ""

  def show_event(self):
    super().show_event()
    self._load()

  def _load(self):
    track_dir = (Path(Paths.comma_home()) / "media" / "0" if PC else Path("/data/media/0")) / "track_mode"
    if not track_dir.exists():
      self._reference = None
      self._error = "No track data saved yet.\nComplete a lap with track mode active."
      return

    files = sorted(track_dir.glob("*.json"))
    if not files:
      self._reference = None
      self._error = "No track data saved yet.\nComplete a lap with track mode active."
      return

    # Load most recently modified
    latest = max(files, key=lambda f: f.stat().st_mtime)
    try:
      payload = json.loads(latest.read_text())
      ref = payload["reference"]
      self._reference = {
        "xs": np.array(ref["xs"], dtype=np.float32),
        "ys": np.array(ref["ys"], dtype=np.float32),
        "target_speeds": np.array(ref["target_speeds"], dtype=np.float32),
        "total_distance": float(ref.get("total_distance", 0.0)),
        "line_confidence": float(ref.get("line_confidence", 0.0)),
        "source_laps": int(ref.get("source_laps", 1)),
      }
      self._completed_laps = int(payload.get("completed_laps", 0))
      self._track_name = latest.stem.replace("_", " ").upper()
      self._error = ""
    except Exception as e:
      self._reference = None
      self._error = f"Failed to load track data:\n{e}"

  def _render(self, rect: rl.Rectangle) -> None:
    font_bold = gui_app.font(FontWeight.BOLD)
    font_medium = gui_app.font(FontWeight.MEDIUM)
    font_semi = gui_app.font(FontWeight.SEMI_BOLD)

    # Background
    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height),
                      rl.Color(8, 10, 16, 255))

    # Title
    title = self._track_name or "TRACK MAP"
    ts = measure_text_cached(font_semi, title, 34)
    rl.draw_text_ex(font_semi, title,
                    rl.Vector2(rect.x + rect.width / 2 - ts.x / 2, rect.y + 18),
                    34, 0, rl.Color(0, 255, 255, 220))

    if not self._reference:
      font_size = 22
      line_gap = 10
      max_w = rect.width - PADDING * 2
      lines = self._error.split("\n")
      # Scale down font if any line is too wide
      for line in lines:
        while font_size > 14 and measure_text_cached(font_medium, line, font_size).x > max_w:
          font_size -= 1
      total_h = len(lines) * font_size + (len(lines) - 1) * line_gap
      start_y = rect.y + rect.height / 2 - total_h / 2
      for i, line in enumerate(lines):
        lw = measure_text_cached(font_medium, line, font_size).x
        rl.draw_text_ex(font_medium, line,
                        rl.Vector2(rect.x + rect.width / 2 - lw / 2,
                                   start_y + i * (font_size + line_gap)),
                        font_size, 0, rl.Color(160, 160, 160, 200))
      return

    ref = self._reference
    xs, ys = ref["xs"], ref["ys"]
    speeds = ref["target_speeds"]
    min_s, max_s = float(speeds.min()), float(speeds.max())
    speed_range = max(max_s - min_s, 1.0)

    # Layout zones
    stats_h = 84
    legend_bar_w = 10
    legend_text_w = 52   # max width for speed labels left of bar
    legend_total_w = legend_text_w + 6 + legend_bar_w + PADDING

    map_rect = rl.Rectangle(
      rect.x + PADDING,
      rect.y + 66,
      rect.width - PADDING - legend_total_w,
      rect.height - 66 - stats_h,
    )

    cx, cy, tcx, tcy, scale = _map_transform(xs, ys, map_rect)

    # Pre-compute screen points
    px = cx + (xs - tcx) * scale
    py = cy - (ys - tcy) * scale

    n = len(px)

    # Shadow/track width outline
    for i in range(n):
      j = (i + 1) % n
      rl.draw_line_ex(rl.Vector2(px[i], py[i]), rl.Vector2(px[j], py[j]),
                      10, rl.Color(30, 30, 30, 180))

    # Racing line colored by speed
    for i in range(n):
      j = (i + 1) % n
      t = (float(speeds[i]) - min_s) / speed_range
      col = _speed_color(t)
      rl.draw_line_ex(rl.Vector2(px[i], py[i]), rl.Vector2(px[j], py[j]), 3, col)

    # Start/finish dot
    rl.draw_circle(int(px[0]), int(py[0]), 9, rl.Color(255, 255, 255, 220))
    rl.draw_circle(int(px[0]), int(py[0]), 5, rl.Color(0, 0, 0, 255))

    # Current car position (if onroad and trackState valid)
    sm = ui_state.sm
    if ui_state.started and sm.valid.get("trackState", False):
      ts_state = sm["trackState"]
      car_sx, car_sy = _apply_transform(ts_state.localX, ts_state.localY,
                                        cx, cy, tcx, tcy, scale)
      rl.draw_circle(int(car_sx), int(car_sy), 12, rl.Color(255, 255, 255, 200))
      rl.draw_circle(int(car_sx), int(car_sy), 7, rl.Color(0, 255, 255, 255))

    # Speed legend — gradient bar with labels to its left, right-anchored
    legend_bar_x = int(rect.x + rect.width - PADDING - legend_bar_w)
    legend_y = int(rect.y + 80)
    legend_h = int(map_rect.height * 0.55)

    for i in range(legend_h):
      t = 1.0 - i / legend_h
      rl.draw_rectangle(legend_bar_x, legend_y + i, legend_bar_w, 1, _speed_color(t, 200))

    use_metric = ui_state.is_metric
    conv = MS_TO_KPH if use_metric else MS_TO_MPH
    unit = "kph" if use_metric else "mph"
    fast_txt = f"{max_s * conv:.0f} {unit}"
    slow_txt = f"{min_s * conv:.0f} {unit}"

    fs = measure_text_cached(font_medium, fast_txt, 16)
    ss = measure_text_cached(font_medium, slow_txt, 16)
    rl.draw_text_ex(font_medium, fast_txt,
                    rl.Vector2(legend_bar_x - fs.x - 6, legend_y),
                    16, 0, rl.Color(0, 255, 255, 200))
    rl.draw_text_ex(font_medium, slow_txt,
                    rl.Vector2(legend_bar_x - ss.x - 6, legend_y + legend_h - 14),
                    16, 0, rl.Color(255, 140, 0, 200))

    # Stats bar
    stats_y = rect.y + rect.height - stats_h
    rl.draw_rectangle(int(rect.x), int(stats_y), int(rect.width), 1,
                      rl.Color(50, 50, 50, 200))

    dist_km = ref["total_distance"] / 1000.0
    conf = ref["line_confidence"]
    source = ref["source_laps"]

    stats = [
      (f"{dist_km:.2f} km", "LENGTH"),
      (f"{self._completed_laps}", "LAPS"),
      (f"{conf:.0%}", "CONFIDENCE"),
      (f"{source}", "SRC LAPS"),
    ]
    col_w = rect.width / len(stats)
    for i, (val, label) in enumerate(stats):
      col_cx = rect.x + i * col_w + col_w / 2
      vs = measure_text_cached(font_bold, val, 26)
      ls = measure_text_cached(font_medium, label, 17)
      rl.draw_text_ex(font_bold, val,
                      rl.Vector2(col_cx - vs.x / 2, stats_y + 10), 26, 0, rl.WHITE)
      rl.draw_text_ex(font_medium, label,
                      rl.Vector2(col_cx - ls.x / 2, stats_y + 44), 17, 0,
                      rl.Color(120, 120, 120, 255))
