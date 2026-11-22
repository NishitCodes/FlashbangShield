"""Helper script to install/update the Flashbang Shield application icon.

Usage:
    python update_icon.py <path_to_image_file>

Accepts any common image format (PNG, JPG, WEBP, ICO) and generates:
- flashbang_shield/assets/icon.png (high-resolution 512x512 RGBA)
- flashbang_shield/assets/icon.ico (multi-resolution: 16, 24, 32, 48, 64, 128, 256)
"""

import sys
from pathlib import Path
from PIL import Image

ASSETS_DIR = Path(__file__).resolve().parent / "flashbang_shield" / "assets"
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def install_icon(image_path: Path) -> bool:
    if not image_path.exists():
        print(f"Error: File not found: {image_path}", file=sys.stderr)
        return False

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    dst_png = ASSETS_DIR / "icon.png"
    dst_ico = ASSETS_DIR / "icon.ico"

    print(f"Processing image: {image_path}")
    im = Image.open(image_path).convert("RGBA")

    # Center crop to square if not square
    w, h = im.size
    if w != h:
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        im = im.crop((left, top, left + min_dim, top + min_dim))
        print(f"Cropped to square: {min_dim}x{min_dim}")

    # Resize to clean 512x512
    im_512 = im.resize((512, 512), Image.Resampling.LANCZOS)
    im_512.save(dst_png, format="PNG")
    print(f"Saved: {dst_png} (512x512 PNG)")

    # Generate multi-resolution ICO
    im_512.save(dst_ico, format="ICO", sizes=ICO_SIZES)
    print(f"Saved: {dst_ico} (Multi-resolution Windows ICO with sizes 16 to 256)")
    print("\nIcon updated successfully! Flashbang Shield will now use this custom icon.")
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_path = Path(sys.argv[1])
    else:
        # Check default locations
        candidates = [
            ASSETS_DIR / "new_icon.png",
            Path.home() / "Desktop" / "icon.png",
            Path(__file__).resolve().parent / "new_icon.png",
        ]
        target_path = next((c for c in candidates if c.exists()), None)
        if not target_path:
            print("Usage: python update_icon.py <path_to_icon_image>")
            sys.exit(1)

    success = install_icon(target_path)
    sys.exit(0 if success else 1)
