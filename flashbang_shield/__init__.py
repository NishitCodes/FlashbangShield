"""Flashbang Shield - Real-time adaptive screen brightness and flashbang protection.

Zero-feedback multi-monitor layered overlay with Gammy-inspired perceptual response curves
and fast asymmetric easing.
"""

from .config import Config, ShieldConfig, PRESETS, load_config, save_config, set_run_at_startup, get_run_at_startup
from .cursor import CursorDimmer
from .sampler import ScreenSampler, MonitorInfo, enumerate_monitors
from .overlay import OverlayManager, OverlayWindow
from .prearm import PreArmManager
from .engine import ShieldEngine, EngineStats
from .hotkeys import HotkeyManager
from .tray import TrayIcon
from .gui import SettingsGUI


def __getattr__(name: str):
    if name in ("FlashbangShieldApp", "main"):
        from .main import FlashbangShieldApp, main
        return {"FlashbangShieldApp": FlashbangShieldApp, "main": main}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Config",
    "ShieldConfig",
    "PRESETS",
    "load_config",
    "save_config",
    "set_run_at_startup",
    "get_run_at_startup",
    "CursorDimmer",
    "PreArmManager",
    "ScreenSampler",
    "MonitorInfo",
    "enumerate_monitors",
    "OverlayManager",
    "OverlayWindow",
    "ShieldEngine",
    "EngineStats",
    "HotkeyManager",
    "TrayIcon",
    "SettingsGUI",
    "FlashbangShieldApp",
    "main",
]

__version__ = "1.0.0"
