import pytest
from openpilot.selfdrive.games.tictactoe.game_logic import Board, WIN_LINES


class TestBoard:
  def test_initial_state(self):
    b = Board()
    assert b.cells == ['-'] * 9
    assert b.to_string() == '---------'

  def test_from_string(self):
    b = Board.from_string('XO-XO-XO-')
    assert b.cells[0] == 'X'
    assert b.cells[1] == 'O'
    assert b.cells[2] == '-'

  def test_from_string_short(self):
    b = Board.from_string('XO')
    assert b.cells[0] == 'X'
    assert b.cells[1] == 'O'
    assert all(c == '-' for c in b.cells[2:])

  def test_is_empty(self):
    b = Board()
    assert b.is_empty(0)
    b.place(0, 'X')
    assert not b.is_empty(0)

  def test_is_empty_out_of_bounds(self):
    b = Board()
    assert not b.is_empty(-1)
    assert not b.is_empty(9)

  def test_place_success(self):
    b = Board()
    assert b.place(4, 'X')
    assert b.cells[4] == 'X'

  def test_place_occupied(self):
    b = Board()
    b.place(4, 'X')
    assert not b.place(4, 'O')
    assert b.cells[4] == 'X'

  # --- Win condition tests: all 8 lines ---

  def test_win_row_0(self):
    b = Board.from_string('XXX------')
    assert b.check_winner() == 'X'

  def test_win_row_1(self):
    b = Board.from_string('---OOO---')
    assert b.check_winner() == 'O'

  def test_win_row_2(self):
    b = Board.from_string('------XXX')
    assert b.check_winner() == 'X'

  def test_win_col_0(self):
    b = Board.from_string('X--X--X--')
    assert b.check_winner() == 'X'

  def test_win_col_1(self):
    b = Board.from_string('-O--O--O-')
    assert b.check_winner() == 'O'

  def test_win_col_2(self):
    b = Board.from_string('--X--X--X')
    assert b.check_winner() == 'X'

  def test_win_diag_main(self):
    b = Board.from_string('X---X---X')
    assert b.check_winner() == 'X'

  def test_win_diag_anti(self):
    b = Board.from_string('--O-O-O--')
    assert b.check_winner() == 'O'

  def test_draw(self):
    b = Board.from_string('XOXOOXXXO')
    assert b.check_winner() == 'D'

  def test_in_progress(self):
    b = Board.from_string('XO-XO----')
    assert b.check_winner() is None

  def test_winning_line_returns_correct_indices(self):
    b = Board.from_string('XXX------')
    assert b.winning_line() == (0, 1, 2)

  def test_winning_line_none_when_in_progress(self):
    b = Board()
    assert b.winning_line() is None

  def test_next_turn(self):
    b = Board()
    assert b.next_turn('X') == 'O'
    assert b.next_turn('O') == 'X'

  def test_serialization_round_trip(self):
    b = Board()
    b.place(0, 'X')
    b.place(4, 'O')
    s = b.to_string()
    b2 = Board.from_string(s)
    assert b2.cells == b.cells

  def test_all_win_lines_covered(self):
    # Verify all 8 win lines are present
    assert len(WIN_LINES) == 8
