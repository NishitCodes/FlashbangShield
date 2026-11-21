"""Configuration management for Flashbang Shield.

Provides dataclass configuration, predefined presets, persistent JSON storage,
and Windows startup registry integration.
"""

import json
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # Non-Windows fallback


# Standard presets inspired by Gammy adaptive brightness curves
PRESETS: Dict[str, Dict[str, Any]] = {
    "Day": {
        "target_brightness": 0.55,
        "min_brightness": 0.35,
        "max_brightness": 1.00,
        "max_dimming": 0.65,
        "attack_speed_ms": 150.0,
        "decay_speed_ms": 500.0,
    },
    "Evening": {
        "target_brightness": 0.38,
        "min_brightness": 0.20,
        "max_brightness": 1.00,
        "max_dimming": 0.82,
        "attack_speed_ms": 180.0,
        "decay_speed_ms": 600.0,
    },
    "Night": {
        "target_brightness": 0.22,
        "min_brightness": 0.12,
        "max_brightness": 0.95,
        "max_dimming": 0.88,
        "attack_speed_ms": 180.0,
        "decay_speed_ms": 800.0,
    },
    "Ultra-Dim": {
        "target_brightness": 0.10,
        "min_brightness": 0.05,
        "max_brightness": 0.85,
        "max_dimming": 0.95,
        "attack_speed_ms": 120.0,
        "decay_speed_ms": 1000.0,
    },
}


@dataclass
class Config:
    """Flashbang Shield runtime configuration."""

    enabled: bool = True
    target_brightness: float = 0.38
    min_brightness: float = 0.20
    max_brightness: float = 1.00
    attack_speed_ms: float = 180.0
    decay_speed_ms: float = 600.0
    max_dimming: float = 0.82
    preset: str = "Evening"
    start_with_windows: bool = False

    # Advanced Gammy & engine tuning
    sample_rate_hz: float = 30.0
    sample_resolution: int = 64
    curve_gamma: float = 2.0
    flash_threshold: float = 0.30
    per_monitor: bool = True
    tint_color: Tuple[int, int, int] = (0, 0, 0)  # RGB overlay tint
    hotkeys_enabled: bool = True

    # Observers for change notifications
    _observers: List[Callable[["Config"], None]] = field(default_factory=list, init=False, repr=False, compare=False)

    # --- Property Aliases for full compatibility ---
    @property
    def target_luminance(self) -> float:
        return self.target_brightness

    @target_luminance.setter
    def target_luminance(self, value: float) -> None:
        self.target_brightness = float(value)

    @property
    def attack_time_ms(self) -> float:
        return self.attack_speed_ms

    @attack_time_ms.setter
    def attack_time_ms(self, value: float) -> None:
        self.attack_speed_ms = float(value)

    @property
    def release_time_ms(self) -> float:
        return self.decay_speed_ms

    @release_time_ms.setter
    def release_time_ms(self, value: float) -> None:
        self.decay_speed_ms = float(value)

    def apply_preset(self, name: str) -> bool:
        """Apply a named preset to configuration."""
        if name not in PRESETS:
            return False
        p = PRESETS[name]
        self.preset = name
        self.target_brightness = p["target_brightness"]
        self.min_brightness = p["min_brightness"]
        self.max_brightness = p["max_brightness"]
        self.max_dimming = p["max_dimming"]
        self.attack_speed_ms = p["attack_speed_ms"]
        self.decay_speed_ms = p["decay_speed_ms"]
        self._notify_observers()
        return True

    def add_observer(self, callback: Callable[["Config"], None]) -> None:
        """Register an observer callback for configuration updates."""
        if callback not in self._observers:
            self._observers.append(callback)

    def remove_observer(self, callback: Callable[["Config"], None]) -> None:
        """Remove an observer callback."""
        if callback in self._observers:
            self._observers.remove(callback)

    def _notify_observers(self) -> None:
        """Notify all registered callbacks."""
        for cb in list(self._observers):
            try:
                cb(self)
            except Exception:
                pass

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        d = {}
        for f in self.__dataclass_fields__:
            if f == "_observers":
                continue
            val = getattr(self, f)
            if isinstance(val, tuple):
                val = list(val)
            d[f] = val
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        """Instantiate Config from dictionary with safe field filtering."""
        valid_keys = {f for f in cls.__dataclass_fields__ if f != "_observers"}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        # Handle tuple conversion for tint_color
        if "tint_color" in filtered and isinstance(filtered["tint_color"], list):
            filtered["tint_color"] = tuple(filtered["tint_color"])
        return cls(**filtered)


# Alias for backward compatibility
ShieldConfig = Config


def get_default_config_path() -> Path:
    """Return default configuration file path."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base_dir = Path(appdata) / "FlashbangShield"
    else:
        base_dir = Path.home() / ".flashbang_shield"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / "config.json"


def load_config(path: Optional[Path | str] = None) -> Config:
    """Load configuration from JSON file or return default."""
    cfg_path = Path(path) if path else get_default_config_path()
    if cfg_path.exists():
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Config.from_dict(data)
        except Exception:
            pass
    return Config()


_save_lock = threading.Lock()


def save_config(config: Config, path: Optional[Path | str] = None) -> None:
    """Persist configuration to JSON file atomically and thread-safely."""
    cfg_path = Path(path) if path else get_default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config.to_dict(), indent=2)

    with _save_lock:
        temp_path = cfg_path.with_name(
            f"{cfg_path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{time.perf_counter_ns()}"
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(payload)

            # Retry replace for Windows file locks (antivirus/indexing/concurrent replace)
            for attempt in range(5):
                try:
                    temp_path.replace(cfg_path)
                    break
                except OSError:
                    if attempt == 4:
                        raise
                    time.sleep(0.01)
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass


def set_run_at_startup(enable: bool, app_name: str = "FlashbangShield", exe_path: Optional[str] = None) -> bool:
    """Register or unregister app in Windows Run registry key."""
    if winreg is None:
        return False

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            if enable:
                if exe_path is None:
                    if getattr(sys, "frozen", False):
                        exe_path = f'"{sys.executable}"'
                    else:
                        main_py = Path(sys.argv[0]).resolve()
                        exe_path = f'"{sys.executable}" "{main_py}"'
                winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, app_name)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def get_run_at_startup(app_name: str = "FlashbangShield") -> bool:
    """Check if app is currently registered in Windows Run registry key."""
    if winreg is None:
        return False

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_QUERY_VALUE) as key:
            winreg.QueryValueEx(key, app_name)
            return True
    except OSError:
        return False
