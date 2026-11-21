"""Modern Dark-Themed Live Monitor and Settings Dialog for Flashbang Shield.

Features:
- Obsidian dark UI theme with native dark mode title bar
- Real-time animated luminance and dimming suppression meters
- One-click presets: Day, Evening, Night, Ultra-Dim
- Live sliders for target brightness, attack speed, decay speed, and max dimming
- Windows startup registry toggle
- Global hotkey reference card
- Minimize-to-tray window management
"""

import ctypes
import logging
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Dict, Optional

from .config import Config, PRESETS, get_run_at_startup, set_run_at_startup
from .engine import ShieldEngine

logger = logging.getLogger("FlashbangShield.GUI")

# Color Palette (Obsidian Modern Dark)
BG_DARK = "#0d1117"        # Deep background
CARD_BG = "#161b22"        # Panel background
CARD_BORDER = "#30363d"    # Card border
TEXT_PRIMARY = "#f0f6fc"   # Bright text
TEXT_MUTED = "#8b949e"     # Secondary text
ACCENT_BLUE = "#58a6ff"    # Primary blue
ACCENT_INDIGO = "#6366f1"  # Indigo
GREEN_ACTIVE = "#238636"   # Active green
GREEN_TEXT = "#3fb950"     # Bright green
AMBER_BG = "#9e6a03"       # Paused amber
AMBER_TEXT = "#d29922"     # Bright amber
RED_ALERT = "#f85149"      # Flash alert
METER_BG = "#21262d"       # Meter background track


class SettingsGUI:
    """Settings dialog and real-time monitor GUI."""

    def __init__(
        self,
        engine: ShieldEngine,
        on_close_to_tray: Optional[Callable[[], None]] = None,
        on_exit_app: Optional[Callable[[], None]] = None,
    ):
        self.engine = engine
        self.config = engine.config
        self.on_close_to_tray = on_close_to_tray
        self.on_exit_app = on_exit_app

        self.root: Optional[tk.Tk] = None
        self._is_visible = False
        self._poll_timer = None

        # UI Widget references
        self._preset_buttons: Dict[str, tk.Button] = {}
        self._status_btn: Optional[tk.Button] = None
        self._lum_canvas: Optional[tk.Canvas] = None
        self._dim_canvas: Optional[tk.Canvas] = None
        self._lum_val_label: Optional[tk.Label] = None
        self._dim_val_label: Optional[tk.Label] = None
        self._flash_badge: Optional[tk.Label] = None
        self._fps_label: Optional[tk.Label] = None

        # Sliders
        self._target_slider: Optional[tk.Scale] = None
        self._attack_slider: Optional[tk.Scale] = None
        self._decay_slider: Optional[tk.Scale] = None
        self._dim_cap_slider: Optional[tk.Scale] = None

        # Checkbox
        self._startup_var: Optional[tk.BooleanVar] = None

    def create_window(self) -> tk.Tk:
        """Create and configure the Tkinter root window."""
        self.root = tk.Tk()
        self.root.title("Flashbang Shield")
        self.root.geometry("520x750")
        self.root.minsize(480, 680)
        self.root.configure(bg=BG_DARK)

        # Apply native Windows dark title bar
        self._apply_dark_title_bar()

        # Set window icon if available
        icon_path = Path(__file__).resolve().parent / "assets" / "icon.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

        # Handle window close event (minimize to tray instead of quitting)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        # Build UI layout
        self._build_ui()

        # Start live telemetry polling loop
        self._schedule_poll()

        self._is_visible = True
        return self.root

    def _apply_dark_title_bar(self):
        """Enable Windows 10/11 immersive dark mode on title bar."""
        if not self.root:
            return
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            c_int = ctypes.c_int(1)
            # Try DWMWA_USE_IMMERSIVE_DARK_MODE (20 on Win11/Win10 20H1+, 19 on older Win10)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(c_int), ctypes.sizeof(c_int))
        except Exception:
            pass

    def _build_ui(self):
        """Construct the UI cards and controls."""
        main_frame = tk.Frame(self.root, bg=BG_DARK, padx=18, pady=16)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Header Card (Title, Subtitle, Shield Active/Paused Status Button)
        self._build_header(main_frame)

        # 2. Real-Time Monitor Card
        self._build_monitor_card(main_frame)

        # 3. Presets Selector Card
        self._build_presets_card(main_frame)

        # 4. Tuning Sliders Card
        self._build_sliders_card(main_frame)

        # 5. System Options & Hotkeys Card
        self._build_system_card(main_frame)

        # 6. Footer (Minimize & Defaults)
        self._build_footer(main_frame)

    def _build_header(self, parent: tk.Widget):
        header_frame = tk.Frame(parent, bg=BG_DARK)
        header_frame.pack(fill=tk.X, pady=(0, 14))

        left_frame = tk.Frame(header_frame, bg=BG_DARK)
        left_frame.pack(side=tk.LEFT)

        title_lbl = tk.Label(
            left_frame,
            text="⚡ FLASHBANG SHIELD",
            font=("Segoe UI", 15, "bold"),
            fg=TEXT_PRIMARY,
            bg=BG_DARK,
        )
        title_lbl.pack(anchor="w")

        subtitle_lbl = tk.Label(
            left_frame,
            text="Adaptive Luminance Protection",
            font=("Segoe UI", 9),
            fg=TEXT_MUTED,
            bg=BG_DARK,
        )
        subtitle_lbl.pack(anchor="w")

        # Status toggle button (Active / Paused)
        self._status_btn = tk.Button(
            header_frame,
            text="● SHIELD ACTIVE",
            font=("Segoe UI", 10, "bold"),
            command=self._on_toggle_shield_clicked,
            relief=tk.FLAT,
            cursor="hand2",
            padx=12,
            pady=4,
        )
        self._status_btn.pack(side=tk.RIGHT)
        self._update_status_button()

    def _build_monitor_card(self, parent: tk.Widget):
        card = self._create_card(parent, "REAL-TIME MONITOR")

        # Luminance Meter
        lum_row = tk.Frame(card, bg=CARD_BG)
        lum_row.pack(fill=tk.X, pady=(2, 2))

        lum_title = tk.Label(
            lum_row,
            text="Screen Luminance:",
            font=("Segoe UI", 9),
            fg=TEXT_PRIMARY,
            bg=CARD_BG,
        )
        lum_title.pack(side=tk.LEFT)

        self._flash_badge = tk.Label(
            lum_row,
            text="",
            font=("Segoe UI", 8, "bold"),
            fg=RED_ALERT,
            bg=CARD_BG,
        )
        self._flash_badge.pack(side=tk.LEFT, padx=6)

        self._lum_val_label = tk.Label(
            lum_row,
            text="0%",
            font=("Segoe UI", 9, "bold"),
            fg=TEXT_PRIMARY,
            bg=CARD_BG,
        )
        self._lum_val_label.pack(side=tk.RIGHT)

        self._lum_canvas = tk.Canvas(card, height=12, bg=METER_BG, highlightthickness=0)
        self._lum_canvas.pack(fill=tk.X, pady=(2, 10))

        # Dimming Overlay Meter
        dim_row = tk.Frame(card, bg=CARD_BG)
        dim_row.pack(fill=tk.X, pady=(2, 2))

        dim_title = tk.Label(
            dim_row,
            text="Active Dimming Overlay:",
            font=("Segoe UI", 9),
            fg=TEXT_PRIMARY,
            bg=CARD_BG,
        )
        dim_title.pack(side=tk.LEFT)

        self._dim_val_label = tk.Label(
            dim_row,
            text="0%",
            font=("Segoe UI", 9, "bold"),
            fg=ACCENT_BLUE,
            bg=CARD_BG,
        )
        self._dim_val_label.pack(side=tk.RIGHT)

        self._dim_canvas = tk.Canvas(card, height=12, bg=METER_BG, highlightthickness=0)
        self._dim_canvas.pack(fill=tk.X, pady=(2, 6))

        # Telemetry footer
        self._fps_label = tk.Label(
            card,
            text="Sampling: 30 FPS  |  Zero-Feedback WDA Overlay",
            font=("Segoe UI", 8),
            fg=TEXT_MUTED,
            bg=CARD_BG,
        )
        self._fps_label.pack(anchor="w")

    def _build_presets_card(self, parent: tk.Widget):
        card = self._create_card(parent, "PRESETS")

        p_frame = tk.Frame(card, bg=CARD_BG)
        p_frame.pack(fill=tk.X, pady=4)

        for p_name in ["Day", "Evening", "Night", "Ultra-Dim"]:
            btn = tk.Button(
                p_frame,
                text=p_name,
                font=("Segoe UI", 9, "bold"),
                command=lambda name=p_name: self._on_preset_selected(name),
                relief=tk.FLAT,
                cursor="hand2",
                padx=10,
                pady=6,
            )
            btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=3)
            self._preset_buttons[p_name] = btn

        self._update_preset_buttons()

    def _build_sliders_card(self, parent: tk.Widget):
        card = self._create_card(parent, "TUNING & THRESHOLDS")

        # 1. Target Comfort Brightness
        self._target_slider = self._create_slider_row(
            card,
            label="Target Comfort Luminance",
            from_=10,
            to=90,
            initial=int(self.config.target_brightness * 100),
            unit="%",
            on_change=self._on_target_changed,
        )

        # 2. Flash Attack Speed
        self._attack_slider = self._create_slider_row(
            card,
            label="Flash Attack Speed (Suppression)",
            from_=50,
            to=400,
            initial=int(self.config.attack_speed_ms),
            unit=" ms",
            on_change=self._on_attack_changed,
        )

        # 3. Relaxation Decay Speed
        self._decay_slider = self._create_slider_row(
            card,
            label="Relaxation Decay Speed (Recovery)",
            from_=200,
            to=1500,
            initial=int(self.config.decay_speed_ms),
            unit=" ms",
            on_change=self._on_decay_changed,
        )

        # 4. Max Dimming Cap
        self._dim_cap_slider = self._create_slider_row(
            card,
            label="Maximum Dimming Cap",
            from_=20,
            to=95,
            initial=int(self.config.max_dimming * 100),
            unit="%",
            on_change=self._on_dim_cap_changed,
        )

    def _build_system_card(self, parent: tk.Widget):
        card = self._create_card(parent, "SYSTEM & SHORTCUTS")

        # Startup checkbox
        self._startup_var = tk.BooleanVar(value=get_run_at_startup())
        startup_cb = tk.Checkbutton(
            card,
            text="Start Flashbang Shield on Windows login",
            variable=self._startup_var,
            command=self._on_startup_toggled,
            font=("Segoe UI", 9),
            fg=TEXT_PRIMARY,
            bg=CARD_BG,
            selectcolor=METER_BG,
            activebackground=CARD_BG,
            activeforeground=TEXT_PRIMARY,
        )
        startup_cb.pack(anchor="w", pady=(0, 8))

        # Hotkeys Reference Card
        hk_frame = tk.Frame(card, bg=METER_BG, padx=8, pady=6)
        hk_frame.pack(fill=tk.X)

        hk_title = tk.Label(
            hk_frame,
            text="Global Shortcuts Reference:",
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_MUTED,
            bg=METER_BG,
        )
        hk_title.pack(anchor="w", pady=(0, 2))

        shortcuts = [
            ("Win + Alt + O", "Toggle Shield On / Off"),
            ("Win + Alt + Up", "Increase Brightness (+5%)"),
            ("Win + Alt + Down", "Decrease Brightness (-5%)"),
        ]
        for key, desc in shortcuts:
            row = tk.Frame(hk_frame, bg=METER_BG)
            row.pack(fill=tk.X, pady=1)
            k_lbl = tk.Label(row, text=key, font=("Consolas", 8, "bold"), fg=ACCENT_BLUE, bg=METER_BG)
            k_lbl.pack(side=tk.LEFT)
            d_lbl = tk.Label(row, text=f"- {desc}", font=("Segoe UI", 8), fg=TEXT_PRIMARY, bg=METER_BG)
            d_lbl.pack(side=tk.LEFT, padx=6)

    def _build_footer(self, parent: tk.Widget):
        footer_frame = tk.Frame(parent, bg=BG_DARK)
        footer_frame.pack(fill=tk.X, pady=(12, 0))

        reset_btn = tk.Button(
            footer_frame,
            text="Reset Defaults",
            font=("Segoe UI", 8),
            command=self._on_reset_defaults,
            bg=METER_BG,
            fg=TEXT_MUTED,
            relief=tk.FLAT,
            cursor="hand2",
            padx=8,
            pady=4,
        )
        reset_btn.pack(side=tk.LEFT)

        min_btn = tk.Button(
            footer_frame,
            text="Minimize to Tray",
            font=("Segoe UI", 9, "bold"),
            command=self.hide_window,
            bg=ACCENT_INDIGO,
            fg="#ffffff",
            relief=tk.FLAT,
            cursor="hand2",
            padx=14,
            pady=4,
        )
        min_btn.pack(side=tk.RIGHT)

    def _create_card(self, parent: tk.Widget, title: str) -> tk.Frame:
        """Create a rounded-look card container."""
        wrapper = tk.Frame(parent, bg=BG_DARK, pady=4)
        wrapper.pack(fill=tk.X)

        header_lbl = tk.Label(
            wrapper,
            text=title,
            font=("Segoe UI", 8, "bold"),
            fg=TEXT_MUTED,
            bg=BG_DARK,
        )
        header_lbl.pack(anchor="w", pady=(0, 2))

        card = tk.Frame(
            wrapper,
            bg=CARD_BG,
            highlightbackground=CARD_BORDER,
            highlightthickness=1,
            padx=12,
            pady=10,
        )
        card.pack(fill=tk.X)
        return card

    def _create_slider_row(
        self,
        parent: tk.Widget,
        label: str,
        from_: int,
        to: int,
        initial: int,
        unit: str,
        on_change: Callable[[int], None],
    ) -> tk.Scale:
        """Helper to create slider with live value readout."""
        frame = tk.Frame(parent, bg=CARD_BG)
        frame.pack(fill=tk.X, pady=3)

        top_row = tk.Frame(frame, bg=CARD_BG)
        top_row.pack(fill=tk.X)

        lbl = tk.Label(top_row, text=label, font=("Segoe UI", 9), fg=TEXT_PRIMARY, bg=CARD_BG)
        lbl.pack(side=tk.LEFT)

        val_lbl = tk.Label(
            top_row,
            text=f"{initial}{unit}",
            font=("Segoe UI", 9, "bold"),
            fg=ACCENT_BLUE,
            bg=CARD_BG,
        )
        val_lbl.pack(side=tk.RIGHT)

        def _slider_cb(val_str):
            val = int(float(val_str))
            val_lbl.config(text=f"{val}{unit}")
            on_change(val)

        slider = tk.Scale(
            frame,
            from_=from_,
            to=to,
            orient=tk.HORIZONTAL,
            showvalue=False,
            bg=CARD_BG,
            fg=ACCENT_BLUE,
            troughcolor=METER_BG,
            activebackground=ACCENT_BLUE,
            highlightthickness=0,
            command=_slider_cb,
            cursor="hand2",
        )
        slider.set(initial)
        slider.pack(fill=tk.X, pady=(2, 0))
        return slider

    # --- Live Polling & Animation ---

    def _schedule_poll(self):
        """Schedule periodic telemetry refresh at ~25Hz."""
        if not self.root:
            return
        self._update_meters()
        self._poll_timer = self.root.after(40, self._schedule_poll)

    def _update_meters(self):
        """Update canvas meters and live status."""
        stats = self.engine.get_stats()
        lum = stats.get("current_luminance", 0.0)
        dim = stats.get("overlay_alpha", 0.0)
        is_flash = stats.get("is_flashbang", False)
        fps = stats.get("fps", 0.0)

        # Update numbers
        lum_pct = int(round(lum * 100))
        dim_pct = int(round(dim * 100))

        if self._lum_val_label:
            self._lum_val_label.config(text=f"{lum_pct}%")
        if self._dim_val_label:
            self._dim_val_label.config(text=f"{dim_pct}%")

        if self._flash_badge:
            if is_flash:
                self._flash_badge.config(text="⚡ FLASHBANG DETECTED!")
            else:
                self._flash_badge.config(text="")

        if self._fps_label:
            self._fps_label.config(text=f"Sampling: {fps:.0f} FPS  |  Zero-Feedback WDA Overlay")

        # Draw meter bars
        self._draw_meter(self._lum_canvas, lum, is_flash=is_flash)
        self._draw_meter(self._dim_canvas, dim, is_dim=True)

        # Update status button if engine state shifted externally (e.g. via hotkey)
        self._update_status_button()

    def _draw_meter(self, canvas: Optional[tk.Canvas], fraction: float, is_flash: bool = False, is_dim: bool = False):
        if not canvas or not canvas.winfo_exists():
            return
        canvas.delete("all")
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w <= 1:
            return

        clamped = max(0.0, min(1.0, fraction))
        fill_w = int(w * clamped)

        if fill_w > 0:
            if is_flash:
                color = RED_ALERT
            elif is_dim:
                color = ACCENT_INDIGO
            else:
                # Color transitions from green -> yellow -> red
                if clamped < 0.5:
                    color = GREEN_TEXT
                elif clamped < 0.75:
                    color = AMBER_TEXT
                else:
                    color = RED_ALERT
            canvas.create_rectangle(0, 0, fill_w, h, fill=color, outline="")

    # --- Button & Slider Handlers ---

    def _on_toggle_shield_clicked(self):
        new_state = self.engine.toggle_enabled()
        self._update_status_button()

    def _update_status_button(self):
        if not self._status_btn:
            return
        enabled = self.engine.is_enabled()
        if enabled:
            self._status_btn.config(
                text="● SHIELD ACTIVE",
                bg=GREEN_ACTIVE,
                fg="#ffffff",
                activebackground=GREEN_TEXT,
                activeforeground="#ffffff",
            )
        else:
            self._status_btn.config(
                text="⏸ SHIELD PAUSED",
                bg=AMBER_BG,
                fg="#ffffff",
                activebackground=AMBER_TEXT,
                activeforeground="#ffffff",
            )

    def _on_preset_selected(self, name: str):
        self.engine.set_preset(name)
        self._update_preset_buttons()
        self._sync_sliders_from_config()

    def _update_preset_buttons(self):
        curr = self.config.preset
        for name, btn in self._preset_buttons.items():
            if name == curr:
                btn.config(bg=ACCENT_INDIGO, fg="#ffffff")
            else:
                btn.config(bg=METER_BG, fg=TEXT_MUTED)

    def _sync_sliders_from_config(self):
        if self._target_slider:
            self._target_slider.set(int(self.config.target_brightness * 100))
        if self._attack_slider:
            self._attack_slider.set(int(self.config.attack_speed_ms))
        if self._decay_slider:
            self._decay_slider.set(int(self.config.decay_speed_ms))
        if self._dim_cap_slider:
            self._dim_cap_slider.set(int(self.config.max_dimming * 100))

    def _on_target_changed(self, val: Any):
        self.config.target_brightness = float(val) / 100.0

    def _on_attack_changed(self, val: Any):
        self.config.attack_speed_ms = float(val)

    def _on_decay_changed(self, val: Any):
        self.config.decay_speed_ms = float(val)

    def _on_dim_cap_changed(self, val: Any):
        self.config.max_dimming = float(val) / 100.0

    def _on_startup_toggled(self):
        if self._startup_var:
            enable = self._startup_var.get()
            set_run_at_startup(enable)

    def _on_reset_defaults(self):
        self.engine.set_preset("Evening")
        self._update_preset_buttons()
        self._sync_sliders_from_config()

    # --- Window Visibility Controls ---

    def show_window(self):
        """Show and bring settings window to foreground."""
        if not self.root:
            self.create_window()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self._is_visible = True
        self._update_preset_buttons()
        self._sync_sliders_from_config()
        self._update_status_button()

    def hide_window(self):
        """Minimize window to system tray."""
        if self.root:
            self.root.withdraw()
        self._is_visible = False
        if self.on_close_to_tray:
            self.on_close_to_tray()

    def toggle_window(self):
        """Toggle window visibility."""
        if self._is_visible:
            self.hide_window()
        else:
            self.show_window()

    def is_visible(self) -> bool:
        return self._is_visible
