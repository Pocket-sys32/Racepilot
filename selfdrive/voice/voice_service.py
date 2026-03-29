"""
VoiceService — singleton push-to-talk voice chat daemon.

Architecture:
  - Supabase Realtime broadcast channel "voice_v1" for WebRTC signaling
  - aiortc peer-to-peer Opus audio (full mesh, up to ~5 devices)
  - Mic input from rawAudioData cereal topic (micd.py, 16 kHz)
  - Speaker output via sounddevice OutputStream (48 kHz, separate from soundd)
  - Speaker mute flag for echo prevention during demos

Thread model:
  Single daemon thread running asyncio event loop.
  All public properties protected by threading.Lock for UI thread reads.
"""
from __future__ import annotations

import asyncio
import fractions
import json
import subprocess
import threading
import time
import numpy as np
from collections import deque
from enum import Enum

import websocket

from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.games.supabase_client import get_supabase_config

try:
  import av
  from aiortc import (
    RTCPeerConnection, RTCSessionDescription,
    RTCIceServer, RTCConfiguration, AudioStreamTrack,
  )
  _AIORTC_OK = True
except Exception:
  _AIORTC_OK = False

# ── Audio constants ────────────────────────────────────────────────────────────
_MIC_RATE     = 16000   # rawAudioData sample rate from micd.py
_OUT_RATE     = 48000   # sounddevice / Opus output rate
_FRAME_OUT    = 960     # 20 ms at 48 kHz
_JIT_MAX      = 24000   # max jitter buffer samples (~500 ms)
_OUT_BLOCK    = 960

# ── Signaling ──────────────────────────────────────────────────────────────────
_CHANNEL       = "voice_v1"
_ICE_URLS      = ["stun:stun.l.google.com:19302", "stun:stun1.l.google.com:19302"]
_RECONNECT     = 3.0
_HEARTBEAT     = 25.0


class VoiceState(Enum):
  OFFLINE      = "offline"
  CONNECTING   = "connecting"
  IDLE         = "idle"
  TALKING      = "talking"
  PEER_TALKING = "peer"


# ── Custom aiortc audio source ─────────────────────────────────────────────────
if _AIORTC_OK:
  class _MicTrack(AudioStreamTrack):
    kind = "audio"

    def __init__(self) -> None:
      super().__init__()
      self._q: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=30)
      self._buf = np.zeros(0, dtype=np.float32)
      self._pts = 0
      self.active = False

    def feed(self, pcm_int16_bytes: bytes) -> None:
      """16 kHz int16 bytes → resample to 48 kHz and enqueue (rawAudioData fallback)."""
      if not self.active:
        return
      pcm = np.frombuffer(pcm_int16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
      pcm48 = np.repeat(pcm, _OUT_RATE // _MIC_RATE)  # 3× upsample (low-quality fallback)
      self._enqueue(pcm48)

    def feed_native(self, pcm_f32: np.ndarray) -> None:
      """48 kHz float32 samples directly — no resampling needed."""
      if not self.active:
        return
      self._enqueue(pcm_f32)

    def _enqueue(self, pcm48: np.ndarray) -> None:
      try:
        self._q.put_nowait(pcm48)
      except asyncio.QueueFull:
        try:
          self._q.get_nowait()  # drop oldest frame
          self._q.put_nowait(pcm48)
        except asyncio.QueueEmpty:
          pass

    async def recv(self) -> "av.AudioFrame":
      while len(self._buf) < _FRAME_OUT:
        try:
          # 25ms timeout — keeps Opus pipeline flowing without 500ms stall artifacts
          chunk = await asyncio.wait_for(self._q.get(), timeout=0.025)
        except asyncio.TimeoutError:
          chunk = np.zeros(_FRAME_OUT, dtype=np.float32)
        self._buf = np.concatenate([self._buf, chunk])

      data = self._buf[:_FRAME_OUT]
      self._buf = self._buf[_FRAME_OUT:]

      s16 = (data * 32767.0).clip(-32768, 32767).astype(np.int16)
      frame = av.AudioFrame.from_ndarray(s16.reshape(1, -1), format="s16", layout="mono")
      frame.sample_rate = _OUT_RATE
      frame.pts = self._pts
      frame.time_base = fractions.Fraction(1, _OUT_RATE)
      self._pts += _FRAME_OUT
      return frame


# ── VoiceService ───────────────────────────────────────────────────────────────
class VoiceService:
  _instance: VoiceService | None = None
  _lock_inst = threading.Lock()

  @classmethod
  def get(cls) -> VoiceService:
    with cls._lock_inst:
      if cls._instance is None:
        cls._instance = cls()
    return cls._instance

  def __init__(self) -> None:
    self._lock           = threading.Lock()
    self._state          = VoiceState.OFFLINE
    self._mic_muted      = False   # Open mic by default — tap icon to mute
    self._speaker_muted  = False   # Speaker ON by default
    self._peer_count     = 0

    raw = Params().get("DongleId")
    self._dongle_id: str = raw.decode("utf-8") if isinstance(raw, bytes) else (raw or "unknown")

    self._out_buf: deque[float] = deque(maxlen=_JIT_MAX)
    self._out_stream  = None
    self._out_ok      = False
    self._out_playing = False  # jitter buffer hysteresis state

    self._thread = threading.Thread(target=self._run, daemon=True, name="voice_svc")
    self._thread.start()

  # ── Public API ─────────────────────────────────────────────────────────────
  @property
  def state(self) -> VoiceState:
    with self._lock:
      return self._state

  @property
  def mic_muted(self) -> bool:
    with self._lock:
      return self._mic_muted

  @property
  def speaker_muted(self) -> bool:
    with self._lock:
      return self._speaker_muted

  @property
  def peer_count(self) -> int:
    with self._lock:
      return self._peer_count

  def toggle_mic_mute(self) -> None:
    with self._lock:
      self._mic_muted = not self._mic_muted

  def set_mic_muted(self, muted: bool) -> None:
    with self._lock:
      self._mic_muted = muted

  def toggle_speaker_mute(self) -> None:
    with self._lock:
      self._speaker_muted = not self._speaker_muted

  def set_speaker_muted(self, muted: bool) -> None:
    with self._lock:
      self._speaker_muted = muted

  # ── Internal ───────────────────────────────────────────────────────────────
  def _set_state(self, s: VoiceState) -> None:
    with self._lock:
      self._state = s

  def _run(self) -> None:
    if not _AIORTC_OK:
      cloudlog.warning("voice: aiortc unavailable — disabled")
      return
    # Import sounddevice after forking (same pattern as soundd/micd)
    try:
      import sounddevice as sd  # noqa: PLC0415
      self._open_output(sd)
    except Exception as e:
      cloudlog.warning(f"voice: sounddevice unavailable: {e}")
    asyncio.run(self._async_main())

  def _open_output(self, sd) -> None:
    for attempt in range(8):
      try:
        # Reinitialize PortAudio before opening (required on comma hardware)
        sd._terminate()
        sd._initialize()
        self._out_stream = sd.OutputStream(
          samplerate=_OUT_RATE, channels=1, dtype="float32",
          callback=self._out_cb, blocksize=_OUT_BLOCK,
        )
        self._out_stream.start()
        self._out_ok = True
        cloudlog.info(f"voice: output stream opened (device={self._out_stream.device})")
        # ALSA 'Playback 0 Volume' resets to 0 on boot unless soundd (car-only) runs.
        # Use 50% (4096/8192) — max causes distortion at the speaker stage.
        try:
          subprocess.run(
            ["amixer", "-c", "0", "cset", "name=Playback 0 Volume", "4096"],
            capture_output=True, timeout=3,
          )
          cloudlog.info("voice: set ALSA Playback 0 Volume to 4096 (50%)")
        except Exception as ve:
          cloudlog.warning(f"voice: amixer volume set failed: {ve}")
        return
      except Exception as e:
        cloudlog.warning(f"voice: output stream attempt {attempt + 1} failed: {e}")
        time.sleep(3)
    cloudlog.warning("voice: output stream could not be opened after retries")

  def _play_tone(self, freq: float = 880.0, duration: float = 0.12, gap: float = 0.08, count: int = 2) -> None:
    """Enqueue N short beeps into the output buffer (e.g. connection confirmed)."""
    if not self._out_ok:
      return
    n = int(_OUT_RATE * duration)
    g = int(_OUT_RATE * gap)
    t = np.linspace(0, duration, n, endpoint=False)
    fade = np.minimum(1.0, np.minimum(t / 0.005, (duration - t) / 0.005))  # 5ms fade
    beep = (np.sin(2 * np.pi * freq * t) * 0.35 * fade).astype(np.float32)
    silence = np.zeros(g, dtype=np.float32)
    for _ in range(count):
      self._out_buf.extend(beep.tolist())
      self._out_buf.extend(silence.tolist())

  def _out_cb(self, out: np.ndarray, frames: int, _t, _s) -> None:
    with self._lock:
      muted = self._speaker_muted
    if muted:
      out[:] = 0.0
      return
    buf_len = len(self._out_buf)
    # Hysteresis: start playing once 3 frames buffered, stop only when empty.
    # Without this, every frame play drains the buffer to 0 → next call is
    # silence → alternates play/silence every 20ms = robotic sound.
    if not self._out_playing:
      if buf_len >= frames * 3:
        self._out_playing = True
      else:
        out[:] = 0.0
        return
    if buf_len < frames:
      self._out_playing = False
      out[:] = 0.0
      return
    out[:, 0] = np.array([self._out_buf.popleft() for _ in range(frames)], dtype=np.float32)

  async def _async_main(self) -> None:
    config = get_supabase_config()
    if not config:
      cloudlog.info("voice: Supabase not configured")
      self._set_state(VoiceState.OFFLINE)
      return

    url, key = config
    self._set_state(VoiceState.CONNECTING)

    # peer_id → (RTCPeerConnection, _MicTrack)
    peers: dict[str, tuple[RTCPeerConnection, _MicTrack]] = {}
    # peer_id → list[dict]  (ICE candidates received before remote desc is set)
    pending_ice: dict[str, list[dict]] = {}

    loop = asyncio.get_running_loop()

    # ── Helpers ──────────────────────────────────────────────────────────────
    send_ref = [100]
    ws_ref: list[websocket.WebSocketApp | None] = [None]

    def ws_send(payload: dict) -> None:
      ws = ws_ref[0]
      if ws is None:
        return
      send_ref[0] += 1
      try:
        ws.send(json.dumps({
          "topic":   f"realtime:{_CHANNEL}",
          "event":   "broadcast",
          "payload": {"type": "broadcast", "event": "sig", "payload": payload},
          "ref":     str(send_ref[0]),
        }))
      except Exception as e:
        cloudlog.warning(f"voice ws_send: {e}")

    async def add_ice(pc: RTCPeerConnection, cand: dict) -> None:
      try:
        from aiortc.sdp import candidate_from_sdp  # type: ignore[import]
        raw = cand.get("candidate", "")
        # strip leading "candidate:" if present
        if raw.lower().startswith("candidate:"):
          raw = raw[len("candidate:"):]
        c = candidate_from_sdp(raw)
        c.sdpMid        = cand.get("sdpMid")
        c.sdpMLineIndex = cand.get("sdpMLineIndex", 0)
        await pc.addIceCandidate(c)
      except Exception as e:
        cloudlog.warning(f"voice ICE add: {e}")

    async def consume_track(track: "AudioStreamTrack", peer_id: str) -> None:
      cloudlog.info(f"voice: consuming track from {peer_id}")
      self._set_state(VoiceState.PEER_TALKING)
      try:
        while True:
          frame = await track.recv()
          pcm = frame.to_ndarray().flatten().astype(np.float32) / 32768.0
          if self._out_ok:
            self._out_buf.extend(pcm.tolist())
      except Exception:
        pass

    def make_pc(peer_id: str) -> tuple[RTCPeerConnection, _MicTrack]:
      cfg = RTCConfiguration([RTCIceServer(urls=_ICE_URLS)])
      pc  = RTCPeerConnection(configuration=cfg)
      mic = _MicTrack()
      pc.addTrack(mic)

      @pc.on("track")
      def on_track(t):
        if t.kind == "audio":
          asyncio.ensure_future(consume_track(t, peer_id))

      @pc.on("icecandidate")
      def on_ice(candidate):
        if candidate is None:
          return
        ws_send({
          "type":      "ice",
          "from":      self._dongle_id,
          "to":        peer_id,
          "candidate": {
            "candidate":     f"candidate:{candidate.candidate}",
            "sdpMid":        candidate.sdpMid,
            "sdpMLineIndex": candidate.sdpMLineIndex,
          },
        })

      return pc, mic

    # ── Mic input loop ───────────────────────────────────────────────────────
    async def mic_loop() -> None:
      in_stream = None

      # Use call_soon_threadsafe to push mic frames directly into the asyncio
      # event loop the instant they arrive — no intermediate queue or sleep loop,
      # so Opus gets perfectly timed 20ms frames with no added jitter.
      try:
        import sounddevice as _sd  # noqa: PLC0415

        def _mic_cb(indata: np.ndarray, _frames: int, _t, _s) -> None:
          data = indata.flatten().copy()
          def _feed():
            with self._lock:
              mic_on = not self._mic_muted
            for _, mic in peers.values():
              mic.active = mic_on
              if mic_on:
                mic.feed_native(data)
          loop.call_soon_threadsafe(_feed)

        in_stream = _sd.InputStream(
          samplerate=_OUT_RATE, channels=1, dtype="float32",
          callback=_mic_cb, blocksize=_FRAME_OUT,  # 960 samples = 20 ms at 48 kHz
        )
        in_stream.start()
        cloudlog.info(f"voice: mic input stream opened (device={in_stream.device})")
      except Exception as e:
        cloudlog.warning(f"voice: mic InputStream failed: {e} — falling back to rawAudioData")

      if in_stream:
        # Coroutine stays alive to update state; actual work done via call_soon_threadsafe
        while True:
          with self._lock:
            mic_on = not self._mic_muted
          if peers:
            self._set_state(VoiceState.TALKING if mic_on else VoiceState.IDLE)
          await asyncio.sleep(0.5)
      else:
        # Fallback: rawAudioData cereal (available when car is connected / micd running)
        import cereal.messaging as messaging  # noqa: PLC0415
        sm = messaging.SubMaster(["rawAudioData"])
        while True:
          try:
            await loop.run_in_executor(None, lambda: sm.update(100))
          except RuntimeError:
            break
          if sm.updated["rawAudioData"]:
            raw = bytes(sm["rawAudioData"].data)
            with self._lock:
              mic_on = not self._mic_muted
            for _, mic in peers.values():
              mic.active = mic_on
            if mic_on:
              for _, mic in peers.values():
                mic.feed(raw)
          with self._lock:
            mic_on = not self._mic_muted
          if peers:
            self._set_state(VoiceState.TALKING if mic_on else VoiceState.IDLE)

    asyncio.ensure_future(mic_loop())

    # ── WebSocket signaling thread ───────────────────────────────────────────
    sig_q: asyncio.Queue[dict] = asyncio.Queue()

    def ws_thread() -> None:
      ws_host = url.replace("https://", "wss://").replace("http://", "ws://")
      ws_url  = f"{ws_host}/realtime/v1/websocket?apikey={key}&vsn=1.0.0"

      def on_open(ws):
        ws_ref[0] = ws
        ws.send(json.dumps({
          "topic":   f"realtime:{_CHANNEL}",
          "event":   "phx_join",
          "payload": {"config": {"broadcast": {"self": False}}},
          "ref":     "1",
        }))
        # announce ourselves
        ws.send(json.dumps({
          "topic":   f"realtime:{_CHANNEL}",
          "event":   "broadcast",
          "payload": {"type": "broadcast", "event": "sig",
                      "payload": {"type": "hello", "from": self._dongle_id}},
          "ref":     "2",
        }))
        loop.call_soon_threadsafe(sig_q.put_nowait, {"_evt": "connected"})

        stop = threading.Event()
        def hb():
          ref = [50]
          while not stop.is_set():
            time.sleep(_HEARTBEAT)
            try:
              ws.send(json.dumps({"topic": "phoenix", "event": "heartbeat",
                                  "payload": {}, "ref": str(ref[0])}))
              ref[0] += 1
            except Exception:
              break
        hb_t = threading.Thread(target=hb, daemon=True)
        hb_t.start()

      def on_message(ws, raw_msg):
        try:
          msg = json.loads(raw_msg)
        except Exception:
          return
        if msg.get("event") in ("broadcast", "sig"):
          inner = msg.get("payload", {})
          # Supabase wraps: payload.payload
          sig = inner.get("payload", inner)
          if isinstance(sig, dict) and sig.get("type"):
            loop.call_soon_threadsafe(sig_q.put_nowait, sig)

      def on_close(ws, *_):
        ws_ref[0] = None
        loop.call_soon_threadsafe(sig_q.put_nowait, {"_evt": "disconnected"})

      while True:
        app = websocket.WebSocketApp(
          ws_url, on_open=on_open, on_message=on_message,
          on_close=on_close, on_error=lambda ws, e: None,
        )
        app.run_forever(ping_interval=0)
        time.sleep(_RECONNECT)

    threading.Thread(target=ws_thread, daemon=True, name="voice_ws").start()

    # ── Dispatch loop ────────────────────────────────────────────────────────
    while True:
      sig = await sig_q.get()

      evt = sig.get("_evt")
      if evt == "connected":
        self._set_state(VoiceState.IDLE)
        cloudlog.info("voice: signaling connected")
        self._play_tone(freq=880.0, count=2)  # two beeps = online
        continue
      if evt == "disconnected":
        self._set_state(VoiceState.CONNECTING)
        for pc, _ in list(peers.values()):
          await pc.close()
        peers.clear()
        pending_ice.clear()
        with self._lock:
          self._peer_count = 0
        continue

      sig_type = sig.get("type", "")
      from_id  = sig.get("from", "")
      to_id    = sig.get("to", "")

      if from_id == self._dongle_id:
        continue

      if sig_type == "hello":
        if from_id not in peers:
          cloudlog.info(f"voice: hello from {from_id}, sending offer")
          pc, mic = make_pc(from_id)
          peers[from_id] = (pc, mic)
          with self._lock:
            self._peer_count = len(peers)
          offer = await pc.createOffer()
          await pc.setLocalDescription(offer)
          ws_send({"type": "offer", "from": self._dongle_id,
                   "to": from_id, "sdp": pc.localDescription.sdp})

      elif sig_type == "offer" and to_id == self._dongle_id:
        cloudlog.info(f"voice: offer from {from_id}")
        if from_id in peers:
          await peers[from_id][0].close()
        pc, mic = make_pc(from_id)
        peers[from_id] = (pc, mic)
        with self._lock:
          self._peer_count = len(peers)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sig["sdp"], type="offer"))
        for ic in pending_ice.pop(from_id, []):
          await add_ice(pc, ic)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        ws_send({"type": "answer", "from": self._dongle_id,
                 "to": from_id, "sdp": pc.localDescription.sdp})

      elif sig_type == "answer" and to_id == self._dongle_id:
        pair = peers.get(from_id)
        if pair:
          cloudlog.info(f"voice: answer from {from_id}")
          await pair[0].setRemoteDescription(
            RTCSessionDescription(sdp=sig["sdp"], type="answer")
          )
          for ic in pending_ice.pop(from_id, []):
            await add_ice(pair[0], ic)

      elif sig_type == "ice" and to_id == self._dongle_id:
        pair = peers.get(from_id)
        ic   = sig.get("candidate", {})
        if pair and pair[0].remoteDescription:
          await add_ice(pair[0], ic)
        else:
          pending_ice.setdefault(from_id, []).append(ic)

      elif sig_type == "bye":
        if from_id in peers:
          await peers[from_id][0].close()
          del peers[from_id]
          with self._lock:
            self._peer_count = len(peers)
          cloudlog.info(f"voice: {from_id} left")
