"""Flashbang Shield - Application Entrypoint.

Orchestrates:
- Single-instance enforcement via Win32 Mutex
- ShieldEngine core adaptive screen sampler and layered overlay
- Global Hotkey Manager (Win+Alt+O, Win+Alt+Up, Win+Alt+Down)
- System Tray Icon (pystray) with live status and preset controls
- Modern Dark-Themed Settings GUI & Real-Time Monitor (Tkinter)
- Graceful shutdown and config persistence
"""

import argparse
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import sys
import threading
import time

# Support direct script execution (e.g. from Windows Startup Run key: pythonw.exe path/to/main.py)
if __name__ == "__main__" and not __package__:
    _parent = Path(__file__).resolve().parent.parent
    if str(_parent) not in sys.path:
        sys.path.insert(0, str(_parent))
    __package__ = "flashbang_shield"

from .config import Config, PRESETS, load_config, save_config, get_default_config_path
from .engine import ShieldEngine
from .gui import SettingsGUI
from .hotkeys import HotkeyManager
from .tray import TrayIcon

# Single-instance Win32 Mutex Name
MUTEX_NAME = "FlashbangShield_SingleInstance_Mutex"
ERROR_ALREADY_EXISTS = 183


def setup_logging(debug: bool = False):
    """Configure console and file logging."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"

    handlers = [logging.StreamHandler(sys.stdout)]

    try:
        appdata = os.environ.get("APPDATA")
        if appdata:
            log_dir = Path(appdata) / "FlashbangShield"
        else:
            log_dir = Path.home() / ".flashbang_shield"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "flashbang_shield.log"
        handlers.append(logging.FileHandler(str(log_file), encoding="utf-8"))
    except Exception:
        pass

    logging.basicConfig(level=log_level, format=log_format, handlers=handlers, force=True)


class FlashbangShieldApp:
    """Main Application Controller."""

    def __init__(self, start_minimized: bool = False, preset: str = None):
        self.start_minimized = start_minimized
        self.preset_override = preset

        self.logger = logging.getLogger("FlashbangShield.App")
        self._mutex_handle = None

        # Core Components
        self.config: Config = load_config()
        if self.preset_override and self.preset_override in PRESETS:
            self.config.apply_preset(self.preset_override)

        self.engine: ShieldEngine = ShieldEngine(self.config)
        self.gui: SettingsGUI = None
        self.tray: TrayIcon = None
        self.hotkeys: HotkeyManager = None

        self._is_shutting_down = False
        self._tray_sync_timer = None

    def acquire_single_instance_mutex(self) -> bool:
        """Ensure only one instance runs concurrently."""
        kernel32 = ctypes.windll.kernel32
        self._mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last_error = kernel32.GetLastError()
        if last_error == ERROR_ALREADY_EXISTS:
            self.logger.warning("Another instance of Flashbang Shield is already running.")
            return False
        return True

    def release_single_instance_mutex(self):
        """Release the named mutex upon shutdown."""
        if self._mutex_handle:
            ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None

    def run(self):
        """Main lifecycle entrypoint."""
        if not self.acquire_single_instance_mutex():
            print("Flashbang Shield is already running in the background.", file=sys.stderr)
            sys.exit(0)

        self.logger.info("Starting Flashbang Shield...")

        try:
            # 1. Start Engine
            self.engine.start()
            self.logger.info("Adaptive engine running")

            # 2. Initialize GUI
            self.gui = SettingsGUI(
                engine=self.engine,
                on_close_to_tray=self._on_gui_minimized,
                on_exit_app=self.shutdown,
            )
            root = self.gui.create_window()

            # 3. Initialize System Tray
            self.tray = TrayIcon(
                on_show_gui=lambda: self._call_on_ui_thread(self.gui.show_window),
                on_toggle_shield=self._on_toggle_shield,
                on_set_preset=self._on_set_preset,
                on_brightness_up=self._on_brightness_up,
                on_brightness_down=self._on_brightness_down,
                on_exit=lambda: self._call_on_ui_thread(self.shutdown),
                current_preset=self.config.preset,
                enabled=self.config.enabled,
            )
            self.tray.start()

            # 4. Initialize Global Hotkeys
            self.hotkeys = HotkeyManager(
                on_toggle=self._on_toggle_shield,
                on_brightness_up=self._on_brightness_up,
                on_brightness_down=self._on_brightness_down,
            )
            self.hotkeys.start()

            # 5. Connect periodic tray sync
            self._schedule_tray_sync(root)

            # 6. Show or minimize GUI
            if self.start_minimized:
                self.gui.hide_window()
                self.logger.info("Flashbang Shield running minimized in system tray")
            else:
                self.gui.show_window()

            # 7. Start Tkinter Mainloop on Main Thread
            root.mainloop()

        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt received")
            self.shutdown()
        finally:
            self._cleanup()

    def _call_on_ui_thread(self, fn, *args):
        """Thread-safe UI invocation."""
        if self.gui and self.gui.root:
            try:
                self.gui.root.after(0, fn, *args)
            except Exception:
                pass

    def _schedule_tray_sync(self, root):
        """Keep tray tooltip & icon synced with live dimming status."""
        if self._is_shutting_down:
            return

        try:
            stats = self.engine.get_stats()
            dim_alpha = stats.get("overlay_alpha", 0.0)
            enabled = self.engine.is_enabled()
            preset = self.config.preset

            if self.tray:
                self.tray.update_status(enabled=enabled, dim_alpha=dim_alpha, preset=preset)
        except Exception:
            pass

        if root and not self._is_shutting_down:
            root.after(1000, lambda: self._schedule_tray_sync(root))

    def _on_gui_minimized(self):
        self.logger.debug("Dashboard minimized to tray")

    def _on_toggle_shield(self):
        new_state = self.engine.toggle_enabled()
        self.logger.info(f"Shield toggled: {'Enabled' if new_state else 'Paused'}")
        if self.tray:
            self.tray.update_status(enabled=new_state, preset=self.config.preset)
        if self.gui:
            self._call_on_ui_thread(self.gui._update_status_button)

    def _on_set_preset(self, preset_name: str):
        success = self.engine.set_preset(preset_name)
        if success:
            self.logger.info(f"Preset changed to {preset_name}")
            if self.tray:
                self.tray.update_status(enabled=self.engine.is_enabled(), preset=preset_name)
            if self.gui:
                self._call_on_ui_thread(self.gui._update_preset_buttons)
                self._call_on_ui_thread(self.gui._sync_sliders_from_config)

    def _on_brightness_up(self):
        new_val = self.engine.adjust_target_brightness(+0.05)
        self.logger.info(f"Brightness increased: {new_val:.2f}")
        if self.gui:
            self._call_on_ui_thread(self.gui._sync_sliders_from_config)

    def _on_brightness_down(self):
        new_val = self.engine.adjust_target_brightness(-0.05)
        self.logger.info(f"Brightness decreased: {new_val:.2f}")
        if self.gui:
            self._call_on_ui_thread(self.gui._sync_sliders_from_config)

    def shutdown(self):
        """Cleanly tear down all components and close application."""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True
        self.logger.info("Shutting down Flashbang Shield...")

        # Save config
        try:
            save_config(self.config)
            self.logger.info("Configuration saved")
        except Exception as e:
            self.logger.error(f"Failed to save config: {e}")

        # Stop hotkeys
        if self.hotkeys:
            try:
                self.hotkeys.stop()
            except Exception as e:
                self.logger.debug(f"Error stopping hotkeys: {e}")

        # Stop tray
        if self.tray:
            try:
                self.tray.stop()
            except Exception as e:
                self.logger.debug(f"Error stopping tray: {e}")

        # Stop engine
        if self.engine:
            try:
                self.engine.stop()
            except Exception as e:
                self.logger.debug(f"Error stopping engine: {e}")

        # Destroy GUI
        if self.gui and self.gui.root:
            try:
                self.gui.root.destroy()
            except Exception:
                pass

        self._cleanup()

    def _cleanup(self):
        self.release_single_instance_mutex()
        self.logger.info("Flashbang Shield exited cleanly.")


def main():
    """Command-line entrypoint."""
    parser = argparse.ArgumentParser(
        prog="flashbang-shield",
        description="Flashbang Shield - Real-time adaptive screen brightness and flashbang protection for Windows.",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start minimized directly to the system tray.",
    )
    parser.add_argument(
        "--background",
        "-b",
        action="store_true",
        help="Launch detached in the background without blocking the console terminal.",
    )
    parser.add_argument(
        "--preset",
        choices=list(PRESETS.keys()),
        help="Launch with specific preset active (Day, Evening, Night, Ultra-Dim).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset configuration to defaults before starting.",
    )
    parser.add_argument(
        "--debug",
        "-v",
        action="store_true",
        help="Enable verbose debug logging.",
    )

    args = parser.parse_args()

    if args.background:
        import subprocess
        child_args = [arg for arg in sys.argv[1:] if arg not in ("--background", "-b")]
        pythonw_path = Path(sys.executable).parent / "pythonw.exe"
        exe = str(pythonw_path) if pythonw_path.exists() else sys.executable
        cmd = [exe, "-m", "flashbang_shield.main"] + child_args
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            cmd,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        print("Flashbang Shield launched detached in the background. Terminal is released.")
        return

    setup_logging(debug=args.debug)

    if args.reset:
        cfg_path = get_default_config_path()
        if cfg_path.exists():
            try:
                cfg_path.unlink()
                print("Configuration reset to defaults.")
            except Exception as e:
                print(f"Failed to delete config file: {e}", file=sys.stderr)

    app = FlashbangShieldApp(start_minimized=args.minimized, preset=args.preset)
    app.run()


if __name__ == "__main__":
    main()
