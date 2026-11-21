"""Predictive Pre-Arming and Window Foreground Monitor for Flashbang Shield.

Listens for window focus changes via lightweight SetWinEventHook (zero DLL injection,
<0.01% CPU) and maintains an in-memory LRU cache of known window luminance.

When a window switch occurs:
1. Arms the 1-frame reflex clamp for 600 ms to capture incoming flashes within 8-16 ms.
2. If the incoming window is known from LRU cache to be bright, immediately initiates
   pre-emptive dampening before DWM even presents the new window frame.
"""

import collections
import ctypes
from ctypes import wintypes
import logging
import threading
import time
from typing import Callable, Optional, Tuple

logger = logging.getLogger("flashbang_shield.prearm")

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None

EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.HWND,
    wintypes.LONG,
    wintypes.LONG,
    wintypes.DWORD,
    wintypes.DWORD,
)

if user32:
    user32.SetWinEventHook.argtypes = [
        wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
        WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD
    ]
    user32.SetWinEventHook.restype = wintypes.HANDLE

    user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    user32.UnhookWinEvent.restype = wintypes.BOOL

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND


class WindowLuminanceCache:
    """Thread-safe LRU cache mapping window HWND to its last measured luminance."""

    def __init__(self, maxsize: int = 64):
        self._maxsize = maxsize
        self._cache = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, hwnd: int) -> Optional[float]:
        with self._lock:
            if hwnd in self._cache:
                val = self._cache.pop(hwnd)
                self._cache[hwnd] = val
                return val
            return None

    def put(self, hwnd: int, luminance: float) -> None:
        with self._lock:
            if hwnd in self._cache:
                self._cache.pop(hwnd)
            elif len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)
            self._cache[hwnd] = float(luminance)


class PreArmManager:
    """Manages window foreground transition hooks and predictive engine arming."""

    def __init__(self, on_prearm_callback: Optional[Callable[[Optional[float]], None]] = None):
        self.on_prearm = on_prearm_callback
        self.cache = WindowLuminanceCache(maxsize=128)
        self._hook_handle = None
        self._hook_proc = None
        self._running = False
        self._thread = None

    def start(self):
        """Start listening for foreground window events on a dedicated message thread."""
        if not user32 or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_hook_thread, name="PreArmHookThread", daemon=True)
        self._thread.start()

    def _run_hook_thread(self):
        def _callback(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
            if event == EVENT_SYSTEM_FOREGROUND and hwnd:
                known_lum = self.cache.get(hwnd)
                if self.on_prearm:
                    try:
                        self.on_prearm(known_lum)
                    except Exception as e:
                        logger.debug(f"Pre-arm callback error: {e}")

        self._hook_proc = WINEVENTPROC(_callback)
        self._hook_handle = user32.SetWinEventHook(
            EVENT_SYSTEM_FOREGROUND,
            EVENT_SYSTEM_FOREGROUND,
            None,
            self._hook_proc,
            0,
            0,
            WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
        )

        msg = wintypes.MSG()
        while self._running:
            # Standard message pump for out-of-context WinEvent hooks
            bRet = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if bRet <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._hook_handle:
            user32.UnhookWinEvent(self._hook_handle)
            self._hook_handle = None

    def update_active_window_luminance(self, luminance: float):
        """Record current active window's luminance into LRU cache."""
        if user32:
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    self.cache.put(hwnd, luminance)
            except Exception:
                pass

    def stop(self):
        """Stop hook and thread."""
        self._running = False
        if user32 and self._hook_handle:
            try:
                user32.UnhookWinEvent(self._hook_handle)
            except Exception:
                pass
            self._hook_handle = None
