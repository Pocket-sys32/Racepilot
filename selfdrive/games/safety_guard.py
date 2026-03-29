from cereal import log
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.widgets.nav_widget import NavWidget

ThermalStatus = log.DeviceState.ThermalStatus
THERMAL_LIMIT = ThermalStatus.yellow  # 75C+


class GameSafetyGuard(NavWidget):
  """Base class for game widgets. Auto-dismisses on engagement or overheating."""

  def __init__(self):
    super().__init__()
    self._params = Params()
    self._game_active = False

  def show_event(self):
    super().show_event()
    self._game_active = True
    self._params.put_bool("GameActive", True)
    ui_state.add_engaged_transition_callback(self._check_engaged)
    ui_state.add_offroad_transition_callback(self._check_onroad)

  def hide_event(self):
    super().hide_event()
    self._game_active = False
    self._params.put_bool("GameActive", False)

  def _update_state(self):
    super()._update_state()
    if not self._game_active:
      return
    # Check engagement
    if ui_state.engaged:
      self._safety_dismiss("engaged")
      return
    # Check thermal status
    if ui_state.sm.valid["deviceState"]:
      thermal = ui_state.sm["deviceState"].thermalStatus
      if thermal >= THERMAL_LIMIT:
        self._safety_dismiss("thermal")

  def _check_engaged(self):
    if ui_state.engaged and self._game_active:
      self._safety_dismiss("engaged")

  def _check_onroad(self):
    if ui_state.started and self._game_active:
      self._safety_dismiss("onroad")

  def _safety_dismiss(self, reason: str):
    cloudlog.warning(f"Game auto-closed: {reason}")
    self._game_active = False
    self._params.put_bool("GameActive", False)
    self.dismiss()
