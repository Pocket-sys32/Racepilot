#!/usr/bin/env python3
from __future__ import annotations

import numpy as np

from cereal import car, messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Priority, config_realtime_process
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.locationd.helpers import Pose, PoseCalibrator
from openpilot.selfdrive.trackd.tracklib import TrackModeConfig, TrackSession


def _model_path_xyz(model) -> tuple[list[float], list[float], list[float]]:
  return list(model.position.x), list(model.position.y), list(model.position.z)


def main() -> None:
  config_realtime_process(5, Priority.CTRL_LOW)

  params = Params()
  cloudlog.info("trackd is waiting for CarParams")
  CP = messaging.log_from_bytes(params.get("CarParams", block=True), car.CarParams)
  config = TrackModeConfig.from_bytes(params.get("TrackModeConfig"))
  cloudlog.info("trackd got CarParams: %s", CP.carFingerprint)

  import os as _os
  RESET_FLAG = "/tmp/track_mode_reset"
  _os.unlink(RESET_FLAG) if _os.path.exists(RESET_FLAG) else None

  pose_calibrator = PoseCalibrator()
  session = TrackSession(config)

  sm = messaging.SubMaster(
    ['carState', 'livePose', 'liveCalibration', 'modelV2', 'selfdriveState'],
    poll='modelV2',
  )
  pm = messaging.PubMaster(['longitudinalPlan', 'lateralManeuverPlan', 'driverAssistance', 'trackPlan', 'trackState'])

  while True:
    sm.update()

    track_allowed = params.get_bool("TrackMode")

    if _os.path.exists(RESET_FLAG):
      _os.unlink(RESET_FLAG)
      session.reset_runtime()
      cloudlog.info("trackd: session reset by user")

    if sm.updated['liveCalibration']:
      pose_calibrator.feed_live_calib(sm['liveCalibration'])

    if not sm.updated['modelV2']:
      continue

    model = sm['modelV2']
    model_x, model_y, model_z = _model_path_xyz(model)

    if sm.updated['livePose']:
      calibrated_pose = pose_calibrator.build_calibrated_pose(Pose.from_live_pose(sm['livePose']))
      yaw = float(calibrated_pose.orientation.yaw)
    else:
      yaw = session.local_yaw

    live_pose = sm['livePose']
    localization_confidence = session.build_localization_confidence(
      sm.valid['livePose'],
      bool(live_pose.inputsOK),
      bool(live_pose.sensorsOK),
      bool(live_pose.posenetOK),
    )

    speed = float(max(sm['carState'].vEgo, 0.0))
    accel = float(sm['carState'].aEgo)
    model_curvature = float(model.action.desiredCurvature)
    t = float(sm.logMonoTime['modelV2']) * 1e-9

    enabled = bool(sm['selfdriveState'].enabled and track_allowed)
    if enabled:
      session.update_odometry(t, speed, yaw, model_curvature, accel)
    else:
      session.last_t = t

    command = session.plan(
      model_curvature=model_curvature,
      model_path_xyz=np.column_stack((model_x, model_y, model_z)).astype('float32'),
      speed=speed,
      yaw=yaw,
      localization_confidence=localization_confidence,
    )

    plan_send = messaging.new_message('trackPlan')
    plan_send.valid = track_allowed and enabled
    if command is not None:
      tp = plan_send.trackPlan
      tp.active = enabled
      tp.exploratory = command.exploratory
      tp.learnedReady = command.learned_ready
      tp.targetCurvature = command.target_curvature
      tp.targetSpeed = command.target_speed
      tp.targetAccel = command.target_accel
      tp.shouldStop = command.should_stop
      tp.progress = command.progress
      tp.localizationConfidence = command.localization_confidence
      tp.lineConfidence = command.line_confidence
      if len(command.path_xyz) > 0:
        tp.pathX = command.path_xyz[:, 0].tolist()
        tp.pathY = command.path_xyz[:, 1].tolist()
        tp.pathZ = command.path_xyz[:, 2].tolist()
    pm.send('trackPlan', plan_send)

    state_send = messaging.new_message('trackState')
    state_send.valid = track_allowed
    ts = state_send.trackState
    ts.active = enabled
    ts.exploratory = command.exploratory if command is not None else True
    ts.learnedReady = session.reference is not None
    ts.currentLap = session.current_lap
    ts.completedLaps = session.completed_laps
    ts.lapDistance = float(session.lap_distance)
    ts.trackLength = 0.0 if session.reference is None else float(session.reference.total_distance)
    ts.progress = 0.0 if command is None else command.progress
    ts.localX = float(session.local_x)
    ts.localY = float(session.local_y)
    ts.localYaw = float(session.local_yaw)
    ts.localizationConfidence = localization_confidence
    ts.lineConfidence = 0.0 if session.reference is None else float(session.reference.line_confidence)
    ts.offTrack = False if command is None else command.off_track
    ts.firstLapComplete = session.first_lap_complete
    ts.statusText1 = command.status_text1 if command is not None else "TrackMode Idle"
    ts.statusText2 = command.status_text2 if command is not None else "Waiting for engagement"
    pm.send('trackState', state_send)

    lat_send = messaging.new_message('lateralManeuverPlan')
    lat_send.valid = track_allowed and enabled and command is not None and not command.should_stop
    if lat_send.valid:
      lat_send.lateralManeuverPlan.desiredCurvature = command.target_curvature
    pm.send('lateralManeuverPlan', lat_send)

    long_send = messaging.new_message('longitudinalPlan')
    long_send.valid = track_allowed and enabled and command is not None
    if command is not None:
      lp = long_send.longitudinalPlan
      lp.modelMonoTime = sm.logMonoTime['modelV2']
      lp.processingDelay = max((long_send.logMonoTime - sm.logMonoTime['modelV2']) / 1e9, 0.0)
      lp.aTarget = command.target_accel
      lp.shouldStop = command.should_stop
      lp.allowBrake = True
      lp.allowThrottle = not command.should_stop
      lp.hasLead = False
      lp.fcw = False
      lp.speeds = [max(command.target_speed, 0.0)]
      lp.accels = [command.target_accel]
      lp.jerks = [0.0]
    pm.send('longitudinalPlan', long_send)

    assistance_send = messaging.new_message('driverAssistance')
    assistance_send.valid = False
    pm.send('driverAssistance', assistance_send)


if __name__ == "__main__":
  main()
