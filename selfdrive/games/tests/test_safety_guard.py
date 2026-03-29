import pytest
from unittest.mock import MagicMock, patch, PropertyMock


class TestGameSafetyGuardLogic:
  """Tests for GameSafetyGuard logic without requiring a Raylib window."""

  def test_thermal_limit_is_yellow(self):
    """Games should close at yellow thermal status (75C+), not red."""
    from cereal import log
    from openpilot.selfdrive.games.safety_guard import THERMAL_LIMIT
    ThermalStatus = log.DeviceState.ThermalStatus
    assert THERMAL_LIMIT == ThermalStatus.yellow

  def test_yellow_is_before_red(self):
    """Confirm yellow < red, so our threshold provides a safety margin."""
    from cereal import log
    ThermalStatus = log.DeviceState.ThermalStatus
    assert ThermalStatus.yellow < ThermalStatus.red

  def test_game_active_param_key_exists(self):
    """GameActive param key must be registered in the params system."""
    from openpilot.common.params import Params
    p = Params()
    # Should not raise -- the key must be known
    p.check_key("GameActive")

  def test_game_high_score_param_key_exists(self):
    from openpilot.common.params import Params
    p = Params()
    p.check_key("GameHighScoreDoom")

  def test_tictactoe_wins_param_key_exists(self):
    from openpilot.common.params import Params
    p = Params()
    p.check_key("TicTacToeWins")

  def test_supabase_url_param_key_exists(self):
    from openpilot.common.params import Params
    p = Params()
    p.check_key("SupabaseUrl")

  def test_supabase_anon_key_param_key_exists(self):
    from openpilot.common.params import Params
    p = Params()
    p.check_key("SupabaseAnonKey")


class TestSupabaseConfig:
  def test_get_supabase_config_returns_none_when_not_set(self):
    from openpilot.selfdrive.games.supabase_client import get_supabase_config
    from openpilot.common.params import Params
    # Ensure params are cleared
    p = Params()
    p.remove("SupabaseUrl")
    p.remove("SupabaseAnonKey")
    result = get_supabase_config()
    assert result is None

  def test_get_supabase_config_returns_tuple_when_set(self):
    from openpilot.selfdrive.games.supabase_client import get_supabase_config
    from openpilot.common.params import Params
    p = Params()
    p.put("SupabaseUrl", "https://test.supabase.co")
    p.put("SupabaseAnonKey", "test_key_123")
    result = get_supabase_config()
    assert result is not None
    url, key = result
    assert url == "https://test.supabase.co"
    assert key == "test_key_123"
    # Cleanup
    p.remove("SupabaseUrl")
    p.remove("SupabaseAnonKey")

  def test_supabase_url_trailing_slash_stripped(self):
    from openpilot.selfdrive.games.supabase_client import get_supabase_config
    from openpilot.common.params import Params
    p = Params()
    p.put("SupabaseUrl", "https://test.supabase.co/")
    p.put("SupabaseAnonKey", "key")
    result = get_supabase_config()
    assert result is not None
    url, _ = result
    assert not url.endswith("/")
    # Cleanup
    p.remove("SupabaseUrl")
    p.remove("SupabaseAnonKey")
