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

OWNER = "WhiteBelStudio"
REPO = "Hereid"
BRANCH = "main"  # Always deploy the verified production branch.
GITHUB_RAW = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/{BRANCH}"
GITHUB_API = f"https://api.github.com/repos/{OWNER}/{REPO}/contents"


def request_bytes(url: str, *, accept: str = "application/octet-stream") -> bytes:
    headers = {
        "User-Agent": "Hereid-Pterodactyl-Launcher",
        "Accept": accept,
    }
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def download_bytes(filename: str, required_marker: bytes) -> bytes:
    """Download public files directly, with authenticated Contents API fallback."""
    errors: list[str] = []

    try:
        data = request_bytes(f"{GITHUB_RAW}/{filename}")
        if data and required_marker in data:
            return data
        errors.append("raw: downloaded content failed validation")
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            errors.append(f"raw: HTTP {exc.code}")
        else:
            errors.append(f"raw: {type(exc).__name__}")

    try:
        payload = request_bytes(
            f"{GITHUB_API}/{filename}?ref={BRANCH}",
            accept="application/vnd.github+json",
        )
        document = json.loads(payload.decode("utf-8"))
        if document.get("encoding") != "base64" or not document.get("content"):
            raise ValueError("GitHub API did not return base64 file content")
        data = base64.b64decode(document["content"], validate=False)
        if not data or required_marker not in data:
            raise ValueError("downloaded content failed validation")
        return data
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            errors.append(f"API: HTTP {exc.code}")
        else:
            errors.append(f"API: {type(exc).__name__}: {exc}")

    if not os.getenv("GITHUB_TOKEN", "").strip():
        errors.append(
            "GITHUB_TOKEN is not set; if this repository is private, "
            "add a fine-grained token with Contents: Read-only access"
        )
    raise RuntimeError(f"{filename}: " + "; ".join(errors))


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)

    # GitHub is the source of truth. Never start a stale local copy when
    # downloading the current source fails.
    files = {
        "requirements.txt": b"aiogram",
        "main.py": b"timeout=disabled",
        "profile.py": b"def format_participant_profile",
        "start.py": b"router",
    }
    staged_data: dict[str, bytes] = {}
    staged_paths: dict[str, Path] = {}
    try:
        # Download and validate every required file before replacing any file.
        for filename, marker in files.items():
            data = download_bytes(filename, marker)
            if filename.endswith(".py"):
                compile(data, filename, "exec")
            staged_data[filename] = data

        for filename, data in staged_data.items():
            temp_path = root / f".{filename}.download"
            temp_path.write_bytes(data)
            staged_paths[filename] = temp_path

        for filename, temp_path in staged_paths.items():
            temp_path.replace(root / filename)

        print(
            f"[startup] Downloaded and validated source from "
            f"{OWNER}/{REPO}@{BRANCH}; HTTP timeout disabled."
        )
    except Exception as exc:
        for filename in files:
            try:
                (root / f".{filename}.download").unlink(missing_ok=True)
            except OSError:
                pass
        print(f"FATAL: GitHub source download/validation failed: {exc}", file=sys.stderr)
        print(
            "The bot will not start from stale local source. Check repository "
            "visibility, GITHUB_TOKEN permissions, branch name, and outbound "
            "HTTPS/DNS access.",
            file=sys.stderr,
        )
        return 2

    requirements = root / "requirements.txt"
    if not requirements.is_file():
        print("FATAL: requirements.txt is missing.", file=sys.stderr)
        return 2

    print("[startup] Installing dependencies from requirements.txt...")
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
            f"FATAL: pip could not install requirements "
            f"(exit code {exc.returncode}).",
            file=sys.stderr,
        )
        return exc.returncode or 1

    try:
        from dotenv import load_dotenv
        load_dotenv(root / ".env")
    except Exception as exc:
        print(
            f"FATAL: could not load .env ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 2

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "PASTE_TOKEN_FROM_BOTFATHER":
        print(
            "FATAL: BOT_TOKEN is missing. Add it to Pterodactyl Startup "
            "variables; do not commit it to GitHub.",
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
        print(f"FATAL: database path is not writable ({exc}).", file=sys.stderr)
        return 2

    os.environ["DB_PATH"] = str(db_path)
    print(f"[startup] Python: {sys.version.split()[0]}")
    print(f"[startup] Database: {db_path}")
    print("[startup] Launching Hereid Telegram bot...")

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
        print(f"FATAL: bot stopped ({type(exc).__name__}): {exc}", file=sys.stderr)
        raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
