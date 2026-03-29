#!/usr/bin/env python3
"""
Racing line simulation visualizer.

Uses the real tracklib optimizer to show:
  - A synthetic circuit (oval with chicane + hairpin)
  - Simulated noisy lap telemetry (as if a driver drove it)
  - The optimized racing line after N laps, colored by target speed
  - Curvature and speed profiles

Run from the openpilot root:
  python selfdrive/trackd/racing_line_sim.py
"""
import math
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Inlined from tracklib.py (avoids capnp/cereal deps for standalone use)
# ---------------------------------------------------------------------------
ACCEL_MIN = -3.5


def _clamp(value, lo, hi):
  return float(min(max(value, lo), hi))


def _circular_smooth(values, window):
  if window <= 1 or values.size < 3:
    return values.copy()
  window = max(3, int(window))
  if window % 2 == 0:
    window += 1
  pad = window // 2
  kernel = np.ones(window, dtype=np.float64) / float(window)
  padded = np.pad(values.astype(np.float64), (pad, pad), mode='wrap')
  return np.convolve(padded, kernel, mode='valid').astype(np.float32)


def _compute_curvature(xs, ys):
  if xs.size < 5:
    return np.zeros_like(xs, dtype=np.float32)
  dx = np.gradient(xs); dy = np.gradient(ys)
  ddx = np.gradient(dx); ddy = np.gradient(dy)
  denom = np.power(np.maximum(dx*dx + dy*dy, 1e-4), 1.5)
  return ((dx*ddy - dy*ddx) / denom).astype(np.float32)


def _cumulative_distance(xs, ys):
  if xs.size == 0:
    return np.zeros((0,), dtype=np.float32)
  deltas = np.hypot(np.diff(xs, append=xs[0]), np.diff(ys, append=ys[0]))
  return np.concatenate(([0.0], np.cumsum(deltas[:-1], dtype=np.float64))).astype(np.float32)


def _resample_closed_path(xs, ys, values, spacing):
  if xs.size < 4:
    return xs.copy(), ys.copy(), values.copy() if values is not None else None
  seg = np.hypot(np.diff(xs, append=xs[0]), np.diff(ys, append=ys[0]))
  total = float(np.sum(seg))
  if total < max(spacing * 4, 5.0):
    return xs.copy(), ys.copy(), values.copy() if values is not None else None
  samples = max(int(total / max(spacing, 0.5)), 32)
  distances = np.concatenate(([0.0], np.cumsum(seg, dtype=np.float64)))
  xs_e = np.append(xs, xs[0]); ys_e = np.append(ys, ys[0])
  s = np.linspace(0.0, total, samples, endpoint=False)
  rx = np.interp(s, distances, xs_e).astype(np.float32)
  ry = np.interp(s, distances, ys_e).astype(np.float32)
  if values is None:
    return rx, ry, None
  v_e = np.append(values, values[0])
  rv = np.interp(s, distances, v_e).astype(np.float32)
  return rx, ry, rv


def _clamp_points_to_reference(points, reference, max_offset):
  delta = points - reference
  delta_norm = np.linalg.norm(delta, axis=1)
  over = delta_norm > max_offset
  if np.any(over):
    points = points.copy()
    points[over] = reference[over] + delta[over] * (max_offset / delta_norm[over])[:, None]
  return points


def _optimize_reference_line(xs, ys, config):
  if xs.size < 8:
    return xs.copy(), ys.copy()
  original = np.column_stack((xs.astype(np.float64), ys.astype(np.float64)))
  current  = original.copy()
  max_offset = min(config.optimization_max_offset,
                   max(config.corridor_half_width * 0.35, 0.25))
  for _ in range(max(config.optimization_iterations, 1)):
    prev_pts = np.roll(current,  1, axis=0)
    next_pts = np.roll(current, -1, axis=0)
    current += config.optimization_smooth_weight  * (0.5*(prev_pts+next_pts) - current)
    current += config.optimization_fidelity_weight * (original - current)
    current  = _clamp_points_to_reference(current, original, max_offset)
  ox = _circular_smooth(current[:, 0].astype(np.float32), max(5, config.smoothing_window//2))
  oy = _circular_smooth(current[:, 1].astype(np.float32), max(5, config.smoothing_window//2))
  optimized = np.column_stack((ox.astype(np.float64), oy.astype(np.float64)))
  optimized = _clamp_points_to_reference(optimized, original, max_offset)
  return optimized[:, 0].astype(np.float32), optimized[:, 1].astype(np.float32)


@dataclass
class TrackModeConfig:
  min_lap_distance: float = 1000.0
  smoothing_window: int = 25
  resample_spacing: float = 2.0
  optimization_iterations: int = 80
  optimization_smooth_weight: float = 0.22
  optimization_fidelity_weight: float = 0.08
  optimization_max_offset: float = 1.0
  corridor_half_width: float = 3.0
  learned_max_speed: float = 38.0
  learned_max_lat_accel: float = 4.5
  exploratory_speed: float = 13.0
  min_line_confidence: float = 0.35
  lap_completion_radius: float = 18.0


@dataclass
class TrackTelemetryPoint:
  x: float; y: float; yaw: float
  speed: float; curvature: float; accel: float; t: float


@dataclass
class TrackReference:
  xs: np.ndarray; ys: np.ndarray
  target_speeds: np.ndarray; curvature: np.ndarray
  progress: np.ndarray; total_distance: float
  line_confidence: float; source_laps: int


def fit_reference_from_lap(points, config, previous=None):
  if len(points) < 64:
    return None
  xs = np.array([p.x for p in points], dtype=np.float32)
  ys = np.array([p.y for p in points], dtype=np.float32)
  speeds = np.array([p.speed for p in points], dtype=np.float32)
  xs, ys, speeds = _resample_closed_path(xs, ys, speeds, config.resample_spacing)
  if xs.size < 32:
    return None
  xs = _circular_smooth(xs, config.smoothing_window)
  ys = _circular_smooth(ys, config.smoothing_window)
  baseline = np.column_stack((xs.astype(np.float64), ys.astype(np.float64)))
  xs, ys = _optimize_reference_line(xs, ys, config)
  if previous is not None and previous.xs.size == xs.size:
    xs = (previous.xs * 0.7 + xs * 0.3).astype(np.float32)
    ys = (previous.ys * 0.7 + ys * 0.3).astype(np.float32)
    blended = _clamp_points_to_reference(
      np.column_stack((xs.astype(np.float64), ys.astype(np.float64))),
      baseline, min(config.optimization_max_offset,
                    max(config.corridor_half_width * 0.35, 0.25)))
    xs = blended[:, 0].astype(np.float32)
    ys = blended[:, 1].astype(np.float32)
  curvature = _compute_curvature(xs, ys)
  closure_err = float(math.hypot(xs[0]-xs[-1], ys[0]-ys[-1]))
  total_dist  = float(np.sum(np.hypot(np.diff(xs, append=xs[0]),
                                       np.diff(ys, append=ys[0]))))
  if total_dist < config.min_lap_distance * 0.5:
    return None
  target_speeds = np.sqrt(np.maximum(
    config.learned_max_lat_accel / np.maximum(np.abs(curvature), 1e-3), 0.0
  )).astype(np.float32)
  target_speeds = np.clip(target_speeds, config.exploratory_speed * 0.8,
                          config.learned_max_speed)
  obs_cap = max(float(np.percentile(speeds, 90)), config.exploratory_speed)
  target_speeds = np.minimum(target_speeds, max(obs_cap*1.4, config.exploratory_speed)).astype(np.float32)
  target_speeds = _circular_smooth(target_speeds, max(5, config.smoothing_window//2))
  progress = _cumulative_distance(xs, ys)
  if total_dist > 1e-3:
    progress = (progress / total_dist).astype(np.float32)
  confidence = _clamp(1.0 - closure_err / max(config.lap_completion_radius*2, 1.0), 0.0, 1.0)
  if previous is not None:
    confidence = max(confidence, previous.line_confidence * 0.8)
  return TrackReference(
    xs=xs, ys=ys, target_speeds=target_speeds, curvature=curvature.astype(np.float32),
    progress=progress.astype(np.float32), total_distance=total_dist,
    line_confidence=confidence, source_laps=1 if previous is None else previous.source_laps+1,
  )

CYAN = '#00ffff'
BG   = '#0d0d14'
GRID = '#1e1e2a'


# ---------------------------------------------------------------------------
# Synthetic track definition
# ---------------------------------------------------------------------------

def make_circuit(n: int = 800) -> tuple[np.ndarray, np.ndarray]:
  """
  A simple circuit with:
    - Two long straights (top/bottom)
    - Two sweeping corners (left/right)
    - A chicane on the back straight
    - A hairpin at the far end
  All in local-coordinate metres.
  """
  t = np.linspace(0, 2 * math.pi, n, endpoint=False)

  # Base oval  (140m long, 70m wide)
  x = 140 * np.cos(t)
  y =  70 * np.sin(t)

  # Chicane on the back straight  (t ~ π)
  chicane_center = math.pi
  chicane_mask = np.exp(-((t - chicane_center) ** 2) / 0.18)
  y += 9.0 * np.sin(6 * (t - chicane_center + 0.18)) * chicane_mask

  # Tighten the hairpin at far end  (t ~ π/2)
  hairpin_center = math.pi / 2
  hairpin_mask = np.exp(-((t - hairpin_center) ** 2) / 0.12)
  x -= 18.0 * hairpin_mask
  y +=  8.0 * hairpin_mask * np.sin(t - hairpin_center)

  return x.astype(np.float32), y.astype(np.float32)


def track_boundary(xs: np.ndarray, ys: np.ndarray, half_w: float):
  """Return inner/outer boundary arrays."""
  n = len(xs)
  nx_list, ny_list = [], []
  for i in range(n):
    dx = xs[(i + 1) % n] - xs[(i - 1) % n]
    dy = ys[(i + 1) % n] - ys[(i - 1) % n]
    d  = math.hypot(dx, dy) or 1.0
    nx_list.append(-dy / d)
    ny_list.append( dx / d)
  nx = np.array(nx_list)
  ny = np.array(ny_list)
  inner_x = xs + nx * half_w
  inner_y = ys + ny * half_w
  outer_x = xs - nx * half_w
  outer_y = ys - ny * half_w
  return inner_x, inner_y, outer_x, outer_y


# ---------------------------------------------------------------------------
# Simulate lap telemetry
# ---------------------------------------------------------------------------

def simulate_lap(
  xs: np.ndarray,
  ys: np.ndarray,
  noise_sigma: float = 1.8,
  t_start: float = 0.0,
) -> list[TrackTelemetryPoint]:
  """
  "Drive" one lap around the centerline with Gaussian position noise.
  Returns a list of TrackTelemetryPoints compatible with fit_reference_from_lap.
  """
  n = len(xs)
  points: list[TrackTelemetryPoint] = []
  t = t_start

  for i in range(n):
    i_prev = (i - 1) % n
    i_next = (i + 1) % n

    # Forward direction
    dx_fwd = xs[i_next] - xs[i]
    dy_fwd = ys[i_next] - ys[i]
    seg_len = math.hypot(dx_fwd, dy_fwd) or 1e-3
    yaw = math.atan2(dy_fwd, dx_fwd)

    # Discrete curvature
    dx1 = xs[i]      - xs[i_prev]
    dy1 = ys[i]      - ys[i_prev]
    dx2 = xs[i_next] - xs[i]
    dy2 = ys[i_next] - ys[i]
    cross = dx1 * dy2 - dy1 * dx2
    denom = math.hypot(dx1, dy1) * math.hypot(dx2, dy2) * math.hypot(dx1 + dx2, dy1 + dy2)
    curvature = 2.0 * cross / max(denom, 1e-6)

    # Speed: slow in tight corners, fast on straights  (m/s)
    speed = 30.0 * (1.0 - 0.55 * min(abs(curvature) * 25.0, 1.0))
    speed = max(speed, 6.0)

    # Noisy position  (driver is not perfectly on the line)
    noisy_x = float(xs[i]) + np.random.normal(0, noise_sigma)
    noisy_y = float(ys[i]) + np.random.normal(0, noise_sigma)

    dt = seg_len / speed
    t += dt

    points.append(TrackTelemetryPoint(
      x=noisy_x, y=noisy_y, yaw=yaw,
      speed=speed, curvature=curvature,
      accel=0.0, t=t,
    ))

  return points


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
  np.random.seed(0)

  config = TrackModeConfig(
    min_lap_distance=200.0,
    smoothing_window=20,
    resample_spacing=1.5,
    optimization_iterations=150,
    optimization_smooth_weight=0.30,
    optimization_fidelity_weight=0.06,
    optimization_max_offset=1.8,
    corridor_half_width=4.5,
    learned_max_speed=40.0,
    learned_max_lat_accel=5.0,
  )

  print("Building synthetic circuit...")
  xs, ys = make_circuit(800)
  inner_x, inner_y, outer_x, outer_y = track_boundary(xs, ys, config.corridor_half_width)

  print("Simulating laps and optimizing racing line...")
  reference = None
  all_lap_xs, all_lap_ys = [], []
  t_cursor = 0.0
  n_laps = 4

  for lap in range(n_laps):
    noise = 1.5 + lap * 0.3          # driver gets slightly messier over laps
    points = simulate_lap(xs, ys, noise_sigma=noise, t_start=t_cursor)
    t_cursor = points[-1].t
    all_lap_xs.append([p.x for p in points])
    all_lap_ys.append([p.y for p in points])
    reference = fit_reference_from_lap(points, config, reference)
    if reference:
      print(f"  Lap {lap + 1}: confidence={reference.line_confidence:.2f}  "
            f"distance={reference.total_distance:.0f}m  "
            f"laps={reference.source_laps}")

  if reference is None:
    print("ERROR: optimizer returned no reference — check min_lap_distance config.")
    sys.exit(1)

  # -------------------------------------------------------------------------
  # Plot
  # -------------------------------------------------------------------------
  fig = plt.figure(figsize=(18, 9), facecolor=BG)
  fig.suptitle('Racing Line Simulation', color='white', fontsize=20, fontweight='bold', y=0.98)

  gs = fig.add_gridspec(2, 2, left=0.05, right=0.97, top=0.92, bottom=0.07,
                        hspace=0.38, wspace=0.28)
  ax_track  = fig.add_subplot(gs[:, 0])   # full left: track view
  ax_speed  = fig.add_subplot(gs[0, 1])   # top-right: speed profile
  ax_curv   = fig.add_subplot(gs[1, 1])   # bottom-right: curvature profile

  def style(ax, title):
    ax.set_facecolor(BG)
    ax.set_title(title, color='white', fontsize=11, pad=6)
    ax.tick_params(colors='#888')
    ax.xaxis.label.set_color('#888')
    ax.yaxis.label.set_color('#888')
    for sp in ax.spines.values():
      sp.set_edgecolor(GRID)
    ax.grid(True, color=GRID, linewidth=0.6, linestyle='--')

  # --- Track view -----------------------------------------------------------
  style(ax_track, 'Circuit — Centerline vs Optimized Racing Line')

  # Track surface
  bx = np.concatenate([outer_x, inner_x[::-1], [outer_x[0]]])
  by = np.concatenate([outer_y, inner_y[::-1], [outer_y[0]]])
  ax_track.fill(bx, by, color='#1a1a28', zorder=0)
  ax_track.plot(np.append(outer_x, outer_x[0]), np.append(outer_y, outer_y[0]),
                color='#ffffff', linewidth=1.2, zorder=1)
  ax_track.plot(np.append(inner_x, inner_x[0]), np.append(inner_y, inner_y[0]),
                color='#ffffff', linewidth=1.2, zorder=1)

  # Simulated driver paths (faint)
  for lx, ly in zip(all_lap_xs, all_lap_ys):
    ax_track.plot(lx + [lx[0]], ly + [ly[0]],
                  color='#ffffff', alpha=0.08, linewidth=0.8, zorder=2)

  # Centerline (dashed)
  ax_track.plot(np.append(xs, xs[0]), np.append(ys, ys[0]),
                '--', color='#555577', linewidth=1.2, label='Centerline', zorder=3)

  # Optimized racing line colored by target speed
  rx = np.append(reference.xs, reference.xs[0])
  ry = np.append(reference.ys, reference.ys[0])
  rs = np.append(reference.target_speeds, reference.target_speeds[0])

  pts    = np.array([rx, ry]).T.reshape(-1, 1, 2)
  segs   = np.concatenate([pts[:-1], pts[1:]], axis=1)
  norm   = plt.Normalize(rs.min(), rs.max())
  lc     = LineCollection(segs, cmap='plasma', norm=norm, linewidth=3.0, zorder=5, capstyle='round')
  lc.set_array(rs)
  ax_track.add_collection(lc)

  cbar = fig.colorbar(lc, ax=ax_track, fraction=0.025, pad=0.02)
  cbar.set_label('Target speed (m/s)', color='white', fontsize=9)
  cbar.ax.yaxis.set_tick_params(color='white', labelsize=8)
  plt.setp(cbar.ax.get_yticklabels(), color='white')

  # Start/finish marker
  ax_track.scatter([reference.xs[0]], [reference.ys[0]],
                   color=CYAN, s=80, zorder=6, label='Start/Finish')

  ax_track.set_aspect('equal')
  ax_track.legend(facecolor='#1a1a2e', labelcolor='white', framealpha=0.85,
                  fontsize=9, loc='upper right')
  ax_track.set_xlabel('X (m)')
  ax_track.set_ylabel('Y (m)')

  # --- Speed profile --------------------------------------------------------
  style(ax_speed, 'Target Speed Profile')
  dist = reference.progress * reference.total_distance
  speeds = reference.target_speeds

  ax_speed.fill_between(dist, speeds, alpha=0.18, color='#ff6030')
  ax_speed.plot(dist, speeds, color='#ff6030', linewidth=1.8)
  ax_speed.set_xlabel('Track distance (m)')
  ax_speed.set_ylabel('Speed (m/s)')
  ax_speed.set_xlim(0, dist[-1])

  # Annotate min/max
  idx_min = int(np.argmin(speeds))
  idx_max = int(np.argmax(speeds))
  ax_speed.annotate(f'{speeds[idx_min]:.1f} m/s',
                    xy=(dist[idx_min], speeds[idx_min]),
                    xytext=(dist[idx_min] + dist[-1]*0.04, speeds[idx_min] + 0.5),
                    color='#ff8888', fontsize=8, arrowprops=dict(arrowstyle='->', color='#ff8888'))
  ax_speed.annotate(f'{speeds[idx_max]:.1f} m/s',
                    xy=(dist[idx_max], speeds[idx_max]),
                    xytext=(dist[idx_max] + dist[-1]*0.04, speeds[idx_max] - 1.5),
                    color='#88ff88', fontsize=8, arrowprops=dict(arrowstyle='->', color='#88ff88'))

  # --- Curvature profile ----------------------------------------------------
  style(ax_curv, 'Curvature Profile')
  curv = reference.curvature

  ax_curv.fill_between(dist,  curv, 0, where=(curv > 0), alpha=0.2, color=CYAN,  label='Left turn')
  ax_curv.fill_between(dist,  curv, 0, where=(curv < 0), alpha=0.2, color='#ff6030', label='Right turn')
  ax_curv.plot(dist, curv, color=CYAN, linewidth=1.5)
  ax_curv.axhline(0, color='#444', linewidth=0.8)
  ax_curv.set_xlabel('Track distance (m)')
  ax_curv.set_ylabel('Curvature (1/m)')
  ax_curv.set_xlim(0, dist[-1])
  ax_curv.legend(facecolor='#1a1a2e', labelcolor='white', framealpha=0.85, fontsize=8)

  out = '/tmp/racing_line_sim.png'
  plt.savefig(out, dpi=150, bbox_inches='tight', facecolor=BG)
  print(f"\nSaved → {out}")
  plt.show()


if __name__ == '__main__':
  main()
