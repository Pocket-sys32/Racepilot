from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from opendbc.car.interfaces import ACCEL_MIN
from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths


def _clamp(value: float, lo: float, hi: float) -> float:
  return float(min(max(value, lo), hi))


def _angle_diff(a: float, b: float) -> float:
  return math.atan2(math.sin(a - b), math.cos(a - b))


def _safe_track_name(name: str) -> str:
  cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip()).strip("_")
  return cleaned or "track"


def _bool_confidence(*flags: bool) -> float:
  if not flags:
    return 0.0
  return float(sum(bool(v) for v in flags)) / float(len(flags))


def _circular_smooth(values: np.ndarray, window: int) -> np.ndarray:
  if window <= 1 or values.size < 3:
    return values.copy()

  window = max(3, int(window))
  if window % 2 == 0:
    window += 1

  pad = window // 2
  kernel = np.ones(window, dtype=np.float64) / float(window)
  padded = np.pad(values.astype(np.float64), (pad, pad), mode="wrap")
  return np.convolve(padded, kernel, mode="valid").astype(np.float32)


def _compute_curvature(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
  if xs.size < 5:
    return np.zeros_like(xs, dtype=np.float32)

  dx = np.gradient(xs)
  dy = np.gradient(ys)
  ddx = np.gradient(dx)
  ddy = np.gradient(dy)
  denom = np.power(np.maximum(dx * dx + dy * dy, 1e-4), 1.5)
  curvature = (dx * ddy - dy * ddx) / denom
  return curvature.astype(np.float32)


def _cumulative_distance(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
  if xs.size == 0:
    return np.zeros((0,), dtype=np.float32)

  deltas = np.hypot(np.diff(xs, append=xs[0]), np.diff(ys, append=ys[0]))
  return np.concatenate(([0.0], np.cumsum(deltas[:-1], dtype=np.float64))).astype(np.float32)


def _resample_closed_path(xs: np.ndarray, ys: np.ndarray, values: np.ndarray | None, spacing: float) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
  if xs.size < 4:
    copied = values.copy() if values is not None else None
    return xs.copy(), ys.copy(), copied

  seg = np.hypot(np.diff(xs, append=xs[0]), np.diff(ys, append=ys[0]))
  total = float(np.sum(seg))
  if total < max(spacing * 4.0, 5.0):
    copied = values.copy() if values is not None else None
    return xs.copy(), ys.copy(), copied

  samples = max(int(total / max(spacing, 0.5)), 32)
  distances = np.concatenate(([0.0], np.cumsum(seg, dtype=np.float64)))
  xs_ext = np.append(xs, xs[0])
  ys_ext = np.append(ys, ys[0])
  target_s = np.linspace(0.0, total, samples, endpoint=False)
  resampled_x = np.interp(target_s, distances, xs_ext).astype(np.float32)
  resampled_y = np.interp(target_s, distances, ys_ext).astype(np.float32)

  if values is None:
    return resampled_x, resampled_y, None

  values_ext = np.append(values, values[0])
  resampled_values = np.interp(target_s, distances, values_ext).astype(np.float32)
  return resampled_x, resampled_y, resampled_values


def _clamp_points_to_reference(points: np.ndarray, reference: np.ndarray, max_offset: float) -> np.ndarray:
  delta = points - reference
  delta_norm = np.linalg.norm(delta, axis=1)
  over = delta_norm > max_offset
  if np.any(over):
    points = points.copy()
    points[over] = reference[over] + delta[over] * (max_offset / delta_norm[over])[:, None]
  return points


@dataclass
class TrackModeConfig:
  track_name: str = "track"
  allow_any_car: bool = True
  allowed_car_fingerprints: tuple[str, ...] = ()
  allowed_brands: tuple[str, ...] = ()
  exploratory_speed: float = 13.0
  exploratory_max_accel: float = 1.0
  learned_max_speed: float = 38.0
  learned_max_accel: float = 2.5
  learned_max_lat_accel: float = 4.5
  lap_completion_radius: float = 18.0
  min_lap_distance: float = 1000.0
  smoothing_window: int = 25
  resample_spacing: float = 2.0
  lookahead_seconds: float = 0.9
  corridor_half_width: float = 3.0
  path_horizon_points: int = 33
  optimization_iterations: int = 80
  optimization_smooth_weight: float = 0.22
  optimization_fidelity_weight: float = 0.08
  optimization_max_offset: float = 1.0
  min_localization_confidence: float = 0.45
  min_line_confidence: float = 0.35
  enable_learned_execution: bool = True

  @classmethod
  def from_bytes(cls, raw: bytes | str | None) -> "TrackModeConfig":
    if raw is None:
      return cls()

    try:
      text = raw.decode("utf-8").strip() if isinstance(raw, bytes) else str(raw).strip()
      if not text:
        return cls()
      payload = json.loads(text)
    except Exception:
      return cls()

    if not isinstance(payload, dict):
      return cls()

    return cls(
      track_name=str(payload.get("track_name", cls.track_name)),
      allow_any_car=bool(payload.get("allow_any_car", cls.allow_any_car)),
      allowed_car_fingerprints=tuple(str(v) for v in payload.get("allowed_car_fingerprints", [])),
      allowed_brands=tuple(str(v) for v in payload.get("allowed_brands", [])),
      exploratory_speed=float(payload.get("exploratory_speed", cls.exploratory_speed)),
      exploratory_max_accel=float(payload.get("exploratory_max_accel", cls.exploratory_max_accel)),
      learned_max_speed=float(payload.get("learned_max_speed", cls.learned_max_speed)),
      learned_max_accel=float(payload.get("learned_max_accel", cls.learned_max_accel)),
      learned_max_lat_accel=float(payload.get("learned_max_lat_accel", cls.learned_max_lat_accel)),
      lap_completion_radius=float(payload.get("lap_completion_radius", cls.lap_completion_radius)),
      min_lap_distance=float(payload.get("min_lap_distance", cls.min_lap_distance)),
      smoothing_window=int(payload.get("smoothing_window", cls.smoothing_window)),
      resample_spacing=float(payload.get("resample_spacing", cls.resample_spacing)),
      lookahead_seconds=float(payload.get("lookahead_seconds", cls.lookahead_seconds)),
      corridor_half_width=float(payload.get("corridor_half_width", cls.corridor_half_width)),
      path_horizon_points=int(payload.get("path_horizon_points", cls.path_horizon_points)),
      optimization_iterations=int(payload.get("optimization_iterations", cls.optimization_iterations)),
      optimization_smooth_weight=float(payload.get("optimization_smooth_weight", cls.optimization_smooth_weight)),
      optimization_fidelity_weight=float(payload.get("optimization_fidelity_weight", cls.optimization_fidelity_weight)),
      optimization_max_offset=float(payload.get("optimization_max_offset", cls.optimization_max_offset)),
      min_localization_confidence=float(payload.get("min_localization_confidence", cls.min_localization_confidence)),
      min_line_confidence=float(payload.get("min_line_confidence", cls.min_line_confidence)),
      enable_learned_execution=bool(payload.get("enable_learned_execution", cls.enable_learned_execution)),
    )

  def is_allowlisted(self, car_fingerprint: str, brand: str) -> bool:
    if self.allow_any_car:
      return True
    if car_fingerprint and car_fingerprint in self.allowed_car_fingerprints:
      return True
    if brand and brand in self.allowed_brands:
      return True
    return False

  @property
  def storage_path(self) -> Path:
    root = (Path(Paths.comma_home()) / "media" / "0" if PC else Path("/data/media/0")) / "track_mode"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{_safe_track_name(self.track_name)}.json"


@dataclass
class TrackTelemetryPoint:
  x: float
  y: float
  yaw: float
  speed: float
  curvature: float
  accel: float
  t: float


@dataclass
class TrackReference:
  xs: np.ndarray
  ys: np.ndarray
  zs: np.ndarray
  target_speeds: np.ndarray
  curvature: np.ndarray
  progress: np.ndarray
  total_distance: float
  line_confidence: float
  source_laps: int

  def to_dict(self) -> dict[str, Any]:
    return {
      "xs": self.xs.tolist(),
      "ys": self.ys.tolist(),
      "zs": self.zs.tolist(),
      "target_speeds": self.target_speeds.tolist(),
      "curvature": self.curvature.tolist(),
      "progress": self.progress.tolist(),
      "total_distance": float(self.total_distance),
      "line_confidence": float(self.line_confidence),
      "source_laps": int(self.source_laps),
    }

  @classmethod
  def from_dict(cls, data: dict[str, Any]) -> "TrackReference":
    return cls(
      xs=np.array(data["xs"], dtype=np.float32),
      ys=np.array(data["ys"], dtype=np.float32),
      zs=np.array(data.get("zs", [0.0] * len(data["xs"])), dtype=np.float32),
      target_speeds=np.array(data["target_speeds"], dtype=np.float32),
      curvature=np.array(data["curvature"], dtype=np.float32),
      progress=np.array(data["progress"], dtype=np.float32),
      total_distance=float(data["total_distance"]),
      line_confidence=float(data["line_confidence"]),
      source_laps=int(data.get("source_laps", 1)),
    )


@dataclass
class TrackCommand:
  exploratory: bool
  learned_ready: bool
  target_curvature: float
  target_speed: float
  target_accel: float
  should_stop: bool
  progress: float
  localization_confidence: float
  line_confidence: float
  path_xyz: np.ndarray
  status_text1: str
  status_text2: str
  off_track: bool


def fit_reference_from_lap(points: list[TrackTelemetryPoint], config: TrackModeConfig,
                           previous: TrackReference | None = None) -> TrackReference | None:
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
    blended = _clamp_points_to_reference(np.column_stack((xs.astype(np.float64), ys.astype(np.float64))), baseline,
                                         min(config.optimization_max_offset, max(config.corridor_half_width * 0.35, 0.25)))
    xs = blended[:, 0].astype(np.float32)
    ys = blended[:, 1].astype(np.float32)

  curvature = _compute_curvature(xs, ys)
  closure_error = float(math.hypot(xs[0] - xs[-1], ys[0] - ys[-1]))
  total_distance = float(np.sum(np.hypot(np.diff(xs, append=xs[0]), np.diff(ys, append=ys[0]))))
  if total_distance < config.min_lap_distance * 0.5:
    return None

  target_speeds = np.sqrt(np.maximum(config.learned_max_lat_accel / np.maximum(np.abs(curvature), 1e-3), 0.0)).astype(np.float32)
  target_speeds = np.clip(target_speeds, config.exploratory_speed * 0.8, config.learned_max_speed)
  observed_cap = max(float(np.percentile(speeds, 90)), config.exploratory_speed)
  target_speeds = np.minimum(target_speeds, max(observed_cap * 1.4, config.exploratory_speed)).astype(np.float32)
  target_speeds = _circular_smooth(target_speeds, max(5, config.smoothing_window // 2))

  progress = _cumulative_distance(xs, ys)
  if total_distance > 1e-3:
    progress = (progress / total_distance).astype(np.float32)

  confidence = _clamp(1.0 - (closure_error / max(config.lap_completion_radius * 2.0, 1.0)), 0.0, 1.0)
  if previous is not None:
    confidence = max(confidence, previous.line_confidence * 0.8)

  return TrackReference(
    xs=xs,
    ys=ys,
    zs=np.zeros_like(xs, dtype=np.float32),
    target_speeds=target_speeds,
    curvature=curvature.astype(np.float32),
    progress=progress.astype(np.float32),
    total_distance=total_distance,
    line_confidence=confidence,
    source_laps=1 if previous is None else previous.source_laps + 1,
  )


def _optimize_reference_line(xs: np.ndarray, ys: np.ndarray, config: TrackModeConfig) -> tuple[np.ndarray, np.ndarray]:
  if xs.size < 8:
    return xs.copy(), ys.copy()

  original = np.column_stack((xs.astype(np.float64), ys.astype(np.float64)))
  current = original.copy()
  max_offset = min(config.optimization_max_offset, max(config.corridor_half_width * 0.35, 0.25))

  for _ in range(max(config.optimization_iterations, 1)):
    prev_pts = np.roll(current, 1, axis=0)
    next_pts = np.roll(current, -1, axis=0)
    laplacian = 0.5 * (prev_pts + next_pts) - current
    current += config.optimization_smooth_weight * laplacian
    current += config.optimization_fidelity_weight * (original - current)
    current = _clamp_points_to_reference(current, original, max_offset)

  optimized_x = _circular_smooth(current[:, 0].astype(np.float32), max(5, config.smoothing_window // 2))
  optimized_y = _circular_smooth(current[:, 1].astype(np.float32), max(5, config.smoothing_window // 2))
  optimized = np.column_stack((optimized_x.astype(np.float64), optimized_y.astype(np.float64)))
  optimized = _clamp_points_to_reference(optimized, original, max_offset)
  return optimized[:, 0].astype(np.float32), optimized[:, 1].astype(np.float32)


class TrackSession:
  def __init__(self, config: TrackModeConfig):
    self.config = config
    self.reference: TrackReference | None = None
    self.previous_reference: TrackReference | None = None

    self.local_x = 0.0
    self.local_y = 0.0
    self.local_yaw = 0.0
    self.last_t: float | None = None
    self.lap_distance = 0.0
    self.current_lap = 1
    self.completed_laps = 0
    self.first_lap_complete = False
    self.last_completion_t = -1e9
    self.start_x = 0.0
    self.start_y = 0.0
    self.start_yaw = 0.0
    self.telemetry: list[TrackTelemetryPoint] = []
    self.telemetry_history: list[list[TrackTelemetryPoint]] = []
    self.load_reference()

  def reset_runtime(self) -> None:
    self.local_x = 0.0
    self.local_y = 0.0
    self.local_yaw = 0.0
    self.last_t = None
    self.lap_distance = 0.0
    self.current_lap = max(self.completed_laps + 1, 1)
    self.last_completion_t = -1e9
    self.start_x = 0.0
    self.start_y = 0.0
    self.start_yaw = 0.0
    self.telemetry = []

  def load_reference(self) -> None:
    path = self.config.storage_path
    if not path.exists():
      return

    try:
      payload = json.loads(path.read_text())
      self.reference = TrackReference.from_dict(payload["reference"])
      self.previous_reference = TrackReference.from_dict(payload["previous_reference"]) if payload.get("previous_reference") else None
      self.completed_laps = int(payload.get("completed_laps", 0))
      self.first_lap_complete = bool(self.reference is not None)
      self.current_lap = self.completed_laps + 1
    except Exception:
      self.reference = None
      self.previous_reference = None

  def save_reference(self) -> None:
    if self.reference is None:
      return

    payload = {
      "completed_laps": self.completed_laps,
      "reference": self.reference.to_dict(),
      "previous_reference": None if self.previous_reference is None else self.previous_reference.to_dict(),
    }
    self.config.storage_path.write_text(json.dumps(payload))

  def update_odometry(self, t: float, speed: float, yaw: float, curvature: float, accel: float) -> bool:
    completed = False

    if self.last_t is None:
      self.last_t = t
      self.local_yaw = yaw
      self.start_yaw = yaw
      self.telemetry.append(TrackTelemetryPoint(0.0, 0.0, yaw, speed, curvature, accel, t))
      return False

    dt = _clamp(t - self.last_t, 0.0, 0.2)
    self.last_t = t
    self.local_yaw = yaw

    dx = speed * math.cos(yaw) * dt
    dy = speed * math.sin(yaw) * dt
    self.local_x += dx
    self.local_y += dy
    self.lap_distance += math.hypot(dx, dy)

    if not self.telemetry:
      self.start_x = self.local_x
      self.start_y = self.local_y
      self.start_yaw = yaw

    self.telemetry.append(TrackTelemetryPoint(self.local_x, self.local_y, yaw, speed, curvature, accel, t))

    if self._lap_complete(t):
      completed = True
      self._finish_lap()

    return completed

  def _lap_complete(self, t: float) -> bool:
    if len(self.telemetry) < 128:
      return False
    if self.lap_distance < self.config.min_lap_distance:
      return False
    if (t - self.last_completion_t) < 10.0:
      return False

    dist_to_start = math.hypot(self.local_x - self.start_x, self.local_y - self.start_y)
    heading_ok = abs(_angle_diff(self.local_yaw, self.start_yaw)) < math.radians(45.0)
    return dist_to_start <= self.config.lap_completion_radius and heading_ok

  def _finish_lap(self) -> None:
    self.telemetry_history.append(self.telemetry.copy())
    candidate = fit_reference_from_lap(self.telemetry, self.config, self.reference)
    if candidate is not None and candidate.line_confidence >= self.config.min_line_confidence:
      self.previous_reference = self.reference
      self.reference = candidate
      self.first_lap_complete = True
      self.save_reference()

    self.completed_laps += 1
    self.current_lap = self.completed_laps + 1
    self.last_completion_t = self.telemetry[-1].t

    last_point = self.telemetry[-1]
    self.local_x = 0.0
    self.local_y = 0.0
    self.lap_distance = 0.0
    self.start_x = 0.0
    self.start_y = 0.0
    self.start_yaw = last_point.yaw
    self.telemetry = [TrackTelemetryPoint(0.0, 0.0, last_point.yaw, last_point.speed, last_point.curvature, last_point.accel, last_point.t)]

  def _nearest_reference_index(self) -> tuple[int, float]:
    assert self.reference is not None
    dx = self.reference.xs - self.local_x
    dy = self.reference.ys - self.local_y
    dist = np.hypot(dx, dy)
    idx = int(np.argmin(dist))
    return idx, float(dist[idx])

  def _reference_path_in_ego(self, yaw: float, speed: float) -> tuple[np.ndarray, int, float]:
    assert self.reference is not None
    idx, dist = self._nearest_reference_index()
    count = max(self.config.path_horizon_points, 8)
    sample_indices = (idx + np.arange(count)) % self.reference.xs.size
    dx = self.reference.xs[sample_indices] - self.local_x
    dy = self.reference.ys[sample_indices] - self.local_y
    c, s = math.cos(yaw), math.sin(yaw)
    x_ego = c * dx + s * dy
    y_ego = -s * dx + c * dy
    path_xyz = np.column_stack((x_ego, y_ego, self.reference.zs[sample_indices])).astype(np.float32)

    min_x = max(speed * self.config.lookahead_seconds, 6.0)
    lookahead_idx = int(np.argmax(path_xyz[:, 0] >= min_x))
    if path_xyz[lookahead_idx, 0] < min_x:
      lookahead_idx = min(len(path_xyz) - 1, max(1, len(path_xyz) // 3))
    return path_xyz, idx, dist

  def plan(self, model_curvature: float, model_path_xyz: np.ndarray, speed: float, yaw: float,
           localization_confidence: float) -> TrackCommand:
    exploratory = self.reference is None or not self.config.enable_learned_execution

    if exploratory:
      target_speed = self.config.exploratory_speed
      target_accel = _clamp((target_speed - speed) * 0.5, ACCEL_MIN, self.config.exploratory_max_accel)
      return TrackCommand(
        exploratory=True,
        learned_ready=self.reference is not None,
        target_curvature=float(model_curvature),
        target_speed=target_speed,
        target_accel=target_accel,
        should_stop=False,
        progress=0.0 if self.reference is None else float(self.reference.progress[-1]),
        localization_confidence=localization_confidence,
        line_confidence=0.0 if self.reference is None else self.reference.line_confidence,
        path_xyz=model_path_xyz.astype(np.float32),
        status_text1=f"Track Lap {self.current_lap}",
        status_text2="Exploratory lap collecting telemetry",
        off_track=False,
      )

    path_xyz, nearest_idx, nearest_dist = self._reference_path_in_ego(yaw, speed)
    line_confidence = self.reference.line_confidence
    off_track = nearest_dist > max(self.config.corridor_half_width, 1.0)
    poor_localization = localization_confidence < self.config.min_localization_confidence
    should_stop = off_track or poor_localization or line_confidence < self.config.min_line_confidence

    progress = float(self.reference.progress[nearest_idx])
    target_speed = float(self.reference.target_speeds[nearest_idx])
    target_accel = _clamp((target_speed - speed) * 0.8, ACCEL_MIN, self.config.learned_max_accel)

    valid_lookahead = path_xyz[(path_xyz[:, 0] > 2.0)]
    if len(valid_lookahead) == 0:
      target_curvature = float(model_curvature)
    else:
      lookahead = valid_lookahead[min(len(valid_lookahead) - 1, max(1, len(valid_lookahead) // 4))]
      target_curvature = float(_clamp((2.0 * lookahead[1]) / max(lookahead[0] ** 2, 4.0), -0.2, 0.2))

    if should_stop:
      target_speed = 0.0
      target_accel = min(target_accel, -1.0)

    status_text2 = "Following learned line"
    if poor_localization:
      status_text2 = "Localization degraded, slowing down"
    elif off_track:
      status_text2 = "Track confidence lost, stopping"

    return TrackCommand(
      exploratory=False,
      learned_ready=True,
      target_curvature=target_curvature,
      target_speed=target_speed,
      target_accel=target_accel,
      should_stop=should_stop,
      progress=progress,
      localization_confidence=localization_confidence,
      line_confidence=line_confidence,
      path_xyz=path_xyz,
      status_text1=f"Track Lap {self.current_lap}",
      status_text2=status_text2,
      off_track=off_track,
    )

  def build_localization_confidence(self, live_pose_valid: bool, inputs_ok: bool, sensors_ok: bool, posenet_ok: bool) -> float:
    return _bool_confidence(live_pose_valid, inputs_ok, sensors_ok, posenet_ok)
