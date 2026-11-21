"""Native DirectX 11 / DXGI Desktop Duplication sampler for Flashbang Shield.

Loads the native shield_core.dll (Phase 3 Native GPU Pipeline) providing:
- Event-driven DXGI Desktop Duplication (0% CPU / GPU idle when static, full PSR2 support)
- Hardware bilinear GPU downsampling to 64x36
- Real-time physical nits calculation and HDR / scRGB awareness
- Fullscreen 3D game detection (QUNS_RUNNING_D3D_FULL_SCREEN)
"""

import ctypes
from dataclasses import dataclass
import logging
import os
from typing import Dict, List, Optional, Tuple

from .sampler import MonitorInfo

logger = logging.getLogger("flashbang_shield.native_sampler")


class ShieldLuminanceResult(ctypes.Structure):
    _fields_ = [
        ("monitor_id", ctypes.c_int),
        ("mean_linear_lum", ctypes.c_float),
        ("p90_linear_lum", ctypes.c_float),
        ("effective_lum", ctypes.c_float),
        ("mean_nits", ctypes.c_float),
        ("peak_nits", ctypes.c_float),
        ("sdr_white_nits", ctypes.c_float),
        ("is_hdr", ctypes.c_int),
        ("has_new_frame", ctypes.c_int),
        ("capture_latency_ms", ctypes.c_float),
    ]


@dataclass
class NativeSampleResult:
    monitor_id: int
    linear_luminance: float
    effective_luminance: float
    mean_nits: float
    peak_nits: float
    is_hdr: bool
    has_new_frame: bool
    latency_ms: float


class NativeDXGISampler:
    """Python interface to native shield_core.dll DirectX 11 / DXGI engine."""

    def __init__(self, prefer_hdr: bool = True):
        self.prefer_hdr = prefer_hdr
        self.dll = None
        self.available = False
        self._active_monitors: Dict[int, bool] = {}

        # Locate shield_core.dll
        curr_dir = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(curr_dir, "shield_core.dll")

        if os.path.exists(dll_path):
            try:
                self.dll = ctypes.CDLL(dll_path)
                self._bind_functions()
                count = self.dll.ShieldCore_Init()
                if count > 0:
                    self.available = True
                    logger.info(f"Native DXGI pipeline initialized for {count} monitor(s)")
            except Exception as e:
                logger.warning(f"Failed to initialize shield_core.dll: {e}")

    def _bind_functions(self):
        self.dll.ShieldCore_Init.restype = ctypes.c_int

        self.dll.ShieldCore_Shutdown.restype = None

        self.dll.ShieldCore_GetMonitorCount.restype = ctypes.c_int

        self.dll.ShieldCore_GetMonitorHDRInfo.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_float),
        ]
        self.dll.ShieldCore_GetMonitorHDRInfo.restype = ctypes.c_int

        self.dll.ShieldCore_StartCapture.argtypes = [ctypes.c_int, ctypes.c_int]
        self.dll.ShieldCore_StartCapture.restype = ctypes.c_int

        self.dll.ShieldCore_Sample.argtypes = [
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.POINTER(ShieldLuminanceResult),
        ]
        self.dll.ShieldCore_Sample.restype = ctypes.c_int

        self.dll.ShieldCore_StopCapture.argtypes = [ctypes.c_int]
        self.dll.ShieldCore_StopCapture.restype = None

        self.dll.ShieldCore_IsFullscreenGameActive.restype = ctypes.c_int

    def start_monitor(self, monitor_id: int) -> bool:
        if not self.available:
            return False
        ok = bool(self.dll.ShieldCore_StartCapture(monitor_id, 1 if self.prefer_hdr else 0))
        if ok:
            self._active_monitors[monitor_id] = True
        return ok

    def sample_monitor(self, monitor_id: int, timeout_ms: int = 0) -> Optional[NativeSampleResult]:
        if not self.available:
            return None

        if monitor_id not in self._active_monitors:
            if not self.start_monitor(monitor_id):
                return None

        res = ShieldLuminanceResult()
        ok = self.dll.ShieldCore_Sample(monitor_id, timeout_ms, ctypes.byref(res))
        if not ok:
            return None

        return NativeSampleResult(
            monitor_id=res.monitor_id,
            linear_luminance=res.mean_linear_lum,
            effective_luminance=res.effective_lum,
            mean_nits=res.mean_nits,
            peak_nits=res.peak_nits,
            is_hdr=bool(res.is_hdr),
            has_new_frame=bool(res.has_new_frame),
            latency_ms=res.capture_latency_ms,
        )

    def is_fullscreen_game_active(self) -> bool:
        if not self.available:
            return False
        return bool(self.dll.ShieldCore_IsFullscreenGameActive())

    def close(self):
        if self.available and self.dll:
            try:
                for m_id in list(self._active_monitors.keys()):
                    self.dll.ShieldCore_StopCapture(m_id)
                self._active_monitors.clear()
                self.dll.ShieldCore_Shutdown()
            except Exception:
                pass
            self.available = False
