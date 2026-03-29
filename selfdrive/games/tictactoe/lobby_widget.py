import random
import string
import threading
import time

import pyray as rl

from openpilot.common.params import Params
from openpilot.selfdrive.games.supabase_client import SupabaseREST, SupabaseRealtimeListener, get_supabase_config
from openpilot.selfdrive.games.tictactoe.tictactoe_widget import TicTacToeWidget
from openpilot.selfdrive.games.safety_guard import GameSafetyGuard
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos
from openpilot.system.ui.widgets.button import Button, ButtonStyle


def _generate_lobby_code() -> str:
  return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


class LobbyWidget(GameSafetyGuard):
  """Lobby screen for creating or joining a Tic-Tac-Toe game."""

  def __init__(self):
    super().__init__()
    self._params = Params()
    self._dongle_id = self._params.get("DongleId") or "unknown"
    self._font = gui_app.font(FontWeight.MEDIUM)
    self._font_bold = gui_app.font(FontWeight.BOLD)

    # State
    self._state = "menu"  # menu, creating, waiting, browsing, error
    self._lobby_code = ""
    self._error_msg = ""
    self._game_data: dict | None = None
    self._listener: SupabaseRealtimeListener | None = None
    self._lock = threading.Lock()

    # Browsing state
    self._open_games: list[dict] = []
    self._browsing_loading = False
    self._last_refresh = 0.0
    self._hovered_row: int = -1

    # Buttons
    self._create_btn = self._child(Button("Create Game", click_callback=self._on_create, font_size=36))
    self._join_btn = self._child(Button("Join Game", click_callback=self._on_join, font_size=36))
    self._back_btn = self._child(Button("Back", click_callback=self._on_back, button_style=ButtonStyle.NORMAL, font_size=30))
    self._refresh_btn = self._child(Button("Refresh", click_callback=self._fetch_open_games, button_style=ButtonStyle.NORMAL, font_size=28))

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
    elif self._state == "browsing":
      self._render_browsing(rect)
    elif self._state == "error":
      self._render_error(rect)

  def _render_menu(self, rect: rl.Rectangle):
    h = rect.height
    pad = rect.x + rect.width * 0.07
    title_sz = max(20, int(h * 0.14))
    sub_sz = max(14, int(h * 0.08))
    btn_h = max(36, int(h * 0.18))
    gap = max(8, int(h * 0.04))

    title_y = rect.y + h * 0.08
    sub_y = title_y + title_sz + gap
    btn1_y = sub_y + sub_sz + h * 0.08
    btn2_y = btn1_y + btn_h + gap

    rl.draw_text_ex(self._font_bold, "Tic-Tac-Toe Online",
                    rl.Vector2(pad, title_y), title_sz, 0, rl.WHITE)
    rl.draw_text_ex(self._font, "Play 1v1 with another comma device",
                    rl.Vector2(pad, sub_y), sub_sz, 0, rl.Color(150, 150, 150, 255))

    btn_w = min(360, rect.width - rect.width * 0.14)
    btn_x = rect.x + (rect.width - btn_w) / 2
    self._create_btn.render(rl.Rectangle(btn_x, btn1_y, btn_w, btn_h))
    self._join_btn.render(rl.Rectangle(btn_x, btn2_y, btn_w, btn_h))

  def _render_waiting(self, rect: rl.Rectangle):
    h = rect.height
    pad = rect.x + rect.width * 0.07
    title_sz = max(18, int(h * 0.13))
    sub_sz = max(12, int(h * 0.07))
    code_sz = max(28, int(h * 0.22))
    btn_h = max(36, int(h * 0.16))
    gap = max(6, int(h * 0.04))

    title_y = rect.y + h * 0.08
    sub_y = title_y + title_sz + gap
    code_y = sub_y + sub_sz + gap
    btn_y = code_y + code_sz + gap

    rl.draw_text_ex(self._font_bold, "Waiting for opponent...",
                    rl.Vector2(pad, title_y), title_sz, 0, rl.WHITE)
    rl.draw_text_ex(self._font, "Lobby open — others can see and join",
                    rl.Vector2(pad, sub_y), sub_sz, 0, rl.Color(150, 150, 150, 255))
    rl.draw_text_ex(self._font_bold, self._lobby_code,
                    rl.Vector2(pad, code_y), code_sz, 0, rl.Color(80, 200, 255, 255))
    self._back_btn.render(rl.Rectangle(pad, btn_y, max(100, int(rect.width * 0.25)), btn_h))

    with self._lock:
      if self._game_data and self._game_data.get("player_o_id"):
        self._launch_game(self._game_data, "X")

  def _render_browsing(self, rect: rl.Rectangle):
    h = rect.height
    pad = rect.x + rect.width * 0.07
    title_sz = max(18, int(h * 0.11))
    sub_sz = max(12, int(h * 0.07))
    row_h = max(44, int(h * 0.18))
    btn_h = max(32, int(h * 0.13))
    gap = max(6, int(h * 0.03))
    avail_w = rect.width - rect.width * 0.14

    title_y = rect.y + h * 0.06
    sub_y = title_y + title_sz + gap
    list_y = sub_y + sub_sz + gap * 2

    rl.draw_text_ex(self._font_bold, "Open Games",
                    rl.Vector2(pad, title_y), title_sz, 0, rl.WHITE)

    # Auto-refresh every 5s
    now = time.monotonic()
    if now - self._last_refresh > 5.0 and not self._browsing_loading:
      self._fetch_open_games()

    with self._lock:
      games = list(self._open_games)
      loading = self._browsing_loading

    if loading and not games:
      rl.draw_text_ex(self._font, "Loading...",
                      rl.Vector2(pad, sub_y), sub_sz, 0, rl.Color(150, 150, 150, 255))
    elif not games:
      rl.draw_text_ex(self._font, "No open games — be the first to create one!",
                      rl.Vector2(pad, sub_y), sub_sz, 0, rl.Color(150, 150, 150, 255))
    else:
      count_str = f"{len(games)} game{'s' if len(games) != 1 else ''} waiting"
      rl.draw_text_ex(self._font, count_str,
                      rl.Vector2(pad, sub_y), sub_sz, 0, rl.Color(150, 150, 150, 255))

      mouse = rl.get_mouse_position()
      for i, game in enumerate(games):
        row_y = list_y + i * (row_h + gap)
        if row_y + row_h > rect.y + rect.height - btn_h - gap * 3:
          break  # don't overflow into buttons

        row_rect = rl.Rectangle(pad, row_y, avail_w, row_h)
        hovered = (row_rect.x <= mouse.x <= row_rect.x + row_rect.width and
                   row_rect.y <= mouse.y <= row_rect.y + row_rect.height)

        bg = rl.Color(50, 80, 120, 220) if hovered else rl.Color(35, 45, 65, 200)
        rl.draw_rectangle_rounded(row_rect, 0.2, 6, bg)
        rl.draw_rectangle_rounded_lines(row_rect, 0.2, 6, rl.Color(80, 120, 180, 180))

        host = game.get("player_x_id", "unknown")
        short_host = host[:12] + "..." if len(host) > 12 else host
        label = f"Host: {short_host}"
        label_sz = max(14, int(row_h * 0.36))
        label_y = row_y + (row_h - label_sz) / 2
        rl.draw_text_ex(self._font_bold, label,
                        rl.Vector2(pad + 12, label_y), label_sz, 0, rl.WHITE)

        # Tap to join
        if hovered and rl.is_mouse_button_released(rl.MouseButton.MOUSE_BUTTON_LEFT):
          self._join_game(game)

    # Bottom buttons
    bottom_y = rect.y + rect.height - btn_h - gap
    self._back_btn.render(rl.Rectangle(pad, bottom_y, max(90, int(avail_w * 0.35)), btn_h))
    self._refresh_btn.render(rl.Rectangle(pad + max(90, int(avail_w * 0.35)) + gap, bottom_y,
                                          max(90, int(avail_w * 0.35)), btn_h))

  def _render_error(self, rect: rl.Rectangle):
    h = rect.height
    pad = rect.x + rect.width * 0.07
    title_sz = max(18, int(h * 0.13))
    sub_sz = max(12, int(h * 0.07))
    btn_h = max(36, int(h * 0.16))
    gap = max(6, int(h * 0.04))

    title_y = rect.y + h * 0.08
    msg_y = title_y + title_sz + gap
    btn_y = msg_y + sub_sz + gap

    rl.draw_text_ex(self._font_bold, "Error",
                    rl.Vector2(pad, title_y), title_sz, 0, rl.Color(255, 80, 80, 255))
    rl.draw_text_ex(self._font, self._error_msg,
                    rl.Vector2(pad, msg_y), sub_sz, 0, rl.Color(200, 200, 200, 255))
    self._back_btn.render(rl.Rectangle(pad, btn_y, max(100, int(rect.width * 0.25)), btn_h))

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
          self._listener = SupabaseRealtimeListener(url, key, result["id"], self._on_realtime_update)
          self._listener.start()
        else:
          self._error_msg = "Failed to create game. Check network connection."
          self._state = "error"

    threading.Thread(target=create, daemon=True).start()

  def _on_join(self):
    self._state = "browsing"
    self._open_games = []
    self._fetch_open_games()

  def _fetch_open_games(self):
    config = get_supabase_config()
    if not config:
      return
    with self._lock:
      self._browsing_loading = True

    def fetch():
      url, key = config
      rest = SupabaseREST(url, key)
      games = rest.select("games", {
        "player_o_id": "is.null",
        "winner": "is.null",
      }) or []
      # Exclude own games
      games = [g for g in games if g.get("player_x_id") != self._dongle_id]
      with self._lock:
        self._open_games = games
        self._browsing_loading = False
        self._last_refresh = time.monotonic()

    threading.Thread(target=fetch, daemon=True).start()

  def _join_game(self, game: dict):
    config = get_supabase_config()
    if not config:
      return

    def join():
      url, key = config
      rest = SupabaseREST(url, key)
      updated = rest.update("games", {"id": game["id"]}, {"player_o_id": self._dongle_id})
      if updated:
        self._launch_game(updated, "O")
      else:
        with self._lock:
          self._error_msg = "Failed to join game — it may have been taken."
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
    self._open_games = []

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
