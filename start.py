"""Welcome menu, community rules and role-aware command guide."""
from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

router = Router(name="start")
log = logging.getLogger("heradeLegend.start")


def main_menu_keyboard(is_staff: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📜 Правила сообщества", callback_data="start:rules")],
        [InlineKeyboardButton(text="📚 Команды и ключевые слова", callback_data="start:commands")],
    ]
    rows.append(
        [InlineKeyboardButton(text="📨 Обратиться к администрации", callback_data="start:contact")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(is_staff: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="start:home")]
        ]
    )


def welcome_text(name: str, is_staff: bool = False) -> str:
    safe_name = html.escape(name or "участник")
    staff_line = (
        "\n🛡️ Для тебя доступен раздел сотрудников."
        if is_staff
        else ""
    )
    return (
        "🕊️ <b>HERADELEGEND</b>\n\n"
        f"👋 Добро пожаловать, <b>{safe_name}</b>!\n\n"
        "Я помогаю поддерживать порядок, знакомиться с правилами "
        "и пользоваться возможностями сообщества.\n\n"
        "✨ <b>В меню ты найдёшь:</b>\n"
        "📜 правила и важные положения;\n"
        "📚 команды, RP-действия и ключевые слова;\n"
        "📨 способ обратиться к администрации."
        f"{staff_line}\n\n"
        "👇 <b>Выбери нужный раздел:</b>"
    )


PUBLIC_COMMANDS = (
    "👤 <b>КОМАНДЫ УЧАСТНИКА</b>\n\n"
    "• <code>/start</code> — главное меню.\n"
    "• <code>/help</code>, <code>помощь</code>, <code>команды</code> — справка.\n"
    "• <code>/profile</code>, <code>/myprofile</code>, <code>/профиль</code> — профиль.\n"
    "• <code>/xp</code>, <code>/level</code>, <code>/mylevel</code>, <code>опыт</code>, <code>уровень</code> — опыт и уровень.\n"
    "• <code>/setbio ТЕКСТ</code> — установить описание профиля; <code>/mybio</code> — посмотреть его.\n"
    "• <code>/rewards</code>, <code>награды</code>, <code>мои награды</code> — награды.\n"
    "• <code>/topxp</code>, <code>/xptop</code>, <code>/топопыта</code>, <code>топ опыта</code> — рейтинг опыта.\n"
    "• <code>/rep</code>, <code>/репутация</code> — репутация; ответь на сообщение участника, чтобы оценить его.\n"
    "• <code>жалоба</code>, <code>апелляция</code>, <code>поддержка</code>, <code>помогите</code> — обратиться к администрации.\n\n"
    "🎭 <b>RP-КЛЮЧЕВЫЕ СЛОВА</b>\n"
    "В каждой группе они могут отличаться. Ниже бот покажет действия, включённые именно в этом чате. "
    "Отправляй ключевое слово отдельным сообщением."
)

ADMIN_COMMANDS = (
    "\n\n🛡️ <b>КОМАНДЫ МОДЕРАЦИИ</b>\n\n"
    "Команды ниже доступны только сотрудникам с соответствующими правами. "
    "Для действий над участником обычно нужно ответить на его сообщение.\n"
    "• Бан: <code>бан 7д спам</code> / <code>ban 7д спам</code>; без срока: <code>бан спам</code>.\n"
    "• Разбан: <code>разбан TELEGRAM_ID</code> / <code>unban TELEGRAM_ID</code>.\n"
    "• Мут: <code>мут 30м флуд</code> / <code>mute 30м флуд</code>.\n"
    "• Снять мут: <code>размут</code> / <code>unmute</code>.\n"
    "• Предупреждение: <code>варн причина</code>, <code>пред причина</code>, <code>предупреждение причина</code>.\n"
    "• Список предупреждений: <code>варны</code> / <code>преды</code>; снять последнее: <code>снять варн</code> / <code>снять пред</code>.\n"
    "• Выговор: <code>выговор причина</code>; список: <code>выговоры</code>; снять: <code>снять выговор</code>.\n"
    "• Панель: <code>/admin</code>, <code>админ меню</code>, <code>меню админа</code>, <code>панель админа</code>.\n"
    "• Сроки: <code>30м</code> — минуты, <code>2ч</code> — часы, <code>7д</code> — дни. Причину указывай после срока.\n\n"
    "⚠️ Наличие команды в справке не выдаёт права автоматически. "
    "Действия проверяют роль и разрешения в группе."
)


async def _is_staff(chat_id: int, user_id: int, is_private: bool) -> bool:
    """Use the bot's actual role model; never trust a button or user-supplied ID alone."""
    try:
        from main import OWNER_TELEGRAM_ID, db, has_permission

        if OWNER_TELEGRAM_ID.isdigit() and user_id == int(OWNER_TELEGRAM_ID):
            return True

        if not is_private:
            return await has_permission(chat_id, user_id, "moderate")

        # In private chat, use configured moderation roles or the configured owner ID.
        # Telegram group-admin status is only verifiable in the specific group.
        with db() as con:
            row = con.execute(
                "SELECT 1 FROM role_assignments WHERE user_id=? "
                "AND role IN ('admin','moderator') LIMIT 1",
                (user_id,),
            ).fetchone()
        return row is not None
    except Exception as exc:
        log.warning("Could not resolve staff status (%s).", type(exc).__name__)
        return False


async def _rules_text(chat_id: int, is_private: bool) -> str:
    try:
        from rules import DEFAULT_RULES, LEGACY_DATABASE_DEFAULT

        if is_private:
            rules = DEFAULT_RULES
            footer = (
                "\\n\\n<i>Это базовые правила. В конкретной группе администрация может настроить дополнительные правила.</i>"
            )
        else:
            from main import get_chat_settings

            settings = get_chat_settings(chat_id)
            configured_rules = str(settings.get("rules_text") or "").strip()
            # Keep group-specific rules when an administrator has customized them.
            # For untouched legacy defaults, use the dedicated rules.py module.
            rules = (
                configured_rules
                if configured_rules and configured_rules != LEGACY_DATABASE_DEFAULT
                else DEFAULT_RULES
            )
            footer = ""

        if not rules:
            return (
                "📜 <b>ПРАВИЛА СООБЩЕСТВА</b>\\n\\n"
                "Правила пока не настроены. Обратись к администрации группы."
            )

        # Telegram HTML blockquote displays the full rules as one quoted block.
        result = (
            "📜 <b>ПРАВИЛА СООБЩЕСТВА</b>\\n\\n"
            "<blockquote>" + html.escape(rules) + "</blockquote>" + footer
        )
        if len(result) > 3900:
            return (
                "⚠️ Правила этой группы слишком длинные для одного сообщения Telegram. "
                "Попроси администрацию сократить их до 3900 символов."
            )
        return result
    except Exception as exc:
        log.warning("Could not load rules for chat_id=%s (%s).", chat_id, type(exc).__name__)
        return "⚠️ Не удалось загрузить правила. Попробуй позже или обратись к администрации."

async def _rp_keywords_text(chat_id: int, is_private: bool) -> str:
    if is_private:
        return (
            "🎭 <b>RP-КЛЮЧЕВЫЕ СЛОВА</b>\n\n"
            "Список зависит от конкретной группы. Открой <code>/help</code> в нужном чате, "
            "чтобы увидеть активные действия и их ключевые слова."
        )

    try:
        from main import db

        with db() as con:
            rows = con.execute(
                "SELECT name, keywords FROM rp_actions "
                "WHERE chat_id=? AND is_enabled=1 "
                "ORDER BY name COLLATE NOCASE, id",
                (chat_id,),
            ).fetchall()

        if not rows:
            return (
                "🎭 <b>RP-КЛЮЧЕВЫЕ СЛОВА</b>\n\n"
                "В этой группе пока нет включённых RP-действий."
            )

        lines = ["🎭 <b>RP-КЛЮЧЕВЫЕ СЛОВА ЭТОЙ ГРУППЫ</b>"]
        for row in rows:
            name = html.escape(str(row["name"] or "RP-действие"))
            words = [
                html.escape(part.strip())
                for part in str(row["keywords"] or "").split(",")
                if part.strip()
            ]
            if words:
                lines.append(f"• <b>{name}</b>: <code>{', '.join(words)}</code>")

        if len(lines) == 1:
            lines.append("У включённых действий пока нет ключевых слов.")
        # Keep the Telegram HTML message below its limit without slicing markup.
        selected = [lines[0]]
        omitted = False
        for line in lines[1:]:
            candidate = "\n".join(selected + [line])
            if len(candidate) > 3500:
                omitted = True
                break
            selected.append(line)
        if omitted:
            selected.append("<i>Список сокращён. Полный список доступен через /help в группе.</i>")
        return "\n".join(selected)
    except Exception as exc:
        log.warning("Could not load RP keywords for chat_id=%s (%s).", chat_id, type(exc).__name__)
        return "⚠️ Не удалось загрузить ключевые слова этой группы."


def _message_chat(message: Message) -> tuple[int, bool]:
    return message.chat.id, message.chat.type == "private"


def _callback_chat(callback: CallbackQuery) -> tuple[int | None, bool]:
    message = callback.message
    if message is None:
        return None, True
    return message.chat.id, message.chat.type == "private"


async def _send_commands(
    target: Message,
    chat_id: int,
    is_private: bool,
    user_id: int,
) -> None:
    staff = await _is_staff(chat_id, user_id, is_private)
    text = PUBLIC_COMMANDS + (ADMIN_COMMANDS if staff else "")
    await target.answer(text, reply_markup=back_keyboard(staff))
    # RP keyword lists can be long; send separately to respect Telegram's message limit.
    await target.answer(await _rp_keywords_text(chat_id, is_private))


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    chat_id, is_private = _message_chat(message)
    user = message.from_user
    user_id = user.id if user else 0
    name = user.first_name if user else "участник"
    staff = await _is_staff(chat_id, user_id, is_private) if user else False
    await message.answer(
        welcome_text(name, staff),
        reply_markup=main_menu_keyboard(staff),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    chat_id, is_private = _message_chat(message)
    user_id = message.from_user.id if message.from_user else 0
    await _send_commands(message, chat_id, is_private, user_id)


@router.callback_query(F.data == "start:home")
async def callback_home(callback: CallbackQuery) -> None:
    chat_id, is_private = _callback_chat(callback)
    if callback.message is not None and chat_id is not None:
        staff = await _is_staff(chat_id, callback.from_user.id, is_private)
        await callback.message.edit_text(
            welcome_text(callback.from_user.first_name, staff),
            reply_markup=main_menu_keyboard(staff),
        )
    await callback.answer()


@router.callback_query(F.data == "start:rules")
async def callback_rules(callback: CallbackQuery) -> None:
    chat_id, is_private = _callback_chat(callback)
    if callback.message is not None and chat_id is not None:
        staff = await _is_staff(chat_id, callback.from_user.id, is_private)
        await callback.message.edit_text(
            await _rules_text(chat_id, is_private),
            reply_markup=back_keyboard(staff),
        )
    await callback.answer()


@router.callback_query(F.data == "start:commands")
async def callback_commands(callback: CallbackQuery) -> None:
    chat_id, is_private = _callback_chat(callback)
    if callback.message is not None and chat_id is not None:
        staff = await _is_staff(chat_id, callback.from_user.id, is_private)
        command_text = PUBLIC_COMMANDS + (ADMIN_COMMANDS if staff else "")
        await callback.message.edit_text(
            command_text,
            reply_markup=back_keyboard(staff),
        )
        await callback.message.answer(await _rp_keywords_text(chat_id, is_private))
    await callback.answer()


@router.callback_query(F.data == "start:contact")
async def callback_contact(callback: CallbackQuery) -> None:
    chat_id, is_private = _callback_chat(callback)
    if is_private:
        text = (
            "📨 <b>Обращение к администрации</b>\n\n"
            "Напиши в нужной группе сообщение со словом "
            "<code>жалоба</code>, <code>апелляция</code>, "
            "<code>поддержка</code> или <code>помогите</code>."
        )
    else:
        text = (
            "📨 <b>Обращение к администрации</b>\n\n"
            "Опиши проблему и напиши слово <code>жалоба</code>, "
            "<code>апелляция</code>, <code>поддержка</code> или <code>помогите</code>. "
            "Не отправляй пароли и коды подтверждения."
        )
    staff = await _is_staff(chat_id or 0, callback.from_user.id, is_private)
    if callback.message is not None:
        await callback.message.edit_text(text, reply_markup=back_keyboard(staff))
    await callback.answer()
