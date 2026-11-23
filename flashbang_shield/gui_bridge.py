"""Thread-safe telemetry ring buffer and cross-thread UI control bridge."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal


@dataclass(slots=True)
class TelemetrySample:
    """Immutable single-frame telemetry snapshot from ShieldEngine."""

    timestamp: float
    luminance: float
    target_luminance: float
    overlay_alpha: float
    is_flashbang: bool
    enabled: bool
    fps: float


class TelemetryBridge(QObject):
    """Decouples the 60 Hz background ShieldEngine thread from the Qt GUI thread.

    - Engine callback pushes into a bounded ring buffer in O(1) (<5 µs lock).
    - UI widgets pull frames via a 60 Hz QTimer only while visible (0% CPU in tray).
    - Tray icon (`pystray`) and Win32 hotkeys trigger UI actions via thread-safe Signals.
    """

    # Cross-thread lifecycle signals (safe to emit from pystray or hotkey threads)
    show_dashboard_requested = Signal()
    hide_dashboard_requested = Signal()
    toggle_mini_hud_requested = Signal()
    quit_requested = Signal()
    external_state_changed = Signal()  # Fired when hotkey/tray changes preset or toggle

    def __init__(self, history_seconds: float = 10.0, sample_rate_hz: float = 60.0) -> None:
        super().__init__()
        capacity = max(120, int(history_seconds * sample_rate_hz))
        self._lock = threading.Lock()
        self._buffer: Deque[TelemetrySample] = deque(maxlen=capacity)
        self._latest: TelemetrySample = TelemetrySample(
            timestamp=time.perf_counter(),
            luminance=0.0,
            target_luminance=0.38,
            overlay_alpha=0.0,
            is_flashbang=False,
            enabled=True,
            fps=0.0,
        )
        self._monitors: List[Dict[str, Any]] = []
        self._sequence: int = 0
        self._flash_trigger_ts: float = 0.0
        self._last_enabled: Optional[bool] = None
        self._last_target: Optional[float] = None

    def on_engine_stats(self, stats: Dict[str, Any]) -> None:
        """Engine stats callback invoked on the background ShieldEngine thread.

        Must remain allocation-minimal and non-blocking to preserve DXGI timing
        and zero-lag cursor attenuation.
        """
        now = float(stats.get("timestamp", time.perf_counter()))
        enabled = bool(stats.get("enabled", True))
        luminance = max(0.0, min(1.0, float(stats.get("current_luminance", 0.0))))
        target = max(0.0, min(1.0, float(stats.get("target_luminance", 0.38))))
        alpha = max(0.0, min(1.0, float(stats.get("overlay_alpha", 0.0))))
        is_flash = bool(stats.get("is_flashbang", False))
        fps = float(stats.get("fps", 0.0))
        monitors = stats.get("monitors")

        sample = TelemetrySample(
            timestamp=now,
            luminance=luminance,
            target_luminance=target,
            overlay_alpha=alpha,
            is_flashbang=is_flash,
            enabled=enabled,
            fps=fps,
        )

        state_changed = False
        with self._lock:
            self._buffer.append(sample)
            self._latest = sample
            self._sequence += 1
            if is_flash:
                self._flash_trigger_ts = now
            if isinstance(monitors, list):
                self._monitors = monitors

            if self._last_enabled is not None and (
                enabled != self._last_enabled or abs(target - (self._last_target or 0.0)) > 1e-4
            ):
                state_changed = True
            self._last_enabled = enabled
            self._last_target = target

        if state_changed:
            # Low-frequency notification only on external hotkey/tray state mutation
            self.external_state_changed.emit()

    def snapshot(self) -> Tuple[int, TelemetrySample, List[TelemetrySample], float]:
        """Thread-safe atomic copy for the 60 FPS UI renderer."""
        with self._lock:
            return (
                self._sequence,
                self._latest,
                list(self._buffer),
                self._flash_trigger_ts,
            )

    def latest(self) -> TelemetrySample:
        """Fast latest-sample read for lightweight views (Mini-HUD, status bar)."""
        with self._lock:
            return self._latest

    def monitor_snapshots(self) -> List[Dict[str, Any]]:
        """Return optional per-monitor stats if emitted by ShieldEngine."""
        with self._lock:
            return list(self._monitors)
