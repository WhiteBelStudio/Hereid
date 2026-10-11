"""Formatting helpers for full participant profiles in heradeLegend."""

from __future__ import annotations

import html
from typing import Any


def format_participant_profile(
    *,
    display_name: str,
    username: str,
    user_id: int,
    profile: dict[str, Any],
    progress: dict[str, int],
) -> str:
    """Build an HTML-safe profile card from Telegram identity and stored group stats."""
    safe_name = html.escape((display_name or "Участник")[:128])
    safe_username = html.escape(username.lstrip("@")[:64]) if username else ""
    bio = str(profile.get("bio", "") or "").strip()
    safe_bio = html.escape(bio[:250]) if bio else "Пока не заполнено."

    xp = max(0, int(profile.get("xp", 0) or 0))
    messages = max(0, int(profile.get("eligible_messages", 0) or 0))
    level = max(1, int(progress.get("level", 1) or 1))
    current_xp = max(0, int(progress.get("current_xp", 0) or 0))
    needed_xp = max(1, int(progress.get("needed_xp", 1) or 1))
    percent = min(100, max(0, int(progress.get("percent", 0) or 0)))
    filled = percent // 10
    bar = "🟦" * filled + "⬜" * (10 - filled)
    username_line = f"\n🔗 Username: <code>@{safe_username}</code>" if safe_username else "\n🔗 Username: <i>не указан</i>"

    return (
        "👤 <b>ПРОФИЛЬ УЧАСТНИКА</b>\n\n"
        f"🪪 Имя: <b>{safe_name}</b>"
        f"{username_line}\n"
        f"🆔 Telegram ID: <code>{int(user_id)}</code>\n\n"
        f"🏅 Уровень: <b>{level}</b>\n"
        f"⭐ Опыт: <b>{xp} XP</b>\n"
        f"📈 Прогресс уровня: <b>{current_xp}/{needed_xp} XP</b>\n"
        f"{bar} <b>{percent}%</b>\n"
        f"💬 Учтённых сообщений: <b>{messages}</b>\n\n"
        f"📝 <b>О себе</b>\n{safe_bio}"
    )
