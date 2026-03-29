import math
from dataclasses import dataclass, field

import numpy as np

from openpilot.selfdrive.games.doom.maps import WORLD_MAP, MAP_WIDTH, MAP_HEIGHT
from openpilot.selfdrive.games.doom.textures import (
  TEX_WIDTH, TEX_HEIGHT, WALL_COLORS, WALL_COLORS_DARK,
  FLOOR_COLOR, CEILING_COLOR, generate_all_textures,
)

SCREEN_W = 320
SCREEN_H = 200
MOVE_SPEED = 0.05
ROT_SPEED = 0.03


@dataclass
class PlayerInputs:
  move_forward: float = 0.0   # -1.0 to 1.0
  move_strafe: float = 0.0    # -1.0 (left) to 1.0 (right)
  rotate: float = 0.0         # -1.0 (left) to 1.0 (right)
  shoot: bool = False


@dataclass
class PlayerState:
  x: float = 12.0
  y: float = 12.0
  dir_x: float = -1.0
  dir_y: float = 0.0
  plane_x: float = 0.0
  plane_y: float = 0.66


class Raycaster:
  """DDA raycasting engine. Renders a 320x200 RGBA framebuffer."""

  def __init__(self):
    self.player = PlayerState()
    self.framebuffer = np.zeros((SCREEN_H, SCREEN_W, 4), dtype=np.uint8)
    self._textures = generate_all_textures()
    self._z_buffer = np.zeros(SCREEN_W, dtype=np.float64)

  def update(self, inputs: PlayerInputs) -> None:
    """Update player position based on inputs."""
    p = self.player

    # Rotation
    if inputs.rotate != 0.0:
      rot = ROT_SPEED * inputs.rotate
      cos_r = math.cos(rot)
      sin_r = math.sin(rot)
      old_dx = p.dir_x
      p.dir_x = p.dir_x * cos_r - p.dir_y * sin_r
      p.dir_y = old_dx * sin_r + p.dir_y * cos_r
      old_px = p.plane_x
      p.plane_x = p.plane_x * cos_r - p.plane_y * sin_r
      p.plane_y = old_px * sin_r + p.plane_y * cos_r

    # Forward/backward movement
    if inputs.move_forward != 0.0:
      move = MOVE_SPEED * inputs.move_forward
      new_x = p.x + p.dir_x * move
      new_y = p.y + p.dir_y * move
      if 0 <= int(new_x) < MAP_WIDTH and WORLD_MAP[int(p.y)][int(new_x)] == 0:
        p.x = new_x
      if 0 <= int(new_y) < MAP_HEIGHT and WORLD_MAP[int(new_y)][int(p.x)] == 0:
        p.y = new_y

    # Strafing (perpendicular to view direction = camera plane direction)
    if inputs.move_strafe != 0.0:
      strafe = MOVE_SPEED * inputs.move_strafe
      new_x = p.x + p.plane_x * strafe
      new_y = p.y + p.plane_y * strafe
      if 0 <= int(new_x) < MAP_WIDTH and WORLD_MAP[int(p.y)][int(new_x)] == 0:
        p.x = new_x
      if 0 <= int(new_y) < MAP_HEIGHT and WORLD_MAP[int(new_y)][int(p.x)] == 0:
        p.y = new_y

  def render_frame(self, inputs: PlayerInputs) -> np.ndarray:
    """Update state and render one frame. Returns the framebuffer."""
    self.update(inputs)
    self._render()
    return self.framebuffer

  def _render(self) -> None:
    fb = self.framebuffer
    p = self.player

    # Clear framebuffer: ceiling and floor
    fb[:SCREEN_H // 2, :] = CEILING_COLOR
    fb[SCREEN_H // 2:, :] = FLOOR_COLOR

    for x in range(SCREEN_W):
      # Calculate ray direction
      camera_x = 2.0 * x / SCREEN_W - 1.0
      ray_dir_x = p.dir_x + p.plane_x * camera_x
      ray_dir_y = p.dir_y + p.plane_y * camera_x

      # Current map cell
      map_x = int(p.x)
      map_y = int(p.y)

      # Length of ray from one x/y-side to next x/y-side
      delta_dist_x = abs(1.0 / ray_dir_x) if ray_dir_x != 0 else 1e30
      delta_dist_y = abs(1.0 / ray_dir_y) if ray_dir_y != 0 else 1e30

      # Calculate step and initial side_dist
      if ray_dir_x < 0:
        step_x = -1
        side_dist_x = (p.x - map_x) * delta_dist_x
      else:
        step_x = 1
        side_dist_x = (map_x + 1.0 - p.x) * delta_dist_x

      if ray_dir_y < 0:
        step_y = -1
        side_dist_y = (p.y - map_y) * delta_dist_y
      else:
        step_y = 1
        side_dist_y = (map_y + 1.0 - p.y) * delta_dist_y

      # DDA
      hit = False
      side = 0  # 0 = x-side, 1 = y-side
      while not hit:
        if side_dist_x < side_dist_y:
          side_dist_x += delta_dist_x
          map_x += step_x
          side = 0
        else:
          side_dist_y += delta_dist_y
          map_y += step_y
          side = 1

        # Bounds check
        if map_x < 0 or map_x >= MAP_WIDTH or map_y < 0 or map_y >= MAP_HEIGHT:
          hit = True
          break

        if WORLD_MAP[map_y][map_x] > 0:
          hit = True

      # Calculate perpendicular distance (avoids fisheye)
      if side == 0:
        perp_wall_dist = side_dist_x - delta_dist_x
      else:
        perp_wall_dist = side_dist_y - delta_dist_y

      if perp_wall_dist < 0.001:
        perp_wall_dist = 0.001

      self._z_buffer[x] = perp_wall_dist

      # Calculate wall column height
      line_height = int(SCREEN_H / perp_wall_dist)

      draw_start = max(0, SCREEN_H // 2 - line_height // 2)
      draw_end = min(SCREEN_H - 1, SCREEN_H // 2 + line_height // 2)

      if draw_start >= draw_end:
        continue

      # Get wall type
      wall_type = WORLD_MAP[map_y][map_x] if 0 <= map_y < MAP_HEIGHT and 0 <= map_x < MAP_WIDTH else 1

      # Calculate texture X coordinate
      if side == 0:
        wall_x = p.y + perp_wall_dist * ray_dir_y
      else:
        wall_x = p.x + perp_wall_dist * ray_dir_x
      wall_x -= math.floor(wall_x)

      tex_x = int(wall_x * TEX_WIDTH)
      if tex_x >= TEX_WIDTH:
        tex_x = TEX_WIDTH - 1

      # Get texture for this wall type
      tex = self._textures.get(wall_type)

      if tex is not None:
        # Textured wall rendering
        step = TEX_HEIGHT / line_height
        tex_pos = (draw_start - SCREEN_H / 2 + line_height / 2) * step

        for y in range(draw_start, draw_end + 1):
          tex_y = int(tex_pos) & (TEX_HEIGHT - 1)
          tex_pos += step
          color = tex[tex_y, tex_x].copy()
          # Darken y-side walls for depth
          if side == 1:
            color[0] = color[0] >> 1
            color[1] = color[1] >> 1
            color[2] = color[2] >> 1
          fb[y, x] = color
      else:
        # Flat color fallback
        colors = WALL_COLORS_DARK if side == 1 else WALL_COLORS
        color = colors.get(wall_type, (128, 128, 128, 255))
        fb[draw_start:draw_end + 1, x] = color
