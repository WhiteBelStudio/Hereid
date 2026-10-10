"""Reliable Pterodactyl entrypoint for heradeLegend."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)

    requirements = root / "requirements.txt"
    if not requirements.is_file():
        print("FATAL: requirements.txt is missing; upload the complete repository.", file=sys.stderr)
        return 2

    print("[startup] Installing/checking Python dependencies...")
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-warn-script-location",
                "-r",
                str(requirements),
            ],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"FATAL: dependency installation failed (exit code {exc.returncode}).", file=sys.stderr)
        return exc.returncode or 1

    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except Exception as exc:
        print(f"FATAL: could not load environment configuration ({type(exc).__name__}).", file=sys.stderr)
        return 2

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_TOKEN_FROM_BOTFATHER":
        print(
            "FATAL: BOT_TOKEN is missing. Set it in Pterodactyl Startup variables "
            "or in a local .env file. Never commit a real token to GitHub.",
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
    print("[startup] Starting Telegram bot; token and proxy credentials are never printed.")

    try:
        from main import main as run_bot
        import asyncio
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("[startup] Bot stopped.")
        return 0
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code
    except Exception as exc:
        print(f"FATAL: bot stopped with an unexpected error ({type(exc).__name__}).", file=sys.stderr)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
