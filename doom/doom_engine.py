import ctypes
import os
import platform

# DOOM key codes (from doomkeys.h)
KEY_RIGHTARROW = 0xAE
KEY_LEFTARROW = 0xAC
KEY_UPARROW = 0xAD
KEY_DOWNARROW = 0xAF
KEY_STRAFE_L = 0xA0
KEY_STRAFE_R = 0xA1
KEY_USE = 0xA2
KEY_FIRE = 0xA3
KEY_ESCAPE = 27
KEY_ENTER = 13
KEY_TAB = 9
KEY_RSHIFT = 0x80 + 0x36
KEY_RCTRL = 0x80 + 0x1D
KEY_RALT = 0x80 + 0x38
KEY_PAUSE = 0xFF
KEY_EQUALS = 0x3D
KEY_MINUS = 0x2D
KEY_F1 = 0x80 + 0x3B

DOOM_DIR = os.path.dirname(os.path.abspath(__file__))


class DoomEngine:
  def __init__(self):
    self._lib = None
    self._initialized = False
    self._resx = 0
    self._resy = 0

  def init(self, wad_path: str | None = None, warp: str = "1 1", skill: int = 3) -> bool:
    if self._initialized:
      return True

    ext = ".dylib" if platform.system() == "Darwin" else ".so"
    lib_path = os.path.join(DOOM_DIR, f"libdoomgeneric{ext}")

    if not os.path.exists(lib_path):
      print(f"DOOM: library not found at {lib_path}")
      return False

    self._lib = ctypes.CDLL(lib_path)
    self._setup_functions()

    if wad_path is None:
      wad_path = os.path.join(DOOM_DIR, "doom1.wad")

    if not os.path.exists(wad_path):
      print(f"DOOM: WAD not found at {wad_path}")
      return False

    args = ["doom", "-iwad", wad_path, "-nosound", "-nosfx", "-nomusic"]
    if warp:
      args.extend(["-warp"] + warp.split())
    if skill:
      args.extend(["-skill", str(skill)])

    argc = len(args)
    argv_type = ctypes.c_char_p * argc
    argv = argv_type(*[a.encode() for a in args])

    ret = self._lib.doom_create(argc, argv)
    if ret != 0:
      print(f"DOOM: doom_create returned {ret}")
      return False

    self._resx = self._lib.doom_get_resx()
    self._resy = self._lib.doom_get_resy()
    self._initialized = True
    return True

  def _setup_functions(self):
    lib = self._lib

    lib.doom_create.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
    lib.doom_create.restype = ctypes.c_int

    lib.doom_tick.argtypes = []
    lib.doom_tick.restype = ctypes.c_int

    lib.doom_add_key.argtypes = [ctypes.c_int, ctypes.c_ubyte]
    lib.doom_add_key.restype = None

    lib.doom_get_rgba_buffer.argtypes = []
    lib.doom_get_rgba_buffer.restype = ctypes.POINTER(ctypes.c_uint32)

    lib.doom_get_resx.argtypes = []
    lib.doom_get_resx.restype = ctypes.c_int

    lib.doom_get_resy.argtypes = []
    lib.doom_get_resy.restype = ctypes.c_int

    lib.doom_frame_ready.argtypes = []
    lib.doom_frame_ready.restype = ctypes.c_int

    lib.doom_has_exited.argtypes = []
    lib.doom_has_exited.restype = ctypes.c_int

  def tick(self) -> bool:
    if not self._initialized:
      return False
    ret = self._lib.doom_tick()
    return ret == 0

  def add_key(self, pressed: bool, key: int):
    if self._initialized:
      self._lib.doom_add_key(1 if pressed else 0, ctypes.c_ubyte(key & 0xFF))

  def frame_ready(self) -> bool:
    if not self._initialized:
      return False
    return self._lib.doom_frame_ready() != 0

  def get_rgba_buffer(self):
    if not self._initialized:
      return None
    return self._lib.doom_get_rgba_buffer()

  def has_exited(self) -> bool:
    if not self._initialized:
      return False
    return self._lib.doom_has_exited() != 0

  @property
  def resx(self) -> int:
    return self._resx

  @property
  def resy(self) -> int:
    return self._resy

  @property
  def initialized(self) -> bool:
    return self._initialized
