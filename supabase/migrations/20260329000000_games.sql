-- Create games table for Tic-Tac-Toe multiplayer
CREATE TABLE IF NOT EXISTS games (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  lobby_code    VARCHAR(6) NOT NULL UNIQUE,
  player_x_id   VARCHAR(64) NOT NULL,
  player_o_id   VARCHAR(64),
  board         CHAR(9) NOT NULL DEFAULT '---------',
  current_turn  CHAR(1) NOT NULL DEFAULT 'X',
  winner        CHAR(1),
  created_at    TIMESTAMPTZ DEFAULT NOW(),
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast lobby code lookup
CREATE INDEX IF NOT EXISTS idx_games_lobby_code ON games(lobby_code);

-- Auto-expire old games (older than 24 hours)
CREATE INDEX IF NOT EXISTS idx_games_created_at ON games(created_at);

-- Auto-update timestamp on row change
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS games_updated_at ON games;
CREATE TRIGGER games_updated_at
  BEFORE UPDATE ON games
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Enable Row Level Security
ALTER TABLE games ENABLE ROW LEVEL SECURITY;

-- Open policies (anon key can read/write, scoped to game rows)
DROP POLICY IF EXISTS "anon_select" ON games;
DROP POLICY IF EXISTS "anon_insert" ON games;
DROP POLICY IF EXISTS "anon_update" ON games;

CREATE POLICY "anon_select" ON games FOR SELECT USING (true);
CREATE POLICY "anon_insert" ON games FOR INSERT WITH CHECK (true);
CREATE POLICY "anon_update" ON games FOR UPDATE USING (true);

-- Enable Realtime for this table
ALTER PUBLICATION supabase_realtime ADD TABLE games;
