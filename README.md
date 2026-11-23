# Flashbang Shield 🛡️

[![CI & Release Build](https://github.com/NishitCodes/FlashbangShield/actions/workflows/ci.yml/badge.svg)](https://github.com/NishitCodes/FlashbangShield/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/NishitCodes/FlashbangShield?color=F59E0B&label=Release)](https://github.com/NishitCodes/FlashbangShield/releases/latest)
[![Platform](https://img.shields.io/badge/Platform-Windows%2011%20%7C%2010-0078D4?logo=windows)](https://github.com/NishitCodes/FlashbangShield)
[![License](https://img.shields.io/badge/License-GPL--3.0-blue.svg)](LICENSE)

**Flashbang Shield** is a high-performance Windows 11 utility that eliminates sudden visual shock ("flashbangs") when switching between dark applications (IDEs, terminals, editors) and blinding white browser tabs or documents.

Unlike legacy gamma-stepping utilities, Flashbang Shield uses a **zero-distortion layered optical overlay (`WS_EX_LAYERED | WS_EX_TRANSPARENT`)** coupled with a **hardware-accelerated 60 FPS DXGI Desktop Duplication pipeline** and **biologically-tuned asymmetric easing** to protect your eyes with zero perceptible lag.

---

## 🚀 Downloads & Direct Installation

Download the latest version directly from the [GitHub Releases](https://github.com/NishitCodes/FlashbangShield/releases/latest):

| Package | Type | Description |
|---|---|---|
| **[FlashbangShield-Setup.exe](https://github.com/NishitCodes/FlashbangShield/releases/latest/download/FlashbangShield-Setup.exe)** | **Windows Installer (Recommended)** | Automatically installs Flashbang Shield to user profile, creates Start Menu and Desktop shortcuts, adds uninstallation entry, and configures optional Windows startup. |
| **[FlashbangShield.exe](https://github.com/NishitCodes/FlashbangShield/releases/latest/download/FlashbangShield.exe)** | **Portable Executable** | Standalone single-file binary. No installation required — run directly from anywhere. |

---

## ✨ Features

- **Windows 11 Fluent Design & Mica Backdrop**: Built with native PySide6 and Fluent Widgets featuring acrylic/mica depth, sleek dark aesthetic, and amber polarized visor accents.
- **60 FPS Live Oscilloscope**: Real-time dual-channel waveform tracking raw screen luminance (Electric Cyan `#38BDF8`), active protective dimming alpha (Visor Amber `#F59E0B`), target comfort threshold, and shock-pulse decay.
- **Log-Softplus Perception Compression**: Implements non-linear smooth-knee human luminance perception curve $D(L) = \min(D_{\max}, k \cdot \ln(1 + e^{(L - L_{\text{target}})/k}))$ with real-time operating point beacon.
- **Floating Mini-HUD**: Lightweight draggable pill widget pinned to desktop corners with live dimming gauge, pause/resume toggle, and one-click expand.
- **Zero Feedback Loop (`WDA_EXCLUDEFROMCAPTURE`)**: Both the dashboard and overlays exclude themselves from Windows desktop duplication APIs, ensuring the sampler never measures the protective dimming surface.
- **Asymmetric Pupillary Easing**:
  - **Fast Attack (~180 ms)**: Reacts instantly to bright flashes before pupil fatigue sets in.
  - **Soft Decay (~600 ms)**: Biphasic gentle relaxation when returning to dark windows.
- **Independent Per-Monitor Topology**: Multi-display support with automatic SDR / HDR (`scRGB FP16`) calibration and per-monitor luminance metrics.
- **Zero Idle CPU**: Telemetry loops sleep completely when the UI is hidden in the system tray.

---

## ⌨️ Global Shortcuts

| Shortcut | Action |
|---|---|
| `Win + Alt + O` | Toggle Flashbang Shield (Active / Paused) |
| `Win + Alt + Up` | Increase comfort target brightness (+5%) |
| `Win + Alt + Down` | Decrease comfort target brightness (-5%) |

---

## ⚙️ Optical Presets

- **Day** (Target: 55%, Cap: 65%): Optimized for bright, sunlit workspaces.
- **Evening** (Target: 38%, Cap: 82%): Default balanced indoor tuning.
- **Night** (Target: 22%, Cap: 88%): Dim lighting and late-night productivity.
- **Ultra-Dim** (Target: 10%, Cap: 95%): Pitch-black environments and OLED displays.

---

## 🛠️ Building from Source

### Prerequisites
- Python 3.10+ (64-bit)
- Windows 10 or 11

### Setup & Run
```powershell
# Clone the repository
git clone https://github.com/NishitCodes/FlashbangShield.git
cd FlashbangShield

# Install dependencies
pip install -r requirements.txt

# Run application
python -m flashbang_shield.main
```

### Build Standalone Executable & Installer
```powershell
# Build portable standalone binary (dist/FlashbangShield.exe)
python build_exe.py

# Build Windows installer setup (requires Inno Setup 6)
iscc installer.iss
```

---

## 📄 License

Distributed under the GNU General Public License v3.0 (GPL-3.0). See [LICENSE](LICENSE) for details.
