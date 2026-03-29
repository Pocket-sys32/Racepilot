import ctypes
import pyray as rl
from openpilot.doom.doom_engine import DoomEngine
from openpilot.doom.touch_controls import TouchControls
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets.nav_widget import NavWidget

class DoomGame(NavWidget):
  def __init__(self):
    super().__init__()
    self._multi_touch = True
    self._engine = DoomEngine()
    self._controls = TouchControls(self._engine)
    self._texture: rl.Texture | None = None
    self._init_failed = False

  def show_event(self):
    super().show_event()
    if not self._engine.initialized and not self._init_failed:
      if not self._engine.init():
        self._init_failed = True
    self._controls.menu_mode = False

  def hide_event(self):
    super().hide_event()
    self._controls.release_all()
    if self._texture is not None:
      rl.unload_texture(self._texture)
      self._texture = None

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.BLACK)

    if self._init_failed or not self._engine.initialized:
      text = "DOOM: Failed to initialize"
      font_size = 40
      font = gui_app.font(FontWeight.BOLD)
      text_size = measure_text_cached(font, text, font_size)
      rl.draw_text_ex(font, text,
                      rl.Vector2(int(rect.x + (rect.width - text_size.x) / 2), int(rect.y + rect.height / 2)),
                      font_size, 0, rl.RED)
      return

    # Process touch input
    self._controls.process(gui_app.mouse_events, rect)

    # Advance DOOM by one tick
    self._engine.tick()

    # Update texture if a new frame was rendered
    if self._engine.frame_ready():
      buf = self._engine.get_rgba_buffer()
      if buf:
        if self._texture is None:
          image = rl.gen_image_color(self._engine.resx, self._engine.resy, rl.BLACK)
          self._texture = rl.load_texture_from_image(image)
          rl.unload_image(image)
        # Convert ctypes pointer to cffi pointer for pyray
        addr = ctypes.addressof(buf.contents)
        cffi_ptr = rl.ffi.cast("void *", addr)
        rl.update_texture(self._texture, cffi_ptr)

    if self._texture is not None:
      src = rl.Rectangle(0, 0, float(self._engine.resx), float(self._engine.resy))
      # Scale to fill width, center vertically
      scale = rect.width / self._engine.resx
      dest_h = self._engine.resy * scale
      dest_y = rect.y + (rect.height - dest_h) / 2
      dest = rl.Rectangle(rect.x, dest_y, rect.width, dest_h)
      rl.draw_texture_pro(self._texture, src, dest, rl.Vector2(0, 0), 0.0, rl.WHITE)

    # Draw touch zone indicators
    self._controls.draw_overlay(rect)


if __name__ == "__main__":
  gui_app.init_window("DOOM")
  game = DoomGame()
  gui_app.push_widget(game)
  for _ in gui_app.render():
    pass
