"""Flashbang Shield Adaptive Engine.

Incorporates Gammy learnings:
- Non-linear human perception response curve
- Fast asymmetric easing (~180ms flash suppression attack, ~600ms relaxation decay)
- Min/Max brightness boundary limits
- Multi-monitor independent or unified sampling and overlay dimming
- WDA_EXCLUDEFROMCAPTURE zero-feedback loop protection
"""

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import Config, PRESETS, load_config, save_config
from .overlay import OverlayManager
from .sampler import MonitorInfo, ScreenSampler


@dataclass
class EngineStats:
    """Real-time engine telemetry snapshot."""

    enabled: bool = True
    current_luminance: float = 0.0
    target_luminance: float = 0.38
    overlay_alpha: float = 0.0
    is_flashbang: bool = False
    fps: float = 0.0
    timestamp: float = 0.0
    monitor_stats: Dict[int, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert stats to UI-ready dictionary."""
        return {
            "enabled": self.enabled,
            "current_luminance": round(self.current_luminance, 3),
            "target_luminance": round(self.target_luminance, 3),
            "overlay_alpha": round(self.overlay_alpha, 3),
            "is_flashbang": self.is_flashbang,
            "fps": round(self.fps, 1),
            "timestamp": self.timestamp,
        }


class ShieldEngine:
    """Core adaptive screen dimming and flashbang suppression engine."""

    def __init__(self, config: Optional[Config] = None):
        self.config = config or load_config()
        self.sampler = ScreenSampler(resolution=self.config.sample_resolution)
        self.overlay = OverlayManager()

        # Engine state
        self._running = threading.Event()
        self._paused = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Dimming state per monitor: monitor_id -> current_dimming (0.0 to 1.0)
        self._current_dimming: Dict[int, float] = {}
        self._overall_current_dimming: float = 0.0
        self._prev_luminance: Dict[int, float] = {}
        self._overall_prev_luminance: float = 0.0

        # Stats & telemetry
        self._stats = EngineStats(
            enabled=self.config.enabled,
            target_luminance=self.config.target_brightness,
        )
        self._stats_callbacks: List[Callable[[Dict[str, Any]], None]] = []

        # Connect config observers
        self.config.add_observer(self._on_config_changed)

    def _on_config_changed(self, cfg: Config):
        """Handle dynamic configuration updates."""
        self._stats.target_luminance = cfg.target_brightness
        self._stats.enabled = cfg.enabled

    # --- UI & Controller Coordination Interface ---

    def start(self):
        """Start the engine background processing loop."""
        if self._thread and self._thread.is_alive():
            return
        self._running.set()
        self._paused.clear()
        self._thread = threading.Thread(target=self._loop, name="FlashbangShieldEngineThread", daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the engine and gracefully restore screen brightness."""
        self._running.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
            self._thread = None
        # Fade overlay out completely
        self.overlay.set_opacity(0.0)

    def toggle_enabled(self) -> bool:
        """Toggle active state on/off and return new state."""
        with self._lock:
            self.config.enabled = not self.config.enabled
            self._stats.enabled = self.config.enabled
            if not self.config.enabled:
                self.overlay.set_opacity(0.0)
            return self.config.enabled

    def set_enabled(self, enabled: bool):
        """Explicitly set engine enabled status."""
        with self._lock:
            self.config.enabled = bool(enabled)
            self._stats.enabled = self.config.enabled
            if not self.config.enabled:
                self.overlay.set_opacity(0.0)

    def is_enabled(self) -> bool:
        """Check if engine is enabled."""
        return self.config.enabled

    def pause(self):
        """Temporarily pause dimming without shutting down threads."""
        self._paused.set()
        self.overlay.set_opacity(0.0)

    def resume(self):
        """Resume paused engine."""
        self._paused.clear()

    def is_paused(self) -> bool:
        """Check if engine is currently paused."""
        return self._paused.is_set()

    def set_preset(self, name: str) -> bool:
        """Apply a named preset (Day, Evening, Night, Ultra-Dim)."""
        with self._lock:
            success = self.config.apply_preset(name)
            if success:
                self._stats.target_luminance = self.config.target_brightness
            return success

    def adjust_target_brightness(self, delta: float) -> float:
        """Adjust target comfort brightness by delta (e.g. +0.05 or -0.05). Clamped [0.05, 0.95]."""
        with self._lock:
            new_val = max(0.05, min(0.95, self.config.target_brightness + delta))
            self.config.target_brightness = new_val
            self._stats.target_luminance = new_val
            return new_val

    def get_stats(self) -> Dict[str, Any]:
        """Return UI-compatible telemetry snapshot."""
        with self._lock:
            return self._stats.to_dict()

    def add_stats_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Subscribe a callback to receive real-time telemetry updates."""
        if callback not in self._stats_callbacks:
            self._stats_callbacks.append(callback)

    def remove_stats_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """Unsubscribe a telemetry callback."""
        if callback in self._stats_callbacks:
            self._stats_callbacks.remove(callback)

    def update_config(self, new_config: Config):
        """Replace current configuration."""
        with self._lock:
            self.config = new_config
            self._stats.target_luminance = new_config.target_brightness
            self._stats.enabled = new_config.enabled

    def get_monitors(self) -> List[MonitorInfo]:
        """Return list of active physical monitors."""
        return self.sampler.monitors

    # --- Gammy Perception & Easing Calculations ---

    def _compute_target_dimming(self, sampled_lum: float, prev_lum: float) -> Tuple[float, bool]:
        """Calculate target dimming opacity using Gammy non-linear human response.

        Incorporates:
        1. Min/Max brightness boundaries
        2. Non-linear perceived excess curve (e.g. power gamma)
        3. Sudden flashbang jump detection
        """
        target_comfort = self.config.target_brightness
        effective_max_dimming = min(self.config.max_dimming, 1.0 - self.config.min_brightness)

        # 1. Sudden flashbang detection: rapid jump in luminance within a single frame
        delta_lum = sampled_lum - prev_lum
        is_flashbang = delta_lum >= self.config.flash_threshold and sampled_lum > target_comfort

        if is_flashbang:
            # Immediate flash suppression ceiling
            return effective_max_dimming, True

        # 2. Gammy adaptive response: if current luminance exceeds user's comfort level
        if sampled_lum <= target_comfort:
            # Content is comfortable or dark; no dimming overlay required
            return 0.0, False

        # Non-linear excess calculation: how far above comfort luminance is the screen?
        excess_span = max(0.01, 1.0 - target_comfort)
        excess_ratio = min(1.0, (sampled_lum - target_comfort) / excess_span)

        # Apply Gammy non-linear perception power curve
        # Human visual sensitivity escalates sharply as brightness nears glaring levels
        gamma = max(1.0, self.config.curve_gamma)
        curve_factor = excess_ratio ** (gamma * 0.75)

        target_dim = curve_factor * effective_max_dimming
        return max(0.0, min(effective_max_dimming, target_dim)), False

    def _apply_asymmetric_easing(
        self,
        current_dim: float,
        target_dim: float,
        dt: float,
    ) -> float:
        """Apply Gammy fast asymmetric easing.

        Attack: ~180ms fast flash suppression when target_dim > current_dim
        Decay / Relaxation: ~600ms gradual relaxation when target_dim <= current_dim
        Continuous exponential smoothing: alpha = 1 - exp(-dt / tau)
        """
        if dt <= 0:
            return current_dim

        if target_dim > current_dim:
            # Attack phase (suppressing bright flashbang) - FAST response
            tau = max(0.02, self.config.attack_speed_ms / 1000.0)
        else:
            # Decay / Relaxation phase (recovering to normal) - SMOOTH slow recovery
            tau = max(0.05, self.config.decay_speed_ms / 1000.0)

        # Time-independent exponential smoothing
        alpha = 1.0 - math.exp(-dt / tau)
        new_dim = current_dim + (target_dim - current_dim) * alpha
        return max(0.0, min(1.0, new_dim))

    # --- Core Heartbeat Loop ---

    def _loop(self):
        """High-frequency adaptive sampling and dimming execution loop."""
        last_time = time.perf_counter()
        fps_last_time = last_time
        frame_count = 0
        current_fps = 0.0

        while self._running.is_set():
            now = time.perf_counter()
            dt = now - last_time
            last_time = now

            # FPS calculation
            frame_count += 1
            if now - fps_last_time >= 0.5:
                current_fps = frame_count / (now - fps_last_time)
                frame_count = 0
                fps_last_time = now

            if not self.config.enabled or self._paused.is_set():
                # Smoothly relax dimming to 0.0 if disabled
                if self._overall_current_dimming > 0.001:
                    self._overall_current_dimming = self._apply_asymmetric_easing(
                        self._overall_current_dimming, 0.0, dt
                    )
                    self.overlay.set_opacity(self._overall_current_dimming)
                else:
                    self._overall_current_dimming = 0.0

                with self._lock:
                    self._stats.enabled = False
                    self._stats.overlay_alpha = self._overall_current_dimming
                    self._stats.fps = current_fps
                    self._stats.timestamp = now

                self._emit_stats()
                time.sleep(1.0 / max(10.0, self.config.sample_rate_hz))
                continue

            # Active Sampling & Dimming
            is_flash_detected = False
            monitor_stats: Dict[int, Dict[str, float]] = {}

            if self.config.per_monitor and len(self.sampler.monitors) > 1:
                # Per-monitor independent adaptive dimming
                readings = self.sampler.sample_all(curve_gamma=self.config.curve_gamma)
                dimming_values = []

                for m_id, lum in readings.items():
                    prev_lum = self._prev_luminance.get(m_id, lum)
                    self._prev_luminance[m_id] = lum

                    target_dim, is_fb = self._compute_target_dimming(lum, prev_lum)
                    if is_fb:
                        is_flash_detected = True

                    curr_dim = self._current_dimming.get(m_id, 0.0)
                    new_dim = self._apply_asymmetric_easing(curr_dim, target_dim, dt)
                    self._current_dimming[m_id] = new_dim
                    dimming_values.append(new_dim)

                    # Update per-monitor overlay
                    self.overlay.set_opacity(new_dim, monitor_id=m_id)

                    monitor_stats[m_id] = {
                        "luminance": lum,
                        "target_dimming": target_dim,
                        "current_dimming": new_dim,
                    }

                overall_lum = max(readings.values()) if readings else 0.0
                self._overall_current_dimming = max(dimming_values) if dimming_values else 0.0

            else:
                # Single monitor or unified screen dimming
                lum = self.sampler.sample_overall(curve_gamma=self.config.curve_gamma)
                prev_lum = self._overall_prev_luminance
                self._overall_prev_luminance = lum

                target_dim, is_fb = self._compute_target_dimming(lum, prev_lum)
                if is_fb:
                    is_flash_detected = True

                self._overall_current_dimming = self._apply_asymmetric_easing(
                    self._overall_current_dimming, target_dim, dt
                )
                self.overlay.set_opacity(self._overall_current_dimming)
                overall_lum = lum

            # Telemetry record
            with self._lock:
                self._stats.enabled = True
                self._stats.current_luminance = overall_lum
                self._stats.target_luminance = self.config.target_brightness
                self._stats.overlay_alpha = self._overall_current_dimming
                self._stats.is_flashbang = is_flash_detected
                self._stats.fps = current_fps
                self._stats.timestamp = now
                self._stats.monitor_stats = monitor_stats

            self._emit_stats()

            # Maintain requested sampling rate
            target_period = 1.0 / max(10.0, min(60.0, self.config.sample_rate_hz))
            elapsed = time.perf_counter() - now
            sleep_time = max(0.001, target_period - elapsed)
            time.sleep(sleep_time)

    def _emit_stats(self):
        """Notify subscribed stats observers."""
        if not self._stats_callbacks:
            return
        snapshot = self._stats.to_dict()
        for cb in list(self._stats_callbacks):
            try:
                cb(snapshot)
            except Exception:
                pass

    def close(self):
        """Complete teardown of engine, sampler, and overlays."""
        self.stop()
        self.sampler.close()
        self.overlay.close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
