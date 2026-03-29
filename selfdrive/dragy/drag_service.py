"""
Drag run timing service.

Runs as a daemon thread inside the UI process. Subscribes to carState,
gpsLocationExternal, and trackState. Only arms when trackState.active is True.

Sensor fusion:
  - carState.vEgo (100 Hz)  — primary speed for timing
  - carState.aEgo (100 Hz)  — launch / brake detection
  - gpsLocationExternal.speed (10 Hz) — slow bias correction of CAN speed
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import cereal.messaging as messaging
from openpilot.common.swaglog import cloudlog
from openpilot.system.hardware import PC
from openpilot.system.hardware.hw import Paths

MS_TO_MPH = 2.23694
MPH_TO_MS = 1.0 / MS_TO_MPH
QM_DISTANCE = 402.336   # metres — exactly 1/4 mile
MAX_RUNS_SAVED = 30

# Thresholds
LAUNCH_ACCEL_THRESHOLD = 1.5   # m/s²  — minimum acceleration to count as a drag launch
GPS_CORRECTION_ALPHA = 0.05    # LP filter weight for GPS bias correction
GPS_MIN_SPEED = 2.0            # m/s   — below this GPS correction is not applied
GPS_MAX_FACTOR = 1.15
GPS_MIN_FACTOR = 0.85

# Auto-stop thresholds
RUN_ABORT_SPEED = 2.0          # m/s   — if speed drops below this after >3 s, abort
RUN_ABORT_MIN_ELAPSED = 3.0    # s
RUN_MAX_SPEED = 62.0           # m/s (~139 mph) — well past 130 mph target
RUN_MAX_DISTANCE = 440.0       # m   — well past 1/4 mile

# Christmas-tree timing (seconds per phase)
CT_RED_DURATION = 0.8
CT_YELLOW_DURATION = 0.8


class DragState(IntEnum):
  IDLE = 0
  ARMED = 1    # at standstill, christmas tree counting down
  RUNNING = 2  # run in progress
  DONE = 3     # showing results (8 s), then back to IDLE


class CountdownPhase(IntEnum):
  RED = 0
  YELLOW = 1
  GREEN = 2


@dataclass
class DragRun:
  timestamp: float            # unix time at launch
  t0_60: float | None         # seconds, interpolated
  t60_130: float | None       # seconds, interpolated
  qm_et: float | None         # seconds, interpolated
  qm_speed_mph: float | None  # mph at 1/4-mile crossing
  max_speed_mph: float
  gps_correction_factor: float
  partial: bool = False       # True if track mode cut out mid-run

  def to_dict(self) -> dict:
    return {
      "timestamp": self.timestamp,
      "t0_60": self.t0_60,
      "t60_130": self.t60_130,
      "qm_et": self.qm_et,
      "qm_speed_mph": self.qm_speed_mph,
      "max_speed_mph": self.max_speed_mph,
      "gps_correction_factor": self.gps_correction_factor,
      "partial": self.partial,
    }

  @classmethod
  def from_dict(cls, d: dict) -> "DragRun":
    return cls(
      timestamp=float(d["timestamp"]),
      t0_60=d.get("t0_60"),
      t60_130=d.get("t60_130"),
      qm_et=d.get("qm_et"),
      qm_speed_mph=d.get("qm_speed_mph"),
      max_speed_mph=float(d.get("max_speed_mph", 0.0)),
      gps_correction_factor=float(d.get("gps_correction_factor", 1.0)),
      partial=bool(d.get("partial", False)),
    )


def _interpolate_speed_crossing(samples: list, threshold_v: float) -> float | None:
  """Return elapsed time (seconds from samples[0]) when speed crosses threshold_v.
  Uses linear interpolation between adjacent samples — no sample-quantization error."""
  t0 = samples[0][0]
  for i in range(1, len(samples)):
    t1, v1, _, _ = samples[i - 1]
    t2, v2, _, _ = samples[i]
    if v1 < threshold_v <= v2:
      frac = (threshold_v - v1) / max(v2 - v1, 1e-9)
      return (t1 + frac * (t2 - t1)) - t0
  return None


def _interpolate_distance_crossing(samples: list, threshold_d: float) -> tuple[float | None, float | None]:
  """Return (elapsed_time, speed_mph) when distance crosses threshold_d.
  Interpolated between adjacent samples."""
  t0 = samples[0][0]
  for i in range(1, len(samples)):
    t1, v1, _, d1 = samples[i - 1]
    t2, v2, _, d2 = samples[i]
    if d1 < threshold_d <= d2:
      frac = (threshold_d - d1) / max(d2 - d1, 1e-9)
      et = (t1 + frac * (t2 - t1)) - t0
      spd = (v1 + frac * (v2 - v1)) * MS_TO_MPH
      return et, spd
  return None, None


class DragService:
  _instance: "DragService | None" = None
  _lock_instance = threading.Lock()

  @classmethod
  def get(cls) -> "DragService":
    with cls._lock_instance:
      if cls._instance is None:
        cls._instance = cls()
    return cls._instance

  def __init__(self):
    self._lock = threading.Lock()

    # State visible to UI thread
    self._state = DragState.IDLE
    self._countdown_phase = CountdownPhase.RED
    self._armed_t: float = 0.0
    self._run_start_t: float = 0.0
    self._current_elapsed: float = 0.0
    self._current_speed_mph: float = 0.0
    self._current_distance: float = 0.0
    self._current_accel: float = 0.0
    self._current_milestones: dict = {}   # e.g. {"t0_60": 3.8, "t60_130": None, "qm": (12.1, 105.2)}
    self._done_t: float = 0.0
    self._last_run: DragRun | None = None
    self._runs: list[DragRun] = []

    # Internal (service thread only, no lock needed)
    self._samples: list = []   # (t, v_fused, a_can, distance)
    self._gps_correction: float = 1.0

    _media_root = Path(Paths.comma_home()) / "media" / "0" if PC else Path("/data/media/0")
    self._persist_dir = _media_root / "drag_runs"
    self._persist_dir.mkdir(parents=True, exist_ok=True)
    self._load_runs()

    self._thread = threading.Thread(target=self._run_loop, daemon=True, name="drag_service")
    self._thread.start()

  # ── Public API (UI thread) ─────────────────────────────────────────────────

  @property
  def state(self) -> DragState:
    with self._lock:
      return self._state

  @property
  def countdown_phase(self) -> CountdownPhase:
    with self._lock:
      return self._countdown_phase

  @property
  def current_elapsed(self) -> float:
    with self._lock:
      return self._current_elapsed

  @property
  def current_speed_mph(self) -> float:
    with self._lock:
      return self._current_speed_mph

  @property
  def current_distance(self) -> float:
    with self._lock:
      return self._current_distance

  @property
  def current_accel(self) -> float:
    with self._lock:
      return self._current_accel

  @property
  def current_milestones(self) -> dict:
    with self._lock:
      return dict(self._current_milestones)

  @property
  def last_run(self) -> DragRun | None:
    with self._lock:
      return self._last_run

  @property
  def runs(self) -> list[DragRun]:
    with self._lock:
      return list(self._runs)

  # ── Persistence ────────────────────────────────────────────────────────────

  def _load_runs(self):
    files = sorted(self._persist_dir.glob("*.json"), reverse=True)[:MAX_RUNS_SAVED]
    for f in files:
      try:
        self._runs.append(DragRun.from_dict(json.loads(f.read_text())))
      except Exception:
        pass
    if self._runs:
      self._last_run = self._runs[0]

  def _save_run(self, run: DragRun):
    fname = self._persist_dir / f"{int(run.timestamp)}.json"
    try:
      fname.write_text(json.dumps(run.to_dict()))
    except Exception as e:
      cloudlog.error(f"drag_service: failed to save run: {e}")

  # ── Background loop ────────────────────────────────────────────────────────

  def _run_loop(self):
    try:
      sm = messaging.SubMaster(
        ['carState', 'gpsLocationExternal', 'trackState'],
        poll='carState',
      )
    except Exception as e:
      cloudlog.error(f"drag_service: SubMaster init failed: {e}")
      return

    prev_standstill = True

    while True:
      try:
        sm.update(timeout=30)
      except Exception:
        time.sleep(0.1)
        continue

      if not sm.updated['carState']:
        continue

      t = sm.logMonoTime['carState'] * 1e-9
      cs = sm['carState']
      v_can = float(max(cs.vEgo, 0.0))
      a_can = float(cs.aEgo)
      standstill = bool(cs.standstill)

      # GPS bias correction
      if sm.updated['gpsLocationExternal'] and sm.valid['gpsLocationExternal']:
        v_gps = float(sm['gpsLocationExternal'].speed)
        if v_can > GPS_MIN_SPEED and v_gps > GPS_MIN_SPEED:
          raw_factor = v_gps / v_can
          raw_factor = max(GPS_MIN_FACTOR, min(GPS_MAX_FACTOR, raw_factor))
          self._gps_correction += GPS_CORRECTION_ALPHA * (raw_factor - self._gps_correction)

      v_fused = v_can * self._gps_correction

      # Track mode gate
      track_active = sm.valid['trackState'] and sm['trackState'].active

      with self._lock:
        state = self._state

        if state == DragState.IDLE:
          if track_active and standstill:
            self._state = DragState.ARMED
            self._armed_t = t
            self._countdown_phase = CountdownPhase.RED
            cloudlog.info("drag_service: ARMED")

        elif state == DragState.ARMED:
          if not track_active or not standstill:
            if not standstill and prev_standstill:
              # Possible launch during armed
              if a_can > LAUNCH_ACCEL_THRESHOLD:
                self._state = DragState.RUNNING
                self._run_start_t = t
                self._samples = [(t, v_fused, a_can, 0.0)]
                self._current_milestones = {"t0_60": None, "t60_130": None, "qm_et": None, "qm_speed": None}
                cloudlog.info("drag_service: RUNNING (launch from ARMED)")
              else:
                # Moved but not a real launch
                self._state = DragState.IDLE
            elif not track_active:
              self._state = DragState.IDLE
          else:
            # Advance christmas tree
            dt = t - self._armed_t
            if dt < CT_RED_DURATION:
              self._countdown_phase = CountdownPhase.RED
            elif dt < CT_RED_DURATION + CT_YELLOW_DURATION:
              self._countdown_phase = CountdownPhase.YELLOW
            else:
              self._countdown_phase = CountdownPhase.GREEN

        elif state == DragState.RUNNING:
          if not track_active:
            cloudlog.info("drag_service: track mode lost mid-run, saving partial")
            self._finish_run(t, partial=True)
          else:
            # Integrate distance (trapezoidal)
            prev_t, prev_v, _, prev_d = self._samples[-1]
            dt = max(t - prev_t, 0.0)
            distance = prev_d + (prev_v + v_fused) / 2.0 * dt
            self._samples.append((t, v_fused, a_can, distance))

            # Update public state
            self._current_elapsed = t - self._run_start_t
            self._current_speed_mph = v_fused * MS_TO_MPH
            self._current_distance = distance
            self._current_accel = a_can

            # Live milestones
            self._update_milestones()

            # Auto-stop
            elapsed = t - self._run_start_t
            if distance > RUN_MAX_DISTANCE or v_fused > RUN_MAX_SPEED:
              self._finish_run(t, partial=False)
            elif v_fused < RUN_ABORT_SPEED and elapsed > RUN_ABORT_MIN_ELAPSED:
              cloudlog.info("drag_service: run aborted (speed dropped)")
              self._finish_run(t, partial=True)

        elif state == DragState.DONE:
          if t - self._done_t > 8.0:
            self._state = DragState.IDLE
            cloudlog.info("drag_service: IDLE (after results display)")

      prev_standstill = standstill

  def _update_milestones(self):
    """Update live milestone display. Called with lock held."""
    samples = self._samples
    m = self._current_milestones

    if m.get("t0_60") is None:
      m["t0_60"] = _interpolate_speed_crossing(samples, 60 * MPH_TO_MS)

    if m.get("t60_130") is None and m.get("t0_60") is not None:
      # Start measuring 60-130 from the point we crossed 60 mph
      t60 = _interpolate_speed_crossing(samples, 60 * MPH_TO_MS)
      t130 = _interpolate_speed_crossing(samples, 130 * MPH_TO_MS)
      if t60 is not None and t130 is not None:
        m["t60_130"] = t130 - t60

    if m.get("qm_et") is None:
      qm_et, qm_speed = _interpolate_distance_crossing(samples, QM_DISTANCE)
      if qm_et is not None:
        m["qm_et"] = qm_et
        m["qm_speed"] = qm_speed

  def _finish_run(self, t: float, partial: bool):
    """Compute final metrics, save, transition to DONE. Called with lock held."""
    samples = self._samples
    if len(samples) < 10:
      self._state = DragState.IDLE
      self._samples = []
      return

    t0_60 = _interpolate_speed_crossing(samples, 60 * MPH_TO_MS)
    t130 = _interpolate_speed_crossing(samples, 130 * MPH_TO_MS)
    t60 = _interpolate_speed_crossing(samples, 60 * MPH_TO_MS)
    t60_130 = (t130 - t60) if (t60 is not None and t130 is not None) else None
    qm_et, qm_speed = _interpolate_distance_crossing(samples, QM_DISTANCE)
    max_speed = max(v * MS_TO_MPH for _, v, _, _ in samples)

    run = DragRun(
      timestamp=time.time(),
      t0_60=t0_60,
      t60_130=t60_130,
      qm_et=qm_et,
      qm_speed_mph=qm_speed,
      max_speed_mph=max_speed,
      gps_correction_factor=self._gps_correction,
      partial=partial,
    )

    self._last_run = run
    self._runs.insert(0, run)
    if len(self._runs) > MAX_RUNS_SAVED:
      self._runs = self._runs[:MAX_RUNS_SAVED]

    self._state = DragState.DONE
    self._done_t = t
    self._current_milestones = {
      "t0_60": t0_60,
      "t60_130": t60_130,
      "qm_et": qm_et,
      "qm_speed": qm_speed,
    }
    self._samples = []

    cloudlog.info(f"drag_service: run complete — 0-60: {t0_60}, QM: {qm_et} @ {qm_speed} mph, partial={partial}")
    self._save_run(run)
