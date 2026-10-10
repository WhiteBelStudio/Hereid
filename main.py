import asyncio
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command

load_dotenv()
from aiogram.types import (
    CallbackQuery, ChatPermissions, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "heradelegend.db")
PROXY_URL = os.getenv("TELEGRAM_PROXY", "").strip() or None
REQUEST_TIMEOUT = float(os.getenv("TELEGRAM_REQUEST_TIMEOUT", "60"))

if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных сервера Pterodactyl.")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("heradeLegend")

# Proxy is optional. If supplied, it must be a working SOCKS5/HTTP proxy URL.
session = AiohttpSession(proxy=PROXY_URL, timeout=REQUEST_TIMEOUT)
bot = Bot(TOKEN, session=session)
dp = Dispatcher()


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS warnings(
          id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS reprimands(
          id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL, moderator_id INTEGER NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS actions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
          user_id INTEGER, moderator_id INTEGER NOT NULL, action TEXT NOT NULL,
          reason TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        con.commit()


def record(table, chat_id, user_id, moderator_id, reason):
    if table not in ("warnings", "reprimands"):
        raise ValueError("invalid table")
    with db() as con:
        cur = con.execute(
            f"INSERT INTO {table}(chat_id,user_id,moderator_id,reason,created_at) VALUES(?,?,?,?,?)",
            (chat_id, user_id, moderator_id, reason, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        con.commit()
        return cur.lastrowid


def log_action(chat_id, user_id, moderator_id, action, reason):
    with db() as con:
        con.execute(
            "INSERT INTO actions(chat_id,user_id,moderator_id,action,reason,created_at) VALUES(?,?,?,?,?,?)",
            (chat_id, user_id, moderator_id, action, reason, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        con.commit()


def duration(value):
    m = re.fullmatch(r"(\d+)(м|мин|ч|д|дн)", value.lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if n < 1 or n > 365:
        return None
    if unit in ("м", "мин"):
        return timedelta(minutes=n)
    if unit == "ч":
        return timedelta(hours=n)
    return timedelta(days=n)


async def admin(message: Message):
    if not message.from_user or message.chat.type == ChatType.PRIVATE:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)
    except TelegramAPIError:
        return False


async def need_admin(message: Message):
    if await admin(message):
        return True
    await message.reply("⛔ Команда доступна только администраторам чата.")
    return False


def home_keyboard(is_admin=False):
    rows = []
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛡️ Меню администратора", callback_data="admin")])
    rows.extend([
        [InlineKeyboardButton(text="📖 Помощь участникам", callback_data="help")],
        [InlineKeyboardButton(text="⚖️ Правила", callback_data="rules")],
        [InlineKeyboardButton(text="📨 Обратиться к администрации", callback_data="contact")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Команды", callback_data="commands"),
         InlineKeyboardButton(text="📊 Моя статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📖 Помощь", callback_data="help"),
         InlineKeyboardButton(text="⬅️ Главное меню", callback_data="home")],
    ])


HELP = (
    "📖 <b>Помощь heradeLegend</b>\n\n"
    "• Соблюдайте правила и уважайте участников.\n"
    "• Для жалобы напишите «жалоба» и опишите ситуацию.\n"
    "• Если не согласны с наказанием, обратитесь к администрации.\n"
    "• Не отправляйте пароли и коды входа."
)
COMMANDS = (
    "🛡️ <b>Команды модерации</b> (используйте ответом на сообщение)\n\n"
    "<code>бан 7д спам</code> — бан на 7 дней; <code>бан спам</code> — навсегда.\n"
    "<code>разбан TELEGRAM_ID</code> — снять бан.\n"
    "<code>мут 30м флуд</code> — временный мут.\n"
    "<code>размут</code> — снять мут.\n"
    "<code>варн флуд</code> — записать предупреждение.\n"
    "<code>варны</code> — показать предупреждения.\n"
    "<code>снять варн</code> — снять последнее активное предупреждение.\n"
    "<code>выговор неактив</code> — записать выговор сотруднику.\n"
    "<code>выговоры</code> — показать выговоры.\n"
    "<code>снять выговор</code> — снять последний активный выговор.\n"
    "<code>админ меню</code> — панель администратора.\n"
    "<code>помощь</code> — справка.\n\n"
    "Сроки: 30м, 2ч, 7д. Нужны права бана и ограничения участников."
)


def parse(text):
    text = text.strip()
    if text.startswith("/"):
        text = text[1:].split("@", 1)[0]
    parts = text.split()
    return (parts[0].lower(), parts[1:]) if parts else ("", [])


async def target_for(message, args):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, args, None
    if args and args[0].isdigit():
        return int(args[0]), args[1:], None
    if args and args[0].startswith("@"):
        try:
            chat = await bot.get_chat(args[0])
            return chat.id, args[1:], None
        except TelegramAPIError:
            return None, args[1:], "Не удалось найти username. Ответьте на сообщение пользователя или укажите Telegram ID."
    return None, args, "Ответьте на сообщение пользователя командой или укажите Telegram ID."


async def send_dm(user_id, text):
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError:
        pass


async def moderation(message, cmd, args):
    chat_id = message.chat.id
    mod_id = message.from_user.id
    aliases = {
        "бан": "ban", "ban": "ban", "разбан": "unban", "unban": "unban",
        "мут": "mute", "mute": "mute", "размут": "unmute", "unmute": "unmute",
        "варн": "warn", "пред": "warn", "предупреждение": "warn",
        "варны": "warns", "преды": "warns", "снять": "unwarn",
        "выговор": "reprimand", "выговоры": "reprimands", "снятьвыговор": "unreprimand",
    }
    if cmd == "снять" and args and args[0].lower() == "выговор":
        action = "unreprimand"
        args = args[1:]
    elif cmd == "снять" and args and args[0].lower() in ("варн", "пред", "предупреждение"):
        action = "unwarn"
        args = args[1:]
    else:
        action = aliases.get(cmd)
    if not action:
        return False
    if not await need_admin(message):
        return True

    if action == "unban":
        if not args or not args[0].isdigit():
            await message.reply("Использование: <code>разбан TELEGRAM_ID</code>")
            return True
        uid = int(args[0])
        try:
            await bot.unban_chat_member(chat_id, uid, only_if_banned=True)
        except TelegramAPIError as e:
            await message.reply(f"Не удалось снять бан: {e}")
            return True
        log_action(chat_id, uid, mod_id, "unban", "Снятие бана")
        await message.reply(f"✅ Пользователь <code>{uid}</code> разбанен.")
        return True

    uid, rest, error = await target_for(message, args)
    if uid is None:
        await message.reply(error)
        return True

    if action in ("warn", "reprimand"):
        reason = " ".join(rest).strip()
        if not reason:
            await message.reply("Укажите причину, например: ответом на сообщение напишите <code>варн флуд</code>.")
            return True
        table = "warnings" if action == "warn" else "reprimands"
        rec_id = record(table, chat_id, uid, mod_id, reason)
        log_action(chat_id, uid, mod_id, action, reason)
        label = "Предупреждение" if action == "warn" else "Выговор сотруднику"
        await message.reply(f"✅ {label} записан. ID: <code>#{rec_id}</code>\nПользователь: <code>{uid}</code>\nПричина: {reason}")
        await send_dm(uid, f"⚠️ Вам выдано взыскание в чате {message.chat.title or chat_id}. Причина: {reason}")
        return True

    if action in ("warns", "reprimands"):
        table = "warnings" if action == "warns" else "reprimands"
        with db() as con:
            rows = con.execute(
                f"SELECT id,reason,created_at FROM {table} WHERE chat_id=? AND user_id=? AND active=1 ORDER BY id DESC LIMIT 20",
                (chat_id, uid),
            ).fetchall()
        label = "Предупреждения" if action == "warns" else "Выговоры"
        if not rows:
            await message.reply(f"📋 Активных записей «{label.lower()}» нет.")
        else:
            lines = [f"📋 <b>{label}: {len(rows)}</b>"]
            lines += [f"• #{r['id']} — {r['reason']} ({r['created_at'][:10]})" for r in rows]
            await message.reply("\n".join(lines))
        return True

    if action in ("unwarn", "unreprimand"):
        table = "reprimands" if action == "unreprimand" else "warnings"
        with db() as con:
            row = con.execute(
                f"SELECT id,reason FROM {table} WHERE chat_id=? AND user_id=? AND active=1 ORDER BY id DESC LIMIT 1",
                (chat_id, uid),
            ).fetchone()
            if row:
                con.execute(f"UPDATE {table} SET active=0 WHERE id=?", (row["id"],))
                con.commit()
        if row:
            log_action(chat_id, uid, mod_id, "remove_" + table, "Снята запись #" + str(row["id"]))
            await message.reply(f"✅ Запись #{row['id']} снята.")
        else:
            await message.reply("Активных записей не найдено.")
        return True

    if action == "ban":
        span = duration(rest[0]) if rest else None
        if span:
            reason = " ".join(rest[1:]).strip() or "Причина не указана"
            until = datetime.now(timezone.utc) + span
            period = rest[0]
        else:
            reason = " ".join(rest).strip() or "Причина не указана"
            until = None
            period = "навсегда"
        try:
            await bot.ban_chat_member(chat_id, uid, until_date=until)
        except TelegramAPIError as e:
            await message.reply(f"Не удалось забанить пользователя: {e}")
            return True
        log_action(chat_id, uid, mod_id, "ban", reason)
        await message.reply(f"🔨 Пользователь <code>{uid}</code> забанен ({period}).\nПричина: {reason}")
        await send_dm(uid, f"Вы заблокированы в чате {message.chat.title or chat_id}. Причина: {reason}")
        return True

    if action == "mute":
        span = duration(rest[0]) if rest else None
        if not span:
            await message.reply("Укажите срок: ответом на сообщение напишите <code>мут 30м флуд</code>.")
            return True
        period = rest.pop(0)
        reason = " ".join(rest).strip() or "Причина не указана"
        try:
            await bot.restrict_chat_member(
                chat_id, uid, permissions=ChatPermissions(can_send_messages=False),
                until_date=datetime.now(timezone.utc) + span,
            )
        except TelegramAPIError as e:
            await message.reply(f"Не удалось выдать мут: {e}")
            return True
        log_action(chat_id, uid, mod_id, "mute", reason)
        await message.reply(f"🔇 Мут выдан пользователю <code>{uid}</code> на {period}.\nПричина: {reason}")
        await send_dm(uid, f"Вам выдан мут в чате {message.chat.title or chat_id} на {period}. Причина: {reason}")
        return True

    if action == "unmute":
        try:
            await bot.restrict_chat_member(
                chat_id, uid,
                permissions=ChatPermissions(
                    can_send_messages=True, can_send_audios=True, can_send_documents=True,
                    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
        except TelegramAPIError as e:
            await message.reply(f"Не удалось снять мут: {e}")
            return True
        log_action(chat_id, uid, mod_id, "unmute", "Мут снят")
        await message.reply(f"🔊 Мут с пользователя <code>{uid}</code> снят.")
        return True
    return True


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "👋 Привет! Я бот-модератор <b>heradeLegend</b>.\nВыберите раздел или напишите «помощь».",
        reply_markup=home_keyboard(await admin(message)),
    )


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(HELP + "\n\n" + COMMANDS, reply_markup=home_keyboard(await admin(message)))


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if await need_admin(message):
        await message.answer("🛡️ <b>Панель администратора</b>", reply_markup=admin_keyboard())


@dp.message(F.text)
async def router(message: Message):
    if not message.text or not message.from_user or message.from_user.is_bot:
        return
    cmd, args = parse(message.text)
    handled = await moderation(message, cmd, args)
    if handled:
        return
    text = message.text.lower().strip(" !?.")
    if text in {"помощь", "команды", "команда", "help", "что умеешь"}:
        await message.answer(HELP + "\n\n" + COMMANDS, reply_markup=home_keyboard(await admin(message)))
    elif text in {"админ меню", "меню админа", "панель админа", "панель модератора"}:
        if await need_admin(message):
            await message.answer("🛡️ <b>Панель администратора</b>", reply_markup=admin_keyboard())
    elif any(word in text for word in ("жалоба", "апелляция", "поддержка", "помогите")):
        await message.reply("📨 Опишите ситуацию и обратитесь к администрации чата. Не отправляйте пароли и коды входа.")


@dp.callback_query(F.data)
async def callbacks(callback: CallbackQuery):
    key = callback.data
    if key == "admin":
        try:
            member = await bot.get_chat_member(callback.message.chat.id, callback.from_user.id)
            allowed = member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)
        except TelegramAPIError:
            allowed = False
        if not allowed:
            await callback.answer("Недостаточно прав.", show_alert=True)
            return
        await callback.message.edit_text("🛡️ <b>Панель администратора</b>", reply_markup=admin_keyboard())
    elif key == "commands":
        await callback.message.edit_text(COMMANDS, reply_markup=admin_keyboard())
    elif key == "stats":
        with db() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM actions WHERE chat_id=? AND moderator_id=?",
                (callback.message.chat.id, callback.from_user.id),
            ).fetchone()
        await callback.message.edit_text(f"📊 <b>Ваша статистика</b>\nЗаписанных действий: {row['n']}", reply_markup=admin_keyboard())
    elif key == "rules":
        await callback.message.edit_text(
            "⚖️ <b>Правила чата</b>\n1. Уважайте участников.\n2. Не спамьте и не флудите.\n3. Соблюдайте требования администрации.",
            reply_markup=home_keyboard(),
        )
    elif key == "contact":
        await callback.message.edit_text("📨 Опишите проблему и обратитесь к администрации чата.", reply_markup=home_keyboard())
    elif key == "help":
        await callback.message.edit_text(HELP, reply_markup=home_keyboard())
    else:
        await callback.message.edit_text("👋 <b>heradeLegend</b> — выберите раздел:", reply_markup=home_keyboard())
    await callback.answer()


async def main():
    init_db()
    try:
        log.info(
            "Starting heradeLegend moderation bot (proxy=%s, timeout=%ss)",
            "enabled" if PROXY_URL else "disabled",
            REQUEST_TIMEOUT,
        )
        # Check connectivity before polling; never print the token or proxy credentials.
        me = await bot.get_me(request_timeout=REQUEST_TIMEOUT)
        log.info("Connected to Telegram as @%s (id=%s)", me.username, me.id)
        await dp.start_polling(bot)
    except TelegramNetworkError:
        log.exception(
            "Cannot reach Telegram Bot API. Check server outbound access, "
            "proxy type/credentials, and TELEGRAM_PROXY."
        )
        raise
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by keyboard interrupt.")
