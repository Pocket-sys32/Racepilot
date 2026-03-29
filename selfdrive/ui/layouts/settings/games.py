import pyray as rl

from openpilot.selfdrive.games.doom.doom_widget import DoomWidget
from openpilot.selfdrive.games.tictactoe.lobby_widget import LobbyWidget
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle

TITLE_SIZE = 70
DESC_SIZE = 40
BTN_HEIGHT = 120
SPACING = 30


class GamesLayout(Widget):
  """Settings panel listing available games."""

  def __init__(self):
    super().__init__()
    self._doom_btn = self._child(Button(
      "DOOM",
      click_callback=self._launch_doom,
      button_style=ButtonStyle.PRIMARY,
      font_size=55,
    ))
    self._ttt_btn = self._child(Button(
      "Tic-Tac-Toe Online",
      click_callback=self._launch_tictactoe,
      button_style=ButtonStyle.PRIMARY,
      font_size=55,
    ))
    self._font = gui_app.font(FontWeight.MEDIUM)
    self._font_bold = gui_app.font(FontWeight.BOLD)

  def _render(self, rect: rl.Rectangle):
    x = rect.x + 30
    y = rect.y + 30
    w = rect.width - 60

    # Title
    rl.draw_text_ex(self._font_bold, "Games", rl.Vector2(x, y), TITLE_SIZE, 0, rl.WHITE)
    y += TITLE_SIZE + 10

    # Subtitle
    rl.draw_text_ex(self._font, "Available while offroad only",
                    rl.Vector2(x, y), DESC_SIZE, 0, rl.Color(150, 150, 150, 255))
    y += DESC_SIZE + SPACING

    # Doom button
    offroad = ui_state.is_offroad()
    self._doom_btn.enabled = offroad
    self._doom_btn.render(rl.Rectangle(x, y, w, BTN_HEIGHT))
    y += BTN_HEIGHT + 15

    rl.draw_text_ex(self._font, "Classic raycaster FPS with touch controls",
                    rl.Vector2(x + 10, y), 35, 0, rl.Color(130, 130, 130, 255))
    y += 35 + SPACING

    # Tic-Tac-Toe button
    self._ttt_btn.enabled = offroad
    self._ttt_btn.render(rl.Rectangle(x, y, w, BTN_HEIGHT))
    y += BTN_HEIGHT + 15

    rl.draw_text_ex(self._font, "1v1 online multiplayer via Supabase",
                    rl.Vector2(x + 10, y), 35, 0, rl.Color(130, 130, 130, 255))

    if not offroad:
      y += 35 + SPACING
      rl.draw_text_ex(self._font, "Park the car to play games",
                      rl.Vector2(x, y), DESC_SIZE, 0, rl.Color(255, 100, 100, 200))

  def _launch_doom(self):
    gui_app.push_widget(DoomWidget())

  def _launch_tictactoe(self):
    gui_app.push_widget(LobbyWidget())
