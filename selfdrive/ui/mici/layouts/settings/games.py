from openpilot.selfdrive.games.doom.doom_widget import DoomWidget
from openpilot.selfdrive.games.tictactoe.lobby_widget import LobbyWidget
from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets.scroller import NavScroller


class GamesLayoutMici(NavScroller):
  """Mici (comma 4) games selection screen."""

  def __init__(self):
    super().__init__()

    doom_btn = BigButton("doom", "raycaster FPS")
    doom_btn.set_click_callback(lambda: gui_app.push_widget(DoomWidget()))

    ttt_btn = BigButton("tic-tac-toe", "online 1v1")
    ttt_btn.set_click_callback(lambda: gui_app.push_widget(LobbyWidget()))

    self._scroller.add_widgets([doom_btn, ttt_btn])
