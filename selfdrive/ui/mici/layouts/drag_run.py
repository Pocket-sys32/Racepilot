import time
import datetime

import pyray as rl

from openpilot.selfdrive.dragy.drag_service import DragService, DragState
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets.nav_widget import NavWidget

QM_DISTANCE = 402.336
_PAD = 30


def _fmt_time(t: float | None) -> str:
  if t is None:
    return "--.-s"
  return f"{t:.2f}s"


def _fmt_speed(s: float | None) -> str:
  if s is None:
    return "---"
  return f"{s:.0f}"


class DragRunLayout(NavWidget):
  def __init__(self):
    super().__init__()

  def _render(self, rect: rl.Rectangle) -> None:
    svc = DragService.get()
    state = svc.state

    font_bold = gui_app.font(FontWeight.BOLD)
    font_medium = gui_app.font(FontWeight.MEDIUM)
    font_semi = gui_app.font(FontWeight.SEMI_BOLD)

    # Background
    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height),
                      rl.Color(8, 10, 16, 255))

    if state == DragState.RUNNING or state == DragState.DONE:
      self._render_live(rect, svc, font_bold, font_medium, font_semi)
    else:
      self._render_history(rect, svc, font_bold, font_medium, font_semi)

  def _render_live(self, rect, svc, font_bold, font_medium, font_semi):
    state = svc.state
    m = svc.current_milestones
    elapsed = svc.current_elapsed
    speed = svc.current_speed_mph
    distance = svc.current_distance
    accel = svc.current_accel

    # Header
    if state == DragState.DONE:
      hdr = "RUN COMPLETE"
      hdr_col = rl.Color(0, 255, 255, 255)
    else:
      hdr = "RUN IN PROGRESS"
      hdr_col = rl.Color(255, 220, 80, 255)

    hs = measure_text_cached(font_semi, hdr, 30)
    rl.draw_text_ex(font_semi, hdr,
                    rl.Vector2(rect.x + rect.width / 2 - hs.x / 2, rect.y + 18),
                    30, 0, hdr_col)

    # Timer
    timer_txt = f"{elapsed:.1f}s" if state == DragState.RUNNING else _fmt_time(elapsed if elapsed > 0 else None)
    ts = measure_text_cached(font_bold, timer_txt, 72)
    rl.draw_text_ex(font_bold, timer_txt,
                    rl.Vector2(rect.x + rect.width / 2 - ts.x / 2, rect.y + 60),
                    72, 0, rl.WHITE)

    # Speed
    spd_txt = f"{speed:.0f} mph"
    t_accel = min(max(accel / 5.0, 0.0), 1.0)  # 0=cyan, 1=orange
    r = int(255 * t_accel)
    g = int(140 + 115 * (1.0 - t_accel))
    b = int(255 * (1.0 - t_accel))
    ss = measure_text_cached(font_bold, spd_txt, 56)
    rl.draw_text_ex(font_bold, spd_txt,
                    rl.Vector2(rect.x + rect.width / 2 - ss.x / 2, rect.y + 150),
                    56, 0, rl.Color(r, g, b, 230))

    # Milestones
    milestones = [
      ("0-60",     m.get("t0_60"),   None),
      ("60-130",   m.get("t60_130"), None),
      ("¼ mile",   m.get("qm_et"),   m.get("qm_speed")),
    ]
    my = rect.y + 230
    for label, val_t, val_spd in milestones:
      done = val_t is not None
      col = rl.Color(0, 255, 255, 255) if done else rl.Color(100, 100, 100, 200)
      if val_spd is not None:
        line = f"{label}:  {_fmt_time(val_t)} @ {_fmt_speed(val_spd)} mph"
      else:
        line = f"{label}:  {_fmt_time(val_t)}"
      ls = measure_text_cached(font_medium, line, 30)
      rl.draw_text_ex(font_medium, line,
                      rl.Vector2(rect.x + rect.width / 2 - ls.x / 2, my),
                      30, 0, col)
      my += 44

    # Distance progress bar
    bar_y = rect.y + rect.height - 60
    bar_x = rect.x + _PAD
    bar_w = rect.width - _PAD * 2
    bar_h = 14
    rl.draw_rectangle(int(bar_x), int(bar_y), int(bar_w), bar_h, rl.Color(40, 40, 40, 200))
    fill = min(distance / QM_DISTANCE, 1.0)
    if fill > 0:
      rl.draw_rectangle(int(bar_x), int(bar_y), int(bar_w * fill), bar_h,
                        rl.Color(0, 255, 255, 200))
    # Label
    lbl = f"{distance:.0f}m / 402m"
    ls = measure_text_cached(font_medium, lbl, 20)
    rl.draw_text_ex(font_medium, lbl,
                    rl.Vector2(rect.x + rect.width / 2 - ls.x / 2, bar_y + bar_h + 6),
                    20, 0, rl.Color(120, 120, 120, 200))

  def _render_history(self, rect, svc, font_bold, font_medium, font_semi):
    runs = svc.runs

    # Title
    title = "DRAG RUNS"
    ts = measure_text_cached(font_semi, title, 34)
    rl.draw_text_ex(font_semi, title,
                    rl.Vector2(rect.x + rect.width / 2 - ts.x / 2, rect.y + 18),
                    34, 0, rl.Color(0, 255, 255, 220))

    if not runs:
      lines = [
        "No runs recorded yet.",
        "Track mode must be active.",
        "Stage at standstill and launch hard.",
      ]
      font_size = 24
      gap = 10
      total_h = len(lines) * font_size + (len(lines) - 1) * gap
      sy = rect.y + rect.height / 2 - total_h / 2
      for i, line in enumerate(lines):
        lw = measure_text_cached(font_medium, line, font_size).x
        rl.draw_text_ex(font_medium, line,
                        rl.Vector2(rect.x + rect.width / 2 - lw / 2,
                                   sy + i * (font_size + gap)),
                        font_size, 0, rl.Color(140, 140, 140, 200))
      return

    # Column headers
    hy = rect.y + 70
    cols = ["DATE", "0-60", "60-130", "¼ MI ET", "TRAP"]
    col_w = rect.width / len(cols)
    for i, col in enumerate(cols):
      cx = rect.x + i * col_w + col_w / 2
      ws = measure_text_cached(font_medium, col, 18)
      rl.draw_text_ex(font_medium, col,
                      rl.Vector2(cx - ws.x / 2, hy), 18, 0,
                      rl.Color(100, 100, 100, 200))

    rl.draw_rectangle(int(rect.x + _PAD), int(hy + 24),
                      int(rect.width - _PAD * 2), 1, rl.Color(50, 50, 50, 200))

    # Rows
    row_h = 54
    row_y = hy + 32
    visible = int((rect.height - row_y + rect.y) / row_h)

    for i, run in enumerate(runs[:visible]):
      if row_y + row_h > rect.y + rect.height - 20:
        break

      col_v = rl.WHITE if not run.partial else rl.Color(180, 140, 80, 255)

      dt = datetime.datetime.fromtimestamp(run.timestamp)
      date_str = dt.strftime("%m/%d %H:%M")

      values = [
        date_str,
        _fmt_time(run.t0_60),
        _fmt_time(run.t60_130),
        _fmt_time(run.qm_et),
        f"{_fmt_speed(run.qm_speed_mph)} mph",
      ]

      for j, val in enumerate(values):
        cx = rect.x + j * col_w + col_w / 2
        vs = measure_text_cached(font_bold if j > 0 else font_medium, val, 26)
        rl.draw_text_ex(font_bold if j > 0 else font_medium, val,
                        rl.Vector2(cx - vs.x / 2, row_y + (row_h - 26) / 2),
                        26, 0, col_v)

      # Separator
      rl.draw_rectangle(int(rect.x + _PAD), int(row_y + row_h - 1),
                        int(rect.width - _PAD * 2), 1, rl.Color(35, 35, 35, 200))
      row_y += row_h
