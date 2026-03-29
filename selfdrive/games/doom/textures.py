import numpy as np

TEX_WIDTH = 64
TEX_HEIGHT = 64

# Wall colors per wall type (RGBA). Wall types 1-8 from the map.
WALL_COLORS = {
  1: (180, 0, 0, 255),       # dark red
  2: (0, 180, 0, 255),       # dark green
  3: (0, 0, 180, 255),       # dark blue
  4: (180, 180, 0, 255),     # yellow
  5: (180, 0, 180, 255),     # magenta
  6: (0, 180, 180, 255),     # cyan
  7: (160, 100, 40, 255),    # brown
  8: (128, 128, 128, 255),   # gray
}

# Darker variants for Y-side walls (gives depth perception)
WALL_COLORS_DARK = {
  k: (v[0] // 2, v[1] // 2, v[2] // 2, 255)
  for k, v in WALL_COLORS.items()
}


def generate_wall_texture(wall_type: int) -> np.ndarray:
  """Generate a 64x64 procedural brick texture for a wall type."""
  tex = np.zeros((TEX_HEIGHT, TEX_WIDTH, 4), dtype=np.uint8)
  base = WALL_COLORS.get(wall_type, (128, 128, 128, 255))

  # Fill base color
  tex[:, :] = base

  # Add brick pattern
  brick_h = 16
  brick_w = 32
  mortar = (max(0, base[0] - 60), max(0, base[1] - 60), max(0, base[2] - 60), 255)

  for y in range(TEX_HEIGHT):
    row = y // brick_h
    offset = (brick_w // 2) if (row % 2) else 0

    # Horizontal mortar lines
    if y % brick_h == 0:
      tex[y, :] = mortar

    # Vertical mortar lines
    for x in range(TEX_WIDTH):
      if (x + offset) % brick_w == 0:
        tex[y, x] = mortar

  return tex


def generate_all_textures() -> dict[int, np.ndarray]:
  """Pre-generate textures for all wall types."""
  return {wt: generate_wall_texture(wt) for wt in WALL_COLORS}


# Floor and ceiling colors (RGBA)
FLOOR_COLOR = np.array([60, 60, 60, 255], dtype=np.uint8)
CEILING_COLOR = np.array([40, 40, 50, 255], dtype=np.uint8)
