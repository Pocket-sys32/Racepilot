import os
import threading

import numpy as np
import pyray as rl

from openpilot.common.realtime import Ratekeeper
from openpilot.selfdrive.games.doom.raycaster import Raycaster, PlayerInputs, SCREEN_W, SCREEN_H
from openpilot.selfdrive.games.doom.renderer import DoomRenderer
from openpilot.selfdrive.games.doom.touch_controls import TouchControls
from openpilot.selfdrive.games.safety_guard import GameSafetyGuard
from openpilot.system.ui.lib.application import gui_app, FontWeight, MouseEvent

TARGET_FPS = 20


class DoomWidget(GameSafetyGuard):
  """Full-screen Doom raycaster game widget."""

  def __init__(self):
    super().__init__()
    self._multi_touch = True
    self._renderer = DoomRenderer()
    self._raycaster = Raycaster()
    self._controls = TouchControls()
    self._thread: threading.Thread | None = None
    self._running = False
    self._lock = threading.Lock()
    self._framebuffer = np.zeros((SCREEN_H, SCREEN_W, 4), dtype=np.uint8)
    self._font = gui_app.font(FontWeight.MEDIUM)

  def show_event(self):
    super().show_event()
    self._running = True
    self._thread = threading.Thread(target=self._raycaster_loop, daemon=True)
    self._thread.start()

  def hide_event(self):
    self._running = False
    if self._thread:
      self._thread.join(timeout=2.0)
      self._thread = None
    self._renderer.unload()
    super().hide_event()

  def _raycaster_loop(self):
    """Background thread: runs raycaster at ~20 FPS with lowest priority."""
    try:
      os.nice(19)
    except OSError:
      pass

    rk = Ratekeeper(TARGET_FPS)
    while self._running:
      inputs = self._controls.get_inputs()
      frame = self._raycaster.render_frame(inputs)
      with self._lock:
        np.copyto(self._framebuffer, frame)
      rk.keep_time()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.BLACK)

    # Upload latest framebuffer to GPU
    with self._lock:
      self._renderer.upload(self._framebuffer)

    # Draw scaled
    self._renderer.draw_scaled(rect)

    # Draw touch control overlay
    self._controls.set_rect(rect)
    self._controls.draw_overlay(rect)

    # FPS indicator
    rl.draw_text_ex(self._font, f"FPS: {rl.get_fps()}", rl.Vector2(rect.x + 5, rect.y + 5), 24, 0, rl.Color(0, 255, 0, 180))

  def _handle_mouse_event(self, mouse_event: MouseEvent):
    super()._handle_mouse_event(mouse_event)
    self._controls.handle_touch(mouse_event)
    # Update continuous inputs from all current-frame events
    self._controls.update_continuous(gui_app.mouse_events)
