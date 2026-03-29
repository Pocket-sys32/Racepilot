import pyray as rl
import numpy as np

DOOM_W = 320
DOOM_H = 200


class DoomRenderer:
  """Manages the low-res Doom framebuffer and scales to screen via RenderTexture2D."""

  def __init__(self):
    self._tex = rl.load_render_texture(DOOM_W, DOOM_H)
    rl.set_texture_filter(self._tex.texture, rl.TextureFilter.TEXTURE_FILTER_BILINEAR)

  def upload(self, framebuffer: np.ndarray) -> None:
    """Upload a 320x200 RGBA numpy array to the GPU texture."""
    rl.update_texture(self._tex.texture, rl.ffi.from_buffer(framebuffer))

  def draw_scaled(self, dest: rl.Rectangle) -> None:
    """Draw the low-res texture scaled to fill dest rect, preserving aspect ratio."""
    src_aspect = DOOM_W / DOOM_H  # 1.6
    dst_aspect = dest.width / dest.height if dest.height > 0 else 1.0

    if dst_aspect > src_aspect:
      # Destination is wider -- letterbox left/right
      draw_h = dest.height
      draw_w = draw_h * src_aspect
      draw_x = dest.x + (dest.width - draw_w) / 2
      draw_y = dest.y
    else:
      # Destination is taller -- letterbox top/bottom
      draw_w = dest.width
      draw_h = draw_w / src_aspect
      draw_x = dest.x
      draw_y = dest.y + (dest.height - draw_h) / 2

    # Y-flip (same pattern as application.py:633)
    src_rect = rl.Rectangle(0, 0, float(DOOM_W), -float(DOOM_H))
    dst_rect = rl.Rectangle(draw_x, draw_y, draw_w, draw_h)
    rl.draw_texture_pro(self._tex.texture, src_rect, dst_rect, rl.Vector2(0, 0), 0.0, rl.WHITE)

  def unload(self) -> None:
    rl.unload_render_texture(self._tex)
