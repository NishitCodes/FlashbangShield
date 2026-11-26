"""Flashbang Shield — Windows 11 Fluent Design Dashboard, Mini-HUD & Controls."""

from __future__ import annotations

import ctypes
from pathlib import Path
import sys
from typing import Any, Callable, Dict, List, Optional, Protocol

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QMouseEvent,
    QPixmap,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon,
    FluentWindow,
    NavigationItemPosition,
    PrimaryPushButton,
    PushButton,
    Slider,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    Theme,
    TransparentToolButton,
    setTheme,
    setThemeColor,
)

from flashbang_shield.config import Config, PRESETS, get_run_at_startup, set_run_at_startup
from flashbang_shield.gui_bridge import TelemetryBridge, TelemetrySample
from flashbang_shield.widgets.telemetry_graphs import OscilloscopeWidget, SoftplusCurveWidget

# Win32 Display Affinity constant to exclude UI windows from DXGI capture loops
WDA_EXCLUDEFROMCAPTURE = 0x00000011


def exclude_window_from_capture(widget: QWidget) -> None:
    """Prevent DXGI Desktop Duplication from capturing this Qt window's pixels."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(widget.winId())
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except Exception:
        pass


class EngineProtocol(Protocol):
    """Structural typing contract matching flashbang_shield.engine.ShieldEngine."""

    def is_enabled(self) -> bool: ...
    def set_enabled(self, value: bool) -> None: ...
    def toggle_enabled(self) -> bool: ...
    def set_preset(self, preset_name: str) -> bool: ...
    def get_stats(self) -> Dict[str, Any]: ...
    def add_stats_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None: ...
    def remove_stats_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None: ...


class PresetCard(CardWidget):
    """Glassmorphic preset selector card with amber active-border glow."""

    selected = Signal(str)

    def __init__(
        self,
        name: str,
        subtitle: str,
        target_pct: int,
        cap_pct: int,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.preset_name = name
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(76)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        top = QHBoxLayout()
        self.title_lbl = StrongBodyLabel(name, self)
        self.badge_lbl = CaptionLabel(f"T:{target_pct}%", self)
        top.addWidget(self.title_lbl)
        top.addStretch(1)
        top.addWidget(self.badge_lbl)

        self.sub_lbl = CaptionLabel(f"{subtitle} · Cap {cap_pct}%", self)
        self.sub_lbl.setTextColor(QColor(156, 163, 175), QColor(156, 163, 175))

        layout.addLayout(top)
        layout.addWidget(self.sub_lbl)

        self.clicked.connect(lambda: self.selected.emit(self.preset_name))
        self.set_active(False)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.setStyleSheet(
                """
                PresetCard {
                    background-color: rgba(245, 158, 11, 0.14);
                    border: 1.5px solid #F59E0B;
                    border-radius: 10px;
                }
                """
            )
            self.badge_lbl.setStyleSheet("color: #FBBF24; font-weight: 600;")
        else:
            self.setStyleSheet(
                """
                PresetCard {
                    background-color: rgba(32, 35, 44, 0.68);
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 10px;
                }
                PresetCard:hover {
                    background-color: rgba(45, 49, 62, 0.80);
                    border: 1px solid rgba(245, 158, 11, 0.45);
                }
                """
            )
            self.badge_lbl.setStyleSheet("color: #94A3B8;")


class SliderSpinRow(QWidget):
    """Synchronized tactile Fluent slider with live numeric readout (no clipped text/spin boxes)."""

    value_changed = Signal(float)

    def __init__(
        self,
        label: str,
        description: str,
        min_val: float,
        max_val: float,
        step: float,
        suffix: str,
        scale: int = 1,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._scale = scale
        self._suffix = suffix
        self._syncing = False
        self._current_value: float = min_val

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(6)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title_box.addWidget(StrongBodyLabel(label, self))
        desc_lbl = CaptionLabel(description, self)
        desc_lbl.setTextColor(QColor(148, 163, 184), QColor(148, 163, 184))
        title_box.addWidget(desc_lbl)
        header.addLayout(title_box)
        header.addStretch(1)

        # Clean typography numeric readout (replaces clipped text/spin boxes)
        self.val_lbl = StrongBodyLabel(self._format_value(min_val), self)
        self.val_lbl.setStyleSheet(
            "color: #F59E0B; font-weight: 600; font-size: 13px; font-family: 'Cascadia Mono', 'Segoe UI';"
        )
        header.addWidget(self.val_lbl, alignment=Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(header)

        self.slider = Slider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(int(round(min_val * scale)), int(round(max_val * scale)))
        self.slider.valueChanged.connect(self._on_slider_changed)
        outer.addWidget(self.slider)

    def _format_value(self, val: float) -> str:
        if self._suffix == "%":
            return f"{val:.0f} %"
        elif self._suffix == "ms":
            return f"{val:.0f} ms"
        else:
            return f"{val:.1f} {self._suffix}"

    def value(self) -> float:
        return self._current_value

    def set_value(self, value: float) -> None:
        self._syncing = True
        self._current_value = value
        try:
            self.slider.setValue(int(round(value * self._scale)))
            self.val_lbl.setText(self._format_value(value))
        finally:
            self._syncing = False

    def _on_slider_changed(self, raw: int) -> None:
        if self._syncing:
            return
        val = raw / self._scale
        self._current_value = val
        self.val_lbl.setText(self._format_value(val))
        self.value_changed.emit(val)

    @property
    def spin(self):
        """Compatibility helper so legacy callers reading spin.value() continue to work."""
        class _SpinCompat:
            def __init__(self, parent_row: SliderSpinRow):
                self._parent = parent_row

            def value(self) -> float:
                return self._parent._current_value

            def setValue(self, v: float) -> None:
                self._parent.set_value(float(v))

        return _SpinCompat(self)


class MonitorCard(CardWidget):
    """Per-display topology card displaying resolution, HDR pipeline, and live luminance."""

    def __init__(
        self,
        device_name: str,
        resolution: str,
        refresh_hz: int,
        is_primary: bool,
        hdr_mode: str = "SDR 80-nit",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.device_name = device_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        top = QHBoxLayout()
        title = StrongBodyLabel(f"{device_name}{' (Primary)' if is_primary else ''}", self)
        self.hdr_badge = CaptionLabel(hdr_mode, self)
        self.hdr_badge.setStyleSheet(
            "background: rgba(56, 189, 248, 0.16); color: #38BDF8; "
            "padding: 2px 8px; border-radius: 6px; font-weight: 600;"
        )
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.hdr_badge)
        layout.addLayout(top)

        meta = CaptionLabel(f"{resolution} @ {refresh_hz} Hz · DXGI Desktop Duplication", self)
        meta.setTextColor(QColor(148, 163, 184), QColor(148, 163, 184))
        layout.addWidget(meta)

        self.luma_bar = QProgressBar(self)
        self.luma_bar.setRange(0, 100)
        self.luma_bar.setFixedHeight(6)
        self.luma_bar.setTextVisible(False)
        self.luma_bar.setStyleSheet(
            """
            QProgressBar { background: rgba(255,255,255,0.08); border-radius: 3px; }
            QProgressBar::chunk { background: #38BDF8; border-radius: 3px; }
            """
        )
        layout.addWidget(self.luma_bar)

        bottom = QHBoxLayout()
        self.metrics_lbl = CaptionLabel("Luma: 0% · Dimming: 0%", self)
        self.enable_switch = SwitchButton(self)
        self.enable_switch.setChecked(True)
        self.enable_switch.setOnText("Active")
        self.enable_switch.setOffText("Bypass")
        bottom.addWidget(self.metrics_lbl)
        bottom.addStretch(1)
        bottom.addWidget(self.enable_switch)
        layout.addLayout(bottom)

    def update_metrics(self, luma: float, alpha: float, hdr: Optional[bool] = None) -> None:
        self.luma_bar.setValue(int(luma * 100))
        self.metrics_lbl.setText(f"Luma: {luma * 100:.0f}% · Dimming: {alpha * 100:.0f}%")
        if hdr is not None:
            self.hdr_badge.setText("scRGB FP16 HDR" if hdr else "SDR 80-nit")


class MiniHudWindow(QWidget):
    """Floating frameless pill HUD pinned to desktop corner (excluded from DXGI capture)."""

    expand_requested = Signal()
    toggle_shield_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._drag_offset: Optional[QPoint] = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(42)

        pill = QFrame(self)
        pill.setObjectName("miniHudPill")
        pill.setStyleSheet(
            """
            #miniHudPill {
                background-color: rgba(18, 20, 26, 228);
                border: 1px solid rgba(245, 158, 11, 0.45);
                border-radius: 21px;
            }
            """
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(pill)

        row = QHBoxLayout(pill)
        row.setContentsMargins(12, 4, 8, 4)
        row.setSpacing(10)

        self.dot = QFrame(pill)
        self.dot.setFixedSize(10, 10)
        self.dot.setStyleSheet("background: #10B981; border-radius: 5px;")
        row.addWidget(self.dot)

        self.stat_lbl = CaptionLabel("DIM 0% · 60 FPS", pill)
        self.stat_lbl.setStyleSheet("color: #F8FAFC; font-family: 'Cascadia Mono'; font-weight: 600;")
        row.addWidget(self.stat_lbl)

        self.dim_bar = QProgressBar(pill)
        self.dim_bar.setRange(0, 100)
        self.dim_bar.setFixedSize(64, 6)
        self.dim_bar.setTextVisible(False)
        self.dim_bar.setStyleSheet(
            """
            QProgressBar { background: rgba(255,255,255,0.12); border-radius: 3px; }
            QProgressBar::chunk { background: #F59E0B; border-radius: 3px; }
            """
        )
        row.addWidget(self.dim_bar)

        self.pause_btn = TransparentToolButton(FluentIcon.PAUSE, pill)
        self.pause_btn.setFixedSize(26, 26)
        self.pause_btn.clicked.connect(self.toggle_shield_requested.emit)
        row.addWidget(self.pause_btn)

        expand_btn = TransparentToolButton(FluentIcon.UP, pill)
        expand_btn.setFixedSize(26, 26)
        expand_btn.clicked.connect(self.expand_requested.emit)
        row.addWidget(expand_btn)

        close_btn = TransparentToolButton(FluentIcon.CLOSE, pill)
        close_btn.setFixedSize(26, 26)
        close_btn.clicked.connect(self.hide)
        row.addWidget(close_btn)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        exclude_window_from_capture(self)
        # Snap to bottom-right work area on first show
        if self.pos() == QPoint(0, 0):
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.right() - self.width() - 20, geo.bottom() - self.height() - 20)

    def update_sample(self, sample: TelemetrySample) -> None:
        self.dim_bar.setValue(int(sample.overlay_alpha * 100))
        if sample.enabled:
            self.dot.setStyleSheet(
                f"background: {'#EF4444' if sample.is_flashbang else '#10B981'}; border-radius: 5px;"
            )
            self.stat_lbl.setText(f"DIM {sample.overlay_alpha * 100:2.0f}% · {sample.fps:4.0f} FPS")
            self.pause_btn.setIcon(FluentIcon.PAUSE)
        else:
            self.dot.setStyleSheet("background: #64748B; border-radius: 5px;")
            self.stat_lbl.setText("PAUSED")
            self.pause_btn.setIcon(FluentIcon.PLAY)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None


class FlashbangDashboard(FluentWindow):
    """Main Windows 11 Fluent Design dashboard for Flashbang Shield."""

    PRESET_DESCRIPTIONS = {
        "Day": "Bright rooms",
        "Evening": "Balanced indoor",
        "Night": "Dark rooms",
        "Ultra-Dim": "Pitch-black OLED",
    }

    def __init__(
        self,
        engine: EngineProtocol,
        config: Config,
        bridge: TelemetryBridge,
        get_startup_fn: Optional[Callable[[], bool]] = None,
        set_startup_fn: Optional[Callable[[bool], None]] = None,
        on_config_saved: Optional[Callable[[Config], None]] = None,
        on_hide_to_tray: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._config = config
        self._bridge = bridge
        self._get_startup_fn = get_startup_fn
        self._set_startup_fn = set_startup_fn
        self._on_config_saved = on_config_saved
        self._on_hide_to_tray = on_hide_to_tray
        self._last_rendered_seq: int = -1
        self._preset_cards: Dict[str, PresetCard] = {}
        self._monitor_cards: List[MonitorCard] = []

        # Fluent window styling & Windows 11 Mica
        setTheme(Theme.DARK)
        setThemeColor("#F59E0B")
        self.setWindowTitle("Flashbang Shield")
        self.resize(900, 680)
        self.setMinimumSize(780, 600)
        self.setMicaEffectEnabled(True)

        icon_path = Path(__file__).resolve().parent / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # Mini-HUD companion window
        self.mini_hud = MiniHudWindow()
        self.mini_hud.expand_requested.connect(self.show_and_focus)
        self.mini_hud.toggle_shield_requested.connect(self._on_toggle_shield)

        # Build navigation pages
        self._overview_page = self._build_overview_page()
        self._tuning_page = self._build_tuning_page()
        self._displays_page = self._build_displays_page()
        self._system_page = self._build_system_page()
        self._about_page = self._build_about_page()

        self.addSubInterface(self._overview_page, FluentIcon.SPEED_HIGH, "Overview")
        self.addSubInterface(self._tuning_page, FluentIcon.SETTING, "Tuning")
        self.addSubInterface(self._displays_page, FluentIcon.LAYOUT, "Displays")
        self.addSubInterface(
            self._system_page,
            FluentIcon.APPLICATION,
            "System",
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self._about_page,
            FluentIcon.INFO,
            "About",
            position=NavigationItemPosition.BOTTOM,
        )

        # Sync initial widget state from Config
        self._sync_controls_from_config()

        # Connect cross-thread bridge signals (tray + Win32 hotkeys)
        self._bridge.show_dashboard_requested.connect(self.show_and_focus)
        self._bridge.hide_dashboard_requested.connect(self.hide)
        self._bridge.toggle_mini_hud_requested.connect(self.toggle_mini_hud)
        self._bridge.external_state_changed.connect(self._sync_controls_from_config)
        self._bridge.quit_requested.connect(self._force_quit)

        # 60 FPS UI paint timer (active ONLY while dashboard or Mini-HUD is visible)
        self._render_timer = QTimer(self)
        self._render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._render_timer.setInterval(16)
        self._render_timer.timeout.connect(self._on_render_tick)

        # Register bridge callback with engine
        if hasattr(self._engine, "add_stats_callback"):
            self._engine.add_stats_callback(self._bridge.on_engine_stats)

    # ------------------------------------------------------------------ Pages
    def _wrap_scroll(self, content: QWidget, object_name: str) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setObjectName(object_name)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea, QWidget#scrollContent { background: transparent; }")
        content.setObjectName("scrollContent")
        scroll.setWidget(content)
        return scroll

    def _build_overview_page(self) -> QScrollArea:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        # Hero status bar
        hero = CardWidget(page)
        hero_row = QHBoxLayout(hero)
        hero_row.setContentsMargins(16, 12, 16, 12)

        status_col = QVBoxLayout()
        status_col.setSpacing(2)
        self._status_title = SubtitleLabel("Shield Active", hero)
        self._status_sub = CaptionLabel("DXGI Desktop Duplication · Adaptive Biological Easing", hero)
        self._status_sub.setTextColor(QColor(148, 163, 184), QColor(148, 163, 184))
        status_col.addWidget(self._status_title)
        status_col.addWidget(self._status_sub)
        hero_row.addLayout(status_col)
        hero_row.addStretch(1)

        hud_btn = PushButton(FluentIcon.PIN, "Mini-HUD", hero)
        hud_btn.clicked.connect(self.toggle_mini_hud)
        hero_row.addWidget(hud_btn)

        self._toggle_btn = PrimaryPushButton("Pause Shield", hero)
        self._toggle_btn.clicked.connect(self._on_toggle_shield)
        hero_row.addWidget(self._toggle_btn)
        layout.addWidget(hero)

        # 60 FPS Oscilloscope
        self._oscilloscope = OscilloscopeWidget(max_points=360, parent=page)
        layout.addWidget(self._oscilloscope)

        # Mid split: Presets grid (left) + Softplus Transfer Curve (right)
        mid_row = QHBoxLayout()
        mid_row.setSpacing(14)

        presets_card = CardWidget(page)
        presets_layout = QVBoxLayout(presets_card)
        presets_layout.setContentsMargins(14, 12, 14, 12)
        presets_layout.setSpacing(10)
        presets_layout.addWidget(StrongBodyLabel("Optical Presets", presets_card))

        grid = QGridLayout()
        grid.setSpacing(10)
        for idx, (name, params) in enumerate(PRESETS.items()):
            card = PresetCard(
                name=name,
                subtitle=self.PRESET_DESCRIPTIONS.get(name, "Custom"),
                target_pct=int(params["target_brightness"] * 100),
                cap_pct=int(params["max_dimming"] * 100),
                parent=presets_card,
            )
            card.selected.connect(self._on_preset_selected)
            self._preset_cards[name] = card
            grid.addWidget(card, idx // 2, idx % 2)
        presets_layout.addLayout(grid)
        mid_row.addWidget(presets_card, stretch=3)

        self._overview_curve = SoftplusCurveWidget(page)
        mid_row.addWidget(self._overview_curve, stretch=2)
        layout.addLayout(mid_row)
        layout.addStretch(1)

        return self._wrap_scroll(page, "overview_page")

    def _build_tuning_page(self) -> QScrollArea:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(SubtitleLabel("Dynamic Range & Biological Easing", page))

        self._tuning_curve = SoftplusCurveWidget(page)
        self._tuning_curve.setMinimumHeight(190)
        layout.addWidget(self._tuning_curve)

        sliders_card = CardWidget(page)
        s_layout = QVBoxLayout(sliders_card)
        s_layout.setContentsMargins(16, 14, 16, 14)
        s_layout.setSpacing(10)

        self._target_ctrl = SliderSpinRow(
            "Comfort Target Brightness",
            "Perceptual luminance ceiling before softplus compression engages",
            10.0, 90.0, 1.0, "%", scale=10, parent=sliders_card,
        )
        self._cap_ctrl = SliderSpinRow(
            "Dimming Suppression Cap",
            "Maximum protective overlay opacity during full-white glare",
            20.0, 95.0, 1.0, "%", scale=10, parent=sliders_card,
        )
        self._attack_ctrl = SliderSpinRow(
            "Fast Attack Speed",
            "Adaptive dimming ramp time constant after 25 ms reflex clamp",
            30.0, 500.0, 10.0, "ms", scale=1, parent=sliders_card,
        )
        self._decay_ctrl = SliderSpinRow(
            "Soft Decay Speed",
            "Biphasic relaxation time constant when returning to dark apps",
            150.0, 2000.0, 25.0, "ms", scale=1, parent=sliders_card,
        )

        self._target_ctrl.value_changed.connect(lambda v: self._update_config("target_brightness", v / 100.0))
        self._cap_ctrl.value_changed.connect(lambda v: self._update_config("max_dimming", v / 100.0))
        self._attack_ctrl.value_changed.connect(lambda v: self._update_config("attack_speed_ms", v))
        self._decay_ctrl.value_changed.connect(lambda v: self._update_config("decay_speed_ms", v))

        for ctrl in (self._target_ctrl, self._cap_ctrl, self._attack_ctrl, self._decay_ctrl):
            s_layout.addWidget(ctrl)

        layout.addWidget(sliders_card)
        layout.addStretch(1)
        return self._wrap_scroll(page, "tuning_page")

    def _build_displays_page(self) -> QScrollArea:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        top = QHBoxLayout()
        top.addWidget(SubtitleLabel("Multi-Monitor Topology", page))
        top.addStretch(1)
        self._per_mon_switch = SwitchButton(page)
        self._per_mon_switch.setOnText("Per-Monitor Independent")
        self._per_mon_switch.setOffText("Synchronized")
        self._per_mon_switch.setChecked(getattr(self._config, "per_monitor", True))
        self._per_mon_switch.checkedChanged.connect(lambda c: self._update_config("per_monitor", c))
        top.addWidget(self._per_mon_switch)
        layout.addLayout(top)

        screens = QGuiApplication.screens()
        primary = QGuiApplication.primaryScreen()
        grid = QGridLayout()
        grid.setSpacing(12)

        for idx, scr in enumerate(screens):
            geo = scr.geometry()
            card = MonitorCard(
                device_name=scr.name() or f"\\\\.\\DISPLAY{idx + 1}",
                resolution=f"{geo.width()}×{geo.height()}",
                refresh_hz=int(round(scr.refreshRate() or 60.0)),
                is_primary=(scr == primary),
                parent=page,
            )
            self._monitor_cards.append(card)
            grid.addWidget(card, idx // 2, idx % 2)

        layout.addLayout(grid)
        layout.addStretch(1)
        return self._wrap_scroll(page, "displays_page")

    def _build_system_page(self) -> QScrollArea:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(SubtitleLabel("System & Hotkeys", page))

        startup_card = CardWidget(page)
        row = QHBoxLayout(startup_card)
        row.setContentsMargins(16, 12, 16, 12)
        col = QVBoxLayout()
        col.addWidget(StrongBodyLabel("Launch at Windows Startup", startup_card))
        col.addWidget(CaptionLabel("Start minimized to system tray on sign-in", startup_card))
        row.addLayout(col)
        row.addStretch(1)
        self._startup_switch = SwitchButton(startup_card)
        if self._get_startup_fn is not None:
            self._startup_switch.setChecked(bool(self._get_startup_fn()))
        else:
            self._startup_switch.setChecked(getattr(self._config, "start_with_windows", False))
        self._startup_switch.checkedChanged.connect(self._on_startup_toggled)
        row.addWidget(self._startup_switch)
        layout.addWidget(startup_card)

        # Hotkey Reference Card
        hk_card = CardWidget(page)
        hk_layout = QVBoxLayout(hk_card)
        hk_layout.setContentsMargins(16, 12, 16, 12)
        hk_layout.setSpacing(8)
        hk_layout.addWidget(StrongBodyLabel("Global Win32 Hotkeys (0 ms RegisterHotKey)", hk_card))

        hotkeys = [
            ("Win + Alt + O", "Toggle Flashbang Shield active / paused"),
            ("Win + Alt + Up", "Increase comfort target brightness (+5%)"),
            ("Win + Alt + Down", "Decrease comfort target brightness (-5%)"),
        ]
        for combo, desc in hotkeys:
            r = QHBoxLayout()
            key_lbl = CaptionLabel(combo, hk_card)
            key_lbl.setStyleSheet(
                "background: rgba(255,255,255,0.09); padding: 3px 8px; "
                "border-radius: 5px; font-family: 'Cascadia Mono';"
            )
            r.addWidget(key_lbl)
            r.addWidget(BodyLabel(desc, hk_card))
            r.addStretch(1)
            hk_layout.addLayout(r)

        layout.addWidget(hk_card)
        layout.addStretch(1)
        return self._wrap_scroll(page, "system_page")

    def _build_about_page(self) -> QScrollArea:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 20)
        layout.setSpacing(14)

        layout.addWidget(SubtitleLabel("About Flashbang Shield", page))

        # Hero Card: Logo, Name, Version, Tagline & Badges
        hero_card = CardWidget(page)
        hero_layout = QVBoxLayout(hero_card)
        hero_layout.setContentsMargins(16, 16, 16, 16)
        hero_layout.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(14)

        icon_path = Path(__file__).resolve().parent / "assets" / "icon.png"
        if icon_path.exists():
            icon_lbl = QLabel(hero_card)
            pm = QPixmap(str(icon_path)).scaled(
                54, 54, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            icon_lbl.setPixmap(pm)
            icon_lbl.setFixedSize(54, 54)
            top_row.addWidget(icon_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        app_title = SubtitleLabel("Flashbang Shield", hero_card)
        app_sub = CaptionLabel(
            "Version 1.0.0 · Optical Glare & Flashbang Protection for Windows 11",
            hero_card,
        )
        app_sub.setTextColor(QColor(148, 163, 184), QColor(148, 163, 184))
        title_col.addWidget(app_title)
        title_col.addWidget(app_sub)
        top_row.addLayout(title_col)
        top_row.addStretch(1)
        hero_layout.addLayout(top_row)

        # Tech badges row
        badges_row = QHBoxLayout()
        badges_row.setSpacing(8)
        badges = [
            ("GPL-3.0 License", "rgba(59, 130, 246, 0.16)", "#60A5FA"),
            ("Windows 11 Fluent", "rgba(245, 158, 11, 0.16)", "#F59E0B"),
            ("60 FPS DXGI Pipeline", "rgba(56, 189, 248, 0.16)", "#38BDF8"),
            ("C SIMD Luminance Core", "rgba(16, 185, 129, 0.16)", "#10B981"),
        ]
        for text, bg, fg in badges:
            b = CaptionLabel(text, hero_card)
            b.setStyleSheet(
                f"background: {bg}; color: {fg}; padding: 3px 8px; "
                "border-radius: 6px; font-weight: 600;"
            )
            badges_row.addWidget(b)
        badges_row.addStretch(1)
        hero_layout.addLayout(badges_row)
        layout.addWidget(hero_card)

        # ⭐ Star & GitHub Redirect Card (Highlighted with Amber Visor glow)
        star_card = CardWidget(page)
        star_card.setObjectName("starCard")
        star_card.setStyleSheet(
            """
            #starCard {
                background-color: rgba(245, 158, 11, 0.08);
                border: 1.5px solid rgba(245, 158, 11, 0.50);
                border-radius: 10px;
            }
            #starCard:hover {
                background-color: rgba(245, 158, 11, 0.12);
                border: 1.5px solid rgba(245, 158, 11, 0.85);
            }
            """
        )
        star_layout = QVBoxLayout(star_card)
        star_layout.setContentsMargins(18, 16, 18, 16)
        star_layout.setSpacing(10)

        star_header = QHBoxLayout()
        star_title = StrongBodyLabel("⭐ Star the Repository on GitHub", star_card)
        star_title.setStyleSheet("color: #FBBF24; font-size: 15px; font-weight: 700;")
        star_header.addWidget(star_title)
        star_header.addStretch(1)
        star_layout.addLayout(star_header)

        star_desc = BodyLabel(
            "Flashbang Shield is free and open-source. If this tool saves your eyes from sudden "
            "blinding white browser tabs or flashbangs in games, please consider starring the repository on GitHub! "
            "Your star takes just one click, shows support for ongoing development, and helps more people find the project.",
            star_card,
        )
        star_desc.setWordWrap(True)
        star_desc.setTextColor(QColor(226, 232, 240), QColor(226, 232, 240))
        star_layout.addWidget(star_desc)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        star_btn = PrimaryPushButton(FluentIcon.GITHUB, "★ Star on GitHub", star_card)
        star_btn.setMinimumHeight(36)
        star_btn.clicked.connect(lambda: self._open_external_url("https://github.com/NishitCodes/FlashbangShield"))
        btn_row.addWidget(star_btn)

        profile_btn = PushButton(FluentIcon.PEOPLE, "Developer Profile (@NishitCodes)", star_card)
        profile_btn.setMinimumHeight(36)
        profile_btn.clicked.connect(lambda: self._open_external_url("https://github.com/NishitCodes"))
        btn_row.addWidget(profile_btn)

        issue_btn = PushButton(FluentIcon.FEEDBACK, "Report Issue", star_card)
        issue_btn.setMinimumHeight(36)
        issue_btn.clicked.connect(lambda: self._open_external_url("https://github.com/NishitCodes/FlashbangShield/issues"))
        btn_row.addWidget(issue_btn)

        btn_row.addStretch(1)
        star_layout.addLayout(btn_row)
        layout.addWidget(star_card)

        # Project Details Card
        info_card = CardWidget(page)
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(16, 14, 16, 14)
        info_layout.setSpacing(10)
        info_layout.addWidget(StrongBodyLabel("Project Information", info_card))

        meta_items = [
            ("Author / Creator", "Nishit (@NishitCodes)"),
            ("Repository", "https://github.com/NishitCodes/FlashbangShield"),
            ("Releases & Downloads", "https://github.com/NishitCodes/FlashbangShield/releases"),
            ("License", "GNU General Public License v3.0 (GPL-3.0)"),
        ]
        for key, val in meta_items:
            r = QHBoxLayout()
            k_lbl = CaptionLabel(key, info_card)
            k_lbl.setFixedWidth(140)
            k_lbl.setTextColor(QColor(148, 163, 184), QColor(148, 163, 184))
            r.addWidget(k_lbl)

            if val.startswith("http"):
                v_btn = PushButton(val, info_card)
                v_btn.setStyleSheet(
                    "text-align: left; background: transparent; border: none; "
                    "color: #38BDF8; text-decoration: underline;"
                )
                v_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                v_btn.clicked.connect(lambda _, url=val: self._open_external_url(url))
                r.addWidget(v_btn)
            else:
                v_lbl = BodyLabel(val, info_card)
                r.addWidget(v_lbl)

            r.addStretch(1)
            info_layout.addLayout(r)

        layout.addWidget(info_card)
        layout.addStretch(1)
        return self._wrap_scroll(page, "about_page")

    @staticmethod
    def _open_external_url(url: str) -> None:
        """Safely open an external HTTP URL in the user's default web browser."""
        try:
            if not QDesktopServices.openUrl(QUrl(url)):
                import webbrowser
                webbrowser.open(url)
        except Exception:
            import webbrowser
            webbrowser.open(url)

    # ------------------------------------------------------------ State & Loop
    def _sync_controls_from_config(self) -> None:
        self._target_ctrl.set_value(self._config.target_brightness * 100.0)
        self._cap_ctrl.set_value(self._config.max_dimming * 100.0)
        self._attack_ctrl.set_value(self._config.attack_speed_ms)
        self._decay_ctrl.set_value(self._config.decay_speed_ms)

        for name, card in self._preset_cards.items():
            card.set_active(name == self._config.preset)

        self._overview_curve.set_parameters(self._config.target_brightness, self._config.max_dimming)
        self._tuning_curve.set_parameters(self._config.target_brightness, self._config.max_dimming)
        if hasattr(self._engine, "is_enabled"):
            self._refresh_enabled_ui(self._engine.is_enabled())

    def _refresh_enabled_ui(self, enabled: bool) -> None:
        if enabled:
            self._status_title.setText("Shield Active")
            self._toggle_btn.setText("Pause Shield")
        else:
            self._status_title.setText("Shield Paused")
            self._toggle_btn.setText("Resume Shield")

    def _update_config(self, field_name: str, value: Any) -> None:
        setattr(self._config, field_name, value)
        if field_name in ("target_brightness", "max_dimming", "attack_speed_ms", "decay_speed_ms"):
            self._config.preset = "Custom"
            for card in self._preset_cards.values():
                card.set_active(False)
        self._overview_curve.set_parameters(self._config.target_brightness, self._config.max_dimming)
        self._tuning_curve.set_parameters(self._config.target_brightness, self._config.max_dimming)
        if hasattr(self._engine, "update_config"):
            self._engine.update_config(self._config)
        if self._on_config_saved is not None:
            self._on_config_saved(self._config)

    @Slot(str)
    def _on_preset_selected(self, preset_name: str) -> None:
        if hasattr(self._engine, "set_preset") and self._engine.set_preset(preset_name):
            self._sync_controls_from_config()
            if self._on_config_saved is not None:
                self._on_config_saved(self._config)
        else:
            self._config.apply_preset(preset_name)
            self._sync_controls_from_config()
            if hasattr(self._engine, "update_config"):
                self._engine.update_config(self._config)
            if self._on_config_saved is not None:
                self._on_config_saved(self._config)

    @Slot()
    def _on_toggle_shield(self) -> None:
        if hasattr(self._engine, "toggle_enabled"):
            new_state = self._engine.toggle_enabled()
        else:
            new_state = not getattr(self._config, "enabled", True)
            self._config.enabled = new_state
        self._refresh_enabled_ui(new_state)

    @Slot(bool)
    def _on_startup_toggled(self, checked: bool) -> None:
        self._config.start_with_windows = checked
        if self._set_startup_fn is not None:
            self._set_startup_fn(checked)

    @Slot()
    def _on_render_tick(self) -> None:
        """60 Hz UI refresh — runs strictly while Dashboard or Mini-HUD is visible."""
        dashboard_visible = self.isVisible() and not self.isMinimized()
        hud_visible = self.mini_hud.isVisible()
        if not dashboard_visible and not hud_visible:
            self._render_timer.stop()
            return

        seq, latest, history, flash_ts = self._bridge.snapshot()

        if hud_visible:
            self.mini_hud.update_sample(latest)

        if dashboard_visible:
            # Always repaint during active shock-pulse decay even if sequence paused
            if seq != self._last_rendered_seq or (flash_ts > 0.0):
                self._last_rendered_seq = seq
                self._oscilloscope.update_data(latest, history, flash_ts)
                self._overview_curve.set_live_point(latest.luminance, latest.overlay_alpha)
                self._tuning_curve.set_live_point(latest.luminance, latest.overlay_alpha)
                self._refresh_enabled_ui(latest.enabled)

                mon_stats = self._bridge.monitor_snapshots()
                for idx, card in enumerate(self._monitor_cards):
                    if idx < len(mon_stats):
                        ms = mon_stats[idx]
                        card.update_metrics(
                            float(ms.get("luminance", latest.luminance)),
                            float(ms.get("alpha", latest.overlay_alpha)),
                            hdr=ms.get("is_hdr"),
                        )
                    else:
                        card.update_metrics(latest.luminance, latest.overlay_alpha)

    # ------------------------------------------------------ Window Lifecycle
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        exclude_window_from_capture(self)
        if not self._render_timer.isActive():
            self._render_timer.start()

    @Slot()
    def show_and_focus(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if not self._render_timer.isActive():
            self._render_timer.start()

    @Slot()
    def toggle_mini_hud(self) -> None:
        if self.mini_hud.isVisible():
            self.mini_hud.hide()
        else:
            self.mini_hud.show()
            if not self._render_timer.isActive():
                self._render_timer.start()

    def is_visible(self) -> bool:
        return self.isVisible()

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show_and_focus()

    def update_status(self, enabled: bool) -> None:
        self._refresh_enabled_ui(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Intercept WM_CLOSE to hide cleanly to system tray instead of exiting."""
        event.ignore()
        self.hide()
        if not self.mini_hud.isVisible():
            self._render_timer.stop()
        if self._on_hide_to_tray is not None:
            self._on_hide_to_tray()

    @Slot()
    def _force_quit(self) -> None:
        self._render_timer.stop()
        if hasattr(self._engine, "remove_stats_callback"):
            self._engine.remove_stats_callback(self._bridge.on_engine_stats)
        self.mini_hud.close()
        QApplication.quit()


def create_gui_runtime(
    engine: EngineProtocol,
    config: Config,
    get_startup_fn: Optional[Callable[[], bool]] = None,
    set_startup_fn: Optional[Callable[[bool], None]] = None,
    on_config_saved: Optional[Callable[[Config], None]] = None,
    on_hide_to_tray: Optional[Callable[[], None]] = None,
    start_minimized: bool = False,
) -> tuple[QApplication, TelemetryBridge, FlashbangDashboard]:
    """Factory entry point for main.py to wire Qt on the main thread with pystray."""
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if get_startup_fn is None:
        get_startup_fn = get_run_at_startup
    if set_startup_fn is None:
        set_startup_fn = set_run_at_startup

    bridge = TelemetryBridge(history_seconds=10.0, sample_rate_hz=config.sample_rate_hz)
    window = FlashbangDashboard(
        engine=engine,
        config=config,
        bridge=bridge,
        get_startup_fn=get_startup_fn,
        set_startup_fn=set_startup_fn,
        on_config_saved=on_config_saved,
        on_hide_to_tray=on_hide_to_tray,
    )
    if not start_minimized:
        window.show_and_focus()
    return app, bridge, window


class SettingsGUI:
    """Compatibility adapter for tests and legacy callers wrapping FlashbangDashboard."""

    def __init__(
        self,
        engine: EngineProtocol,
        on_close_to_tray: Optional[Callable[[], None]] = None,
        on_exit_app: Optional[Callable[[], None]] = None,
    ):
        self.engine = engine
        self.config = getattr(engine, "config", Config())
        self.on_close_to_tray = on_close_to_tray
        self.on_exit_app = on_exit_app
        self.root: Optional[Any] = None
        self._dashboard: Optional[FlashbangDashboard] = None
        self._bridge: Optional[TelemetryBridge] = None

    def create_window(self) -> Any:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        self._bridge = TelemetryBridge(history_seconds=10.0, sample_rate_hz=self.config.sample_rate_hz)
        self._dashboard = FlashbangDashboard(
            engine=self.engine,
            config=self.config,
            bridge=self._bridge,
            get_startup_fn=get_run_at_startup,
            set_startup_fn=set_run_at_startup,
            on_hide_to_tray=self.on_close_to_tray,
        )

        class _WindowCompatWrapper:
            def __init__(self, win: FlashbangDashboard):
                self._win = win

            def withdraw(self) -> None:
                self._win.hide()

            def deiconify(self) -> None:
                self._win.show_and_focus()

            def update(self) -> None:
                QApplication.processEvents()

            def destroy(self) -> None:
                self._win.close()

            def __getattr__(self, name: str) -> Any:
                return getattr(self._win, name)

        self.root = _WindowCompatWrapper(self._dashboard)
        return self.root

    def show(self) -> None:
        if self._dashboard is None:
            self.create_window()
        self._dashboard.show_and_focus()

    def hide(self) -> None:
        if self._dashboard is not None:
            self._dashboard.hide()

    def toggle(self) -> None:
        if self._dashboard is None:
            self.create_window()
        if self._dashboard.isVisible():
            self._dashboard.hide()
        else:
            self._dashboard.show_and_focus()

    def is_visible(self) -> bool:
        return self._dashboard is not None and self._dashboard.isVisible()

    def update_status(self, enabled: bool) -> None:
        if self._dashboard is not None:
            self._dashboard._refresh_enabled_ui(enabled)

    # Legacy test callbacks
    def _on_preset_selected(self, preset_name: str) -> None:
        if hasattr(self.engine, "set_preset"):
            self.engine.set_preset(preset_name)
        else:
            self.config.apply_preset(preset_name)
        if self._dashboard is not None:
            self._dashboard._sync_controls_from_config()

    def _on_target_changed(self, val: Any) -> None:
        v = float(val) / 100.0 if float(val) > 1.0 else float(val)
        self.config.target_brightness = v
        self.config.preset = "Custom"
        if hasattr(self.engine, "update_config"):
            self.engine.update_config(self.config)
        if self._dashboard is not None:
            self._dashboard._sync_controls_from_config()

    def _on_dim_cap_changed(self, val: Any) -> None:
        v = float(val) / 100.0 if float(val) > 1.0 else float(val)
        self.config.max_dimming = v
        self.config.preset = "Custom"
        if hasattr(self.engine, "update_config"):
            self.engine.update_config(self.config)
        if self._dashboard is not None:
            self._dashboard._sync_controls_from_config()

    def _on_attack_changed(self, val: Any) -> None:
        self.config.attack_speed_ms = float(val)
        self.config.preset = "Custom"
        if hasattr(self.engine, "update_config"):
            self.engine.update_config(self.config)
        if self._dashboard is not None:
            self._dashboard._sync_controls_from_config()

    def _on_decay_changed(self, val: Any) -> None:
        self.config.decay_speed_ms = float(val)
        self.config.preset = "Custom"
        if hasattr(self.engine, "update_config"):
            self.engine.update_config(self.config)
        if self._dashboard is not None:
            self._dashboard._sync_controls_from_config()

    def _on_toggle_shield_clicked(self) -> None:
        if hasattr(self.engine, "toggle_enabled"):
            self.engine.toggle_enabled()
        else:
            self.config.enabled = not getattr(self.config, "enabled", True)
        if self._dashboard is not None:
            self._dashboard._refresh_enabled_ui(self.engine.is_enabled())
