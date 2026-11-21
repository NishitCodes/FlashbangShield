"""
PyInstaller build script for Flashbang Shield.
Packages the project into a single standalone Windows executable.
"""

import sys
import subprocess
from pathlib import Path

def build():
    root = Path(__file__).resolve().parent
    entrypoint = root / "flashbang_shield" / "main.py"
    output_dir = root / "dist"
    build_dir = root / "build"
    icon_path = root / "flashbang_shield" / "assets" / "icon.ico"

    if not entrypoint.exists():
        print(f"Error: Entrypoint not found at {entrypoint}")
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "FlashbangShield",
        "--distpath",
        str(output_dir),
        "--workpath",
        str(build_dir),
        "--hidden-import", "win32gui",
        "--hidden-import", "win32con",
        "--hidden-import", "win32api",
        "--hidden-import", "win32process",
        "--hidden-import", "screeninfo",
        "--hidden-import", "PIL",
        "--hidden-import", "numpy",
        "--hidden-import", "pystray",
    ]

    if icon_path.exists():
        cmd.extend(["--icon", str(icon_path)])

    cmd.append(str(entrypoint))

    print("Executing PyInstaller command:")
    print(" ".join(cmd))

    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode == 0:
        exe_path = output_dir / "FlashbangShield.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"\nBuild successful! Binary created at: {exe_path} ({size_mb:.2f} MB)")
        else:
            print(f"\nBuild finished successfully in: {output_dir}")
    else:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(result.returncode)

if __name__ == "__main__":
    build()
