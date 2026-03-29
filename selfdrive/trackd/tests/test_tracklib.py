import math

import numpy as np

from openpilot.selfdrive.trackd.tracklib import TrackModeConfig, TrackSession, TrackTelemetryPoint, fit_reference_from_lap
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


def test_fit_reference_from_lap_returns_smoothed_reference():
  config = TrackModeConfig(track_name="unit", allow_any_car=True, min_lap_distance=200.0, learned_max_speed=30.0)
  points = _circle_points()
  reference = fit_reference_from_lap(points, config)

  assert reference is not None
  assert reference.xs.size >= 32
  assert 0.0 <= reference.line_confidence <= 1.0
  assert float(np.max(reference.target_speeds)) <= config.learned_max_speed + 1e-3
  assert float(reference.total_distance) >= config.min_lap_distance


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
