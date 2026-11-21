"""Global system-wide hotkeys manager for Flashbang Shield.

Listens for:
- Win + Alt + O (and fallback Ctrl + Alt + O): Toggle Shield On / Off
- Win + Alt + Up (and fallback Ctrl + Alt + Up): Increase Brightness (+5%)
- Win + Alt + Down (and fallback Ctrl + Alt + Down): Decrease Brightness (-5%)

Uses low-level Windows keyboard hook (WH_KEYBOARD_LL) to reliably intercept
Win+Alt combinations without collision with Windows Game Bar or OEM overlays,
dispatches callbacks non-blockingly to protect hook responsiveness.
"""

import ctypes
from ctypes import wintypes
import logging
import threading
import time
from typing import Callable, Optional

logger = logging.getLogger("FlashbangShield.Hotkeys")

# Windows Constants
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012

VK_UP = 0x26
VK_DOWN = 0x28
VK_O = 0x4F

VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt key

# KBDLLHOOKSTRUCT structure
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong),
    ]

# Hook callback type
HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class HotkeyManager:
    """Manages global hotkey listening on Windows."""

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
        self._hook_handle = None
        self._running = threading.Event()
        self._c_callback = None  # Prevent garbage collection of callback

        # Debounce tracking
        self._last_trigger_time = 0.0
        self._debounce_interval = 0.20  # 200ms debounce between key triggers

    def start(self):
        """Start global hotkey listener in background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._thread = threading.Thread(
            target=self._hook_loop,
            name="FlashbangShieldHotkeyThread",
            daemon=True,
        )
        self._thread.start()
        logger.info("Global hotkeys listener started (Win+Alt+O, Win+Alt+Up, Win+Alt+Down)")

    def stop(self):
        """Stop global hotkey listener."""
        self._running.clear()
        if self._thread_id:
            user32 = ctypes.windll.user32
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("Global hotkeys listener stopped")

    def is_running(self) -> bool:
        """Check if listener is running."""
        return self._running.is_set() and (self._thread is not None and self._thread.is_alive())

    def _dispatch(self, callback: Optional[Callable[[], None]]):
        """Execute callback asynchronously so hook returns immediately."""
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

    def _hook_loop(self):
        """Message loop and hook procedure."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        self._thread_id = kernel32.GetCurrentThreadId()

        def _is_key_down(vk: int) -> bool:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)

        def _hook_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = KBDLLHOOKSTRUCT.from_address(lParam)
                vk = kb.vkCode

                win_down = _is_key_down(VK_LWIN) or _is_key_down(VK_RWIN)
                alt_down = _is_key_down(VK_MENU)
                ctrl_down = _is_key_down(VK_CONTROL)

                # Check modifier match: Win+Alt or Ctrl+Alt
                has_modifiers = (win_down and alt_down) or (ctrl_down and alt_down)

                if has_modifiers:
                    if vk == VK_O:
                        logger.debug("Hotkey triggered: Toggle Shield (Win/Ctrl + Alt + O)")
                        self._dispatch(self.on_toggle)
                        return 1  # Handled: suppress key event
                    elif vk == VK_UP:
                        logger.debug("Hotkey triggered: Brightness Up (Win/Ctrl + Alt + Up)")
                        self._dispatch(self.on_brightness_up)
                        return 1  # Handled
                    elif vk == VK_DOWN:
                        logger.debug("Hotkey triggered: Brightness Down (Win/Ctrl + Alt + Down)")
                        self._dispatch(self.on_brightness_down)
                        return 1  # Handled

            return user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)

        self._c_callback = HOOKPROC(_hook_proc)
        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._c_callback,
            None,
            0,
        )

        if not self._hook_handle:
            err = ctypes.GetLastError()
            logger.warning(f"Failed to install keyboard hook (error {err}). Hotkeys may be unavailable.")
            return

        try:
            msg = wintypes.MSG()
            while self._running.is_set():
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res <= 0:  # WM_QUIT or error
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if self._hook_handle:
                user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None
            self._thread_id = None
