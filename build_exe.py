"""PyInstaller build script for Flashbang Shield.

Packages the project into a high-performance standalone Windows executable.
Excludes unused Qt modules (QtWebEngine, QtMultimedia, etc.) to keep the binary lean.
"""

from pathlib import Path
import subprocess
import sys


EXCLUDES = [
    "PySide6.QtWebEngine",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.Qt3DCore",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtNetwork",
    "PySide6.QtPdf",
    "tkinter",
    "unittest",
    "scipy",
    "matplotlib",
    "pandas",
    "sklearn",
    "torch",
]


def build():
    root = Path(__file__).resolve().parent
    entrypoint = root / "flashbang_shield" / "main.py"
    output_dir = root / "dist"
    build_dir = root / "build"
    icon_path = root / "flashbang_shield" / "assets" / "icon.ico"
    assets_dir = root / "flashbang_shield" / "assets"
    dll_path = root / "flashbang_shield" / "shield_core.dll"

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
        "--collect-all",
        "qfluentwidgets",
        "--hidden-import",
        "win32gui",
        "--hidden-import",
        "win32con",
        "--hidden-import",
        "win32api",
        "--hidden-import",
        "win32process",
        "--hidden-import",
        "screeninfo",
        "--hidden-import",
        "PIL",
        "--hidden-import",
        "numpy",
        "--hidden-import",
        "pystray",
        "--hidden-import",
        "PySide6",
    ]

    for ex in EXCLUDES:
        cmd.extend(["--exclude-module", ex])

    if assets_dir.exists():
        cmd.extend(["--add-data", f"{assets_dir};flashbang_shield/assets"])

    if dll_path.exists():
        cmd.extend(["--add-data", f"{dll_path};flashbang_shield"])

    if icon_path.exists():
        cmd.extend(["--icon", str(icon_path)])

    cmd.append(str(entrypoint))

    print("Executing PyInstaller command:")
    print(" ".join(cmd))

    result = subprocess.run(cmd, cwd=str(root))
    if result.returncode == 0:
        exe_path = output_dir / "FlashbangShield.exe"
        if exe_path.exists():
            import shutil
            portable_path = output_dir / "FlashbangShield-Portable.exe"
            shutil.copy2(exe_path, portable_path)
            size_mb = portable_path.stat().st_size / (1024 * 1024)
            print(f"\nBuild successful! Standalone & Portable binaries created at:")
            print(f"  - {exe_path} ({size_mb:.2f} MB)")
            print(f"  - {portable_path} ({size_mb:.2f} MB)")
        else:
            print(f"\nBuild finished successfully in: {output_dir}")
    else:
        print(f"\nBuild failed with exit code {result.returncode}")
        sys.exit(result.returncode)


if __name__ == "__main__":
    build()
