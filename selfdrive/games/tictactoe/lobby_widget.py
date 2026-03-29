import random
import string
import threading

import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.games.supabase_client import SupabaseREST, SupabaseRealtimeListener, get_supabase_config
from openpilot.selfdrive.games.tictactoe.tictactoe_widget import TicTacToeWidget
from openpilot.selfdrive.games.safety_guard import GameSafetyGuard
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets import DialogResult
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.widgets.keyboard import Keyboard


def _generate_lobby_code() -> str:
  return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


class LobbyWidget(GameSafetyGuard):
  """Lobby screen for creating or joining a Tic-Tac-Toe game."""

  def __init__(self):
    super().__init__()
    self._params = Params()
    self._dongle_id = self._params.get("DongleId", encoding="utf-8") or "unknown"
    self._font = gui_app.font(FontWeight.MEDIUM)
    self._font_bold = gui_app.font(FontWeight.BOLD)

    # State
    self._state = "menu"  # menu, creating, waiting, joining, error
    self._lobby_code = ""
    self._error_msg = ""
    self._game_data: dict | None = None
    self._listener: SupabaseRealtimeListener | None = None
    self._lock = threading.Lock()

    # Buttons
    self._create_btn = self._child(Button("Create Game", click_callback=self._on_create))
    self._join_btn = self._child(Button("Join Game", click_callback=self._on_join))
    self._back_btn = self._child(Button("Back", click_callback=self._on_back, button_style=ButtonStyle.NORMAL))

    # Keyboard for entering lobby code
    self._keyboard = Keyboard(max_text_size=6, min_text_size=6, callback=self._on_keyboard_result)
    self._keyboard.set_title("Enter Lobby Code", "6-character code from the other player")

  def hide_event(self):
    if self._listener:
      self._listener.stop()
      self._listener = None
    super().hide_event()

  def _render(self, rect: rl.Rectangle):
    rl.draw_rectangle_rec(rect, rl.Color(20, 20, 25, 255))

    if self._state == "menu":
      self._render_menu(rect)
    elif self._state == "waiting":
      self._render_waiting(rect)
    elif self._state == "error":
      self._render_error(rect)

  def _render_menu(self, rect: rl.Rectangle):
    # Title
    rl.draw_text_ex(self._font_bold, "Tic-Tac-Toe Online",
                    rl.Vector2(rect.x + 20, rect.y + 30), 50, 0, rl.WHITE)
    rl.draw_text_ex(self._font, "Play 1v1 with another comma device",
                    rl.Vector2(rect.x + 20, rect.y + 90), 30, 0, rl.Color(150, 150, 150, 255))

    btn_w = min(400, rect.width - 40)
    btn_x = rect.x + (rect.width - btn_w) / 2
    self._create_btn.render(rl.Rectangle(btn_x, rect.y + 160, btn_w, 80))
    self._join_btn.render(rl.Rectangle(btn_x, rect.y + 260, btn_w, 80))

  def _render_waiting(self, rect: rl.Rectangle):
    rl.draw_text_ex(self._font_bold, "Waiting for opponent...",
                    rl.Vector2(rect.x + 20, rect.y + 40), 40, 0, rl.WHITE)

    # Show lobby code prominently
    rl.draw_text_ex(self._font, "Share this code:",
                    rl.Vector2(rect.x + 20, rect.y + 110), 30, 0, rl.Color(150, 150, 150, 255))
    rl.draw_text_ex(self._font_bold, self._lobby_code,
                    rl.Vector2(rect.x + 20, rect.y + 155), 70, 0, rl.Color(80, 200, 255, 255))

    self._back_btn.render(rl.Rectangle(rect.x + 20, rect.y + rect.height - 100, 150, 60))

    # Check for opponent join
    with self._lock:
      if self._game_data and self._game_data.get("player_o_id"):
        self._launch_game(self._game_data, "X")

  def _render_error(self, rect: rl.Rectangle):
    rl.draw_text_ex(self._font_bold, "Error",
                    rl.Vector2(rect.x + 20, rect.y + 40), 50, 0, rl.Color(255, 80, 80, 255))
    rl.draw_text_ex(self._font, self._error_msg,
                    rl.Vector2(rect.x + 20, rect.y + 110), 30, 0, rl.Color(200, 200, 200, 255))
    self._back_btn.render(rl.Rectangle(rect.x + 20, rect.y + 200, 150, 60))

  def _on_create(self):
    config = get_supabase_config()
    if not config:
      self._error_msg = "Supabase not configured. Set SupabaseUrl and SupabaseAnonKey params."
      self._state = "error"
      return

    self._state = "creating"
    self._lobby_code = _generate_lobby_code()

    def create():
      url, key = config
      rest = SupabaseREST(url, key)
      result = rest.insert("games", {
        "lobby_code": self._lobby_code,
        "player_x_id": self._dongle_id,
      })
      with self._lock:
        if result:
          self._game_data = result
          self._state = "waiting"
          # Start listening for opponent join
          self._listener = SupabaseRealtimeListener(url, key, result["id"], self._on_realtime_update)
          self._listener.start()
        else:
          self._error_msg = "Failed to create game. Check network connection."
          self._state = "error"

    threading.Thread(target=create, daemon=True).start()

  def _on_join(self):
    gui_app.push_widget(self._keyboard)

  def _on_keyboard_result(self, result: DialogResult):
    if result == DialogResult.CANCEL:
      return

    code = self._keyboard.text.strip().upper()
    self._keyboard.clear()

    if len(code) != 6:
      self._error_msg = "Lobby code must be 6 characters."
      self._state = "error"
      return

    config = get_supabase_config()
    if not config:
      self._error_msg = "Supabase not configured."
      self._state = "error"
      return

    def join():
      url, key = config
      rest = SupabaseREST(url, key)

      # Find the game
      games = rest.select("games", {"lobby_code": f"eq.{code}", "winner": "is.null"})
      if not games:
        with self._lock:
          self._error_msg = f"No open game found with code {code}"
          self._state = "error"
        return

      game = games[0]
      if game.get("player_o_id"):
        with self._lock:
          self._error_msg = "Game is already full."
          self._state = "error"
        return

      # Join the game
      updated = rest.update("games", {"id": game["id"]}, {"player_o_id": self._dongle_id})
      if updated:
        self._launch_game(updated, "O")
      else:
        with self._lock:
          self._error_msg = "Failed to join game."
          self._state = "error"

    threading.Thread(target=join, daemon=True).start()

  def _on_realtime_update(self, record: dict):
    with self._lock:
      self._game_data = record

  def _on_back(self):
    if self._listener:
      self._listener.stop()
      self._listener = None
    self._state = "menu"
    self._game_data = None

  def _launch_game(self, game_data: dict, my_mark: str):
    if self._listener:
      self._listener.stop()
      self._listener = None
    game_widget = TicTacToeWidget(
      game_id=game_data["id"],
      my_mark=my_mark,
      game_data=game_data,
    )
    gui_app.push_widget(game_widget)
