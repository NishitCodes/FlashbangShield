# Flashbang Shield 🛡️

**Flashbang Shield** is a lightweight, open-source Windows utility that eliminates the visual shock ("flashbang") when switching between dark applications and blinding white browser tabs, documents, or windows.

---

## The Problem

When using dark-mode tools (like an IDE, terminal, or dark website) and switching to a bright white tab (like a blank document, search page, or PDF), your eyes experience sudden glare and pupil strain. The human pupillary light reflex takes approximately 200–300 ms to respond, resulting in uncomfortable eye shock.

---

## How Flashbang Shield Works

1. **Sub-Millisecond Sampling**: Continuously measures the average luminance of each display using lightweight desktop downsampling (<0.2% CPU overhead).
2. **Asymmetric Easing**:
   - **Attack (~180 ms)**: Reacts rapidly to sudden white flashes to soften the blow before your eyes experience strain.
   - **Decay (~600 ms)**: Smoothly and gently relaxes back to standard brightness when returning to dark windows.
3. **Transparent Layered Overlay**: Uses native Windows `WS_EX_LAYERED | WS_EX_TRANSPARENT` windows to apply a pure optical tint, preserving native display contrast without distorting gamma curves.
4. **Capture-Exclusion (`WDA_EXCLUDEFROMCAPTURE`)**: The dimming overlay is excluded from screen capture APIs, preventing feedback loops.
5. **Per-Monitor Adaptation**: Monitors each display independently.

---

## Comparison: Flashbang Shield vs. Prior Tools

| Dimension | Windows 11 CABC | Gammy (Legacy) | Flashbang Shield |
|---|---|---|---|
| **Goal** | Power conservation | Adaptive brightness | **Eye strain & flashbang prevention** |
| **Dimming Method** | Hardware backlight | GPU Gamma ramps | **Zero-distortion layered overlay** |
| **Color Fidelity** | Shifts color temperature | Crushes blacks/contrast | **100% true color preservation** |
| **Capture Immunity** | Hardware level | None | **`WDA_EXCLUDEFROMCAPTURE`** |
| **Easing** | Abrupt stepping | Linear | **Biologically-tuned asymmetric easing** |
| **Multi-Display** | Internal panel only | Global | **Independent per-monitor overlays** |

---

## Global Shortcuts

- `Win + Alt + Up`: Increase target brightness (+5%)
- `Win + Alt + Down`: Decrease target brightness (-5%)
- `Win + Alt + O`: Toggle Flashbang Shield on/off

---

## Quick Start

```powershell
# Install requirements
pip install -r requirements.txt

# Run Flashbang Shield
python -m flashbang_shield.main
```

To build a standalone executable:
```powershell
python build_exe.py
```
