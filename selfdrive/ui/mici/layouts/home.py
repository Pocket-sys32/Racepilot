import datetime
import math
import time
from pathlib import Path

from cereal import log
import pyray as rl
from collections.abc import Callable
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.layouts import HBoxLayout
from openpilot.system.ui.widgets.icon_widget import IconWidget
from openpilot.system.ui.widgets.label import UnifiedLabel
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths
from openpilot.system.version import RELEASE_BRANCHES

HEAD_BUTTON_FONT_SIZE = 40
HOME_PADDING = 8

NetworkType = log.DeviceState.NetworkType

NETWORK_TYPES = {
  NetworkType.none: "Offline",
  NetworkType.wifi: "WiFi",
  NetworkType.cell2G: "2G",
  NetworkType.cell3G: "3G",
  NetworkType.cell4G: "LTE",
  NetworkType.cell5G: "5G",
  NetworkType.ethernet: "Ethernet",
}


class NetworkIcon(Widget):
  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, 54, 44))  # max size of all icons
    self._net_type = NetworkType.none
    self._net_strength = 0

    self._wifi_slash_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_slash.png", 50, 44)
    self._wifi_none_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_none.png", 50, 37)
    self._wifi_low_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_low.png", 50, 37)
    self._wifi_medium_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_medium.png", 50, 37)
    self._wifi_full_txt = gui_app.texture("icons_mici/settings/network/wifi_strength_full.png", 50, 37)

    self._cell_none_txt = gui_app.texture("icons_mici/settings/network/cell_strength_none.png", 54, 36)
    self._cell_low_txt = gui_app.texture("icons_mici/settings/network/cell_strength_low.png", 54, 36)
    self._cell_medium_txt = gui_app.texture("icons_mici/settings/network/cell_strength_medium.png", 54, 36)
    self._cell_high_txt = gui_app.texture("icons_mici/settings/network/cell_strength_high.png", 54, 36)
    self._cell_full_txt = gui_app.texture("icons_mici/settings/network/cell_strength_full.png", 54, 36)

  def _update_state(self):
    device_state = ui_state.sm['deviceState']
    self._net_type = device_state.networkType
    strength = device_state.networkStrength
    self._net_strength = max(0, min(5, strength.raw + 1)) if strength.raw > 0 else 0

  def _render(self, _):
    if self._net_type == NetworkType.wifi:
      # There is no 1
      draw_net_txt = {0: self._wifi_none_txt,
                      2: self._wifi_low_txt,
                      3: self._wifi_medium_txt,
                      4: self._wifi_full_txt,
                      5: self._wifi_full_txt}.get(self._net_strength, self._wifi_low_txt)
    elif self._net_type in (NetworkType.cell2G, NetworkType.cell3G, NetworkType.cell4G, NetworkType.cell5G):
      draw_net_txt = {0: self._cell_none_txt,
                      2: self._cell_low_txt,
                      3: self._cell_medium_txt,
                      4: self._cell_high_txt,
                      5: self._cell_full_txt}.get(self._net_strength, self._cell_none_txt)
    else:
      draw_net_txt = self._wifi_slash_txt

    draw_x = self._rect.x + (self._rect.width - draw_net_txt.width) / 2
    draw_y = self._rect.y + (self._rect.height - draw_net_txt.height) / 2

    if draw_net_txt == self._wifi_slash_txt:
      # Offset by difference in height between slashless and slash icons to make center align match
      draw_y -= (self._wifi_slash_txt.height - self._wifi_none_txt.height) / 2

    rl.draw_texture_ex(draw_net_txt, rl.Vector2(draw_x, draw_y), 0.0, 1.0, rl.Color(255, 255, 255, int(255 * 0.9)))


class TrackIconWidget(Widget):
  """Draws a mini track oval icon. Glows cyan if learned data exists."""

  SIZE = 48

  def __init__(self):
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, self.SIZE, self.SIZE))
    self._has_data = False
    self._last_check = -999.0

  def _update_state(self):
    # Check for saved track data every 10 seconds
    now = time.monotonic()
    if now - self._last_check > 10.0:
      self._last_check = now
      track_dir = (Path(Paths.comma_home()) / "media" / "0" if PC else Path("/data/media/0")) / "track_mode"
      self._has_data = track_dir.exists() and any(track_dir.glob("*.json"))

  def _render(self, _) -> None:
    cx = self._rect.x + self._rect.width / 2
    cy = self._rect.y + self._rect.height / 2
    rw = int(self._rect.width * 0.46)
    rh = int(self._rect.height * 0.30)

    col = rl.Color(0, 255, 255, 220) if self._has_data else rl.Color(180, 180, 180, 160)
    thick = 3 if self._has_data else 2

    # Outer oval
    rl.draw_ellipse_lines(int(cx), int(cy), rw, rh, col)

    # Inner oval (narrower track)
    rl.draw_ellipse_lines(int(cx), int(cy), max(rw - 7, 4), max(rh - 6, 2),
                          rl.Color(col.r, col.g, col.b, 100))

    # Start/finish line tick
    tick_x = int(cx + rw)
    rl.draw_line(tick_x, int(cy - rh), tick_x, int(cy + rh), col)

    # Dot — glows when data present
    if self._has_data:
      rl.draw_circle(int(cx + rw - 4), int(cy), 5, rl.Color(0, 255, 255, 255))


class VoiceIconWidget(Widget):
  """Microphone icon. Shows PTT state and peer connection status."""
  SIZE = 42

  def __init__(self) -> None:
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, self.SIZE, self.SIZE))
    self._pulse_t: float = 0.0

  def _render(self, _) -> None:
    from openpilot.selfdrive.voice.voice_service import VoiceService, VoiceState
    svc       = VoiceService.get()
    state     = svc.state
    mic_muted = svc.mic_muted
    t         = rl.get_time()

    cx  = int(self._rect.x + self._rect.width / 2)
    cy  = int(self._rect.y + self._rect.height / 2)
    bw  = int(self.SIZE * 0.30)   # capsule half-width
    bh  = int(self.SIZE * 0.38)   # capsule half-height
    br  = bw                      # corner radius = half-width → full pill

    # Pick colour
    if mic_muted:
      col  = rl.Color(220, 60, 60, 200)
      fill = False
    elif state == VoiceState.OFFLINE:
      col   = rl.Color(120, 120, 120, 100)
      fill  = False
    elif state == VoiceState.CONNECTING:
      alpha = int(140 + 80 * abs(math.sin(t * 2.5)))
      col   = rl.Color(220, 220, 220, alpha)
      fill  = False
    elif state == VoiceState.TALKING:
      col  = rl.Color(60, 220, 80, 240)
      fill = True
    elif state == VoiceState.PEER_TALKING:
      alpha = int(180 + 60 * abs(math.sin(t * 4.0)))
      col   = rl.Color(0, 220, 255, alpha)
      fill  = False
      # expanding ring
      ring_r = int(self.SIZE * 0.42 + 8 * abs(math.sin(t * 4.0)))
      rl.draw_circle_lines(cx, cy, ring_r, rl.Color(0, 220, 255, 80))
    else:
      col   = rl.Color(0, 200, 80, 180)
      fill  = False

    # Mic capsule body
    cap_rect = rl.Rectangle(cx - bw, cy - bh, bw * 2, bh * 2)
    if fill:
      rl.draw_rectangle_rounded(cap_rect, 1.0, 8, col)
    else:
      rl.draw_rectangle_rounded_lines(cap_rect, 1.0, 8, col)

    # Mic stand arc + base
    base_y = cy + bh + 2
    rl.draw_line(cx, base_y, cx, base_y + 5, col)
    rl.draw_line(cx - bw, base_y + 5, cx + bw, base_y + 5, col)
    # Stand arc (half circle below capsule)
    rl.draw_ring_lines(rl.Vector2(float(cx), float(cy + bh + 2)), float(bw), float(bw + 3), 180.0, 360.0, 12, col)

    # Red slash when mic muted
    if mic_muted:
      sl = int(self.SIZE * 0.38)
      rl.draw_line_ex(
        rl.Vector2(float(cx - sl // 2), float(cy - sl // 2)),
        rl.Vector2(float(cx + sl // 2), float(cy + sl // 2)),
        2.5, rl.Color(220, 60, 60, 240),
      )


class MuteIconWidget(Widget):
  """Speaker icon. Tap to toggle speaker mute (echo prevention for demos)."""
  SIZE = 42

  def __init__(self) -> None:
    super().__init__()
    self.set_rect(rl.Rectangle(0, 0, self.SIZE, self.SIZE))

  def _render(self, _) -> None:
    from openpilot.selfdrive.voice.voice_service import VoiceService
    muted = VoiceService.get().speaker_muted

    cx  = int(self._rect.x + self._rect.width / 2)
    cy  = int(self._rect.y + self._rect.height / 2)
    col = rl.Color(220, 60, 60, 220) if muted else rl.Color(140, 140, 140, 180)

    # Classic speaker icon: box + trapezoid cone + wave arcs
    # Layout (left → right): [box][cone][waves]
    box_x = cx - 17
    box_w = 7
    box_h = 11
    box_y = cy - box_h // 2

    # Box (speaker enclosure)
    rl.draw_rectangle(box_x, box_y, box_w, box_h, col)

    # Cone — trapezoid widening to the right, drawn as two triangles
    cx0 = box_x + box_w          # inner x (narrow end)
    cx1 = box_x + box_w + 10     # outer x (wide end)
    inner_h = box_h // 2         # half-height at narrow end
    outer_h = box_h              # half-height at wide end

    p_tl = rl.Vector2(float(cx0), float(cy - inner_h))
    p_bl = rl.Vector2(float(cx0), float(cy + inner_h))
    p_tr = rl.Vector2(float(cx1), float(cy - outer_h))
    p_br = rl.Vector2(float(cx1), float(cy + outer_h))

    rl.draw_triangle(p_tl, p_bl, p_br, col)
    rl.draw_triangle(p_tl, p_br, p_tr, col)

    # Sound waves (arcs) or mute slash
    wave_cx = float(cx1 + 1)
    if muted:
      sl = 15
      rl.draw_line_ex(
        rl.Vector2(float(cx - sl // 2 + 4), float(cy - sl // 2)),
        rl.Vector2(float(cx + sl // 2 + 4), float(cy + sl // 2)),
        2.5, rl.Color(220, 60, 60, 255),
      )
    else:
      rl.draw_ring_lines(rl.Vector2(wave_cx, float(cy)), 5.0, 7.0, -50.0, 50.0, 8, col)
      rl.draw_ring_lines(rl.Vector2(wave_cx, float(cy)), 10.0, 12.0, -50.0, 50.0, 8, col)


class MiciHomeLayout(Widget):
  def __init__(self):
    super().__init__()
    self._on_settings_click: Callable | None = None
    self._on_games_click: Callable | None = None
    self._on_track_click: Callable | None = None

    self._last_refresh = 0
    self._mouse_down_t: None | float = None
    self._did_long_press = False
    self._is_pressed_prev = False

    self._version_text = None
    self._experimental_mode = False

    self._on_voice_click: Callable | None = None
    self._on_mute_click: Callable | None = None

    self._experimental_icon = IconWidget("icons_mici/experimental_mode.png", (48, 48))
    self._mic_icon = IconWidget("icons_mici/microphone.png", (32, 46))
    self._games_icon = IconWidget("icons_mici/settings/games.png", (48, 48), opacity=0.9)
    self._track_icon = TrackIconWidget()
    self._voice_icon = VoiceIconWidget()
    self._mute_icon  = MuteIconWidget()

    self._status_bar_layout = HBoxLayout([
      IconWidget("icons_mici/settings.png", (48, 48), opacity=0.9),
      NetworkIcon(),
      self._experimental_icon,
      self._mic_icon,
      self._games_icon,
      self._track_icon,
      self._voice_icon,
      self._mute_icon,
    ], spacing=18)

    self._race_label = UnifiedLabel("race", font_size=72, font_weight=FontWeight.DISPLAY, max_width=480, wrap_text=False,
                                    text_color=rl.Color(0, 255, 255, 255))
    self._comma_label = UnifiedLabel("pilot", font_size=72, font_weight=FontWeight.DISPLAY, max_width=480, wrap_text=False,
                                     text_color=rl.WHITE)
    self._version_label = UnifiedLabel("", font_size=36, font_weight=FontWeight.ROMAN, max_width=480, wrap_text=False)
    self._large_version_label = UnifiedLabel("", font_size=64, text_color=rl.GRAY, font_weight=FontWeight.ROMAN, max_width=480, wrap_text=False)
    self._date_label = UnifiedLabel("", font_size=36, text_color=rl.GRAY, font_weight=FontWeight.ROMAN, max_width=480, wrap_text=False)
    self._branch_label = UnifiedLabel("", font_size=36, text_color=rl.GRAY, font_weight=FontWeight.ROMAN, scroll=True)
    self._version_commit_label = UnifiedLabel("", font_size=36, text_color=rl.GRAY, font_weight=FontWeight.ROMAN, max_width=480, wrap_text=False)

  def show_event(self):
    super().show_event()
    self._version_text = self._get_version_text()
    self._update_params()

  def _update_params(self):
    self._experimental_mode = ui_state.params.get_bool("ExperimentalMode")

  def _update_state(self):
    if self.is_pressed and not self._is_pressed_prev:
      self._mouse_down_t = time.monotonic()
    elif not self.is_pressed and self._is_pressed_prev:
      self._mouse_down_t = None
      self._did_long_press = False
    self._is_pressed_prev = self.is_pressed

    if self._mouse_down_t is not None:
      if time.monotonic() - self._mouse_down_t > 0.5:
        # long gating for experimental mode - only allow toggle if longitudinal control is available
        if ui_state.has_longitudinal_control:
          self._experimental_mode = not self._experimental_mode
          ui_state.params.put("ExperimentalMode", self._experimental_mode)
        self._mouse_down_t = None
        self._did_long_press = True

    if rl.get_time() - self._last_refresh > 5.0:
      # Update version text
      self._version_text = self._get_version_text()
      self._last_refresh = rl.get_time()
      self._update_params()

  def set_callbacks(self, on_settings: Callable | None = None, on_games: Callable | None = None,
                    on_track: Callable | None = None, on_voice: Callable | None = None,
                    on_mute: Callable | None = None):
    self._on_settings_click = on_settings
    self._on_games_click    = on_games
    self._on_track_click    = on_track
    self._on_voice_click    = on_voice
    self._on_mute_click     = on_mute

  def _handle_mouse_release(self, mouse_pos: MousePos):
    if not self._did_long_press:
      def _in_icon(icon):
        r = icon.rect
        return r.x <= mouse_pos.x <= r.x + r.width and r.y <= mouse_pos.y <= r.y + r.height

      if self._on_mute_click and _in_icon(self._mute_icon):
        self._on_mute_click()
      elif self._on_voice_click and _in_icon(self._voice_icon):
        self._on_voice_click()
      elif self._on_track_click and _in_icon(self._track_icon):
        self._on_track_click()
      elif self._on_games_click and _in_icon(self._games_icon):
        self._on_games_click()
      elif self._on_settings_click:
        self._on_settings_click()
    self._did_long_press = False

  def _get_version_text(self) -> tuple[str, str, str, str] | None:
    version = ui_state.params.get("Version")
    branch = ui_state.params.get("GitBranch")
    commit = ui_state.params.get("GitCommit")

    if not all((version, branch, commit)):
      return None

    commit_date_raw = ui_state.params.get("GitCommitDate")
    try:
      # GitCommitDate format from get_commit_date(): '%ct %ci' e.g. "'1708012345 2024-02-15 ...'"
      unix_ts = int(commit_date_raw.strip("'").split()[0])
      date_str = datetime.datetime.fromtimestamp(unix_ts).strftime("%b %d")
    except (ValueError, IndexError, TypeError, AttributeError):
      date_str = ""

    return version, branch, commit[:7], date_str

  def _draw_checkered_flag(self, x: float, y: float, w: float, h: float = 20) -> None:
    sq = int(h / round(h / 10))  # square size that divides h evenly, close to 10
    rows = max(1, round(h / sq))
    cols = int(w / sq) + 1
    for row in range(rows):
      for col in range(cols):
        if (row + col) % 2 == 0:
          rx = int(x + col * sq)
          ry = int(y + row * sq)
          rw = int(min(sq, x + w - rx))
          rh = sq
          if rw > 0:
            rl.draw_rectangle(rx, ry, rw, rh, rl.Color(255, 255, 255, 180))

  def _render(self, _):
    # TODO: why is there extra space here to get it to be flush?
    text_pos = rl.Vector2(self.rect.x - 2 + HOME_PADDING, self.rect.y + 8)
    self._race_label.set_position(text_pos.x, text_pos.y)
    self._race_label.render()

    pilot_x = text_pos.x + self._race_label.text_width
    self._comma_label.set_position(pilot_x, text_pos.y)
    self._comma_label.render()

    flag_x = pilot_x + self._comma_label.text_width + 8
    flag_w = self.rect.x + self.rect.width - flag_x
    flag_h = self._race_label.font_size
    if flag_w > 0:
      self._draw_checkered_flag(flag_x, text_pos.y, flag_w, flag_h)

    if self._version_text is not None:
      # release branch
      release_branch = self._version_text[1] in RELEASE_BRANCHES
      version_pos = rl.Rectangle(text_pos.x, text_pos.y + self._race_label.font_size + 16, 100, 44)
      self._version_label.set_text(self._version_text[0])
      self._version_label.set_position(version_pos.x, version_pos.y)
      self._version_label.render()

      self._date_label.set_text(" " + self._version_text[3])
      self._date_label.set_position(version_pos.x + self._version_label.text_width + 10, version_pos.y)
      self._date_label.render()

      self._branch_label.set_max_width(gui_app.width - self._version_label.text_width - self._date_label.text_width - 32)
      self._branch_label.set_text(" " + ("release" if release_branch else self._version_text[1]))
      self._branch_label.set_position(version_pos.x + self._version_label.text_width + self._date_label.text_width + 20, version_pos.y)
      self._branch_label.render()

      if not release_branch:
        # 2nd line
        self._version_commit_label.set_text(self._version_text[2])
        self._version_commit_label.set_position(version_pos.x, version_pos.y + self._date_label.font_size + 7)
        self._version_commit_label.render()

    # ***** Center-aligned bottom section icons *****
    self._experimental_icon.set_visible(self._experimental_mode)
    self._mic_icon.set_visible(ui_state.recording_audio)

    footer_rect = rl.Rectangle(self.rect.x + HOME_PADDING, self.rect.y + self.rect.height - 64, self.rect.width - HOME_PADDING, 48)
    self._status_bar_layout.render(footer_rect)
