"""GitHub-powered Pterodactyl entrypoint for Hereid."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

GITHUB_RAW = "https://raw.githubusercontent.com/WhiteBelStudio/Hereid/main"
GITHUB_API = "https://api.github.com/repos/WhiteBelStudio/Hereid/contents"


def request_bytes(url: str, *, accept: str = "application/octet-stream") -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Hereid-Pterodactyl-Launcher",
            "Accept": accept,
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def download_bytes(filename: str, required_marker: bytes) -> bytes:
    """Try GitHub raw CDN, then the GitHub Contents API."""
    errors: list[str] = []
    try:
        data = request_bytes(f"{GITHUB_RAW}/{filename}")
        if data and required_marker in data:
            return data
        errors.append("raw response failed validation")
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        errors.append(f"raw: {type(exc).__name__}")

    try:
        payload = request_bytes(
            f"{GITHUB_API}/{filename}?ref=main",
            accept="application/vnd.github+json",
        )
        document = json.loads(payload.decode("utf-8"))
        if document.get("encoding") == "base64" and document.get("content"):
            data = base64.b64decode(document["content"])
        else:
            # Some API responses may honor a raw media type instead of JSON.
            data = payload
        if data and required_marker in data:
            return data
        errors.append("API response failed validation")
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        errors.append(f"API: {type(exc).__name__}")

    raise RuntimeError(f"could not download {filename} from GitHub ({'; '.join(errors)})")


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)

    auto_update = (
        os.getenv("AUTO_UPDATE_MAIN", "1").strip().lower()
        not in {"0", "false", "no"}
    )
    files = {
        "requirements.txt": b"aiogram",
        "main.py": b"async def main",
        "profile.py": b"def format_participant_profile",
    }

    if auto_update:
        staged_data: dict[str, bytes] = {}
        try:
            # Download and validate every file before changing any local copy.
            for filename, marker in files.items():
                staged_data[filename] = download_bytes(filename, marker)

            staged_paths: dict[str, Path] = {}
            for filename, data in staged_data.items():
                temp_path = root / f".{filename}.download"
                temp_path.write_bytes(data)
                staged_paths[filename] = temp_path

            for filename, temp_path in staged_paths.items():
                temp_path.replace(root / filename)

            print(
                "[startup] Downloaded requirements.txt, main.py and "
                "profile.py from GitHub."
            )
        except Exception as exc:
            for filename in files:
                try:
                    (root / f".{filename}.download").unlink(missing_ok=True)
                except OSError:
                    pass

            missing = [name for name in files if not (root / name).is_file()]
            if missing:
                print(
                    f"FATAL: GitHub download failed ({type(exc).__name__}); "
                    f"missing: {', '.join(missing)}. Check outbound HTTPS/DNS "
                    "access to raw.githubusercontent.com and api.github.com.",
                    file=sys.stderr,
                )
                return 2

            print(
                f"[startup] WARNING: GitHub download failed "
                f"({type(exc).__name__}); using existing local files.",
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
        print(
            f"FATAL: could not load configuration ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_TOKEN_FROM_BOTFATHER":
        print(
            "FATAL: BOT_TOKEN is missing. Set BOT_TOKEN in Pterodactyl "
            "Startup variables. Never commit a real token to GitHub.",
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
        print(
            f"FATAL: database directory is not writable ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2

    os.environ["DB_PATH"] = str(db_path)
    print(f"[startup] Python: {sys.version.split()[0]}")
    print(f"[startup] Database: {db_path}")
    print("[startup] Starting Telegram bot...")

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
