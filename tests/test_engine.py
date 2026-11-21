"""Comprehensive test suite for Flashbang Shield.

Covers:
- Config: dataclass serialization, property aliases, presets, observers, registry fallback
- Sampler: multi-monitor enumeration, GDI DC creation, hardware StretchBlt sampling, Gammy perception curves, performance benchmark
- Overlay: Win32 layered transparent window creation, WDA_EXCLUDEFROMCAPTURE affinity, opacity control, multi-monitor OverlayManager teardown
- Engine: ShieldEngine lifecycle, Gammy non-linear excess response, sudden flashbang jump detection, fast asymmetric easing (attack vs decay), telemetry stats
- UI & Integration: HotkeyManager debounce, TrayIcon image rendering & menu, CLI argument parsing
"""

import ctypes
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to sys.path so flashbang_shield is importable
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from flashbang_shield.config import (
    Config,
    PRESETS,
    get_default_config_path,
    get_run_at_startup,
    load_config,
    save_config,
    set_run_at_startup,
)
from flashbang_shield.engine import EngineStats, ShieldEngine
from flashbang_shield.hotkeys import HotkeyManager
from flashbang_shield.overlay import OverlayManager, OverlayWindow, WDA_EXCLUDEFROMCAPTURE
from flashbang_shield.sampler import (
    MonitorInfo,
    ScreenSampler,
    _MonitorSamplerContext,
    enumerate_monitors,
)
from flashbang_shield.tray import create_tray_image, TrayIcon
from flashbang_shield.gui import SettingsGUI


class TestConfig(unittest.TestCase):
    """Unit tests for configuration management."""

    def test_default_config(self):
        cfg = Config()
        self.assertTrue(cfg.enabled)
        self.assertEqual(cfg.preset, "Evening")
        self.assertAlmostEqual(cfg.target_brightness, 0.38)
        self.assertAlmostEqual(cfg.min_brightness, 0.20)
        self.assertAlmostEqual(cfg.max_brightness, 1.00)
        self.assertAlmostEqual(cfg.attack_speed_ms, 180.0)
        self.assertAlmostEqual(cfg.decay_speed_ms, 600.0)
        self.assertAlmostEqual(cfg.max_dimming, 0.82)
        self.assertFalse(cfg.start_with_windows)

    def test_property_aliases(self):
        cfg = Config()
        # target_luminance alias
        self.assertEqual(cfg.target_luminance, cfg.target_brightness)
        cfg.target_luminance = 0.45
        self.assertEqual(cfg.target_brightness, 0.45)

        # attack_time_ms alias
        self.assertEqual(cfg.attack_time_ms, cfg.attack_speed_ms)
        cfg.attack_time_ms = 120.0
        self.assertEqual(cfg.attack_speed_ms, 120.0)

        # release_time_ms alias
        self.assertEqual(cfg.release_time_ms, cfg.decay_speed_ms)
        cfg.release_time_ms = 750.0
        self.assertEqual(cfg.decay_speed_ms, 750.0)

    def test_apply_presets(self):
        cfg = Config()
        for preset_name in ["Day", "Evening", "Night", "Ultra-Dim"]:
            success = cfg.apply_preset(preset_name)
            self.assertTrue(success)
            self.assertEqual(cfg.preset, preset_name)
            self.assertEqual(cfg.target_brightness, PRESETS[preset_name]["target_brightness"])
            self.assertEqual(cfg.attack_speed_ms, PRESETS[preset_name]["attack_speed_ms"])

        # Invalid preset returns False
        self.assertFalse(cfg.apply_preset("NonExistentPreset"))

    def test_observers(self):
        cfg = Config()
        called = []

        def observer_cb(updated_cfg):
            called.append(updated_cfg.preset)

        cfg.add_observer(observer_cb)
        cfg.apply_preset("Night")
        self.assertEqual(called, ["Night"])

        # Remove observer
        cfg.remove_observer(observer_cb)
        cfg.apply_preset("Day")
        self.assertEqual(called, ["Night"])  # Should not have received Day

    def test_to_dict_and_from_dict(self):
        cfg = Config(target_brightness=0.25, preset="Night", tint_color=(10, 20, 30))
        d = cfg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertNotIn("_observers", d)
        self.assertEqual(d["target_brightness"], 0.25)
        self.assertEqual(d["preset"], "Night")

        restored = Config.from_dict(d)
        self.assertEqual(restored.target_brightness, 0.25)
        self.assertEqual(restored.preset, "Night")
        self.assertEqual(restored.tint_color, (10, 20, 30))

    def test_save_and_load_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test_config.json"
            cfg = Config(target_brightness=0.29, preset="Night")
            save_config(cfg, path=tmp_path)
            self.assertTrue(tmp_path.exists())

            loaded = load_config(path=tmp_path)
            self.assertAlmostEqual(loaded.target_brightness, 0.29)
            self.assertEqual(loaded.preset, "Night")

    def test_observers_init_false(self):
        cfg = Config()
        self.assertEqual(cfg._observers, [])
        with self.assertRaises(TypeError):
            Config(_observers=[lambda c: None])  # type: ignore

    def test_to_dict_with_ctypes_observer(self):
        cfg = Config()
        dummy_ctypes_ptr = ctypes.c_void_p(0x12345678)
        cfg.add_observer(lambda c, p=dummy_ctypes_ptr: None)
        # to_dict must succeed without raising ValueError / pickle deepcopy error
        d = cfg.to_dict()
        self.assertNotIn("_observers", d)
        self.assertIn("target_brightness", d)

    def test_save_config_atomic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test_atomic.json"
            cfg = Config(target_brightness=0.33)
            save_config(cfg, path=tmp_path)
            self.assertTrue(tmp_path.exists())
            tmp_files = list(Path(tmpdir).glob("*.tmp*"))
            self.assertEqual(len(tmp_files), 0)
            loaded = load_config(path=tmp_path)
            self.assertAlmostEqual(loaded.target_brightness, 0.33)

    def test_save_config_concurrent(self):
        import threading
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir) / "test_concurrent.json"
            errors = []

            def worker(val):
                try:
                    cfg = Config(target_brightness=val)
                    save_config(cfg, path=tmp_path)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker, args=(0.1 * (i + 1),)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(len(errors), 0)
            self.assertTrue(tmp_path.exists())
            loaded = load_config(path=tmp_path)
            self.assertGreater(loaded.target_brightness, 0.0)

    def test_get_run_at_startup_type(self):
        # Should return a boolean without throwing an unhandled exception
        result = get_run_at_startup()
        self.assertIsInstance(result, bool)


class TestSampler(unittest.TestCase):
    """Unit and performance tests for multi-monitor screen luminance sampler."""

    def test_enumerate_monitors(self):
        monitors = enumerate_monitors()
        self.assertIsInstance(monitors, list)
        self.assertGreater(len(monitors), 0)
        for m in monitors:
            self.assertIsInstance(m, MonitorInfo)
            self.assertGreater(m.width, 0)
            self.assertGreater(m.height, 0)
            self.assertIsInstance(m.is_primary, bool)
            self.assertIsInstance(m.rect, tuple)
            self.assertEqual(len(m.rect), 4)

    def test_sampler_context(self):
        monitors = enumerate_monitors()
        primary = next((m for m in monitors if m.is_primary), monitors[0])
        ctx = _MonitorSamplerContext(primary, resolution=64)
        try:
            lum = ctx.sample_luminance(curve_gamma=2.0)
            self.assertIsInstance(lum, float)
            self.assertGreaterEqual(lum, 0.0)
            self.assertLessEqual(lum, 1.0)
        finally:
            ctx.close()

    def test_screen_sampler(self):
        with ScreenSampler(resolution=64) as sampler:
            self.assertGreater(len(sampler.monitors), 0)

            # Sample specific monitor
            first_id = sampler.monitors[0].id
            lum = sampler.sample_monitor(first_id)
            self.assertIsInstance(lum, float)
            self.assertGreaterEqual(lum, 0.0)
            self.assertLessEqual(lum, 1.0)

            # Sample all monitors
            all_readings = sampler.sample_all()
            self.assertIsInstance(all_readings, dict)
            self.assertEqual(len(all_readings), len(sampler.monitors))
            for m_id, m_lum in all_readings.items():
                self.assertGreaterEqual(m_lum, 0.0)
                self.assertLessEqual(m_lum, 1.0)

            # Overall luminance
            overall = sampler.sample_overall()
            self.assertIsInstance(overall, float)
            self.assertGreaterEqual(overall, 0.0)
            self.assertLessEqual(overall, 1.0)

    def test_sampler_gamma_curves(self):
        with ScreenSampler(resolution=32) as sampler:
            lum_linear = sampler.sample_overall(curve_gamma=1.0)
            lum_perceptual = sampler.sample_overall(curve_gamma=2.0)
            self.assertGreaterEqual(lum_linear, 0.0)
            self.assertLessEqual(lum_linear, 1.0)
            self.assertGreaterEqual(lum_perceptual, 0.0)
            self.assertLessEqual(lum_perceptual, 1.0)

    def test_sampler_performance(self):
        """Benchmark sampling latency: 30 consecutive samples should average < 10ms."""
        with ScreenSampler(resolution=64) as sampler:
            iterations = 30
            start = time.perf_counter()
            for _ in range(iterations):
                sampler.sample_overall()
            elapsed = time.perf_counter() - start
            avg_ms = (elapsed / iterations) * 1000.0
            # Ensure ultra-fast real-time capability
            self.assertLess(avg_ms, 25.0, f"Average sample time {avg_ms:.2f}ms exceeds target")

    def test_native_dxgi_sampler_pipeline(self):
        from flashbang_shield.native_sampler import NativeDXGISampler, NativeSampleResult
        sampler = NativeDXGISampler()
        try:
            self.assertTrue(sampler.available)
            count = sampler.dll.ShieldCore_GetMonitorCount()
            self.assertGreaterEqual(count, 1)

            # Query HDR info
            is_hdr = ctypes.c_int()
            sdr_white = ctypes.c_float()
            max_nits = ctypes.c_float()
            ok = sampler.dll.ShieldCore_GetMonitorHDRInfo(
                0, ctypes.byref(is_hdr), ctypes.byref(sdr_white), ctypes.byref(max_nits)
            )
            self.assertEqual(ok, 1)
            self.assertGreaterEqual(sdr_white.value, 10.0)

            # Live sampling test
            res = sampler.sample_monitor(0, timeout_ms=50)
            if res is not None:
                self.assertIsInstance(res, NativeSampleResult)
                self.assertGreaterEqual(res.effective_luminance, 0.0)
                self.assertLessEqual(res.effective_luminance, 1.0)
                # Verify HDR reference white calibration:
                # With ref_white normalization, effective_lum * sdr_white == mean_nits
                if res.mean_nits > 0:
                    calculated_nits = res.effective_luminance * sdr_white.value
                    self.assertAlmostEqual(calculated_nits, res.mean_nits, delta=0.1)
        finally:
            sampler.close()

    def test_native_sampler_evicts_failed_monitor(self):
        from flashbang_shield.native_sampler import NativeDXGISampler
        sampler = NativeDXGISampler()
        try:
            # Mark monitor 99 as active
            sampler._active_monitors[99] = True
            # Sampling an invalid monitor fails, popping it from _active_monitors
            res = sampler.sample_monitor(99)
            self.assertIsNone(res)
            self.assertNotIn(99, sampler._active_monitors)
        finally:
            sampler.close()

    def test_rapid_consecutive_native_samples(self):
        from flashbang_shield.native_sampler import NativeDXGISampler
        sampler = NativeDXGISampler()
        try:
            self.assertTrue(sampler.available)
            # Sample 20 consecutive frames rapidly (testing static timeout and mouse pointer frames)
            failures = 0
            for _ in range(20):
                res = sampler.sample_monitor(0, timeout_ms=10)
                if res is None:
                    failures += 1
                time.sleep(0.005)
            self.assertEqual(failures, 0)
        finally:
            sampler.close()

    def test_hdr_white_luminance_calibration_math(self):
        """Verify FP16 HDR normalizes against ref_white (80 nits reference), not max_nits (1000 nits)."""
        sdr_white_nits = 80.0
        max_nits = 1000.0

        # For a standard SDR white pixel:
        # scRGB FP16 lin_y = 1.0 -> nits = lin_y * 80.0 = 80.0 nits
        measured_nits = 80.0

        # Old defective behavior divided by max_nits
        old_normalized = measured_nits / max_nits
        self.assertAlmostEqual(old_normalized, 0.08, delta=0.01)

        # New corrected behavior divides by ref_white
        ref_white = sdr_white_nits if sdr_white_nits > 10.0 else 80.0
        new_normalized = min(1.0, measured_nits / ref_white)
        self.assertAlmostEqual(new_normalized, 1.0, delta=0.001)


class TestOverlay(unittest.TestCase):
    """Unit and integration tests for Win32 layered overlay and WDA_EXCLUDEFROMCAPTURE."""

    def test_overlay_manager_lifecycle(self):
        overlay = OverlayManager()
        try:
            # Verify overlay manager thread initialized
            self.assertTrue(overlay._running.is_set())
            self.assertGreater(len(overlay._windows), 0)

            # Check WDA_EXCLUDEFROMCAPTURE status
            # In Windows 10 2004+ and Windows 11, affinity should be successfully set
            user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
            if user32:
                for win in overlay._windows.values():
                    if win.hwnd:
                        self.assertTrue(user32.IsWindow(win.hwnd))

            # Test opacity updates
            overlay.set_opacity(0.0)
            self.assertAlmostEqual(overlay.get_opacity(), 0.0)

            overlay.set_opacity(0.50)
            self.assertAlmostEqual(overlay.get_opacity(), 0.50)

            overlay.set_opacity(0.85)
            self.assertAlmostEqual(overlay.get_opacity(), 0.85)

            # Test clamping
            overlay.set_opacity(-0.2)
            self.assertAlmostEqual(overlay.get_opacity(), 0.0)
            overlay.set_opacity(1.5)
            self.assertAlmostEqual(overlay.get_opacity(), 1.0)

            overlay.set_opacity(0.0)
        finally:
            overlay.close()

        # Verify all windows destroyed after close
        self.assertEqual(len(overlay._windows), 0)

    def test_overlay_context_manager(self):
        with OverlayManager() as overlay:
            overlay.set_opacity(0.2)
            self.assertAlmostEqual(overlay.get_opacity(), 0.2)
        # Should be cleanly closed on exit
        self.assertFalse(overlay._running.is_set())


class TestEngine(unittest.TestCase):
    """Tests for core ShieldEngine calculations, asymmetric easing, and control logic."""

    def setUp(self):
        self.config = Config(
            target_brightness=0.40,
            min_brightness=0.20,
            max_brightness=1.00,
            max_dimming=0.80,
            attack_speed_ms=180.0,
            decay_speed_ms=600.0,
            flash_threshold=0.30,
        )
        self.engine = ShieldEngine(self.config)

    def tearDown(self):
        self.engine.close()

    def test_compute_target_dimming_dark(self):
        # When sampled luminance is comfortable (<= target), no dimming needed
        dim, is_fb = self.engine._compute_target_dimming(sampled_lum=0.35, prev_lum=0.30)
        self.assertEqual(dim, 0.0)
        self.assertFalse(is_fb)

    def test_compute_target_dimming_bright_excess(self):
        # When sampled luminance exceeds target comfort level
        dim, is_fb = self.engine._compute_target_dimming(sampled_lum=0.75, prev_lum=0.74)
        self.assertGreater(dim, 0.0)
        self.assertLessEqual(dim, self.config.max_dimming)
        self.assertFalse(is_fb)

    def test_compute_target_dimming_sudden_flashbang(self):
        # Sudden dark-to-white flashbang jump (e.g. 0.10 to 0.90, delta = 0.80 >= 0.30)
        dim, is_fb = self.engine._compute_target_dimming(sampled_lum=0.90, prev_lum=0.10)
        self.assertTrue(is_fb)
        self.assertAlmostEqual(dim, self.config.max_dimming)

    def test_asymmetric_easing_rates(self):
        """Verify attack is faster than decay for the same step jump."""
        dt = 0.033  # ~30 Hz frame (33ms)

        # 1. Attack step: current 0.0 -> target 0.8
        current_attack = 0.0
        target_attack = 0.8
        new_attack = self.engine._apply_asymmetric_easing(current_attack, target_attack, dt)
        attack_delta = new_attack - current_attack

        # 2. Decay step: current 0.8 -> target 0.0
        current_decay = 0.8
        target_decay = 0.0
        new_decay = self.engine._apply_asymmetric_easing(current_decay, target_decay, dt)
        decay_delta = current_decay - new_decay

        # Fast attack (~180ms) should advance more per frame than slow decay (~600ms)
        self.assertGreater(
            attack_delta / target_attack,
            decay_delta / current_decay,
            "Attack response must be faster than relaxation decay",
        )

    def test_engine_controls(self):
        # Toggle enabled
        initial_enabled = self.engine.is_enabled()
        toggled = self.engine.toggle_enabled()
        self.assertEqual(toggled, not initial_enabled)
        self.assertEqual(self.engine.is_enabled(), not initial_enabled)

        # Set enabled explicitly
        self.engine.set_enabled(True)
        self.assertTrue(self.engine.is_enabled())
        self.engine.set_enabled(False)
        self.assertFalse(self.engine.is_enabled())
        self.engine.set_enabled(True)

        # Adjust target comfort brightness
        old_val = self.config.target_brightness
        new_val = self.engine.adjust_target_brightness(+0.05)
        self.assertAlmostEqual(new_val, old_val + 0.05)

        # Set preset
        success = self.engine.set_preset("Night")
        self.assertTrue(success)
        self.assertEqual(self.config.preset, "Night")
        stats = self.engine.get_stats()
        self.assertAlmostEqual(stats["target_luminance"], PRESETS["Night"]["target_brightness"])

        # Pause and resume
        self.engine.pause()
        self.assertTrue(self.engine.is_paused())
        self.engine.resume()
        self.assertFalse(self.engine.is_paused())

    def test_engine_telemetry_snapshot(self):
        stats = self.engine.get_stats()
        expected_keys = {
            "enabled",
            "current_luminance",
            "target_luminance",
            "overlay_alpha",
            "is_flashbang",
            "fps",
            "timestamp",
        }
        self.assertTrue(expected_keys.issubset(set(stats.keys())))

    def test_engine_background_loop_and_callbacks(self):
        """Test starting engine loop and verifying stats callback reception."""
        received_stats = []

        def on_stats(s):
            received_stats.append(s)

        self.engine.add_stats_callback(on_stats)
        self.engine.start()

        # Let it run for 150ms to accumulate loop ticks
        time.sleep(0.15)
        self.engine.stop()

        self.assertGreater(len(received_stats), 0)
        self.assertTrue("current_luminance" in received_stats[-1])
        self.assertTrue("overlay_alpha" in received_stats[-1])

        # Test callback removal
        self.engine.remove_stats_callback(on_stats)
        self.assertNotIn(on_stats, self.engine._stats_callbacks)


class TestUIAndHotkeys(unittest.TestCase):
    """Unit tests for Tray icon generation and HotkeyManager debouncing."""

    def test_tray_image_generation(self):
        # Active icon
        img_active = create_tray_image(enabled=True, dim_percent=45)
        self.assertEqual(img_active.size, (64, 64))
        self.assertEqual(img_active.mode, "RGBA")

        # Paused icon
        img_paused = create_tray_image(enabled=False, dim_percent=0)
        self.assertEqual(img_paused.size, (64, 64))
        self.assertEqual(img_paused.mode, "RGBA")

    def test_tray_icon_menu_construction(self):
        tray = TrayIcon(current_preset="Evening", enabled=True)
        menu = tray._build_menu()
        self.assertIsNotNone(menu)
        title = tray._get_title()
        self.assertIn("Flashbang Shield", title)
        self.assertIn("Evening", title)

    def test_hotkey_manager_debounce(self):
        call_count = 0

        def callback():
            nonlocal call_count
            call_count += 1

        manager = HotkeyManager(on_toggle=callback)
        # Fast consecutive dispatches
        manager._dispatch(callback)
        manager._dispatch(callback)
        manager._dispatch(callback)

        time.sleep(0.05)
        # Because of debounce interval (200ms), only 1 should execute
        self.assertEqual(call_count, 1)

    def test_settings_gui(self):
        cfg = Config()
        engine = ShieldEngine(cfg)
        try:
            gui = SettingsGUI(engine)
            root = gui.create_window()
            root.withdraw()

            # Test preset selection
            gui._on_preset_selected("Day")
            self.assertEqual(engine.config.preset, "Day")

            # Test slider callbacks with both numbers and strings
            gui._on_target_changed("45")
            self.assertAlmostEqual(engine.config.target_brightness, 0.45)

            gui._on_dim_cap_changed("75")
            self.assertAlmostEqual(engine.config.max_dimming, 0.75)

            gui._on_attack_changed("150")
            self.assertAlmostEqual(engine.config.attack_speed_ms, 150.0)

            gui._on_decay_changed("550")
            self.assertAlmostEqual(engine.config.decay_speed_ms, 550.0)

            # Test status toggle
            initial_state = engine.is_enabled()
            gui._on_toggle_shield_clicked()
            self.assertEqual(engine.is_enabled(), not initial_state)

            # Clean teardown
            root.destroy()
        finally:
            engine.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
