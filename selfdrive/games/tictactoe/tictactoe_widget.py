import threading

import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.games.safety_guard import GameSafetyGuard
from openpilot.selfdrive.games.supabase_client import SupabaseREST, SupabaseRealtimeListener, get_supabase_config
from openpilot.selfdrive.games.tictactoe.board_renderer import BoardRenderer
from openpilot.selfdrive.games.tictactoe.game_logic import Board
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets.button import Button, ButtonStyle


class TicTacToeWidget(GameSafetyGuard):
  """Full-screen Tic-Tac-Toe game widget with Supabase Realtime sync."""

  def __init__(self, game_id: str, my_mark: str, game_data: dict):
    super().__init__()
    self._game_id = game_id
    self._my_mark = my_mark
    self._board = Board.from_string(game_data.get("board", "---------"))
    self._current_turn = game_data.get("current_turn", "X")
    self._winner = game_data.get("winner")
    self._board_renderer = BoardRenderer()
    self._listener: SupabaseRealtimeListener | None = None
    self._rest: SupabaseREST | None = None
    self._lock = threading.Lock()
    self._pending_update: dict | None = None

    self._rematch_btn = self._child(Button("New Game", click_callback=self._on_rematch, button_style=ButtonStyle.PRIMARY))
    self._exit_btn = self._child(Button("Exit", click_callback=self._on_exit))

    # Set up Supabase client
    config = get_supabase_config()
    if config:
      url, key = config
      self._rest = SupabaseREST(url, key)
      self._listener = SupabaseRealtimeListener(url, key, game_id, self._on_remote_update)

  def show_event(self):
    super().show_event()
    if self._listener:
      self._listener.start()

  def hide_event(self):
    if self._listener:
      self._listener.stop()
      self._listener = None
    super().hide_event()

  def _on_remote_update(self, record: dict):
    """Called from websocket thread when the game row changes."""
    with self._lock:
      self._pending_update = record

  def _update_state(self):
    super()._update_state()
    with self._lock:
      if self._pending_update:
        self._board = Board.from_string(self._pending_update.get("board", "---------"))
        self._current_turn = self._pending_update.get("current_turn", self._current_turn)
        self._winner = self._pending_update.get("winner")
        self._pending_update = None

        # Track wins
        if self._winner == self._my_mark:
          params = Params()
          wins = int(params.get("TicTacToeWins", encoding="utf-8") or "0")
          params.put("TicTacToeWins", str(wins + 1))

  def _render(self, rect: rl.Rectangle):
    self._board_renderer.draw(rect, self._board, self._current_turn, self._my_mark, self._winner)

    # Show game-over buttons
    if self._winner:
      btn_w = min(200, rect.width / 3)
      btn_y = rect.y + rect.height - 80
      mid = rect.x + rect.width / 2
      self._exit_btn.render(rl.Rectangle(mid - btn_w - 10, btn_y, btn_w, 55))
      self._rematch_btn.render(rl.Rectangle(mid + 10, btn_y, btn_w, 55))

  def _handle_mouse_release(self, mouse_pos: MousePos):
    super()._handle_mouse_release(mouse_pos)

    # Only allow moves on our turn, when game isn't over
    if self._winner or self._current_turn != self._my_mark:
      return

    cell = self._board_renderer.hit_test(mouse_pos)
    if cell is not None and self._board.is_empty(cell):
      # Optimistic local update
      self._board.place(cell, self._my_mark)
      new_winner = self._board.check_winner()
      self._current_turn = self._board.next_turn(self._my_mark)
      if new_winner:
        self._winner = new_winner

      # Send move to Supabase in background
      board_str = self._board.to_string()
      threading.Thread(
        target=self._send_move,
        args=(board_str, self._current_turn, new_winner),
        daemon=True,
      ).start()

  def _send_move(self, board_str: str, next_turn: str, winner: str | None):
    if not self._rest:
      return
    data = {
      "board": board_str,
      "current_turn": next_turn,
    }
    if winner:
      data["winner"] = winner
    self._rest.update("games", {"id": self._game_id}, data)

  def _on_rematch(self):
    self.dismiss()

  def _on_exit(self):
    # Pop both the game widget and the lobby widget
    self.dismiss()
