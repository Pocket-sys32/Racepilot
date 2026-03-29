import pyray as rl
from collections.abc import Callable
from dataclasses import dataclass
from openpilot.common.constants import CV
import os as _os
from openpilot.common.params import Params
from openpilot.selfdrive.dragy.drag_service import DragService, DragState, CountdownPhase
from openpilot.selfdrive.ui.mici.onroad.torque_bar import TorqueBar
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.common.filter_simple import FirstOrderFilter
from cereal import log

EventName = log.OnroadEvent.EventName

# Constants
SET_SPEED_NA = 255
KM_TO_MILE = 0.621371
CRUISE_DISABLED_CHAR = '–'

SET_SPEED_PERSISTENCE = 2.5  # seconds


@dataclass(frozen=True)
class FontSizes:
  current_speed: int = 176
  speed_unit: int = 66
  max_speed: int = 36
  set_speed: int = 112
  track_mode: int = 22


@dataclass(frozen=True)
class Colors:
  WHITE = rl.WHITE
  WHITE_TRANSLUCENT = rl.Color(255, 255, 255, 200)


FONT_SIZES = FontSizes()
COLORS = Colors()


class TurnIntent(Widget):
  FADE_IN_ANGLE = 30  # degrees

  def __init__(self):
    super().__init__()
    self._pre = False
    self._turn_intent_direction: int = 0

    self._turn_intent_alpha_filter = FirstOrderFilter(0, 0.05, 1 / gui_app.target_fps)
    self._turn_intent_rotation_filter = FirstOrderFilter(0, 0.1, 1 / gui_app.target_fps)

    self._txt_turn_intent_left: rl.Texture = gui_app.texture('icons_mici/turn_intent_left.png', 50, 20)
    self._txt_turn_intent_right: rl.Texture = gui_app.texture('icons_mici/turn_intent_left.png', 50, 20, flip_x=True)

  def _render(self, _):
    if self._turn_intent_alpha_filter.x > 1e-2:
      turn_intent_texture = self._txt_turn_intent_right if self._turn_intent_direction == 1 else self._txt_turn_intent_left
      src_rect = rl.Rectangle(0, 0, turn_intent_texture.width, turn_intent_texture.height)
      dest_rect = rl.Rectangle(self._rect.x + self._rect.width / 2, self._rect.y + self._rect.height / 2,
                               turn_intent_texture.width, turn_intent_texture.height)

      origin = (turn_intent_texture.width / 2, self._rect.height / 2)
      color = rl.Color(255, 255, 255, int(255 * self._turn_intent_alpha_filter.x))
      rl.draw_texture_pro(turn_intent_texture, src_rect, dest_rect, origin, self._turn_intent_rotation_filter.x, color)

  def _update_state(self) -> None:
    sm = ui_state.sm

    left = any(e.name == EventName.preLaneChangeLeft for e in sm['onroadEvents'])
    right = any(e.name == EventName.preLaneChangeRight for e in sm['onroadEvents'])
    if left or right:
      # pre lane change
      if not self._pre:
        self._turn_intent_rotation_filter.x = self.FADE_IN_ANGLE if left else -self.FADE_IN_ANGLE

      self._pre = True
      self._turn_intent_direction = -1 if left else 1
      self._turn_intent_alpha_filter.update(1)
      self._turn_intent_rotation_filter.update(0)
    elif any(e.name == EventName.laneChange for e in sm['onroadEvents']):
      # fade out and rotate away
      self._pre = False
      self._turn_intent_alpha_filter.update(0)

      if self._turn_intent_direction == 0:
        # unknown. missed pre frame?
        self._turn_intent_rotation_filter.update(0)
      else:
        self._turn_intent_rotation_filter.update(self._turn_intent_direction * self.FADE_IN_ANGLE)
    else:
      # didn't complete lane change, just hide
      self._pre = False
      self._turn_intent_direction = 0
      self._turn_intent_alpha_filter.update(0)
      self._turn_intent_rotation_filter.update(0)


_TRACK_RESET_FLAG = "/tmp/track_mode_reset"


class TrackButton(Widget):
  SIZE = 42
  PADDING = 14

  def __init__(self):
    super().__init__()
    self._params = Params()
    self._press_flash_t: float = -1.0

  def _draw_checkered(self, x: float, y: float, w: float, h: float, alpha: int) -> None:
    sq = 8
    cols = int(w / sq) + 1
    rows = int(h / sq) + 1
    for row in range(rows):
      for col in range(cols):
        if (row + col) % 2 == 0:
          rx = int(x + col * sq)
          ry = int(y + row * sq)
          rw = int(min(sq, x + w - rx))
          rh = int(min(sq, y + h - ry))
          if rw > 0 and rh > 0:
            rl.draw_rectangle(rx, ry, rw, rh, rl.Color(255, 255, 255, alpha))

  def _handle_mouse_release(self, mouse_pos) -> None:
    self._press_flash_t = rl.get_time()
    currently_on = self._params.get_bool("TrackMode")
    if currently_on:
      self._params.put_bool("TrackMode", False)
    else:
      self._params.put_bool("TrackMode", True)
      # Reset session so the new lap starts fresh
      open(_TRACK_RESET_FLAG, "w").close()

  def _render(self, rect: rl.Rectangle) -> None:
    track_on = self._params.get_bool("TrackMode")
    sm = ui_state.sm
    ts = sm["trackState"] if sm.valid["trackState"] else None

    flash = max(0.0, 1.0 - (rl.get_time() - self._press_flash_t) / 0.25) if self._press_flash_t > 0 else 0.0

    if flash > 0:
      bg = rl.Color(255, 255, 255, 70)
      border_col = rl.Color(255, 255, 255, 220)
    elif not track_on:
      # Off — dim grey
      bg = rl.Color(80, 80, 80, 30)
      border_col = rl.Color(120, 120, 120, 120)
    elif ts is not None and ts.learnedReady and not ts.exploratory:
      # Learned — following line
      bg = rl.Color(0, 255, 255, 40)
      border_col = rl.Color(0, 255, 255, 200)
    else:
      # On + exploratory — learning lap
      bg = rl.Color(255, 183, 77, 50)
      border_col = rl.Color(255, 183, 77, 180)

    btn = rl.Rectangle(rect.x, rect.y, self.SIZE, self.SIZE)
    rl.draw_rectangle_rounded(btn, 0.3, 8, bg)
    rl.draw_rectangle_rounded_lines_ex(btn, 0.3, 8, 2, border_col)

    inner_pad = 10
    check_alpha = 60 if not track_on else (200 if (ts and ts.learnedReady) else 120)
    self._draw_checkered(rect.x + inner_pad, rect.y + inner_pad,
                         self.SIZE - inner_pad * 2, self.SIZE - inner_pad * 2,
                         check_alpha)

    # Small state dot bottom-right of button
    dot_col = border_col
    rl.draw_circle(int(rect.x + self.SIZE - 6), int(rect.y + self.SIZE - 6), 4, dot_col)


class DragButton(Widget):
  SIZE = 42
  PADDING = 14

  def __init__(self, callback: Callable | None = None):
    super().__init__()
    self._callback = callback
    self._alpha_filter = FirstOrderFilter(0, 0.08, 1 / gui_app.target_fps)
    self._press_flash_t: float = -1.0

  def _handle_mouse_release(self, mouse_pos) -> None:
    self._press_flash_t = rl.get_time()
    if self._callback:
      self._callback()

  def _render(self, rect: rl.Rectangle) -> None:
    sm = ui_state.sm
    if not (sm.valid.get("trackState", False) and sm["trackState"].active):
      self._alpha_filter.update(0)
      if self._alpha_filter.x < 1e-2:
        return
    else:
      self._alpha_filter.update(1.0)

    alpha = self._alpha_filter.x
    svc = DragService.get()
    state = svc.state
    flash = max(0.0, 1.0 - (rl.get_time() - self._press_flash_t) / 0.25) if self._press_flash_t > 0 else 0.0

    # Color by state
    if flash > 0:
      bg = rl.Color(255, 255, 255, int(80 * alpha))
      border_col = rl.Color(255, 255, 255, int(220 * alpha))
    elif state == DragState.RUNNING:
      bg = rl.Color(255, 140, 0, int(50 * alpha))
      border_col = rl.Color(255, 140, 0, int(200 * alpha))
    elif state == DragState.ARMED:
      pulse = abs((rl.get_time() % 1.0) * 2 - 1.0)  # 0→1→0
      bg = rl.Color(0, 255, 80, int(40 * alpha * pulse))
      border_col = rl.Color(0, 255, 80, int((120 + 80 * pulse) * alpha))
    else:
      has_runs = bool(svc.runs)
      c = 200 if has_runs else 120
      bg = rl.Color(0, c, c, int(30 * alpha))
      border_col = rl.Color(0, c, c, int(160 * alpha))

    btn = rl.Rectangle(rect.x, rect.y, self.SIZE, self.SIZE)
    rl.draw_rectangle_rounded(btn, 0.3, 8, bg)
    rl.draw_rectangle_rounded_lines_ex(btn, 0.3, 8, 2, border_col)

    # Drag strip icon: two parallel horizontal lines + finish line
    cx = rect.x + self.SIZE / 2
    cy = rect.y + self.SIZE / 2
    lane_w = int(self.SIZE * 0.55)
    lane_x = int(cx - lane_w / 2)
    lane_col = rl.Color(border_col.r, border_col.g, border_col.b, int(200 * alpha))

    rl.draw_line(lane_x, int(cy - 7), lane_x + lane_w, int(cy - 7), lane_col)
    rl.draw_line(lane_x, int(cy + 7), lane_x + lane_w, int(cy + 7), lane_col)
    # Finish line (right end)
    rl.draw_line(lane_x + lane_w, int(cy - 14), lane_x + lane_w, int(cy + 14), lane_col)
    # Start dot
    rl.draw_circle(lane_x, int(cy), 4, lane_col)

    # Run count badge
    runs = svc.runs
    if runs:
      badge = str(len(runs))
      bf = gui_app.font(FontWeight.BOLD)
      bs = measure_text_cached(bf, badge, 18)
      rl.draw_text_ex(bf, badge,
                      rl.Vector2(rect.x + self.SIZE - bs.x - 4, rect.y + 4),
                      18, 0, rl.Color(border_col.r, border_col.g, border_col.b, int(255 * alpha)))


class HudRenderer(Widget):
  def __init__(self):
    super().__init__()
    """Initialize the HUD renderer."""
    self.is_cruise_set: bool = False
    self.is_cruise_available: bool = True
    self.set_speed: float = SET_SPEED_NA
    self._set_speed_changed_time: float = 0
    self.speed: float = 0.0
    self.v_ego_cluster_seen: bool = False
    self._engaged: bool = False

    self._can_draw_top_icons = True
    self._show_wheel_critical = False

    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)
    self._font_semi_bold: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)
    self._font_display: rl.Font = gui_app.font(FontWeight.DISPLAY)

    self._turn_intent = TurnIntent()
    self._torque_bar = TorqueBar()
    self._track_button = TrackButton()
    self._drag_button = DragButton()

    self._txt_wheel: rl.Texture = gui_app.texture('icons_mici/wheel.png', 50, 50)
    self._txt_wheel_critical: rl.Texture = gui_app.texture('icons_mici/wheel_critical.png', 50, 50)
    self._txt_exclamation_point: rl.Texture = gui_app.texture('icons_mici/exclamation_point.png', 44, 44)

    self._wheel_alpha_filter = FirstOrderFilter(0, 0.05, 1 / gui_app.target_fps)
    self._wheel_y_filter = FirstOrderFilter(0, 0.1, 1 / gui_app.target_fps)

    self._set_speed_alpha_filter = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)

  def set_drag_callback(self, callback: Callable | None) -> None:
    self._drag_button._callback = callback

  def set_wheel_critical_icon(self, critical: bool):
    """Set the wheel icon to critical or normal state."""
    self._show_wheel_critical = critical

  def set_can_draw_top_icons(self, can_draw_top_icons: bool):
    """Set whether to draw the top part of the HUD."""
    self._can_draw_top_icons = can_draw_top_icons

  def drawing_top_icons(self) -> bool:
    # whether we're drawing any top icons currently
    return bool(self._set_speed_alpha_filter.x > 1e-2)

  def _update_state(self) -> None:
    """Update HUD state based on car state and controls state."""
    sm = ui_state.sm
    if sm.recv_frame["carState"] < ui_state.started_frame:
      self.is_cruise_set = False
      self.set_speed = SET_SPEED_NA
      self.speed = 0.0
      return

    controls_state = sm['controlsState']
    car_state = sm['carState']

    v_cruise_cluster = car_state.vCruiseCluster
    set_speed = (
      controls_state.vCruiseDEPRECATED if v_cruise_cluster == 0.0 else v_cruise_cluster
    )
    engaged = sm['selfdriveState'].enabled
    if (set_speed != self.set_speed and engaged) or (engaged and not self._engaged):
      self._set_speed_changed_time = rl.get_time()
    self._engaged = engaged
    self.set_speed = set_speed
    self.is_cruise_set = 0 < self.set_speed < SET_SPEED_NA
    self.is_cruise_available = self.set_speed != -1

    v_ego_cluster = car_state.vEgoCluster
    self.v_ego_cluster_seen = self.v_ego_cluster_seen or v_ego_cluster != 0.0
    v_ego = v_ego_cluster if self.v_ego_cluster_seen else car_state.vEgo
    speed_conversion = CV.MS_TO_KPH if ui_state.is_metric else CV.MS_TO_MPH
    self.speed = max(0.0, v_ego * speed_conversion)

  def _render(self, rect: rl.Rectangle) -> None:
    """Render HUD elements to the screen."""

    self._torque_bar.render(rect)

    if self.is_cruise_set:
      self._draw_set_speed(rect)

    self._draw_steering_wheel(rect)
    self._draw_track_mode_label(rect)
    if ui_state.status != UIStatus.DISENGAGED and self.speed > 0.5:
      self._draw_current_speed(rect)

    if self._can_draw_top_icons and ui_state.started:
      # Track button — top right corner
      # Resets the current lap / racing line session when tapped
      btn_x = rect.x + rect.width - TrackButton.SIZE - TrackButton.PADDING
      btn_y = rect.y + TrackButton.PADDING
      self._track_button.render(rl.Rectangle(btn_x, btn_y, TrackButton.SIZE, TrackButton.SIZE))
      lbl = "TRACK"
      ls = measure_text_cached(self._font_medium, lbl, 13)
      rl.draw_text_ex(self._font_medium, lbl,
                      rl.Vector2(btn_x + TrackButton.SIZE / 2 - ls.x / 2,
                                 btn_y + TrackButton.SIZE + 3),
                      13, 0, rl.Color(180, 180, 180, 160))

      # Drag button — below track button
      # Opens drag run history / shows 0-60 and ¼-mile timing
      drag_btn_y = btn_y + TrackButton.SIZE + 20
      self._drag_button.render(rl.Rectangle(btn_x, drag_btn_y, DragButton.SIZE, DragButton.SIZE))
      dlbl = "DRAG"
      ds = measure_text_cached(self._font_medium, dlbl, 13)
      rl.draw_text_ex(self._font_medium, dlbl,
                      rl.Vector2(btn_x + DragButton.SIZE / 2 - ds.x / 2,
                                 drag_btn_y + DragButton.SIZE + 3),
                      13, 0, rl.Color(180, 180, 180, 160))

    # Drag HUD overlay (christmas tree / run in progress / results)
    self._draw_drag_overlay(rect)

  def _draw_steering_wheel(self, rect: rl.Rectangle) -> None:
    wheel_txt = self._txt_wheel_critical if self._show_wheel_critical else self._txt_wheel

    if self._show_wheel_critical:
      self._wheel_alpha_filter.update(255)
      self._wheel_y_filter.update(0)
    else:
      if ui_state.status == UIStatus.DISENGAGED:
        self._wheel_alpha_filter.update(0)
        self._wheel_y_filter.update(wheel_txt.height / 2)
      else:
        self._wheel_alpha_filter.update(255 * 0.9)
        self._wheel_y_filter.update(0)

    # pos
    pos_x = int(rect.x + 21 + wheel_txt.width / 2)
    pos_y = int(rect.y + rect.height - 14 - wheel_txt.height / 2 + self._wheel_y_filter.x)
    rotation = -ui_state.sm['carState'].steeringAngleDeg

    turn_intent_margin = 25
    self._turn_intent.render(rl.Rectangle(
      pos_x - wheel_txt.width / 2 - turn_intent_margin,
      pos_y - wheel_txt.height / 2 - turn_intent_margin,
      wheel_txt.width + turn_intent_margin * 2,
      wheel_txt.height + turn_intent_margin * 2,
    ))

    src_rect = rl.Rectangle(0, 0, wheel_txt.width, wheel_txt.height)
    dest_rect = rl.Rectangle(pos_x, pos_y, wheel_txt.width, wheel_txt.height)
    origin = (wheel_txt.width / 2, wheel_txt.height / 2)

    # color and draw
    color = rl.Color(255, 255, 255, int(self._wheel_alpha_filter.x))
    rl.draw_texture_pro(wheel_txt, src_rect, dest_rect, origin, rotation, color)

    if self._show_wheel_critical:
      # Draw exclamation point icon
      EXCLAMATION_POINT_SPACING = 10
      exclamation_pos_x = pos_x - self._txt_exclamation_point.width / 2 + wheel_txt.width / 2 + EXCLAMATION_POINT_SPACING
      exclamation_pos_y = pos_y - self._txt_exclamation_point.height / 2
      rl.draw_texture_ex(self._txt_exclamation_point, rl.Vector2(exclamation_pos_x, exclamation_pos_y), 0.0, 1.0, rl.WHITE)

  def _draw_set_speed(self, rect: rl.Rectangle) -> None:
    """Draw the MAX speed indicator box."""
    alpha = self._set_speed_alpha_filter.update(0 < rl.get_time() - self._set_speed_changed_time < SET_SPEED_PERSISTENCE and
                                                self._can_draw_top_icons and self._engaged)
    if alpha < 1e-2:
      return

    x = rect.x
    y = rect.y

    # draw drop shadow
    circle_radius = 162 // 2
    rl.draw_circle_gradient(int(x + circle_radius), int(y + circle_radius), circle_radius,
                            rl.Color(0, 0, 0, int(255 / 2 * alpha)), rl.BLANK)

    set_speed_color = rl.Color(255, 255, 255, int(255 * 0.9 * alpha))
    max_color = rl.Color(255, 255, 255, int(255 * 0.9 * alpha))

    set_speed = self.set_speed
    if self.is_cruise_set and not ui_state.is_metric:
      set_speed *= KM_TO_MILE

    set_speed_text = CRUISE_DISABLED_CHAR if not self.is_cruise_set else str(round(set_speed))
    rl.draw_text_ex(
      self._font_display,
      set_speed_text,
      rl.Vector2(x + 13 + 4, y + 3 - 8 - 3 + 4),
      FONT_SIZES.set_speed,
      0,
      set_speed_color,
    )

    max_text = tr("MAX")
    rl.draw_text_ex(
      self._font_semi_bold,
      max_text,
      rl.Vector2(x + 25, y + FONT_SIZES.set_speed - 7 + 4),
      FONT_SIZES.max_speed,
      0,
      max_color,
    )

  def _draw_track_mode_label(self, rect: rl.Rectangle) -> None:
    sm = ui_state.sm
    if not self._can_draw_top_icons or not sm.valid["trackState"] or not sm["trackState"].active:
      return

    label = tr("TRACK MODE")
    exploratory = sm["trackState"].exploratory
    bg = rl.Color(255, 183, 77, 55) if exploratory else rl.Color(0, 255, 255, 40)
    border = rl.Color(255, 183, 77, 180) if exploratory else rl.Color(0, 255, 255, 200)
    text = rl.Color(255, 220, 170, 255) if exploratory else rl.Color(0, 255, 255, 255)

    text_size = measure_text_cached(self._font_semi_bold, label, FONT_SIZES.track_mode)
    width = text_size.x + 60
    height = text_size.y + 24
    x = rect.x + (rect.width - width) / 2
    y = rect.y + 24
    label_rect = rl.Rectangle(x, y, width, height)
    rl.draw_rectangle_rounded(label_rect, 0.45, 8, bg)
    rl.draw_rectangle_rounded_lines_ex(label_rect, 0.45, 8, 3, border)
    rl.draw_text_ex(self._font_semi_bold, label, rl.Vector2(x + (width - text_size.x) / 2, y + (height - text_size.y) / 2 - 1),
                    FONT_SIZES.track_mode, 0, text)

  def _draw_current_speed(self, rect: rl.Rectangle) -> None:
    """Draw the current vehicle speed and unit at bottom right."""
    speed_font_size = 42
    unit_font_size = 15

    speed_text = str(round(self.speed))
    speed_text_size = measure_text_cached(self._font_bold, speed_text, speed_font_size)

    unit_text = tr("km/h") if ui_state.is_metric else tr("mph")
    unit_text_size = measure_text_cached(self._font_medium, unit_text, unit_font_size)

    margin = 10
    block_right = rect.x + rect.width - margin
    block_cx = block_right - speed_text_size.x / 2
    speed_x = block_cx - speed_text_size.x / 2
    unit_x = block_cx - unit_text_size.x / 2

    speed_y = rect.y + rect.height - margin - unit_text_size.y - speed_text_size.y
    unit_y = rect.y + rect.height - margin - unit_text_size.y

    rl.draw_text_ex(self._font_bold, speed_text, rl.Vector2(speed_x, speed_y), speed_font_size, 0, COLORS.WHITE)
    rl.draw_text_ex(self._font_medium, unit_text, rl.Vector2(unit_x, unit_y), unit_font_size, 0, COLORS.WHITE_TRANSLUCENT)

  def _draw_drag_overlay(self, rect: rl.Rectangle) -> None:
    svc = DragService.get()
    state = svc.state
    if state == DragState.IDLE:
      return

    font_bold = self._font_bold
    font_medium = self._font_medium
    font_semi = self._font_semi_bold

    if state == DragState.ARMED:
      self._draw_christmas_tree(rect, svc.countdown_phase)
      return

    # RUNNING or DONE — semi-transparent overlay
    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height),
                      rl.Color(0, 0, 0, 160))

    elapsed = svc.current_elapsed
    speed = svc.current_speed_mph
    distance = svc.current_distance
    accel = svc.current_accel
    m = svc.current_milestones

    if state == DragState.DONE:
      hdr = "RUN COMPLETE"
      hdr_col = rl.Color(0, 255, 255, 255)
    else:
      hdr = f"{elapsed:.1f}s"
      hdr_col = rl.WHITE

    # Timer / header
    hs = measure_text_cached(font_bold, hdr, 80)
    rl.draw_text_ex(font_bold, hdr,
                    rl.Vector2(rect.x + rect.width / 2 - hs.x / 2, rect.y + 30),
                    80, 0, hdr_col)

    # Speed
    spd_txt = f"{speed:.0f}"
    t_accel = min(max(accel / 5.0, 0.0), 1.0)
    r = int(255 * t_accel)
    g = int(140 + 115 * (1.0 - t_accel))
    b = int(255 * (1.0 - t_accel))
    ss = measure_text_cached(font_bold, spd_txt, 120)
    rl.draw_text_ex(font_bold, spd_txt,
                    rl.Vector2(rect.x + rect.width / 2 - ss.x / 2, rect.y + 130),
                    120, 0, rl.Color(r, g, b, 230))
    mph_s = measure_text_cached(font_medium, "mph", 28)
    rl.draw_text_ex(font_medium, "mph",
                    rl.Vector2(rect.x + rect.width / 2 - mph_s.x / 2, rect.y + 260),
                    28, 0, rl.Color(180, 180, 180, 200))

    # Milestones (right side)
    milestones = [
      ("0-60",   m.get("t0_60"),   None),
      ("60-130", m.get("t60_130"), None),
      ("¼ mile", m.get("qm_et"),   m.get("qm_speed")),
    ]
    mx = rect.x + rect.width * 0.60
    my = rect.y + 130
    for label, val_t, val_spd in milestones:
      done = val_t is not None
      col = rl.Color(0, 255, 255, 255) if done else rl.Color(80, 80, 80, 200)
      if val_spd is not None:
        line = f"{label}: {val_t:.2f}s @ {val_spd:.0f}mph" if done else f"{label}: --"
      else:
        line = f"{label}: {val_t:.2f}s" if done else f"{label}: --"
      rl.draw_text_ex(font_medium, line, rl.Vector2(mx, my), 26, 0, col)
      my += 40

    # Distance bar
    bar_y = rect.y + rect.height - 50
    bar_x = rect.x + 30
    bar_w = rect.width - 60
    bar_h = 12
    rl.draw_rectangle(int(bar_x), int(bar_y), int(bar_w), bar_h, rl.Color(40, 40, 40, 200))
    fill = min(distance / 402.336, 1.0)
    if fill > 0:
      rl.draw_rectangle(int(bar_x), int(bar_y), int(bar_w * fill), bar_h,
                        rl.Color(0, 255, 255, 180))
    lbl = f"{distance:.0f} / 402 m"
    ls = measure_text_cached(font_medium, lbl, 18)
    rl.draw_text_ex(font_medium, lbl,
                    rl.Vector2(rect.x + rect.width / 2 - ls.x / 2, bar_y + bar_h + 4),
                    18, 0, rl.Color(100, 100, 100, 200))

  def _draw_christmas_tree(self, rect: rl.Rectangle, phase: CountdownPhase) -> None:
    """Draw red/yellow/green staging lights center-screen."""
    cx = rect.x + rect.width / 2
    cy = rect.y + rect.height / 2

    circle_r = 36
    gap = 20
    total_h = 3 * circle_r * 2 + 2 * gap
    top_y = cy - total_h / 2 + circle_r

    lights = [
      (CountdownPhase.RED,    rl.Color(255, 50,  50,  255), rl.Color(80, 20, 20, 180)),
      (CountdownPhase.YELLOW, rl.Color(255, 220, 0,   255), rl.Color(80, 70,  0, 180)),
      (CountdownPhase.GREEN,  rl.Color(0,   255, 80,  255), rl.Color(0,  80, 30, 180)),
    ]

    for i, (lphase, lit_col, dim_col) in enumerate(lights):
      lx = int(cx)
      ly = int(top_y + i * (circle_r * 2 + gap))
      lit = phase >= lphase
      col = lit_col if lit else dim_col
      rl.draw_circle(lx, ly, circle_r, col)
      rl.draw_circle_lines(lx, ly, circle_r, rl.Color(255, 255, 255, 60))

    # Label
    labels = {
      CountdownPhase.RED: "STAGE",
      CountdownPhase.YELLOW: "READY",
      CountdownPhase.GREEN: "GO!",
    }
    lbl = labels[phase]
    font_bold = self._font_bold
    ls = measure_text_cached(font_bold, lbl, 40)
    rl.draw_text_ex(font_bold, lbl,
                    rl.Vector2(cx - ls.x / 2, top_y + 3 * (circle_r * 2 + gap) + 10),
                    40, 0, rl.WHITE)
