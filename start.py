"""Pterodactyl startup entrypoint for heradeLegend."""
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    requirements = root / "requirements.txt"
    if requirements.exists():
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "-r", str(requirements)
        ])
    os.chdir(root)
    from main import main as run_bot
    import asyncio
    asyncio.run(run_bot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
