import json
import time
import threading
from collections.abc import Callable

import requests
import websocket

from openpilot.common.params import Params


def get_supabase_config() -> tuple[str, str] | None:
  """Read Supabase URL and anon key from params. Returns (url, key) or None."""
  params = Params()
  url = params.get("SupabaseUrl")
  key = params.get("SupabaseAnonKey")
  if not url or not key:
    return None
  return url.rstrip("/"), key


class SupabaseREST:
  """Minimal Supabase PostgREST client."""

  def __init__(self, url: str, anon_key: str):
    self._base = f"{url}/rest/v1"
    self._headers = {
      "apikey": anon_key,
      "Authorization": f"Bearer {anon_key}",
      "Content-Type": "application/json",
      "Prefer": "return=representation",
    }

  def insert(self, table: str, data: dict) -> dict | None:
    resp = requests.post(f"{self._base}/{table}", headers=self._headers, json=data, timeout=10)
    if resp.status_code in (200, 201):
      rows = resp.json()
      return rows[0] if rows else None
    return None

  def select(self, table: str, params: dict) -> list[dict]:
    resp = requests.get(f"{self._base}/{table}", headers=self._headers, params=params, timeout=10)
    if resp.status_code == 200:
      return resp.json()
    return []

  def update(self, table: str, match: dict, data: dict) -> dict | None:
    query_params = {f"{k}": f"eq.{v}" for k, v in match.items()}
    resp = requests.patch(
      f"{self._base}/{table}",
      headers=self._headers,
      params=query_params,
      json=data,
      timeout=10,
    )
    if resp.status_code == 200:
      rows = resp.json()
      return rows[0] if rows else None
    return None


class SupabaseRealtimeListener:
  """Listen to row-level changes on a Supabase table via Realtime websockets."""

  def __init__(self, supabase_url: str, anon_key: str, game_id: str, on_change: Callable[[dict], None]):
    ws_host = supabase_url.replace("https://", "wss://").replace("http://", "ws://")
    self._ws_url = f"{ws_host}/realtime/v1/websocket?apikey={anon_key}&vsn=1.0.0"
    self._game_id = game_id
    self._on_change = on_change
    self._ws: websocket.WebSocketApp | None = None
    self._stop = threading.Event()
    self._thread: threading.Thread | None = None
    self._heartbeat_ref = 0

  def start(self):
    self._stop.clear()
    self._thread = threading.Thread(target=self._run, daemon=True)
    self._thread.start()

  def stop(self):
    self._stop.set()
    if self._ws:
      try:
        self._ws.close()
      except Exception:
        pass
    if self._thread:
      self._thread.join(timeout=3.0)

  def _run(self):
    while not self._stop.is_set():
      try:
        self._ws = websocket.WebSocketApp(
          self._ws_url,
          on_open=self._on_open,
          on_message=self._on_message,
          on_error=lambda ws, e: None,
          on_close=lambda ws, cc, cm: None,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)
      except Exception:
        pass
      if not self._stop.is_set():
        time.sleep(2)

  def _on_open(self, ws):
    join_msg = {
      "topic": f"realtime:public:games:id=eq.{self._game_id}",
      "event": "phx_join",
      "payload": {
        "config": {
          "postgres_changes": [
            {
              "event": "UPDATE",
              "schema": "public",
              "table": "games",
              "filter": f"id=eq.{self._game_id}",
            }
          ]
        }
      },
      "ref": "1",
    }
    ws.send(json.dumps(join_msg))
    self._start_heartbeat(ws)

  def _start_heartbeat(self, ws):
    def heartbeat():
      while not self._stop.is_set():
        time.sleep(25)
        try:
          self._heartbeat_ref += 1
          ws.send(json.dumps({
            "topic": "phoenix",
            "event": "heartbeat",
            "payload": {},
            "ref": str(self._heartbeat_ref),
          }))
        except Exception:
          break

    threading.Thread(target=heartbeat, daemon=True).start()

  def _on_message(self, ws, message):
    try:
      data = json.loads(message)
    except json.JSONDecodeError:
      return
    if data.get("event") == "postgres_changes":
      payload = data.get("payload", {})
      if payload.get("type") == "UPDATE":
        record = payload.get("record", {})
        if record:
          self._on_change(record)
