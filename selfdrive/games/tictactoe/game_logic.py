WIN_LINES = [
  (0, 1, 2), (3, 4, 5), (6, 7, 8),  # rows
  (0, 3, 6), (1, 4, 7), (2, 5, 8),  # cols
  (0, 4, 8), (2, 4, 6),              # diagonals
]


class Board:
  """3x3 Tic-Tac-Toe board."""

  def __init__(self):
    self.cells: list[str] = ['-'] * 9

  @classmethod
  def from_string(cls, s: str) -> 'Board':
    b = cls()
    b.cells = list(s[:9].ljust(9, '-'))
    return b

  def to_string(self) -> str:
    return ''.join(self.cells)

  def is_empty(self, idx: int) -> bool:
    return 0 <= idx < 9 and self.cells[idx] == '-'

  def place(self, idx: int, mark: str) -> bool:
    """Place a mark (X or O) at index. Returns True if successful."""
    if not self.is_empty(idx):
      return False
    self.cells[idx] = mark
    return True

  def check_winner(self) -> str | None:
    """Returns 'X', 'O', 'D' (draw), or None (in progress)."""
    for a, b, c in WIN_LINES:
      if self.cells[a] != '-' and self.cells[a] == self.cells[b] == self.cells[c]:
        return self.cells[a]
    if '-' not in self.cells:
      return 'D'
    return None

  def winning_line(self) -> tuple[int, int, int] | None:
    """Returns the indices of the winning line, if any."""
    for line in WIN_LINES:
      a, b, c = line
      if self.cells[a] != '-' and self.cells[a] == self.cells[b] == self.cells[c]:
        return line
    return None

  def next_turn(self, current: str) -> str:
    return 'O' if current == 'X' else 'X'
