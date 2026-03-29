import pyray as rl
from openpilot.doom.doom import DoomGame
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle


class GamesLayout(Widget):
  def __init__(self):
    super().__init__()
    self._doom_game = DoomGame()
    self._doom_button = Button("DOOM", click_callback=self._launch_doom, button_style=ButtonStyle.DANGER, font_size=60)

  def _launch_doom(self):
    gui_app.push_widget(self._doom_game)

  def _render(self, rect: rl.Rectangle):
    btn_rect = rl.Rectangle(rect.x + 50, rect.y + 50, rect.width - 100, 150)
    self._doom_button.render(btn_rect)
