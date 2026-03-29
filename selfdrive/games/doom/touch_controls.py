import pyray as rl
from dataclasses import dataclass

from openpilot.selfdrive.games.doom.raycaster import PlayerInputs
from openpilot.system.ui.lib.application import MouseEvent


@dataclass
class TouchZone:
  """Rectangular touch zone with its activation state."""
  rect: rl.Rectangle
  active: bool = False
  start_x: float = 0.0
  start_y: float = 0.0
  current_x: float = 0.0
  current_y: float = 0.0


class TouchControls:
  """Maps touch regions to Doom player inputs.

  Layout:
    +----------------------------+
    |                            |
    |      TAP = SHOOT           |   (top 50%)
    |                            |
    +-------------+--------------+
    | LEFT THUMB  | RIGHT THUMB  |   (bottom 50%)
    | D-PAD:      | LOOK:        |
    | fwd/back/   | swipe L/R =  |
    | strafe      | rotate       |
    +-------------+--------------+
  """

  DPAD_DEAD_ZONE = 10.0  # pixels before registering input

  def __init__(self):
    self._left_touch: dict[int, tuple[float, float]] = {}   # slot -> (start_x, start_y)
    self._right_touch: dict[int, tuple[float, float]] = {}
    self._shoot_touch: dict[int, float] = {}  # slot -> start_time
    self._current_inputs = PlayerInputs()
    self._game_rect = rl.Rectangle(0, 0, 1, 1)

  def set_rect(self, rect: rl.Rectangle) -> None:
    self._game_rect = rect

  def handle_touch(self, event: MouseEvent) -> None:
    """Process a touch event and update internal state."""
    r = self._game_rect
    mid_x = r.x + r.width / 2
    mid_y = r.y + r.height / 2
    slot = event.slot

    if event.left_pressed:
      # Determine which zone this touch lands in
      if event.pos.y < mid_y:
        # Top half = shoot zone
        self._shoot_touch[slot] = event.t
      elif event.pos.x < mid_x:
        # Bottom-left = D-pad
        self._left_touch[slot] = (event.pos.x, event.pos.y)
      else:
        # Bottom-right = Look
        self._right_touch[slot] = (event.pos.x, event.pos.y)

    elif event.left_released:
      self._left_touch.pop(slot, None)
      self._right_touch.pop(slot, None)
      self._shoot_touch.pop(slot, None)

  def get_inputs(self) -> PlayerInputs:
    """Compute player inputs from current touch state. Called each raycaster frame."""
    inputs = PlayerInputs()

    # Shoot if any touch in shoot zone
    inputs.shoot = len(self._shoot_touch) > 0

    # D-pad: movement based on drag from start position
    # We track the most recent mouse events to get current positions,
    # but since we only get events during handle_touch, we use the
    # stored inputs directly
    inputs.move_forward = self._current_inputs.move_forward
    inputs.move_strafe = self._current_inputs.move_strafe
    inputs.rotate = self._current_inputs.rotate

    return inputs

  def update_continuous(self, events: list[MouseEvent]) -> None:
    """Update continuous inputs from current-frame mouse events."""
    r = self._game_rect
    mid_x = r.x + r.width / 2

    self._current_inputs = PlayerInputs()

    for event in events:
      if not event.left_down:
        continue

      slot = event.slot

      # D-pad zone (bottom-left)
      if slot in self._left_touch:
        start_x, start_y = self._left_touch[slot]
        dx = event.pos.x - start_x
        dy = event.pos.y - start_y

        # Forward/backward (Y axis, inverted: drag up = forward)
        if abs(dy) > self.DPAD_DEAD_ZONE:
          self._current_inputs.move_forward = max(-1.0, min(1.0, -dy / 60.0))

        # Strafe (X axis)
        if abs(dx) > self.DPAD_DEAD_ZONE:
          self._current_inputs.move_strafe = max(-1.0, min(1.0, dx / 60.0))

      # Look zone (bottom-right)
      elif slot in self._right_touch:
        start_x, _ = self._right_touch[slot]
        dx = event.pos.x - start_x
        if abs(dx) > self.DPAD_DEAD_ZONE:
          self._current_inputs.rotate = max(-1.0, min(1.0, dx / 40.0))
        # Update start position for continuous rotation
        self._right_touch[slot] = (event.pos.x, event.pos.y)

  def draw_overlay(self, rect: rl.Rectangle) -> None:
    """Draw semi-transparent touch zone indicators."""
    mid_x = rect.x + rect.width / 2
    mid_y = rect.y + rect.height / 2

    overlay = rl.Color(255, 255, 255, 20)
    line_color = rl.Color(255, 255, 255, 40)

    # Horizontal divider
    rl.draw_line_ex(
      rl.Vector2(rect.x, mid_y),
      rl.Vector2(rect.x + rect.width, mid_y),
      1.0, line_color,
    )

    # Vertical divider (bottom half only)
    rl.draw_line_ex(
      rl.Vector2(mid_x, mid_y),
      rl.Vector2(mid_x, rect.y + rect.height),
      1.0, line_color,
    )

    # D-pad crosshair in bottom-left
    pad_cx = rect.x + rect.width * 0.25
    pad_cy = mid_y + (rect.y + rect.height - mid_y) * 0.5
    rl.draw_circle_lines_v(rl.Vector2(pad_cx, pad_cy), 20, rl.Color(255, 255, 255, 50))

    # Active input indicators
    inputs = self._current_inputs
    if abs(inputs.move_forward) > 0.1 or abs(inputs.move_strafe) > 0.1:
      ind_x = pad_cx + inputs.move_strafe * 20
      ind_y = pad_cy - inputs.move_forward * 20
      rl.draw_circle_v(rl.Vector2(ind_x, ind_y), 5, rl.Color(0, 255, 0, 150))

    if abs(inputs.rotate) > 0.1:
      look_cx = rect.x + rect.width * 0.75
      look_cy = pad_cy
      arrow_x = look_cx + inputs.rotate * 20
      rl.draw_circle_v(rl.Vector2(arrow_x, look_cy), 5, rl.Color(0, 150, 255, 150))
