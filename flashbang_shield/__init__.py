"""Flashbang Shield - Real-time adaptive screen brightness and flashbang protection.

Zero-feedback multi-monitor layered overlay with Gammy-inspired perceptual response curves
and fast asymmetric easing.
"""

from .config import Config, ShieldConfig, PRESETS, load_config, save_config, set_run_at_startup, get_run_at_startup
from .sampler import ScreenSampler, MonitorInfo, enumerate_monitors
from .overlay import OverlayManager, OverlayWindow
from .engine import ShieldEngine, EngineStats
from .hotkeys import HotkeyManager
from .tray import TrayIcon
from .gui import SettingsGUI
from .main import FlashbangShieldApp, main

__all__ = [
    "Config",
    "ShieldConfig",
    "PRESETS",
    "load_config",
    "save_config",
    "set_run_at_startup",
    "get_run_at_startup",
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
