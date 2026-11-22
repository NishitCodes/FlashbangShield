"""System tray icon and context menu for Flashbang Shield.

Provides quick tray menu access to:
- Open Settings Dashboard
- Toggle Shield On / Off
- Switch Presets (Day, Evening, Night, Ultra-Dim)
- Nudge brightness (+5% / -5%)
- Windows startup toggle
- Exit application
- Live status indicator (green badge when active, amber when paused)
"""

import logging
from pathlib import Path
import threading
from typing import Callable, Optional
from PIL import Image, ImageDraw

try:
    import pystray
    from pystray import MenuItem as item, Menu as menu
except ImportError:
    pystray = None

from .config import PRESETS, get_run_at_startup, set_run_at_startup

logger = logging.getLogger("FlashbangShield.Tray")


def create_tray_image(enabled: bool = True, dim_percent: int = 0) -> Image.Image:
    """Generate dynamic tray icon with live status dot."""
    size = 64
    pad = 6
    asset_path = Path(__file__).resolve().parent / "assets" / "icon.png"

    img = None
    if asset_path.exists():
        try:
            base_img = Image.open(asset_path).convert("RGBA")
            img = base_img.resize((size, size), Image.Resampling.LANCZOS)
            if not enabled:
                # Dim and desaturate slightly when shield is paused
                r, g, b, a = img.split()
                gray = Image.merge("RGB", (r, g, b)).convert("L")
                dimmed = Image.merge("RGBA", (gray, gray, gray, a))
                img = Image.blend(img, dimmed, 0.65)
        except Exception as e:
            logger.debug(f"Could not load custom icon asset: {e}")
            img = None

    if img is None:
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Outer shield shape
        w = size - 2 * pad
        h = size - 2 * pad
        cx = size // 2
        top = pad
        bottom = size - pad
        left = pad
        right = size - pad
        mid_y = int(pad + h * 0.55)

        shield_pts = [
            (left, top),
            (right, top),
            (right, mid_y),
            (cx, bottom),
            (left, mid_y),
        ]

        # Theme colors
        if enabled:
            shield_fill = (15, 23, 42, 255)       # Slate 900
            shield_border = (99, 102, 241, 255)   # Indigo
            bolt_color = (56, 189, 248, 255)      # Cyan
            status_color = (16, 185, 129, 255)    # Emerald green
        else:
            shield_fill = (24, 24, 27, 255)       # Zinc 900
            shield_border = (113, 113, 122, 255)  # Muted zinc
            bolt_color = (161, 161, 170, 255)     # Muted gray
            status_color = (245, 158, 11, 255)    # Amber paused

        draw.polygon(shield_pts, fill=shield_fill, outline=shield_border, width=3)

        # Inner lightning emblem
        bolt = [
            (cx + 3, int(top + h * 0.16)),
            (cx - 10, int(top + h * 0.48)),
            (cx - 2, int(top + h * 0.48)),
            (cx - 6, int(top + h * 0.82)),
            (cx + 11, int(top + h * 0.44)),
            (cx + 2, int(top + h * 0.44)),
        ]
        draw.polygon(bolt, fill=bolt_color)
    else:
        draw = ImageDraw.Draw(img)
        status_color = (16, 185, 129, 255) if enabled else (245, 158, 11, 255)

    # Status indicator circle (bottom right)
    dot_r = 7
    dot_cx = size - pad - 2
    dot_cy = size - pad - 2
    draw.ellipse(
        [(dot_cx - dot_r, dot_cy - dot_r), (dot_cx + dot_r, dot_cy + dot_r)],
        fill=status_color,
        outline=(0, 0, 0, 255),
        width=2,
    )

    return img


class TrayIcon:
    """System tray manager wrapping pystray."""

    def __init__(
        self,
        on_show_gui: Optional[Callable[[], None]] = None,
        on_toggle_shield: Optional[Callable[[], None]] = None,
        on_set_preset: Optional[Callable[[str], None]] = None,
        on_brightness_up: Optional[Callable[[], None]] = None,
        on_brightness_down: Optional[Callable[[], None]] = None,
        on_exit: Optional[Callable[[], None]] = None,
        current_preset: str = "Evening",
        enabled: bool = True,
    ):
        self.on_show_gui = on_show_gui
        self.on_toggle_shield = on_toggle_shield
        self.on_set_preset = on_set_preset
        self.on_brightness_up = on_brightness_up
        self.on_brightness_down = on_brightness_down
        self.on_exit = on_exit

        self.current_preset = current_preset
        self.enabled = enabled
        self.dim_percent = 0

        self._icon: Optional[pystray.Icon] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Initialize and run tray icon in background."""
        if pystray is None:
            logger.warning("pystray not installed, tray icon will be disabled")
            return

        image = create_tray_image(self.enabled, self.dim_percent)
        self._icon = pystray.Icon(
            name="FlashbangShield",
            icon=image,
            title=self._get_title(),
            menu=self._build_menu(),
        )

        # Run pystray in a dedicated thread
        self._thread = threading.Thread(target=self._run_icon, name="FlashbangShieldTrayThread", daemon=True)
        self._thread.start()
        logger.info("System tray icon started")

    def stop(self):
        """Stop and remove tray icon."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception as e:
                logger.debug(f"Error stopping tray icon: {e}")
            self._icon = None

    def update_status(self, enabled: bool, dim_alpha: float = 0.0, preset: Optional[str] = None):
        """Update live status in tray icon and tooltip."""
        self.enabled = enabled
        self.dim_percent = int(round(dim_alpha * 100))
        if preset:
            self.current_preset = preset

        if self._icon:
            try:
                self._icon.title = self._get_title()
                self._icon.icon = create_tray_image(self.enabled, self.dim_percent)
                self._icon.update_menu()
            except Exception as e:
                logger.debug(f"Tray update exception: {e}")

    def _get_title(self) -> str:
        status_text = f"Active (Dim: {self.dim_percent}%)" if self.enabled else "Paused"
        return f"Flashbang Shield - {status_text} [{self.current_preset}]"

    def _build_menu(self) -> "pystray.Menu":
        """Build context menu items."""
        def _preset_action(name: str):
            def _handler(icon, item):
                if self.on_set_preset:
                    self.on_set_preset(name)
            return _handler

        def _is_preset_checked(name: str):
            def _checked(item):
                return self.current_preset == name
            return _checked

        def _is_startup_checked(item):
            return get_run_at_startup()

        def _toggle_startup(icon, item):
            curr = get_run_at_startup()
            set_run_at_startup(not curr)

        def _toggle_action(icon, item):
            if self.on_toggle_shield:
                self.on_toggle_shield()

        def _show_gui_action(icon, item):
            if self.on_show_gui:
                self.on_show_gui()

        def _bright_up(icon, item):
            if self.on_brightness_up:
                self.on_brightness_up()

        def _bright_down(icon, item):
            if self.on_brightness_down:
                self.on_brightness_down()

        def _exit_app(icon, item):
            if self.on_exit:
                self.on_exit()
            else:
                self.stop()

        preset_items = [
            item(p_name, _preset_action(p_name), checked=_is_preset_checked(p_name), radio=True)
            for p_name in PRESETS.keys()
        ]

        menu_items = [
            item("Open Dashboard / Settings", _show_gui_action, default=True),
            item(
                lambda text: "Pause Shield (Win+Alt+O)" if self.enabled else "Resume Shield (Win+Alt+O)",
                _toggle_action,
            ),
            pystray.Menu.SEPARATOR,
            item("Presets", menu(*preset_items)),
            item("Brightness Up (+5%)  [Win+Alt+Up]", _bright_up),
            item("Brightness Down (-5%) [Win+Alt+Down]", _bright_down),
            pystray.Menu.SEPARATOR,
            item("Start with Windows", _toggle_startup, checked=_is_startup_checked),
            pystray.Menu.SEPARATOR,
            item("Exit Flashbang Shield", _exit_app),
        ]

        return menu(*menu_items)

    def _run_icon(self):
        """Execute pystray main loop."""
        try:
            self._icon.run()
        except Exception as e:
            logger.error(f"Error in tray execution: {e}", exc_info=True)
