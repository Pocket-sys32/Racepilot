import threading
import time

import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.games.safety_guard import GameSafetyGuard
from openpilot.selfdrive.games.supabase_client import SupabaseREST, SupabaseRealtimeListener, get_supabase_config
from openpilot.selfdrive.games.tictactoe.board_renderer import BoardRenderer
from openpilot.selfdrive.games.tictactoe.game_logic import Board
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos

_GAME_OVER_CLOSE_DELAY = 6.0  # seconds before auto-closing after win/draw/loss


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

    self._game_over_at: float | None = None
    self._safe_close = False  # True when closed by safety guard (no forfeit)
    self._closing = False     # Guard against double _close_game() call
    self._last_poll = 0.0

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

  def _safety_dismiss(self, reason: str):
    self._safe_close = True
    super()._safety_dismiss(reason)

  def hide_event(self):
    # Forfeit if the game is still in progress and this was a manual close
    if self._winner is None and not self._safe_close and self._rest:
      opponent = "O" if self._my_mark == "X" else "X"
      forfeit_winner = opponent + "F"  # e.g. "XF" = X wins because O forfeited
      rest, game_id = self._rest, self._game_id
      threading.Thread(
        target=lambda: rest.update("games", {"id": game_id}, {"winner": forfeit_winner}),
        daemon=True,
      ).start()
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
        board_str = self._pending_update.get("board")
        if board_str:
          new_board = Board.from_string(board_str)
          # Only accept if not a stale/lagging event: mark count must be >= current
          new_marks = sum(c != '-' for c in new_board.cells)
          cur_marks = sum(c != '-' for c in self._board.cells)
          if new_marks >= cur_marks:
            self._board = new_board
        self._current_turn = self._pending_update.get("current_turn", self._current_turn)
        new_winner = self._pending_update.get("winner")
        if new_winner and not self._winner:
          self._winner = new_winner
          self._game_over_at = time.monotonic()
          if self._winner.startswith(self._my_mark):
            params = Params()
            wins = int(params.get("TicTacToeWins", encoding="utf-8") or "0")
            params.put("TicTacToeWins", str(wins + 1))
        self._pending_update = None

    # REST poll fallback every 1.5s — ensures opponent moves are seen even if Realtime drops
    now = time.monotonic()
    if self._rest and not self._winner and now - self._last_poll >= 1.5:
      self._last_poll = now
      rest, game_id = self._rest, self._game_id
      threading.Thread(target=self._poll_state, args=(rest, game_id), daemon=True).start()

    # Auto-close after delay when game is over
    if self._game_over_at is None and self._winner:
      self._game_over_at = time.monotonic()
    if self._game_over_at is not None and time.monotonic() - self._game_over_at >= _GAME_OVER_CLOSE_DELAY:
      self._close_game()

  def _render(self, rect: rl.Rectangle):
    self._board_renderer.draw(rect, self._board, self._current_turn, self._my_mark, self._winner)

    # Show countdown overlay when game is over
    if self._winner and self._game_over_at is not None:
      elapsed = time.monotonic() - self._game_over_at
      remaining = max(0, _GAME_OVER_CLOSE_DELAY - elapsed)
      font = gui_app.font(FontWeight.MEDIUM)
      msg = f"Closing in {remaining:.0f}s..."
      rl.draw_text_ex(font, msg, rl.Vector2(rect.x + rect.width / 2 - 80, rect.y + rect.height - 50), 28, 0,
                      rl.Color(180, 180, 180, 200))

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
      if new_winner and not self._winner:
        self._winner = new_winner
        self._game_over_at = time.monotonic()
        if self._winner.startswith(self._my_mark):
          params = Params()
          wins = int(params.get("TicTacToeWins", encoding="utf-8") or "0")
          params.put("TicTacToeWins", str(wins + 1))

      # Send move to Supabase in background
      board_str = self._board.to_string()
      threading.Thread(
        target=self._send_move,
        args=(board_str, self._current_turn, new_winner),
        daemon=True,
      ).start()

  def _poll_state(self, rest: SupabaseREST, game_id: str) -> None:
    rows = rest.select("games", {"id": f"eq.{game_id}"})
    if rows:
      self._on_remote_update(rows[0])
    elif not self._winner:
      # Row was deleted (winner already closed their side) — treat as loss for us
      opponent = "O" if self._my_mark == "X" else "X"
      self._on_remote_update({"winner": opponent})

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

  def _close_game(self):
    """Delete the game row and dismiss. Guard prevents double-call."""
    if self._closing:
      return
    self._closing = True
    if self._listener:
      self._listener.stop()
      self._listener = None
    if self._rest:
      threading.Thread(target=self._rest.delete, args=("games", {"id": self._game_id}), daemon=True).start()
    self.dismiss()
