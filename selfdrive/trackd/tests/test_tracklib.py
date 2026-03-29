import math

import numpy as np

from openpilot.selfdrive.trackd.tracklib import (
  TrackModeConfig,
  TrackSession,
  TrackTelemetryPoint,
  _circular_smooth,
  _optimize_reference_line,
  _resample_closed_path,
  fit_reference_from_lap,
)
from openpilot.system.hardware.hw import Paths


def _circle_points(radius: float = 55.0, samples: int = 360, speed: float = 18.0) -> list[TrackTelemetryPoint]:
  points = []
  circumference = 2.0 * math.pi * radius
  dt = circumference / samples / speed
  t = 0.0
  for i in range(samples):
    theta = (2.0 * math.pi * i) / samples
    points.append(
      TrackTelemetryPoint(
        x=radius * math.cos(theta),
        y=radius * math.sin(theta),
        yaw=theta + math.pi / 2.0,
        speed=speed,
        curvature=1.0 / radius,
        accel=0.0,
        t=t,
      )
    )
    t += dt
  return points


def _wavy_track_points(samples: int = 420, speed: float = 16.0) -> list[TrackTelemetryPoint]:
  points = []
  t = 0.0
  dt = 0.1
  for i in range(samples):
    theta = (2.0 * math.pi * i) / samples
    radius = 55.0 + 8.0 * math.sin(2.0 * theta) - 4.0 * math.cos(3.0 * theta)
    x = radius * math.cos(theta)
    y = 0.8 * radius * math.sin(theta)
    points.append(
      TrackTelemetryPoint(
        x=x,
        y=y,
        yaw=theta + math.pi / 2.0,
        speed=speed,
        curvature=0.0,
        accel=0.0,
        t=t,
      )
    )
    t += dt
  return points


def test_fit_reference_from_lap_returns_smoothed_reference():
  config = TrackModeConfig(track_name="unit", allow_any_car=True, min_lap_distance=200.0, learned_max_speed=30.0)
  points = _circle_points()
  reference = fit_reference_from_lap(points, config)

  assert reference is not None
  assert reference.xs.size >= 32
  assert 0.0 <= reference.line_confidence <= 1.0
  assert float(np.max(reference.target_speeds)) <= config.learned_max_speed + 1e-3
  assert float(reference.total_distance) >= config.min_lap_distance


def test_optimizer_makes_small_bounded_change_from_driven_line():
  config = TrackModeConfig(
    track_name="optimizer_unit",
    allow_any_car=True,
    min_lap_distance=200.0,
    corridor_half_width=3.0,
    optimization_max_offset=0.9,
  )
  points = _wavy_track_points()
  xs = np.array([p.x for p in points], dtype=np.float32)
  ys = np.array([p.y for p in points], dtype=np.float32)
  xs, ys, _ = _resample_closed_path(xs, ys, None, config.resample_spacing)
  xs = _circular_smooth(xs, config.smoothing_window)
  ys = _circular_smooth(ys, config.smoothing_window)

  optimized_xs, optimized_ys = _optimize_reference_line(xs, ys, config)
  offsets = np.hypot(optimized_xs - xs, optimized_ys - ys)

  assert float(np.max(offsets)) <= config.optimization_max_offset + 1e-3
  assert float(np.mean(offsets)) > 1e-3

  baseline_len = float(np.sum(np.hypot(np.diff(xs, append=xs[0]), np.diff(ys, append=ys[0]))))
  optimized_len = float(np.sum(np.hypot(np.diff(optimized_xs, append=optimized_xs[0]), np.diff(optimized_ys, append=optimized_ys[0]))))
  assert optimized_len <= baseline_len + 1e-3

  reference = fit_reference_from_lap(points, config)
  assert reference is not None
  final_offsets = np.hypot(reference.xs - xs, reference.ys - ys)
  assert float(np.max(final_offsets)) <= config.optimization_max_offset + 1e-3
  assert float(np.sum(np.hypot(np.diff(reference.xs, append=reference.xs[0]), np.diff(reference.ys, append=reference.ys[0])))) <= baseline_len + 1e-3


def test_track_session_learns_after_first_lap_and_falls_back_on_low_confidence(monkeypatch, tmp_path):
  monkeypatch.setattr(Paths, "persist_root", staticmethod(lambda: str(tmp_path)))

  config = TrackModeConfig(
    track_name="unit_session",
    allow_any_car=True,
    min_lap_distance=220.0,
    lap_completion_radius=10.0,
    exploratory_speed=12.0,
    learned_max_speed=28.0,
  )
  session = TrackSession(config)

  points = _circle_points(radius=40.0, samples=420, speed=12.0)
  for point in points:
    session.update_odometry(point.t, point.speed, point.yaw, point.curvature, point.accel)

  assert session.first_lap_complete
  assert session.reference is not None
  assert session.completed_laps >= 1

  model_path = np.column_stack((np.linspace(0.0, 60.0, 33), np.zeros(33), np.zeros(33))).astype(np.float32)
  learned = session.plan(0.0, model_path, speed=12.0, yaw=session.local_yaw, localization_confidence=1.0)
  degraded = session.plan(0.0, model_path, speed=12.0, yaw=session.local_yaw, localization_confidence=0.0)

  assert learned.learned_ready
  assert not learned.exploratory
  assert learned.path_xyz.shape[1] == 3
  assert not learned.should_stop
  assert degraded.should_stop


def test_multi_lap_blend_stays_close_to_latest_lap_baseline():
  config = TrackModeConfig(
    track_name="multi_lap_unit",
    allow_any_car=True,
    min_lap_distance=200.0,
    corridor_half_width=3.0,
    optimization_max_offset=0.9,
  )
  first_points = _wavy_track_points()
  previous = fit_reference_from_lap(first_points, config)
  assert previous is not None

  second_points = []
  for point in first_points:
    second_points.append(
      TrackTelemetryPoint(
        x=point.x + 0.25 * math.cos(point.t * 0.15),
        y=point.y + 0.25 * math.sin(point.t * 0.2),
        yaw=point.yaw,
        speed=point.speed,
        curvature=point.curvature,
        accel=point.accel,
        t=point.t,
      )
    )

  xs = np.array([p.x for p in second_points], dtype=np.float32)
  ys = np.array([p.y for p in second_points], dtype=np.float32)
  xs, ys, _ = _resample_closed_path(xs, ys, None, config.resample_spacing)
  xs = _circular_smooth(xs, config.smoothing_window)
  ys = _circular_smooth(ys, config.smoothing_window)

  reference = fit_reference_from_lap(second_points, config, previous=previous)
  assert reference is not None
  offsets = np.hypot(reference.xs - xs, reference.ys - ys)
  assert float(np.max(offsets)) <= config.optimization_max_offset + 1e-3
