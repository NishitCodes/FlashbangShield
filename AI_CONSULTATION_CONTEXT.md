# Flashbang Shield: Architecture, Technical Specification & Engineering Context

> **Document Purpose**: This file provides comprehensive technical context on the **Flashbang Shield** project for an advanced AI model / systems engineer. It details the problem statement, human biological constraints, architectural components, mathematical models, source code, and key open technical challenges.

---

## 1. Executive Summary & Problem Definition

### The "Flashbang" Phenomenon
Modern computer users constantly switch between dark-themed applications (IDEs like VS Code, terminals, dark Discord, Spotify) and bright white surfaces (Google search, Wikipedia, blank browser tabs, white PDFs, Word documents). 

When switching abruptly from a dark interface (luminance ~5–10 cd/m²) to a full-screen white page (luminance ~250–400 cd/m²):
- **Biological Constraint (Pupillary Light Reflex)**: The human pupil takes approximately **200 to 300 ms** to initiate constriction and up to **1 to 2 seconds** to complete adaptation.
- **The Result**: The retina receives an un-attenuated blast of photons ("visual flashbang"), causing instant eye strain, squinting, headache, and visual fatigue.
- **Reverse Adaptation**: Dilation when returning to dark scenes takes even longer (**3 to 5 seconds**).

### Flashbang Shield's Solution
Flashbang Shield is a lightweight, non-invasive Windows utility that continuously monitors the screen's content luminance in real time. When an abrupt bright white surface appears, it immediately and smoothly applies an optical layered dimming overlay to maintain a constant, comfortable light output at the user's eyes. As the user switches back to dark scenes, the dimming relaxes gently without jarring transitions.

---

## 2. Competitive Landscape & Architectural Decisions

| Approach | Prior Art (e.g. Gammy, Windows CABC) | Flashbang Shield |
|---|---|---|
| **Dimming Mechanism** | **GPU Gamma Ramps** (Gammy) or **Hardware Backlight** (CABC) | **Transparent Layered Overlay Window** (`WS_EX_LAYERED \| WS_EX_TRANSPARENT`) |
| **Color Fidelity** | Modifying gamma ramps crushes dark contrast, causes banding, and turns deep blacks into washed-out gray. Hardware backlight has stepped latency. | Acts as an optical Neutral Density (ND) filter. 100% true color fidelity, zero gamma distortion, crisp blacks. |
| **Capture Loop Prevention** | Prone to feedback loops (sampling its own darkened screen). | Uses Win32 **`SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)`** (`0x00000011`). The overlay is visible to human eyes but 100% invisible to screen capture APIs. |
| **Response Dynamics** | Symmetrical or linear transitions. | **Biologically-tuned asymmetric exponential easing**: Fast Attack (~180 ms) to suppress flashes before pupillary shock, gentle Decay (~600 ms) for dark relaxation. |
| **Multi-Monitor** | Global single-screen or driver-limited. | Independent sampling and overlay threads per physical display. |

---

## 3. Mathematical & Algorithmic Models

### A. Screen Luminance Sampling ($L$)
Each display is sampled via GDI `StretchBlt` downsampled to a $64 \times 36$ buffer ($N = 2304$ pixels) at 30 Hz.
For each pixel $(R_i, G_i, B_i) \in [0, 255]$:
$$Y_i = 0.2126 \cdot R_i + 0.7152 \cdot G_i + 0.0722 \cdot B_i \quad \text{(ITU-R BT.709 perceived luminance)}$$
$$L_{mean} = \frac{1}{255 \cdot N} \sum_{i=1}^{N} Y_i \in [0.0, 1.0]$$

In addition to mean luminance, the 90th-percentile luminance ($L_{p90}$) is evaluated to detect localized flashes (e.g., a massive white search box or modal occupying 60% of an otherwise dark page):
$$L_{effective} = 0.6 \cdot L_{mean} + 0.4 \cdot L_{p90}$$

### B. Gammy-Inspired Non-Linear Perception Curve
Given user target comfort luminance $T \in [0.05, 0.95]$ and effective maximum dimming cap $D_{max} \in [0.40, 0.95]$:

1. **Sudden Flashbang Jump**:
   If $\Delta L = L_{current} - L_{prev} \ge \text{threshold}$ (default $0.30$) and $L_{current} > T$:
   $$\text{Instant Flash Detected} \implies \text{Target Dimming } D_{target} = D_{max}$$

2. **Normal Adaptive Curve**:
   If $L_{effective} \le T$:
   $$D_{target} = 0.0 \quad \text{(content is comfortable or dark; overlay transparent)}$$
   If $L_{effective} > T$:
   $$\text{Excess Ratio } E = \min\left(1.0, \; \frac{L_{effective} - T}{1.0 - T}\right)$$
   $$\text{Perceptual Factor } F = E^{\gamma \cdot 0.75} \quad (\gamma \approx 2.0)$$
   $$D_{target} = F \cdot D_{max}$$

### C. Asymmetric Continuous Exponential Smoothing
Rather than discrete frame-dependent stepping, transitions use time-independent continuous exponential decay:
$$\Delta t = t_{now} - t_{last}$$
$$\tau = \begin{cases}
\tau_{attack} = \frac{\text{attack\_speed\_ms}}{1000} \approx 0.18\text{ s} & \text{if } D_{target} > D_{current} \quad (\text{suppressing bright flash}) \\
\tau_{decay} = \frac{\text{decay\_speed\_ms}}{1000} \approx 0.60\text{ s} & \text{if } D_{target} \le D_{current} \quad (\text{relaxing back to normal})
\end{cases}$$
$$\alpha = 1.0 - e^{-\Delta t / \tau}$$
$$D_{current} \leftarrow D_{current} + (D_{target} - D_{current}) \cdot \alpha$$

---

## 4. Software Architecture & File Layout

```
FlashbangShield/
├── .github/workflows/ci.yml     # Automated CI linting and pytest
├── flashbang_shield/
│   ├── __init__.py              # Package exports
│   ├── config.py                # Config dataclass, Presets (Day/Evening/Night/Ultra-Dim), JSON persistence, Registry
│   ├── sampler.py               # GDI StretchBlt downsampling, multi-monitor enumerator, DPI v2 aware
│   ├── overlay.py               # Win32 WS_EX_LAYERED click-through window with WDA_EXCLUDEFROMCAPTURE
│   ├── engine.py                # ShieldEngine coordinator, asymmetric easing loop, telemetry stats
│   ├── hotkeys.py               # WH_KEYBOARD_LL low-level hook (Win+Alt+Up/Down/O)
│   ├── tray.py                  # Pystray tray icon with dynamic colored dot, context menu
│   ├── gui.py                   # Modern Tkinter dark dashboard with 25Hz live animated canvas meters
│   ├── main.py                  # CLI flags, single-instance named Mutex, graceful shutdown
│   └── assets/                  # Icons (icon.ico, icon.png)
├── tests/
│   ├── __init__.py
│   └── test_engine.py           # Comprehensive pytest suite (25 tests)
├── verify_flashbang_shield.py   # Self-contained end-to-end integration verifier
├── build_exe.py                 # PyInstaller standalone executable packager
├── pyproject.toml / requirements.txt
└── README.md
```

---

## 5. Key Source Code Implementation Excerpts

### A. Screen Sampler (`flashbang_shield/sampler.py`)
```python
# Downscales screen in GDI to 64x36 (<0.2% CPU, ~0.06 ms per call)
ret = gdi32.StretchBlt(
    hdc_mem, 0, 0, self.thumb_w, self.thumb_h,
    hdc_screen, mon_x, mon_y, mon_w, mon_h,
    0x00CC0020  # SRCCOPY
)
# Read 32-bit DIB section directly via ctypes pointer as numpy array
arr = np.ctypeslib.as_array(self.ppv_bits, shape=(self.thumb_h, self.thumb_w, 4))
b = arr[:, :, 0].astype(np.float32)
g = arr[:, :, 1].astype(np.float32)
r = arr[:, :, 2].astype(np.float32)
lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
mean_lum = float(np.mean(lum))
p90_lum = float(np.percentile(lum, 90))
effective_lum = 0.6 * mean_lum + 0.4 * p90_lum
```

### B. Capture-Exclusion Overlay (`flashbang_shield/overlay.py`)
```python
ex_style = (
    win32con.WS_EX_LAYERED |
    win32con.WS_EX_TRANSPARENT |      # Click-through: mouse clicks pass directly to apps
    win32con.WS_EX_TOPMOST |          # Always on top of normal windows
    win32con.WS_EX_TOOLWINDOW |       # Omit from Alt+Tab task switcher
    win32con.WS_EX_NOACTIVATE         # Never steals focus
)
hwnd = win32gui.CreateWindowEx(
    ex_style, wc_name, "FlashbangShieldOverlay",
    win32con.WS_POPUP | win32con.WS_VISIBLE,
    x, y, w, h, 0, 0, hinst, None
)

# CRITICAL: Exclude overlay window from all Windows desktop capture APIs
# Value 0x00000011 = WDA_EXCLUDEFROMCAPTURE (Win10 2004+)
user32.SetWindowDisplayAffinity(hwnd, 0x00000011)

# Dynamic 60 FPS opacity updates
user32.SetLayeredWindowAttributes(hwnd, 0, int(round(alpha * 255)), 0x00000002)
```

### C. Asymmetric Easing Math (`flashbang_shield/engine.py`)
```python
def _apply_asymmetric_easing(self, current_dim: float, target_dim: float, dt: float) -> float:
    if dt <= 0:
        return current_dim
    if target_dim > current_dim:
        # Attack phase (suppressing bright flashbang) - FAST (~180 ms)
        tau = max(0.02, self.config.attack_speed_ms / 1000.0)
    else:
        # Decay phase (recovering to dark theme) - SMOOTH (~600 ms)
        tau = max(0.05, self.config.decay_speed_ms / 1000.0)
    
    alpha = 1.0 - math.exp(-dt / tau)
    return current_dim + (target_dim - current_dim) * alpha
```

---

## 6. Questions & Improvement Areas for Advanced Consultation

1. **Hardware Cursor Glare**:
   - On Windows, hardware mouse cursors are rendered by the GPU display scanout engine *above* layered windows (`WS_EX_TOPMOST`). When the screen dims, the white mouse pointer remains at full brightness.
   - *Question*: What is the cleanest, lowest-latency method to either dim the cursor synchronously, apply a custom dark cursor scheme during dimming, or use an API like `MagSetColorTransform` / Windows Magnification API without introducing input lag or cursor flicker?

2. **HDR / Wide Color Gamut (WCG)**:
   - On Windows 11 with HDR enabled, SDR applications are mapped to an SDR white level (e.g., 200 nits) while HDR content (or pure white SDR in HDR mode) can hit 600–1000+ nits.
   - *Question*: How should screen luminance sampling and overlay blending be adapted for Windows Advanced Color / HDR displays? Does GDI capture clip HDR highlights, and should we transition to Windows Graphics Capture (WGC) or DirectX Desktop Duplication (DXGI) for HDR metadata?

3. **GDI vs. DirectX Desktop Duplication (DXGI) vs. Windows Graphics Capture (WGC)**:
   - Current GDI downsampling takes ~0.06 ms on a 64x36 thumbnail, which is very lightweight.
   - *Question*: Are there architectural edge cases where GDI fails (e.g., exclusive fullscreen DirectX 12 games, Protected Media Path / DRM video players like Netflix/Protected browsers)? How should a robust fallback chain be designed?

4. **Hybrid Overlay + DDC/CI Hardware Backlight**:
   - The layered overlay provides instantaneous 60 FPS transitions without hardware latency, but does not save laptop battery or reduce actual physical backlight emission.
   - *Question*: How could an asynchronous dual-layer controller be architected, where the transparent overlay instantly absorbs the sudden flash (0–200 ms), and a slow background thread gradually negotiates physical DDC/CI or Windows WMI backlight changes over 2–5 seconds while relaxing the overlay proportionally?

5. **Eye Tracking / Focused Window Weighting**:
   - Currently, luminance is calculated full-screen (60% mean + 40% 90th percentile).
   - *Question*: Should the active focused window (retrieved via `GetForegroundWindow` / `GetWindowRect`) be weighted more heavily (e.g., 70% active window, 30% background)? How to avoid jarring jumps when switching focus between windows on a split-screen desktop?

6. **Machine Learning / Predictive Tab Switch Anticipation**:
   - Can browser tab switching or navigation clicks be anticipated (e.g., detecting `Ctrl+Tab`, browser URL enter, or link click) to pre-arm the shield 50 ms before the white page renders?
