"""Zero-feedback multi-monitor layered dimming overlay.

Uses Windows WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOPMOST, and
WDA_EXCLUDEFROMCAPTURE (SetWindowDisplayAffinity = 0x00000011).
Excluding the overlay from capture prevents positive feedback loops
(screen dims -> sampler detects darkness -> reduces dimming -> flashbang -> repeats).
"""

import ctypes
from ctypes import wintypes
import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from .sampler import MonitorInfo, enumerate_monitors

logger = logging.getLogger("flashbang_shield.overlay")

# Win32 Constants
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

LWA_ALPHA = 0x00000002
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1
WM_DESTROY = 0x0002
WM_USER = 0x0400
WM_SET_OPACITY = WM_USER + 1
WM_SET_COLOR = WM_USER + 2
WM_CLOSE_ALL = WM_USER + 3

# WDA_EXCLUDEFROMCAPTURE = 0x00000011 (Windows 10 version 2004+ and Windows 11)
WDA_EXCLUDEFROMCAPTURE = 0x00000011

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None
gdi32 = ctypes.windll.gdi32 if hasattr(ctypes, "windll") else None

LRESULT = ctypes.c_int64

if user32:
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT

    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID
    ]
    user32.CreateWindowExW.restype = wintypes.HWND

    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL

    user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL

    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT
    ]
    user32.SetWindowPos.restype = wintypes.BOOL

    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL

    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL

    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL

    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL

    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL

    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT

if gdi32:
    gdi32.GetStockObject.argtypes = [ctypes.c_int]
    gdi32.GetStockObject.restype = wintypes.HGDIOBJ

if kernel32:
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


def _default_wnd_proc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_global_wnd_proc = WNDPROC(_default_wnd_proc) if user32 else None


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HICON),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class OverlayWindow:
    """Individual transparent layered overlay window mapped to a physical monitor."""

    def __init__(self, monitor: MonitorInfo, class_name: str, hinst: int):
        self.monitor = monitor
        self.class_name = class_name
        self.hinst = hinst
        self.hwnd: Optional[int] = None
        self.opacity: float = 0.0
        self.affinity_set: bool = False
        self._create_window()

    def _create_window(self):
        if user32 is None or kernel32 is None:
            return

        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )

        title = f"FlashbangShield_Overlay_{self.monitor.id}"
        self.hwnd = user32.CreateWindowExW(
            ex_style,
            self.class_name,
            title,
            WS_POPUP,
            self.monitor.x,
            self.monitor.y,
            self.monitor.width,
            self.monitor.height,
            None,
            None,
            self.hinst,
            None,
        )

        if not self.hwnd:
            logger.warning(f"Failed to create overlay window for monitor {self.monitor.id}")
            return

        # Crucial Gammy learning: Apply WDA_EXCLUDEFROMCAPTURE
        try:
            res = user32.SetWindowDisplayAffinity(self.hwnd, WDA_EXCLUDEFROMCAPTURE)
            self.affinity_set = bool(res)
            if not self.affinity_set:
                logger.debug("SetWindowDisplayAffinity WDA_EXCLUDEFROMCAPTURE returned 0")
        except Exception as e:
            logger.debug(f"SetWindowDisplayAffinity not supported or error: {e}")

        # Set initial alpha = 0 (completely transparent)
        user32.SetLayeredWindowAttributes(self.hwnd, 0, 0, LWA_ALPHA)

        # Position and display without taking focus
        user32.SetWindowPos(
            self.hwnd,
            HWND_TOPMOST,
            self.monitor.x,
            self.monitor.y,
            self.monitor.width,
            self.monitor.height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def set_opacity(self, opacity: float):
        """Set overlay opacity in range [0.0, 1.0]."""
        self.opacity = max(0.0, min(1.0, float(opacity)))
        if self.hwnd and user32:
            alpha = int(self.opacity * 255.0)
            user32.SetLayeredWindowAttributes(self.hwnd, 0, alpha, LWA_ALPHA)

    def destroy(self):
        """Destroy window handle."""
        if self.hwnd and user32:
            try:
                user32.DestroyWindow(self.hwnd)
            except Exception:
                pass
            self.hwnd = None


class OverlayManager:
    """Manages multi-monitor overlay windows across a dedicated Win32 message pump thread."""

    CLASS_NAME = "FlashbangShieldOverlayClass"

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._thread_id: int = 0
        self._running = threading.Event()
        self._ready = threading.Event()
        self._monitors: List[MonitorInfo] = []
        self._windows: Dict[int, OverlayWindow] = {}
        self._opacities: Dict[int, float] = {}
        self._global_opacity: float = 0.0
        self._lock = threading.Lock()
        self._hinst = kernel32.GetModuleHandleW(None) if kernel32 else 0
        self._atom = 0
        self.start()

    def start(self):
        """Start the background Win32 message loop thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, name="FlashbangOverlayThread", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def _register_class(self):
        if user32 is None or gdi32 is None:
            return
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = _global_wnd_proc
        wc.hInstance = self._hinst
        wc.lpszClassName = self.CLASS_NAME
        wc.hbrBackground = gdi32.GetStockObject(4)  # BLACK_BRUSH (stock black brush)
        self._atom = user32.RegisterClassExW(ctypes.byref(wc))

    def _unregister_class(self):
        if user32 and self._atom:
            user32.UnregisterClassW(self.CLASS_NAME, self._hinst)
            self._atom = 0

    def _run_loop(self):
        """Win32 message loop thread."""
        if kernel32:
            self._thread_id = kernel32.GetCurrentThreadId()

        self._register_class()
        self._recreate_windows()
        self._ready.set()

        msg = wintypes.MSG()
        while self._running.is_set():
            # Process pending Win32 messages
            if user32:
                # Use MsgWaitForMultipleObjects / PeekMessage to avoid blocking shutdown
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE = 1
                    if msg.message == WM_CLOSE_ALL:
                        self._running.clear()
                        break
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

            time.sleep(0.016)  # ~60 Hz event check rate

        # Tear down windows on thread exit
        with self._lock:
            for w in self._windows.values():
                w.destroy()
            self._windows.clear()
            self._unregister_class()

    def _recreate_windows(self):
        with self._lock:
            for w in self._windows.values():
                w.destroy()
            self._windows.clear()

            self._monitors = enumerate_monitors()
            for m in self._monitors:
                win = OverlayWindow(m, self.CLASS_NAME, self._hinst)
                self._windows[m.id] = win
                initial_op = self._opacities.get(m.id, self._global_opacity)
                win.set_opacity(initial_op)

    def refresh_monitors(self):
        """Re-scan display configuration and update overlays."""
        self._recreate_windows()

    def set_opacity(self, opacity: float, monitor_id: Optional[int] = None):
        """Update opacity globally or for a specific monitor."""
        clamped = max(0.0, min(1.0, float(opacity)))
        with self._lock:
            if monitor_id is None:
                self._global_opacity = clamped
                for m_id, win in self._windows.items():
                    self._opacities[m_id] = clamped
                    win.set_opacity(clamped)
            else:
                self._opacities[monitor_id] = clamped
                win = self._windows.get(monitor_id)
                if win:
                    win.set_opacity(clamped)

    def get_opacity(self, monitor_id: Optional[int] = None) -> float:
        """Get current opacity for a specific monitor or global average."""
        with self._lock:
            if monitor_id is not None:
                return self._opacities.get(monitor_id, self._global_opacity)
            return self._global_opacity

    def is_affinity_active(self) -> bool:
        """Check if WDA_EXCLUDEFROMCAPTURE is active on overlay windows."""
        with self._lock:
            if not self._windows:
                return True
            return all(w.affinity_set for w in self._windows.values())

    def close(self):
        """Shut down overlay thread and clean up windows."""
        self._running.clear()
        if user32 and self._thread_id:
            try:
                user32.PostThreadMessageW(self._thread_id, WM_CLOSE_ALL, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
            self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
