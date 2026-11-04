"""Build the Windows endpoint sensor as a one-file PyInstaller executable."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
SENSOR = ROOT / "sensor_agent.py"
CONFIG = ROOT / "config.json"
DIST = ROOT / "dist"
WORK = ROOT / "build" / "sensor-agent"


def _install_pyinstaller() -> None:
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller"],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--noconsole",
        action="store_true",
        help="Build a deployment executable that writes logs beside itself.",
    )
    parser.add_argument(
        "--install-pyinstaller",
        action="store_true",
        help="Install PyInstaller into the active Python environment if missing.",
    )
    parser.add_argument(
        "--force-config",
        action="store_true",
        help="Replace an existing dist/config.json with this repository's example.",
    )
    args = parser.parse_args()

    if os.name != "nt":
        parser.error("Build the Windows executable on Windows.")
    missing = [
        package for package in ("psutil", "requests", "watchdog")
        if importlib.util.find_spec(package) is None
    ]
    if missing:
        parser.error(
            "Missing runtime dependencies: "
            + ", ".join(missing)
            + ". Install them with: python -m pip install -r requirements.txt"
        )
    if importlib.util.find_spec("PyInstaller") is None:
        if not args.install_pyinstaller:
            parser.error(
                "PyInstaller is missing. Install it with "
                "'python -m pip install pyinstaller' or pass --install-pyinstaller."
            )
        _install_pyinstaller()

    command = [
        sys.executable, "-m", "PyInstaller",
        "--clean", "--noconfirm", "--onefile",
        "--name", "insighter-sensor",
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
    ]
    if args.noconsole:
        command.append("--noconsole")
    command.append(str(SENSOR))
    subprocess.run(command, cwd=ROOT, check=True)

    DIST.mkdir(parents=True, exist_ok=True)
    target_config = DIST / "config.json"
    if args.force_config or not target_config.exists():
        shutil.copy2(CONFIG, target_config)
        print(f"Copied example configuration to {target_config}")
    else:
        print(f"Preserved existing configuration at {target_config}")
    print(f"Built executable: {DIST / 'insighter-sensor.exe'}")
    print("Edit config.json and set INSIGHTER_SENSOR_API_TOKEN before running the sensor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
