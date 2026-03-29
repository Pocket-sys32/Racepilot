import pyray as rl
from openpilot.doom.doom_engine import (
  DoomEngine,
  KEY_UPARROW, KEY_DOWNARROW, KEY_LEFTARROW, KEY_RIGHTARROW,
  KEY_FIRE, KEY_USE, KEY_ENTER,
)
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# Touch zone thresholds (fractions of screen width/height)
DPAD_ZONE_RIGHT = 0.25
LOOK_ZONE_LEFT = 0.25
LOOK_ZONE_RIGHT = 0.75
ACTION_ZONE_LEFT = 0.75
DPAD_DEAD_ZONE = 20  # pixels
LOOK_SENSITIVITY = 0.8  # pixels of drag per turn event

# D-pad maps drag direction to keys
STRAFE_LEFT_KEY = ord(',')
STRAFE_RIGHT_KEY = ord('.')


class TouchControls:
  def __init__(self, engine: DoomEngine):
    self._engine = engine
    # Track what each slot is doing: None, 'dpad', 'look', 'fire', 'use'
    self._slot_zone: list[str | None] = [None, None]
    self._slot_start: list[tuple[float, float] | None] = [None, None]
    # Currently held keys per slot
    self._slot_keys: list[set[int]] = [set(), set()]
    # Whether we're in menu mode (tap = enter)
    self.menu_mode = False

  def process(self, mouse_events, rect: rl.Rectangle):
    for ev in mouse_events:
      if ev.slot > 1:
        continue

      if ev.left_pressed:
        self._on_press(ev.slot, ev.pos.x, ev.pos.y, rect)
      elif ev.left_down:
        self._on_move(ev.slot, ev.pos.x, ev.pos.y, rect)
      elif ev.left_released:
        self._on_release(ev.slot, ev.pos.x, ev.pos.y, rect)

  def _on_press(self, slot: int, x: float, y: float, rect: rl.Rectangle):
    # Determine which zone
    rel_x = (x - rect.x) / rect.width
    rel_y = (y - rect.y) / rect.height

    # Ignore touches in top 15% (NavWidget swipe zone)
    if rel_y < 0.15:
      return

    if self.menu_mode:
      self._slot_zone[slot] = 'menu'
      self._slot_start[slot] = (x, y)
      return

    if rel_x < DPAD_ZONE_RIGHT:
      self._slot_zone[slot] = 'dpad'
      self._slot_start[slot] = (x, y)
    elif rel_x > ACTION_ZONE_LEFT:
      if rel_y > 0.5:
        self._slot_zone[slot] = 'fire'
        self._engine.add_key(True, KEY_FIRE)
        self._slot_keys[slot].add(KEY_FIRE)
      else:
        self._slot_zone[slot] = 'use'
        self._engine.add_key(True, KEY_USE)
        self._slot_keys[slot].add(KEY_USE)
      self._slot_start[slot] = (x, y)
    else:
      self._slot_zone[slot] = 'look'
      self._slot_start[slot] = (x, y)

  def _on_move(self, slot: int, x: float, y: float, rect: rl.Rectangle):
    zone = self._slot_zone[slot]
    start = self._slot_start[slot]
    if zone is None or start is None:
      return

    if zone == 'dpad':
      dx = x - start[0]
      dy = y - start[1]

      new_keys: set[int] = set()

      if dy < -DPAD_DEAD_ZONE:
        new_keys.add(KEY_UPARROW)
      elif dy > DPAD_DEAD_ZONE:
        new_keys.add(KEY_DOWNARROW)

      if dx < -DPAD_DEAD_ZONE:
        new_keys.add(STRAFE_LEFT_KEY)
      elif dx > DPAD_DEAD_ZONE:
        new_keys.add(STRAFE_RIGHT_KEY)

      self._update_keys(slot, new_keys)

    elif zone == 'look':
      dx = x - start[0]
      new_keys: set[int] = set()

      if dx < -DPAD_DEAD_ZONE * LOOK_SENSITIVITY:
        new_keys.add(KEY_LEFTARROW)
      elif dx > DPAD_DEAD_ZONE * LOOK_SENSITIVITY:
        new_keys.add(KEY_RIGHTARROW)

      self._update_keys(slot, new_keys)
      # Reset start for continuous turning
      self._slot_start[slot] = (x, y)

  def _on_release(self, slot: int, x: float, y: float, rect: rl.Rectangle):
    zone = self._slot_zone[slot]

    if zone == 'menu':
      self._engine.add_key(True, KEY_ENTER)
      self._engine.add_key(False, KEY_ENTER)

    # Release all held keys for this slot
    for key in self._slot_keys[slot]:
      self._engine.add_key(False, key)
    self._slot_keys[slot].clear()

    self._slot_zone[slot] = None
    self._slot_start[slot] = None

  def _update_keys(self, slot: int, new_keys: set[int]):
    old_keys = self._slot_keys[slot]

    # Release keys no longer held
    for key in old_keys - new_keys:
      self._engine.add_key(False, key)

    # Press newly held keys
    for key in new_keys - old_keys:
      self._engine.add_key(True, key)

    self._slot_keys[slot] = new_keys

  def release_all(self):
    for slot in range(2):
      for key in self._slot_keys[slot]:
        self._engine.add_key(False, key)
      self._slot_keys[slot].clear()
      self._slot_zone[slot] = None
      self._slot_start[slot] = None

  def draw_overlay(self, rect: rl.Rectangle):
    # Draw semi-transparent touch zone indicators
    alpha = 30

    # D-pad zone (left)
    dpad_w = rect.width * DPAD_ZONE_RIGHT
    rl.draw_rectangle(int(rect.x), int(rect.y + rect.height * 0.15),
                      int(dpad_w), int(rect.height * 0.85),
                      rl.Color(255, 255, 255, alpha))

    # Action zone (right)
    action_x = rect.x + rect.width * ACTION_ZONE_LEFT
    action_w = rect.width * (1 - ACTION_ZONE_LEFT)

    # Fire (bottom right)
    rl.draw_rectangle(int(action_x), int(rect.y + rect.height * 0.5),
                      int(action_w), int(rect.height * 0.5),
                      rl.Color(255, 50, 50, alpha))

    # Use (top right)
    rl.draw_rectangle(int(action_x), int(rect.y + rect.height * 0.15),
                      int(action_w), int(rect.height * 0.35),
                      rl.Color(50, 50, 255, alpha))

    # Labels
    font_size = int(rect.height * 0.06)

    # D-pad label
    self._draw_label("MOVE", rect.x + dpad_w * 0.5, rect.y + rect.height * 0.9, font_size)

    # Look label
    look_center = rect.x + rect.width * 0.5
    self._draw_label("LOOK", look_center, rect.y + rect.height * 0.9, font_size)

    # Fire label
    fire_center = action_x + action_w * 0.5
    self._draw_label("FIRE", fire_center, rect.y + rect.height * 0.75, font_size)

    # Use label
    self._draw_label("USE", fire_center, rect.y + rect.height * 0.4, font_size)

  def _draw_label(self, text: str, cx: float, cy: float, font_size: int):
    font = gui_app.font(FontWeight.MEDIUM)
    text_size = measure_text_cached(font, text, font_size)
    rl.draw_text_ex(font, text,
                    rl.Vector2(int(cx - text_size.x / 2), int(cy - text_size.y / 2)),
                    font_size, 0, rl.Color(255, 255, 255, 80))
