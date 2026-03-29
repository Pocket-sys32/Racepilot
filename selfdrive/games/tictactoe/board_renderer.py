import pyray as rl

from openpilot.selfdrive.games.tictactoe.game_logic import Board
from openpilot.system.ui.lib.application import gui_app, FontWeight, MousePos

# Colors
GRID_COLOR = rl.Color(200, 200, 200, 255)
X_COLOR = rl.Color(255, 80, 80, 255)
O_COLOR = rl.Color(80, 150, 255, 255)
WIN_LINE_COLOR = rl.Color(0, 255, 100, 200)
TEXT_COLOR = rl.WHITE
DIM_TEXT = rl.Color(150, 150, 150, 255)
BG_COLOR = rl.Color(30, 30, 30, 255)

LINE_THICKNESS = 4.0
MARK_THICKNESS = 5.0
MARK_PADDING = 0.2  # fraction of cell size


class BoardRenderer:
  """Renders a 3x3 Tic-Tac-Toe board using pyray primitives."""

  def __init__(self):
    self._font = gui_app.font(FontWeight.MEDIUM)
    self._cell_rects: list[rl.Rectangle] = [rl.Rectangle(0, 0, 0, 0)] * 9
    self._board_rect = rl.Rectangle(0, 0, 0, 0)

  def draw(self, rect: rl.Rectangle, board: Board, current_turn: str,
           my_mark: str, winner: str | None) -> None:
    rl.draw_rectangle_rec(rect, BG_COLOR)

    # Calculate board dimensions (square, centered)
    header_h = 60
    avail_w = rect.width - 20
    avail_h = rect.height - header_h - 20
    board_size = min(avail_w, avail_h)
    board_x = rect.x + (rect.width - board_size) / 2
    board_y = rect.y + header_h + (avail_h - board_size) / 2
    self._board_rect = rl.Rectangle(board_x, board_y, board_size, board_size)

    cell_size = board_size / 3

    # Store cell rects for hit testing
    for i in range(9):
      row, col = divmod(i, 3)
      self._cell_rects[i] = rl.Rectangle(
        board_x + col * cell_size,
        board_y + row * cell_size,
        cell_size, cell_size,
      )

    # Draw header text
    self._draw_header(rect, header_h, current_turn, my_mark, winner)

    # Draw grid lines
    for i in range(1, 3):
      # Vertical
      x = board_x + i * cell_size
      rl.draw_line_ex(rl.Vector2(x, board_y), rl.Vector2(x, board_y + board_size), LINE_THICKNESS, GRID_COLOR)
      # Horizontal
      y = board_y + i * cell_size
      rl.draw_line_ex(rl.Vector2(board_x, y), rl.Vector2(board_x + board_size, y), LINE_THICKNESS, GRID_COLOR)

    # Draw marks
    for i in range(9):
      mark = board.cells[i]
      if mark == 'X':
        self._draw_x(self._cell_rects[i])
      elif mark == 'O':
        self._draw_o(self._cell_rects[i])

    # Draw winning line
    win_line = board.winning_line()
    if win_line:
      self._draw_win_line(win_line)

  def _draw_header(self, rect: rl.Rectangle, header_h: float,
                   current_turn: str, my_mark: str, winner: str | None) -> None:
    y = rect.y + 15
    if winner:
      forfeit = winner.endswith('F')
      winning_mark = winner[0] if forfeit else winner
      if winner == 'D':
        text = "Draw!"
        color = DIM_TEXT
      elif winning_mark == my_mark:
        text = "Opponent Forfeited — You Win!" if forfeit else "You Win!"
        color = rl.Color(0, 255, 100, 255)
      else:
        text = "You Forfeited" if forfeit else "You Lose!"
        color = rl.Color(255, 80, 80, 255)
    elif current_turn == my_mark:
      text = "Your turn"
      color = TEXT_COLOR
    else:
      text = "Waiting for opponent..."
      color = DIM_TEXT

    rl.draw_text_ex(self._font, text, rl.Vector2(rect.x + 20, y), 40, 0, color)

  def _draw_x(self, cell: rl.Rectangle) -> None:
    pad = cell.width * MARK_PADDING
    x1, y1 = cell.x + pad, cell.y + pad
    x2, y2 = cell.x + cell.width - pad, cell.y + cell.height - pad
    rl.draw_line_ex(rl.Vector2(x1, y1), rl.Vector2(x2, y2), MARK_THICKNESS, X_COLOR)
    rl.draw_line_ex(rl.Vector2(x2, y1), rl.Vector2(x1, y2), MARK_THICKNESS, X_COLOR)

  def _draw_o(self, cell: rl.Rectangle) -> None:
    cx = cell.x + cell.width / 2
    cy = cell.y + cell.height / 2
    radius = (cell.width / 2) * (1.0 - MARK_PADDING * 2)
    inner = radius - MARK_THICKNESS
    rl.draw_ring(rl.Vector2(cx, cy), inner, radius, 0, 360, 36, O_COLOR)

  def _draw_win_line(self, line: tuple[int, int, int]) -> None:
    a, c = line[0], line[2]
    ra, rc = self._cell_rects[a], self._cell_rects[c]
    start = rl.Vector2(ra.x + ra.width / 2, ra.y + ra.height / 2)
    end = rl.Vector2(rc.x + rc.width / 2, rc.y + rc.height / 2)
    rl.draw_line_ex(start, end, MARK_THICKNESS + 3, WIN_LINE_COLOR)

  def hit_test(self, pos: MousePos) -> int | None:
    """Returns the cell index (0-8) at the given position, or None."""
    for i, cell in enumerate(self._cell_rects):
      if rl.check_collision_point_rec(pos, cell):
        return i
    return None
