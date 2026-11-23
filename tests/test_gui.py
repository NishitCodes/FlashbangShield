"""Unit and integration tests for Flashbang Shield Fluent GUI & Telemetry Bridge."""

from __future__ import annotations

import os
import sys
import unittest

# Ensure offscreen Qt platform for headless CI / unit test runner
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from flashbang_shield.config import Config, PRESETS
from flashbang_shield.engine import ShieldEngine
from flashbang_shield.gui import (
    FlashbangDashboard,
    MiniHudWindow,
    MonitorCard,
    PresetCard,
    SliderSpinRow,
    create_gui_runtime,
)
from flashbang_shield.gui_bridge import TelemetryBridge, TelemetrySample
from flashbang_shield.widgets.telemetry_graphs import OscilloscopeWidget, SoftplusCurveWidget


class TestTelemetryBridge(unittest.TestCase):
    """Test the lock-free/micro-lock ring buffer and telemetry bridge."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_sample_creation(self):
        sample = TelemetrySample(
            timestamp=100.0,
            luminance=0.75,
            target_luminance=0.38,
            overlay_alpha=0.42,
            is_flashbang=True,
            enabled=True,
            fps=59.9,
        )
        self.assertEqual(sample.luminance, 0.75)
        self.assertEqual(sample.target_luminance, 0.38)
        self.assertEqual(sample.overlay_alpha, 0.42)
        self.assertTrue(sample.is_flashbang)
        self.assertTrue(sample.enabled)
        self.assertAlmostEqual(sample.fps, 59.9)

    def test_bridge_buffer_and_snapshot(self):
        bridge = TelemetryBridge(history_seconds=2.0, sample_rate_hz=60.0)

        # Feed 10 synthetic engine frames
        for i in range(10):
            bridge.on_engine_stats({
                "timestamp": 100.0 + i * 0.016,
                "current_luminance": 0.1 * i,
                "target_luminance": 0.38,
                "overlay_alpha": 0.05 * i,
                "is_flashbang": (i == 5),
                "enabled": True,
                "fps": 60.0,
            })

        seq, latest, history, flash_ts = bridge.snapshot()
        self.assertEqual(seq, 10)
        self.assertEqual(len(history), 10)
        self.assertAlmostEqual(latest.luminance, 0.9)
        self.assertEqual(flash_ts, 100.0 + 5 * 0.016)

    def test_bridge_state_change_signal(self):
        bridge = TelemetryBridge()
        events = []
        bridge.external_state_changed.connect(lambda: events.append(True))

        # Push frame 1 (baseline)
        bridge.on_engine_stats({"enabled": True, "target_luminance": 0.38})
        # Push frame 2 with changed state
        bridge.on_engine_stats({"enabled": False, "target_luminance": 0.38})

        self.assertGreaterEqual(len(events), 1)


class TestTelemetryWidgets(unittest.TestCase):
    """Test 60 FPS QPainter oscilloscope and Log-Softplus transfer curve widgets."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_softplus_math(self):
        # Numerical stability checks
        val_huge = SoftplusCurveWidget.evaluate_softplus(10.0, 0.38, 0.82)
        self.assertLessEqual(val_huge, 0.82)

        val_tiny = SoftplusCurveWidget.evaluate_softplus(0.0, 0.50, 0.82)
        self.assertGreaterEqual(val_tiny, 0.0)
        self.assertLess(val_tiny, 0.01)

        val_normal = SoftplusCurveWidget.evaluate_softplus(0.70, 0.38, 0.82)
        self.assertGreater(val_normal, 0.1)

    def test_softplus_widget_rendering(self):
        widget = SoftplusCurveWidget()
        widget.resize(240, 180)
        widget.set_parameters(0.40, 0.80)
        widget.set_live_point(0.65, 0.35)

        # Render onto a test pixmap to ensure zero paint crashes
        pm = QPixmap(240, 180)
        widget.render(pm)
        self.assertFalse(pm.isNull())

    def test_oscilloscope_widget_rendering(self):
        widget = OscilloscopeWidget(max_points=60)
        widget.resize(500, 220)

        samples = [
            TelemetrySample(
                timestamp=100.0 + i * 0.016,
                luminance=0.3 + 0.01 * i,
                target_luminance=0.38,
                overlay_alpha=0.1 + 0.005 * i,
                is_flashbang=(i == 10),
                enabled=True,
                fps=60.0,
            )
            for i in range(30)
        ]

        widget.update_data(samples[-1], samples, 100.0 + 10 * 0.016)
        pm = QPixmap(500, 220)
        widget.render(pm)
        self.assertFalse(pm.isNull())


class TestDashboardAndCards(unittest.TestCase):
    """Test Fluent dashboard pages, preset cards, sliders, and Mini-HUD."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.config = Config()
        self.engine = ShieldEngine(self.config)

    def test_preset_card(self):
        card = PresetCard("Day", "Bright rooms", 55, 65)
        selected = []
        card.selected.connect(lambda name: selected.append(name))
        card.set_active(True)
        self.assertTrue(card._active)
        card.set_active(False)
        self.assertFalse(card._active)

    def test_slider_spin_row(self):
        row = SliderSpinRow("Target", "Comfort ceiling", 10.0, 90.0, 1.0, "%", scale=10)
        values = []
        row.value_changed.connect(lambda v: values.append(v))

        row.set_value(45.0)
        self.assertEqual(row.slider.value(), 450)
        self.assertEqual(row.spin.value(), 45.0)

    def test_mini_hud(self):
        hud = MiniHudWindow()
        sample = TelemetrySample(
            timestamp=100.0,
            luminance=0.50,
            target_luminance=0.38,
            overlay_alpha=0.25,
            is_flashbang=False,
            enabled=True,
            fps=60.0,
        )
        hud.update_sample(sample)
        self.assertEqual(hud.dim_bar.value(), 25)

        sample_flash = TelemetrySample(
            timestamp=101.0,
            luminance=0.95,
            target_luminance=0.38,
            overlay_alpha=0.75,
            is_flashbang=True,
            enabled=True,
            fps=60.0,
        )
        hud.update_sample(sample_flash)
        self.assertEqual(hud.dim_bar.value(), 75)

    def test_dashboard_full_lifecycle(self):
        saved_configs = []
        app, bridge, dashboard = create_gui_runtime(
            engine=self.engine,
            config=self.config,
            on_config_saved=lambda cfg: saved_configs.append(cfg),
            start_minimized=True,
        )

        self.assertIsNotNone(dashboard)
        self.assertEqual(dashboard.windowTitle(), "Flashbang Shield")

        # Test preset selection via UI
        dashboard._on_preset_selected("Night")
        self.assertEqual(self.config.preset, "Night")
        self.assertAlmostEqual(self.config.target_brightness, 0.22)

        # Test toggle shield
        initial_state = self.engine.is_enabled()
        dashboard._on_toggle_shield()
        self.assertEqual(self.engine.is_enabled(), not initial_state)

        # Test render tick
        dashboard.show_and_focus()
        dashboard._on_render_tick()

        # Test Mini-HUD toggle
        dashboard.toggle_mini_hud()
        self.assertTrue(dashboard.mini_hud.isVisible())
        dashboard.toggle_mini_hud()
        self.assertFalse(dashboard.mini_hud.isVisible())

        # Cleanup
        dashboard.hide()


if __name__ == "__main__":
    unittest.main()
