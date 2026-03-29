import time
import pytest
import numpy as np

from openpilot.selfdrive.games.doom.raycaster import Raycaster, PlayerInputs, SCREEN_W, SCREEN_H


class TestRaycaster:
  def setup_method(self):
    self.raycaster = Raycaster()

  def test_framebuffer_dimensions(self):
    inputs = PlayerInputs()
    frame = self.raycaster.render_frame(inputs)
    assert frame.shape == (SCREEN_H, SCREEN_W, 4)

  def test_framebuffer_dtype(self):
    inputs = PlayerInputs()
    frame = self.raycaster.render_frame(inputs)
    assert frame.dtype == np.uint8

  def test_render_produces_nonzero_output(self):
    inputs = PlayerInputs()
    frame = self.raycaster.render_frame(inputs)
    # At least some pixels should be non-black (walls/floor/ceiling)
    assert np.any(frame[:, :, :3] > 0)

  def test_render_ceiling_is_dark(self):
    """Top quarter of screen should be ceiling color (dark)."""
    inputs = PlayerInputs()
    frame = self.raycaster.render_frame(inputs)
    ceiling_strip = frame[:SCREEN_H // 4, :, :]
    # Ceiling pixels should not be brightly colored (not walls)
    assert np.mean(ceiling_strip[:, :, :3]) < 100

  def test_render_timing(self):
    """A single frame must render in under 100ms (budget for 20fps raycaster)."""
    inputs = PlayerInputs()
    # Warm up
    self.raycaster.render_frame(inputs)
    start = time.monotonic()
    self.raycaster.render_frame(inputs)
    elapsed = time.monotonic() - start
    assert elapsed < 0.100, f"Frame took {elapsed:.3f}s, budget is 0.100s"

  def test_player_starts_at_correct_position(self):
    from openpilot.selfdrive.games.doom.maps import PLAYER_START_X, PLAYER_START_Y
    assert self.raycaster.player.x == pytest.approx(PLAYER_START_X)
    assert self.raycaster.player.y == pytest.approx(PLAYER_START_Y)

  def test_strafe_movement_changes_position(self):
    # Default start (12,12) faces walls on x-axis but y-axis is open.
    # Strafe uses the camera plane direction (plane_y=0.66), so it moves along y.
    initial_x = self.raycaster.player.x
    initial_y = self.raycaster.player.y
    inputs = PlayerInputs(move_strafe=1.0)
    for _ in range(10):
      self.raycaster.render_frame(inputs)
    new_x = self.raycaster.player.x
    new_y = self.raycaster.player.y
    moved = abs(new_x - initial_x) > 0.001 or abs(new_y - initial_y) > 0.001
    assert moved, "Player did not strafe"

  def test_rotation_changes_direction(self):
    initial_dir = (self.raycaster.player.dir_x, self.raycaster.player.dir_y)
    inputs = PlayerInputs(rotate=1.0)
    for _ in range(10):
      self.raycaster.render_frame(inputs)
    new_dir = (self.raycaster.player.dir_x, self.raycaster.player.dir_y)
    changed = abs(new_dir[0] - initial_dir[0]) > 0.001 or abs(new_dir[1] - initial_dir[1]) > 0.001
    assert changed, "Direction did not change when rotating"

  def test_no_walk_through_walls(self):
    """Player should not clip through walls regardless of direction."""
    from openpilot.selfdrive.games.doom.maps import WORLD_MAP
    # Strafe in one direction until blocked
    for _ in range(200):
      self.raycaster.render_frame(PlayerInputs(move_strafe=1.0))
    p = self.raycaster.player
    assert WORLD_MAP[int(p.y)][int(p.x)] == 0, f"Player clipped into wall at ({int(p.x)}, {int(p.y)})"

  def test_screen_constants(self):
    assert SCREEN_W == 320
    assert SCREEN_H == 200
