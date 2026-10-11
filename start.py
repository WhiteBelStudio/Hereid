"""GitHub-powered Pterodactyl entrypoint for Hereid."""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GITHUB_RAW = "https://raw.githubusercontent.com/WhiteBelStudio/Hereid/main"


def download_file(root: Path, filename: str, required_marker: bytes = b"") -> None:
    """Download and validate a single project file before replacing its local copy."""
    url = f"{GITHUB_RAW}/{filename}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Hereid-Pterodactyl-Launcher"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()

    if not data:
        raise ValueError(f"{filename} download was empty")
    if required_marker and required_marker not in data:
        raise ValueError(f"{filename} failed basic validation")

    temp = root / f".{filename}.download"
    temp.write_bytes(data)
    temp.replace(root / filename)


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)

    # This launcher is the only project file that needs to be uploaded manually.
    # Secrets and the database are never downloaded from GitHub or overwritten.
    if os.getenv("AUTO_UPDATE_MAIN", "1").strip().lower() not in {"0", "false", "no"}:
        files = {
            "requirements.txt": b"aiogram",
            "main.py": b"async def main",
            "profile.py": b"def format_participant_profile",
        }
        try:
            for filename, marker in files.items():
                download_file(root, filename, marker)
            print("[startup] Downloaded requirements.txt, main.py and profile.py from GitHub.")
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            missing = [name for name in files if not (root / name).is_file()]
            if missing:
                print(
                    f"FATAL: GitHub download failed ({type(exc).__name__}); "
                    f"missing files: {', '.join(missing)}.",
                    file=sys.stderr,
                )
                return 2
            print(
                f"[startup] WARNING: GitHub download failed ({type(exc).__name__}); "
                "using existing local files.",
                file=sys.stderr,
            )

    requirements = root / "requirements.txt"
    if not requirements.is_file():
        print("FATAL: requirements.txt is missing.", file=sys.stderr)
        return 2

    print("[startup] Installing/checking dependencies...")
    try:
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--disable-pip-version-check",
                "--no-warn-script-location",
                "-r", str(requirements),
            ],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"FATAL: dependency installation failed (exit code {exc.returncode}).",
            file=sys.stderr,
        )
        return exc.returncode or 1

    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except Exception as exc:
        print(f"FATAL: could not load configuration ({type(exc).__name__}).", file=sys.stderr)
        return 2

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_TOKEN_FROM_BOTFATHER":
        print(
            "FATAL: BOT_TOKEN is missing. Set BOT_TOKEN in Pterodactyl Startup variables. "
            "Do not commit a real token to GitHub.",
            file=sys.stderr,
        )
        return 2

    db_value = os.getenv("DB_PATH", "heradelegend.db").strip() or "heradelegend.db"
    db_path = Path(db_value)
    if not db_path.is_absolute():
        db_path = root / db_path
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"FATAL: database directory is not writable ({type(exc).__name__}).", file=sys.stderr)
        return 2
    os.environ["DB_PATH"] = str(db_path)

    print(f"[startup] Python: {sys.version.split()[0]}")
    print(f"[startup] Database: {db_path}")
    print("[startup] Starting Telegram bot.")

    try:
        import asyncio
        from main import main as run_bot
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("[startup] Bot stopped.")
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        print(f"FATAL: bot stopped ({type(exc).__name__}).", file=sys.stderr)
        raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
