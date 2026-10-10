"""Reliable Pterodactyl entrypoint for heradeLegend."""
from __future__ import annotations

import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)

    # Keep the deployed bot code aligned with the repository without touching
    # .env or the SQLite database. If GitHub is temporarily unavailable, use
    # the already-installed local main.py and report a non-secret warning.
    auto_update = os.getenv("AUTO_UPDATE_MAIN", "1").strip().lower() not in {"0", "false", "no"}
    main_file = root / "main.py"
    if auto_update:
        source_url = "https://raw.githubusercontent.com/WhiteBelStudio/Hereid/main/main.py"
        try:
            request = urllib.request.Request(
                source_url,
                headers={"User-Agent": "heradeLegend-Pterodactyl-Updater"},
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                latest_code = response.read()
            if not latest_code or b"async def main" not in latest_code:
                raise ValueError("downloaded main.py failed basic validation")
            temporary_file = root / "main.py.update"
            temporary_file.write_bytes(latest_code)
            temporary_file.replace(main_file)
            print("[startup] Updated main.py from the configured GitHub repository.")
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            if not main_file.is_file():
                print(
                    f"FATAL: could not download main.py and no local copy exists ({type(exc).__name__}).",
                    file=sys.stderr,
                )
                return 2
            print(
                f"[startup] WARNING: could not update main.py ({type(exc).__name__}); "
                "using the existing local copy.",
                file=sys.stderr,
            )

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
