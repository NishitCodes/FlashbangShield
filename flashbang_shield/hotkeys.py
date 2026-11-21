"""Global system-wide hotkeys manager for Flashbang Shield.

Listens for:
- Win + Alt + O (and fallback Ctrl + Alt + O): Toggle Shield On / Off
- Win + Alt + Up (and fallback Ctrl + Alt + Up): Increase Brightness (+5%)
- Win + Alt + Down (and fallback Ctrl + Alt + Down): Decrease Brightness (-5%)

Uses native Windows RegisterHotKey API on a dedicated background message thread.
Unlike WH_KEYBOARD_LL hooks, RegisterHotKey is completely non-intrusive:
- Does NOT intercept or process arbitrary keystrokes
- Adds 0 ms latency to normal typing
- Never hangs, drops keys, or interferes with PowerShell/terminals/editors
- Native OS dispatch directly delivers WM_HOTKEY messages
"""

import ctypes
from ctypes import wintypes
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("FlashbangShield.Hotkeys")

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None

if user32:
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL

# Windows Constants
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # Windows 7+ flag: do not generate multiple hotkey messages when key is held down

VK_UP = 0x26
VK_DOWN = 0x28
VK_O = 0x4F

# Hotkey Identifier Constants
ID_TOGGLE_WIN_ALT = 101
ID_TOGGLE_CTRL_ALT = 102
ID_BRIGHT_UP_WIN_ALT = 103
ID_BRIGHT_UP_CTRL_ALT = 104
ID_BRIGHT_DOWN_WIN_ALT = 105
ID_BRIGHT_DOWN_CTRL_ALT = 106


class HotkeyManager:
    """Manages non-intrusive global hotkey registration on Windows using RegisterHotKey."""

    def __init__(
        self,
        on_toggle: Optional[Callable[[], None]] = None,
        on_brightness_up: Optional[Callable[[], None]] = None,
        on_brightness_down: Optional[Callable[[], None]] = None,
    ):
        self.on_toggle = on_toggle
        self.on_brightness_up = on_brightness_up
        self.on_brightness_down = on_brightness_down

        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._running = threading.Event()
        self._registered_ids = []

        # Debounce tracking
        self._last_trigger_time = 0.0
        self._debounce_interval = 0.20  # 200ms debounce between key triggers

    def start(self):
        """Start global hotkey listener in background thread."""
        if not user32:
            logger.warning("Windows user32 not available; hotkeys disabled.")
            return

        if self._thread and self._thread.is_alive():
            return

        self._running.set()
        ready_evt = threading.Event()
        self._thread = threading.Thread(
            target=self._hotkey_loop,
            args=(ready_evt,),
            name="FlashbangShieldHotkeyThread",
            daemon=True,
        )
        self._thread.start()
        ready_evt.wait(timeout=2.0)
        logger.info("Global hotkeys listener started (Win/Ctrl+Alt+O, Win/Ctrl+Alt+Up/Down)")

    def stop(self):
        """Stop global hotkey listener."""
        self._running.clear()
        if self._thread_id and user32:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception as e:
                logger.debug(f"Error posting WM_QUIT to hotkey thread: {e}")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("Global hotkeys listener stopped")

    def is_running(self) -> bool:
        """Check if listener is running."""
        return self._running.is_set() and (self._thread is not None and self._thread.is_alive())

    def _dispatch(self, callback: Optional[Callable[[], None]]):
        """Execute callback asynchronously so hotkey message loop never blocks."""
        if not callback:
            return
        now = time.perf_counter()
        if now - self._last_trigger_time < self._debounce_interval:
            return
        self._last_trigger_time = now

        def _runner():
            try:
                callback()
            except Exception as e:
                logger.error(f"Error in hotkey callback: {e}", exc_info=True)

        threading.Thread(target=_runner, daemon=True).start()

    def _hotkey_loop(self, ready_evt: threading.Event):
        """Message loop for RegisterHotKey."""
        if not user32 or not kernel32:
            ready_evt.set()
            return

        self._thread_id = kernel32.GetCurrentThreadId()

        # Force thread message queue creation
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)

        # Definitions: (ID, modifiers, vkCode, description)
        hotkey_definitions = [
            (ID_TOGGLE_WIN_ALT, MOD_WIN | MOD_ALT | MOD_NOREPEAT, VK_O, "Win+Alt+O"),
            (ID_TOGGLE_CTRL_ALT, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_O, "Ctrl+Alt+O"),
            (ID_BRIGHT_UP_WIN_ALT, MOD_WIN | MOD_ALT | MOD_NOREPEAT, VK_UP, "Win+Alt+Up"),
            (ID_BRIGHT_UP_CTRL_ALT, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_UP, "Ctrl+Alt+Up"),
            (ID_BRIGHT_DOWN_WIN_ALT, MOD_WIN | MOD_ALT | MOD_NOREPEAT, VK_DOWN, "Win+Alt+Down"),
            (ID_BRIGHT_DOWN_CTRL_ALT, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_DOWN, "Ctrl+Alt+Down"),
        ]

        self._registered_ids = []
        for hid, mod, vk, desc in hotkey_definitions:
            ret = user32.RegisterHotKey(None, hid, mod, vk)
            if ret:
                self._registered_ids.append(hid)
                logger.debug(f"Registered hotkey: {desc} (ID {hid})")
            else:
                err = ctypes.GetLastError()
                logger.debug(f"Could not register hotkey {desc} (ID {hid}, Win32 error {err})")

        ready_evt.set()

        try:
            while self._running.is_set():
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res <= 0:  # WM_QUIT or error
                    break

                if msg.message == WM_HOTKEY:
                    hid = msg.wParam
                    if hid in (ID_TOGGLE_WIN_ALT, ID_TOGGLE_CTRL_ALT):
                        logger.debug("Hotkey triggered: Toggle Shield")
                        self._dispatch(self.on_toggle)
                    elif hid in (ID_BRIGHT_UP_WIN_ALT, ID_BRIGHT_UP_CTRL_ALT):
                        logger.debug("Hotkey triggered: Brightness Up")
                        self._dispatch(self.on_brightness_up)
                    elif hid in (ID_BRIGHT_DOWN_WIN_ALT, ID_BRIGHT_DOWN_CTRL_ALT):
                        logger.debug("Hotkey triggered: Brightness Down")
                        self._dispatch(self.on_brightness_down)

                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hid in self._registered_ids:
                try:
                    user32.UnregisterHotKey(None, hid)
                except Exception:
                    pass
            self._registered_ids.clear()
            self._thread_id = None
