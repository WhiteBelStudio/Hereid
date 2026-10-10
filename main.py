import asyncio
import html
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command

load_dotenv()
from aiogram.types import (
    CallbackQuery, ChatPermissions, ErrorEvent, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

TOKEN = os.getenv("BOT_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "heradelegend.db")
PROXY_URL = os.getenv("TELEGRAM_PROXY", "").strip() or None
REQUEST_TIMEOUT = float(os.getenv("TELEGRAM_REQUEST_TIMEOUT", "60"))
OWNER_TELEGRAM_ID = os.getenv("OWNER_TELEGRAM_ID", "").strip()

ROLE_LABELS = {
    "owner": "👑 Владелец",
    "admin": "🛡️ Администратор",
    "moderator": "🔨 Модератор",
    "helper": "🤝 Помощник",
}
ROLE_PERMISSIONS = {
    "owner": frozenset({"panel", "manage_roles", "manage_settings", "manage_actions", "moderate", "view_logs", "view_stats"}),
    "admin": frozenset({"panel", "manage_roles", "manage_settings", "manage_actions", "moderate", "view_logs", "view_stats"}),
    "moderator": frozenset({"panel", "moderate", "view_logs", "view_stats"}),
    "helper": frozenset({"panel", "view_stats"}),
}

if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в переменных сервера Pterodactyl.")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("heradeLegend")

# Proxy is optional. If supplied, it must be a working SOCKS5/HTTP proxy URL.
session = AiohttpSession(proxy=PROXY_URL, timeout=REQUEST_TIMEOUT)
bot = Bot(TOKEN, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
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
        CREATE TABLE IF NOT EXISTS role_assignments(
          chat_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('admin','moderator','helper')),
          assigned_by INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS pending_role_changes(
          chat_id INTEGER NOT NULL,
          admin_id INTEGER NOT NULL,
          role TEXT NOT NULL,
          created_at TEXT NOT NULL,
          PRIMARY KEY(chat_id, admin_id)
        );
        CREATE TABLE IF NOT EXISTS rp_actions(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          chat_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          keywords TEXT NOT NULL,
          response TEXT NOT NULL,
          created_by INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          is_enabled INTEGER NOT NULL DEFAULT 1,
          UNIQUE(chat_id, name)
        );
        CREATE TABLE IF NOT EXISTS pending_rp_edits(
          chat_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          action_id INTEGER NOT NULL,
          field TEXT NOT NULL CHECK(field IN ('name','keywords','response')),
          created_at TEXT NOT NULL,
          PRIMARY KEY(chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS pending_user_actions(
          chat_id INTEGER NOT NULL,
          admin_id INTEGER NOT NULL,
          target_id INTEGER,
          action TEXT NOT NULL CHECK(action IN ('lookup','ban','mute','warn')),
          created_at TEXT NOT NULL,
          PRIMARY KEY(chat_id, admin_id)
        );
        CREATE TABLE IF NOT EXISTS pending_panel_inputs(
          chat_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL,
          kind TEXT NOT NULL CHECK(kind IN ('rp_create')),
          created_at TEXT NOT NULL,
          PRIMARY KEY(chat_id, user_id)
        );
        """)
        # Add the new column in-place; preserve existing SQLite data.
        rp_columns = {row[1] for row in con.execute("PRAGMA table_info(rp_actions)").fetchall()}
        if "is_enabled" not in rp_columns:
            con.execute("ALTER TABLE rp_actions ADD COLUMN is_enabled INTEGER NOT NULL DEFAULT 1")
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
    """Return whether the sender is a chat admin; fail closed on API/proxy errors."""
    if not message.from_user or message.chat.type == ChatType.PRIVATE:
        return False
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)
    except TelegramAPIError as exc:
        log.warning("Cannot check admin rights for user_id=%s chat_id=%s: %s",
                    message.from_user.id, message.chat.id, type(exc).__name__)
        return False
    except Exception as exc:
        # aiohttp-socks can raise ProxyError directly, outside aiogram's API exceptions.
        # Do not let a broken proxy crash /start or /help; authorization remains denied.
        log.warning(
            "Admin check failed for user_id=%s chat_id=%s (%s). "
            "Check TELEGRAM_PROXY connectivity and SOCKS5/HTTP proxy type.",
            message.from_user.id, message.chat.id, type(exc).__name__,
        )
        return False


async def get_role(chat_id: int, user_id: int):
    """Resolve the user's effective role. Owner is configured outside the assignable role table."""
    if OWNER_TELEGRAM_ID.isdigit() and user_id == int(OWNER_TELEGRAM_ID):
        return "owner"
    with db() as con:
        row = con.execute(
            "SELECT role FROM role_assignments WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()
    if row and row["role"] in ROLE_LABELS:
        return row["role"]
    # Backward compatibility: existing Telegram group administrators remain admins.
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
            return "admin"
    except Exception as exc:
        log.warning("Role lookup failed for user_id=%s chat_id=%s (%s).",
                    user_id, chat_id, type(exc).__name__)
    return None


async def has_permission(chat_id: int, user_id: int, permission: str):
    role = await get_role(chat_id, user_id)
    return role is not None and permission in ROLE_PERMISSIONS.get(role, frozenset())


async def need_admin(message: Message):
    if message.from_user and message.chat.type != ChatType.PRIVATE:
        if await has_permission(message.chat.id, message.from_user.id, "moderate"):
            return True
    await message.reply("⛔ Недостаточно прав. Нужна роль владельца, администратора или модератора.")
    return False


async def need_panel_access(message: Message):
    if message.from_user and message.chat.type != ChatType.PRIVATE:
        if await has_permission(message.chat.id, message.from_user.id, "panel"):
            return True
    await message.reply("⛔ Панель доступна только пользователям с назначенной ролью или администраторам группы.")
    return False


async def callback_is_admin(callback: CallbackQuery):
    """Authorize admin-panel callbacks using the internal role and permission model."""
    if not callback.message or callback.message.chat.type == ChatType.PRIVATE:
        return False
    return await has_permission(callback.message.chat.id, callback.from_user.id, "panel")


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


def role_overview(chat_id: int, viewer_id: int):
    """Show role management controls and the viewer's current effective role."""
    viewer_role = "owner" if OWNER_TELEGRAM_ID.isdigit() and viewer_id == int(OWNER_TELEGRAM_ID) else None
    if viewer_role is None:
        with db() as con:
            row = con.execute(
                "SELECT role FROM role_assignments WHERE chat_id=? AND user_id=?",
                (chat_id, viewer_id),
            ).fetchone()
        viewer_role = row["role"] if row else "admin"
    return (
        "👑 <b>Управление ролями</b>\n\n"
        f"Ваша роль: <b>{html.escape(ROLE_LABELS.get(viewer_role, 'Не определена'))}</b>\n\n"
        "Выберите роль для назначения сотруднику или снимите текущую роль. "
        "После выбора бот попросит Telegram ID сотрудника.\n\n"
        "⚠️ Владелец задаётся через OWNER_TELEGRAM_ID и не может быть понижен через панель."
    )


def roles_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡️ Назначить администратора", callback_data="roles:grant:admin")],
        [InlineKeyboardButton(text="🔨 Назначить модератора", callback_data="roles:grant:moderator")],
        [InlineKeyboardButton(text="🤝 Назначить помощника", callback_data="roles:grant:helper")],
        [InlineKeyboardButton(text="➖ Снять роль", callback_data="roles:remove")],
        [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin")],
    ])


def rp_keyboard(can_manage: bool = True):
    rows = []
    if can_manage:
        rows.append([InlineKeyboardButton(text="➕ Создать RP-действие", callback_data="rp:create")])
    rows.extend([
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="panel:rp")],
        [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rp_catalog(chat_id: int):
    with db() as con:
        rows = con.execute(
            "SELECT id,name,keywords,response,is_enabled FROM rp_actions WHERE chat_id=? ORDER BY id DESC LIMIT 30",
            (chat_id,),
        ).fetchall()
    if not rows:
        return "🎭 <b>Каталог RP-действий</b>\n\nПока нет действий. Нажмите «Создать RP-действие»."
    lines = ["🎭 <b>Каталог RP-действий</b>", ""]
    for row in rows:
        words = ", ".join(x.strip() for x in row["keywords"].split(",") if x.strip())
        state = "🟢 Включено" if row["is_enabled"] else "⚪ Выключено"
        lines.append(
            f"<b>#{row['id']} · {html.escape(row['name'])}</b> — {state}\n"
            f"Ключевые слова: <code>{html.escape(words)}</code>\n"
            f"Ответ: {html.escape(row['response'][:180])}"
        )
    lines.append("\nВыберите действие кнопкой ниже, чтобы включить, отключить или изменить его.")
    return "\n\n".join(lines)


def rp_catalog_keyboard(chat_id: int, can_manage: bool = True):
    rows = []
    if can_manage:
        with db() as con:
            actions = con.execute(
                "SELECT id,name,is_enabled FROM rp_actions WHERE chat_id=? ORDER BY id DESC LIMIT 30",
                (chat_id,),
            ).fetchall()
        for action in actions:
            state = "🟢" if action["is_enabled"] else "⚪"
            rows.append([InlineKeyboardButton(
                text=f"{state} #{action['id']} {action['name']}"[:60],
                callback_data=f"rp:item:{action['id']}",
            )])
        rows.append([InlineKeyboardButton(text="➕ Создать RP-действие", callback_data="rp:create")])
    rows.extend([
        [InlineKeyboardButton(text="🔄 Обновить список", callback_data="panel:rp")],
        [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rp_item_view(chat_id: int, action_id: int):
    with db() as con:
        row = con.execute(
            "SELECT id,name,keywords,response,is_enabled FROM rp_actions WHERE chat_id=? AND id=?",
            (chat_id, action_id),
        ).fetchone()
    if not row:
        return None, None
    status = "🟢 Включено" if row["is_enabled"] else "⚪ Выключено"
    text = (
        f"🎭 <b>{html.escape(row['name'])}</b> (#{row['id']})\n"
        f"Статус: {status}\n"
        f"Ключевые слова: <code>{html.escape(row['keywords'])}</code>\n\n"
        f"<b>Ответ:</b>\n{html.escape(row['response'])}"
    )
    return text, bool(row["is_enabled"])


def rp_item_keyboard(action_id: int, is_enabled: bool):
    toggle_label = "⏸ Отключить" if is_enabled else "▶️ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_label, callback_data=f"rp:toggle:{action_id}")],
        [InlineKeyboardButton(text="✏️ Изменить название", callback_data=f"rp:edit:{action_id}:name")],
        [InlineKeyboardButton(text="🔑 Изменить ключевые слова", callback_data=f"rp:edit:{action_id}:keywords")],
        [InlineKeyboardButton(text="💬 Изменить ответ", callback_data=f"rp:edit:{action_id}:response")],
        [InlineKeyboardButton(text="⬅️ В каталог", callback_data="panel:rp")],
    ])


def user_lookup_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Найти по Telegram ID", callback_data="users:find")],
        [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin")],
    ])


def user_card_keyboard(target_id: int, can_moderate: bool):
    rows = []
    if can_moderate:
        rows.extend([
            [InlineKeyboardButton(text="⚠️ Выдать предупреждение", callback_data=f"users:action:warn:{target_id}")],
            [
                InlineKeyboardButton(text="🔨 Мут", callback_data=f"users:action:mute:{target_id}"),
                InlineKeyboardButton(text="🔓 Снять мут", callback_data=f"users:action:unmute:{target_id}"),
            ],
            [
                InlineKeyboardButton(text="⛔ Бан", callback_data=f"users:action:ban:{target_id}"),
                InlineKeyboardButton(text="♻️ Разбан", callback_data=f"users:action:unban:{target_id}"),
            ],
        ])
    rows.extend([
        [InlineKeyboardButton(text="🔄 Обновить карточку", callback_data=f"users:card:{target_id}")],
        [InlineKeyboardButton(text="⬅️ К пользователям", callback_data="panel:users")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def user_card(chat_id: int, target_id: int, can_moderate: bool):
    try:
        member = await bot.get_chat_member(chat_id, target_id)
        status_map = {
            ChatMemberStatus.CREATOR: "создатель чата",
            ChatMemberStatus.ADMINISTRATOR: "администратор Telegram",
            ChatMemberStatus.MEMBER: "участник",
            ChatMemberStatus.RESTRICTED: "ограничен",
            ChatMemberStatus.LEFT: "вышел из чата",
            ChatMemberStatus.KICKED: "заблокирован в чате",
        }
        status = status_map.get(member.status, str(member.status))
        user = member.user
        full_name = html.escape(" ".join(x for x in (user.first_name, user.last_name) if x) or "Без имени")
        username = f"@{html.escape(user.username)}" if user.username else "не указан"
    except TelegramAPIError:
        return (
            "⚠️ Не удалось получить карточку участника. Проверьте Telegram ID, права бота и доступность Telegram API.",
            user_lookup_keyboard(),
        )
    with db() as con:
        warnings = con.execute("SELECT COUNT(*) AS n FROM warnings WHERE chat_id=? AND user_id=? AND active=1",
                               (chat_id, target_id)).fetchone()["n"]
        reprimands = con.execute("SELECT COUNT(*) AS n FROM reprimands WHERE chat_id=? AND user_id=? AND active=1",
                                 (chat_id, target_id)).fetchone()["n"]
        role = con.execute("SELECT role FROM role_assignments WHERE chat_id=? AND user_id=?",
                           (chat_id, target_id)).fetchone()
        recent = con.execute("SELECT action,reason,created_at FROM actions WHERE chat_id=? AND user_id=? ORDER BY id DESC LIMIT 5",
                             (chat_id, target_id)).fetchall()
    role_label = ROLE_LABELS.get(role["role"], "не назначена") if role else "не назначена"
    lines = [
        "👤 <b>Карточка пользователя</b>",
        f"Имя: <b>{full_name}</b>",
        f"Telegram ID: <code>{target_id}</code>",
        f"Username: {username}",
        f"Статус в чате: <b>{html.escape(status)}</b>",
        f"Внутренняя роль: {html.escape(role_label)}",
        f"Активные предупреждения: <b>{warnings}</b>",
        f"Активные выговоры: <b>{reprimands}</b>",
    ]
    if recent:
        lines.append("\n<b>Последние действия:</b>")
        for item in recent:
            lines.append(f"• {html.escape(item['action'])}: {html.escape(item['reason'][:100])} ({html.escape(item['created_at'][:10])})")
    else:
        lines.append("\nПоследних записей в журнале нет.")
    if member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
        lines.append("\nℹ️ Ограничения для администраторов Telegram из этой карточки недоступны.")
        can_moderate = False
    return "\n".join(lines), user_card_keyboard(target_id, can_moderate)


def admin_keyboard():
    """Main navigation for the admin panel. Feature modules are connected in later checklist items."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Пользователи", callback_data="panel:users"),
            InlineKeyboardButton(text="🎭 RP-действия", callback_data="panel:rp"),
        ],
        [
            InlineKeyboardButton(text="🛡️ Модерация", callback_data="panel:moderation"),
            InlineKeyboardButton(text="👑 Роли и права", callback_data="panel:roles"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки чата", callback_data="panel:settings"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        ],
        [
            InlineKeyboardButton(text="📜 Журнал действий", callback_data="panel:logs"),
            InlineKeyboardButton(text="📚 Команды", callback_data="commands"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить панель", callback_data="admin"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="home"),
        ],
    ])


HELP = (
    "📖 <b>Справка heradeLegend</b>\n\n"
    "• Соблюдайте правила чата и уважайте других участников.\n"
    "• Чтобы сообщить о нарушении, напишите «жалоба» и кратко опишите ситуацию.\n"
    "• Если вы не согласны с наказанием, обратитесь к администрации чата.\n"
    "• Никому не сообщайте пароли и коды подтверждения."
)
COMMANDS = (
    "🛡️ <b>Команды модерации</b>\n\n"
    "Для команд с участником ответьте на его сообщение. Причину указывайте после команды.\n\n"
    "<code>бан 7д спам</code> — заблокировать участника на 7 дней.\n"
    "<code>бан спам</code> — заблокировать без указания срока.\n"
    "<code>разбан TELEGRAM_ID</code> — снять блокировку по Telegram ID.\n"
    "<code>мут 30м флуд</code> — запретить отправку сообщений на 30 минут.\n"
    "<code>размут</code> — снять ограничение на отправку сообщений.\n"
    "<code>варн флуд</code> — выдать предупреждение.\n"
    "<code>варны</code> — показать активные предупреждения участника.\n"
    "<code>снять варн</code> — снять последнее активное предупреждение.\n"
    "<code>выговор неактив</code> — записать выговор участнику.\n"
    "<code>выговоры</code> — показать активные выговоры.\n"
    "<code>снять выговор</code> — снять последний активный выговор.\n"
    "<code>админ меню</code> — открыть меню администратора.\n"
    "<code>помощь</code> — открыть эту справку.\n\n"
    "Поддерживаемые сроки: 30м — минуты, 2ч — часы, 7д — дни.\n"
    "Для модерации бот должен иметь необходимые права в группе."
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
            return None, args[1:], "Не удалось найти пользователя по этому username. Ответьте на его сообщение или укажите Telegram ID."
    return None, args, "Ответьте на сообщение участника командой или укажите его Telegram ID."


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
            log.warning("Unban failed in chat_id=%s for user_id=%s: %s", chat_id, uid, type(e).__name__)
            await message.reply("⚠️ Не удалось снять блокировку. Проверьте ID пользователя, права бота и доступность Telegram.")
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
            await message.reply("Укажите причину. Пример: ответьте на сообщение участника командой <code>варн флуд</code>.")
            return True
        table = "warnings" if action == "warn" else "reprimands"
        rec_id = record(table, chat_id, uid, mod_id, reason)
        log_action(chat_id, uid, mod_id, action, reason)
        label = "Предупреждение" if action == "warn" else "Выговор сотруднику"
        await message.reply(f"✅ {label} добавлен в журнал. Номер записи: <code>#{rec_id}</code>\nУчастник: <code>{uid}</code>\nПричина: {html.escape(reason)}")
        await send_dm(uid, f"⚠️ Вам вынесено взыскание в чате {html.escape(message.chat.title or str(chat_id))}. Причина: {html.escape(reason)}")
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
            lines += [f"• #{r['id']} — {html.escape(r['reason'])} ({r['created_at'][:10]})" for r in rows]
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
            log.warning("Ban failed in chat_id=%s for user_id=%s: %s", chat_id, uid, type(e).__name__)
            await message.reply("⚠️ Не удалось заблокировать участника. Проверьте его статус, права бота и доступность Telegram.")
            return True
        log_action(chat_id, uid, mod_id, "ban", reason)
        await message.reply(f"🔨 Участник <code>{uid}</code> заблокирован. Срок: {html.escape(period)}.\nПричина: {html.escape(reason)}")
        await send_dm(uid, f"Вы заблокированы в чате {html.escape(message.chat.title or str(chat_id))}. Причина: {html.escape(reason)}")
        return True

    if action == "mute":
        span = duration(rest[0]) if rest else None
        if not span:
            await message.reply("Укажите срок и причину. Пример: ответьте на сообщение участника командой <code>мут 30м флуд</code>. Срок можно указать в минутах (м), часах (ч) или днях (д).")
            return True
        period = rest.pop(0)
        reason = " ".join(rest).strip() or "Причина не указана"
        try:
            await bot.restrict_chat_member(
                chat_id, uid, permissions=ChatPermissions(can_send_messages=False),
                until_date=datetime.now(timezone.utc) + span,
            )
        except TelegramAPIError as e:
            log.warning("Mute failed in chat_id=%s for user_id=%s: %s", chat_id, uid, type(e).__name__)
            await message.reply("⚠️ Не удалось ограничить участника. Проверьте его статус, права бота и доступность Telegram.")
            return True
        log_action(chat_id, uid, mod_id, "mute", reason)
        await message.reply(f"🔇 Участнику <code>{uid}</code> запрещено отправлять сообщения на {html.escape(period)}.\nПричина: {html.escape(reason)}")
        await send_dm(uid, f"В чате {html.escape(message.chat.title or str(chat_id))} вам запрещено отправлять сообщения на {html.escape(period)}. Причина: {html.escape(reason)}")
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
            log.warning("Unmute failed in chat_id=%s for user_id=%s: %s", chat_id, uid, type(e).__name__)
            await message.reply("⚠️ Не удалось снять ограничение. Проверьте права бота и доступность Telegram.")
            return True
        log_action(chat_id, uid, mod_id, "unmute", "Мут снят")
        await message.reply(f"🔊 Ограничение на отправку сообщений для участника <code>{uid}</code> снято.")
        return True
    return True


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "👋 Привет! Я <b>heradeLegend</b> — бот для помощи в модерации чатов.\nВыберите нужный раздел в меню ниже или отправьте команду <code>/help</code>.",
        reply_markup=home_keyboard(bool(message.from_user and message.chat.type != ChatType.PRIVATE and await has_permission(message.chat.id, message.from_user.id, "panel"))),
    )


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(HELP + "\n\n" + COMMANDS, reply_markup=home_keyboard(bool(message.from_user and message.chat.type != ChatType.PRIVATE and await has_permission(message.chat.id, message.from_user.id, "panel"))))


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if await need_panel_access(message):
        await message.answer("🛡️ <b>Панель администратора</b>", reply_markup=admin_keyboard())


@dp.message(F.text)
async def router(message: Message):
    if not message.text or not message.from_user or message.from_user.is_bot:
        return
    if message.chat.type != ChatType.PRIVATE:
        with db() as con:
            pending_user = con.execute(
                "SELECT target_id,action FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                (message.chat.id, message.from_user.id),
            ).fetchone()
        if pending_user:
            if not await has_permission(message.chat.id, message.from_user.id, "panel"):
                with db() as con:
                    con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("⛔ Ваш доступ к панели изменился. Операция отменена.")
                return
            value = message.text.strip()
            if value.casefold() == "отмена":
                with db() as con:
                    con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("❌ Операция отменена.")
                return
            action_name = pending_user["action"]
            target_id = pending_user["target_id"]
            if action_name == "lookup":
                if not value.isdigit():
                    await message.reply("Отправьте числовой Telegram ID или «отмена».")
                    return
                target_id = int(value)
                with db() as con:
                    con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                can_moderate = await has_permission(message.chat.id, message.from_user.id, "moderate")
                card_text, card_markup = await user_card(message.chat.id, target_id, can_moderate)
                await message.reply(card_text, reply_markup=card_markup)
                return
            if not await has_permission(message.chat.id, message.from_user.id, "moderate"):
                with db() as con:
                    con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("⛔ Недостаточно прав для модерации. Операция отменена.")
                return
            if target_id is None:
                await message.reply("Не удалось определить пользователя. Откройте карточку повторно.")
                return
            try:
                target_member = await bot.get_chat_member(message.chat.id, target_id)
            except TelegramAPIError:
                await message.reply("Не удалось проверить участника. Откройте карточку и попробуйте снова.")
                return
            if target_member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
                with db() as con:
                    con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("⛔ Нельзя применять ограничения к создателю или администратору Telegram.")
                return
            try:
                if action_name == "warn":
                    reason = value[:300]
                    if not reason:
                        await message.reply("Укажите причину предупреждения или отправьте «отмена».")
                        return
                    record("warnings", message.chat.id, target_id, message.from_user.id, reason)
                    log_action(message.chat.id, target_id, message.from_user.id, "warn", reason)
                    await send_dm(target_id, f"⚠️ Вам вынесено предупреждение в чате {html.escape(message.chat.title or str(message.chat.id))}. Причина: {html.escape(reason)}")
                    result = "✅ Предупреждение добавлено."
                elif action_name == "ban":
                    parts = value.split(maxsplit=1)
                    span = duration(parts[0]) if parts else None
                    if span:
                        reason = parts[1].strip() if len(parts) > 1 else "Причина не указана"
                        until = datetime.now(timezone.utc) + span
                    else:
                        reason = value[:300] or "Причина не указана"
                        until = None
                    await bot.ban_chat_member(message.chat.id, target_id, until_date=until)
                    log_action(message.chat.id, target_id, message.from_user.id, "ban", reason)
                    result = "✅ Пользователь заблокирован."
                elif action_name == "mute":
                    parts = value.split(maxsplit=1)
                    span = duration(parts[0]) if parts else None
                    if not span:
                        await message.reply("Для мута нужен срок, например <code>30м флуд</code>, или «отмена».")
                        return
                    reason = parts[1].strip() if len(parts) > 1 else "Причина не указана"
                    until = datetime.now(timezone.utc) + span
                    await bot.restrict_chat_member(
                        message.chat.id, target_id, permissions=ChatPermissions(can_send_messages=False),
                        until_date=until,
                    )
                    log_action(message.chat.id, target_id, message.from_user.id, "mute", reason)
                    result = "✅ Пользователь получил мут."
                else:
                    await message.reply("Неизвестная операция. Откройте карточку пользователя заново.")
                    return
            except TelegramAPIError:
                await message.reply("⚠️ Telegram не выполнил действие. Проверьте права бота, статус пользователя и корректность срока.")
                return
            with db() as con:
                con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                            (message.chat.id, message.from_user.id))
                con.commit()
            log_action(message.chat.id, target_id, message.from_user.id, "user_panel_action",
                       f"{action_name}: {reason}")
            can_moderate = await has_permission(message.chat.id, message.from_user.id, "moderate")
            card_text, card_markup = await user_card(message.chat.id, target_id, can_moderate)
            await message.reply(result + "\n\n" + card_text, reply_markup=card_markup)
            return

    if message.chat.type != ChatType.PRIVATE:
        with db() as con:
            pending = con.execute(
                "SELECT role FROM pending_role_changes WHERE chat_id=? AND admin_id=?",
                (message.chat.id, message.from_user.id),
            ).fetchone()
        if pending:
            if not await has_permission(message.chat.id, message.from_user.id, "manage_roles"):
                await message.reply("⛔ Ваши права изменились. Операция с ролью отменена.")
                with db() as con:
                    con.execute("DELETE FROM pending_role_changes WHERE chat_id=? AND admin_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                return
            value = message.text.strip()
            if value.lower() == "отмена":
                with db() as con:
                    con.execute("DELETE FROM pending_role_changes WHERE chat_id=? AND admin_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("❌ Операция отменена.")
                return
            if not value.isdigit():
                await message.reply("Отправьте числовой Telegram ID или «отмена».")
                return
            target_id = int(value)
            selected_role = pending["role"]
            if OWNER_TELEGRAM_ID.isdigit() and target_id == int(OWNER_TELEGRAM_ID):
                await message.reply("⛔ Роль владельца защищена. Изменить её через панель нельзя.")
                return
            actor_is_owner = OWNER_TELEGRAM_ID.isdigit() and message.from_user.id == int(OWNER_TELEGRAM_ID)
            target_effective_role = await get_role(message.chat.id, target_id)
            if target_effective_role == "owner":
                await message.reply("⛔ Учётная запись владельца защищена.")
                return
            if target_effective_role == "admin" and not actor_is_owner:
                await message.reply("⛔ Изменять роль администратора или другого администратора группы может только владелец.")
                return
            with db() as con:
                current = con.execute(
                    "SELECT role FROM role_assignments WHERE chat_id=? AND user_id=?",
                    (message.chat.id, target_id),
                ).fetchone()
                current_role = current["role"] if current else None
                if selected_role == "admin" and not actor_is_owner:
                    await message.reply("⛔ Назначать администраторов может только владелец.")
                    return
                if selected_role == "__remove__":
                    con.execute("DELETE FROM role_assignments WHERE chat_id=? AND user_id=?",
                                (message.chat.id, target_id))
                    action_name = "role_removed"
                    detail = "Роль снята"
                else:
                    con.execute(
                        "INSERT INTO role_assignments(chat_id,user_id,role,assigned_by,created_at) VALUES(?,?,?,?,?) "
                        "ON CONFLICT(chat_id,user_id) DO UPDATE SET role=excluded.role,assigned_by=excluded.assigned_by,created_at=excluded.created_at",
                        (message.chat.id, target_id, selected_role, message.from_user.id,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")),
                    )
                    action_name = "role_assigned"
                    detail = "Назначена роль: " + ROLE_LABELS[selected_role]
                con.execute("DELETE FROM pending_role_changes WHERE chat_id=? AND admin_id=?",
                            (message.chat.id, message.from_user.id))
                con.commit()
            log_action(message.chat.id, target_id, message.from_user.id, action_name, detail)
            await message.reply(
                f"✅ {html.escape(detail)} пользователю <code>{target_id}</code>.",
                reply_markup=roles_keyboard(),
            )
            return

    if message.chat.type != ChatType.PRIVATE:
        with db() as con:
            pending_edit = con.execute(
                "SELECT action_id,field FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
                (message.chat.id, message.from_user.id),
            ).fetchone()
        if pending_edit:
            if not await has_permission(message.chat.id, message.from_user.id, "manage_actions"):
                with db() as con:
                    con.execute("DELETE FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("⛔ Ваши права изменились. Редактирование отменено.")
                return
            value = message.text.strip()
            if value.lower() == "отмена":
                with db() as con:
                    con.execute("DELETE FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("❌ Редактирование отменено.")
                return
            action_id, field = pending_edit["action_id"], pending_edit["field"]
            if not value:
                await message.reply("Значение не может быть пустым. Отправьте новое значение или «отмена».")
                return
            if field == "name":
                if len(value) > 80:
                    await message.reply("Название должно быть не длиннее 80 символов.")
                    return
                with db() as con:
                    duplicate = con.execute(
                        "SELECT 1 FROM rp_actions WHERE chat_id=? AND name=? AND id<>?",
                        (message.chat.id, value, action_id),
                    ).fetchone()
                    if duplicate:
                        await message.reply("Действие с таким названием уже есть в этом чате. Отправьте другое или «отмена».")
                        return
                    cur = con.execute(
                        "UPDATE rp_actions SET name=? WHERE chat_id=? AND id=?",
                        (value, message.chat.id, action_id),
                    )
                    con.execute("DELETE FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
            elif field == "keywords":
                keywords = list(dict.fromkeys(word.strip().casefold() for word in value.split(",") if word.strip()))
                if not keywords or any(len(word) > 60 for word in keywords):
                    await message.reply("Укажите минимум одно ключевое слово; каждое должно быть не длиннее 60 символов.")
                    return
                with db() as con:
                    cur = con.execute(
                        "UPDATE rp_actions SET keywords=? WHERE chat_id=? AND id=?",
                        (", ".join(keywords), message.chat.id, action_id),
                    )
                    con.execute("DELETE FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
            else:
                if len(value) > 1000:
                    await message.reply("Ответ должен быть не длиннее 1000 символов.")
                    return
                with db() as con:
                    cur = con.execute(
                        "UPDATE rp_actions SET response=? WHERE chat_id=? AND id=?",
                        (value, message.chat.id, action_id),
                    )
                    con.execute("DELETE FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
            if cur.rowcount == 0:
                await message.reply("Действие не найдено. Откройте каталог и выберите его снова.")
                return
            log_action(message.chat.id, None, message.from_user.id, "rp_action_edited",
                       f"#{action_id}: изменено поле {field}")
            await message.reply("✅ Изменение сохранено в базе данных.")
            return

        with db() as con:
            pending_rp = con.execute(
                "SELECT kind FROM pending_panel_inputs WHERE chat_id=? AND user_id=?",
                (message.chat.id, message.from_user.id),
            ).fetchone()
        if pending_rp:
            if not await has_permission(message.chat.id, message.from_user.id, "manage_actions"):
                with db() as con:
                    con.execute("DELETE FROM pending_panel_inputs WHERE chat_id=? AND user_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("⛔ Ваши права изменились. Создание действия отменено.")
                return
            value = message.text.strip()
            if value.lower() == "отмена":
                with db() as con:
                    con.execute("DELETE FROM pending_panel_inputs WHERE chat_id=? AND user_id=?",
                                (message.chat.id, message.from_user.id))
                    con.commit()
                await message.reply("❌ Создание RP-действия отменено.")
                return
            parts = [part.strip() for part in value.split("|", 2)]
            if len(parts) != 3 or not all(parts):
                await message.reply("Неверный формат. Используйте: <code>Название | ключ1, ключ2 | Ответ бота</code> или отправьте «отмена».")
                return
            name, raw_keywords, response = parts
            keywords = [word.strip().lower() for word in raw_keywords.split(",") if word.strip()]
            if not keywords or len(name) > 80 or len(response) > 1000 or any(len(word) > 60 for word in keywords):
                await message.reply("Проверьте данные: название до 80 символов, ключевое слово до 60, ответ до 1000; нужен минимум один ключ.")
                return
            if len(set(keywords)) != len(keywords):
                keywords = list(dict.fromkeys(keywords))
            with db() as con:
                try:
                    cur = con.execute(
                        "INSERT INTO rp_actions(chat_id,name,keywords,response,created_by,created_at) VALUES(?,?,?,?,?,?)",
                        (message.chat.id, name, ", ".join(keywords), response, message.from_user.id,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")),
                    )
                except sqlite3.IntegrityError:
                    await message.reply("Действие с таким названием уже существует в этом чате. Выберите другое название.")
                    return
                action_id = cur.lastrowid
                con.execute("DELETE FROM pending_panel_inputs WHERE chat_id=? AND user_id=?",
                            (message.chat.id, message.from_user.id))
                con.commit()
            log_action(message.chat.id, None, message.from_user.id, "rp_action_created", f"#{action_id}: {name}")
            await message.reply(f"✅ RP-действие «{html.escape(name)}» создано. Ключевых слов: {len(keywords)}.")
            return

        # Exact keyword matches only; ordinary messages otherwise pass through unchanged.
        if not message.text.startswith("/"):
            normalized = " ".join(message.text.casefold().split())
            with db() as con:
                rows = con.execute(
                    "SELECT name,keywords,response FROM rp_actions WHERE chat_id=? AND is_enabled=1 ORDER BY id DESC",
                    (message.chat.id,),
                ).fetchall()
            for row in rows:
                if any(normalized == " ".join(word.casefold().split())
                       for word in row["keywords"].split(",") if word.strip()):
                    await message.reply(
                        f"<b>{html.escape(row['name'])}</b>\n{html.escape(row['response'])}"
                    )
                    return

    cmd, args = parse(message.text)
    handled = await moderation(message, cmd, args)
    if handled:
        return
    text = message.text.lower().strip(" !?.")
    if text in {"помощь", "команды", "команда", "help", "что умеешь"}:
        await message.answer(HELP + "\n\n" + COMMANDS, reply_markup=home_keyboard(bool(message.from_user and message.chat.type != ChatType.PRIVATE and await has_permission(message.chat.id, message.from_user.id, "panel"))))
    elif text in {"админ меню", "меню админа", "панель админа", "панель модератора"}:
        if await need_panel_access(message):
            await message.answer("🛡️ <b>Панель администратора</b>", reply_markup=admin_keyboard())
    elif any(word in text for word in ("жалоба", "апелляция", "поддержка", "помогите")):
        await message.reply("📨 Опишите ситуацию и обратитесь к администрации чата. Не отправляйте пароли и коды входа.")


@dp.callback_query(F.data)
async def callbacks(callback: CallbackQuery):
    key = callback.data or ""
    admin_permissions = {
        "admin": "panel",
        "roles:grant:admin": "manage_roles",
        "roles:grant:moderator": "manage_roles",
        "roles:grant:helper": "manage_roles",
        "roles:remove": "manage_roles",
        "roles:cancel": "manage_roles",
        "rp:create": "manage_actions",
        "rp:cancel": "manage_actions",
        "commands": "panel",
        "stats": "view_stats",
        "panel:users": "panel",
        "panel:rp": "panel",
        "panel:moderation": "panel",
        "panel:roles": "manage_roles",
        "panel:settings": "manage_settings",
        "panel:logs": "view_logs",
    }
    required_permission = admin_permissions.get(key)
    if key.startswith(("rp:item:", "rp:toggle:", "rp:edit:")):
        required_permission = "manage_actions"
    if key in ("users:find", "users:cancel"):
        required_permission = "panel"
    if key.startswith("users:action:"):
        required_permission = "moderate"
    elif key.startswith("users:card:"):
        required_permission = "panel"
    if required_permission:
        if not callback.message or not await has_permission(
            callback.message.chat.id, callback.from_user.id, required_permission
        ):
            await callback.answer("⛔ Недостаточно прав для этого раздела.", show_alert=True)
            return

    if not callback.message:
        await callback.answer()
        return

    if key == "admin":
        await callback.message.edit_text("🛡️ <b>Панель администратора</b>\nВыберите раздел:", reply_markup=admin_keyboard())
    elif key == "commands":
        await callback.message.edit_text(COMMANDS, reply_markup=admin_keyboard())
    elif key == "stats":
        with db() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM actions WHERE chat_id=? AND moderator_id=?",
                (callback.message.chat.id, callback.from_user.id),
            ).fetchone()
        await callback.message.edit_text(
            f"📊 <b>Ваша статистика</b>\nЗафиксировано действий: <b>{row['n']}</b>",
            reply_markup=admin_keyboard(),
        )
    elif key.startswith("roles:grant:") or key == "roles:remove":
        selected_role = key.rsplit(":", 1)[-1] if key != "roles:remove" else "__remove__"
        if selected_role not in ("admin", "moderator", "helper", "__remove__"):
            await callback.answer("Неизвестная роль.", show_alert=True)
            return
        if selected_role == "admin" and not (
            OWNER_TELEGRAM_ID.isdigit() and callback.from_user.id == int(OWNER_TELEGRAM_ID)
        ):
            await callback.answer("Назначать администраторов может только владелец.", show_alert=True)
            return
        with db() as con:
            con.execute(
                "INSERT INTO pending_role_changes(chat_id,admin_id,role,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(chat_id,admin_id) DO UPDATE SET role=excluded.role,created_at=excluded.created_at",
                (callback.message.chat.id, callback.from_user.id, selected_role,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            con.commit()
        action_label = "снятия роли" if selected_role == "__remove__" else "назначения роли «" + ROLE_LABELS[selected_role] + "»"
        await callback.message.edit_text(
            f"👤 <b>Введите Telegram ID сотрудника для {html.escape(action_label)}</b>\n\n"
            "Отправьте ID следующим сообщением в этот чат. Чтобы отменить операцию, отправьте «отмена».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="roles:cancel")],
                [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin")],
            ]),
        )
    elif key == "roles:cancel":
        with db() as con:
            con.execute("DELETE FROM pending_role_changes WHERE chat_id=? AND admin_id=?",
                        (callback.message.chat.id, callback.from_user.id))
            con.commit()
        await callback.message.edit_text("Операция отменена.", reply_markup=roles_keyboard())
    elif key.startswith("panel:"):
        sections = {
            "panel:users": ("👥 Управление пользователями", "Найдите участника по Telegram ID, посмотрите статус, активные взыскания и последние действия."),
            "panel:rp": ( "🎭 RP-действия", rp_catalog(callback.message.chat.id)),
            "panel:moderation": ("🛡️ Настройки модерации", "Здесь появятся параметры модерации и антиспама. Подключение — в пункте 008."),
            "panel:roles": ("👑 Роли и права", role_overview(callback.message.chat.id, callback.from_user.id)),
            "panel:settings": ("⚙️ Настройки чата", "Здесь появятся настройки панели для этого чата. Подключение — в пункте 009."),
            "panel:logs": ("📜 Журнал действий", "Журнал действий уже записывается в базу. Расширенный просмотр и фильтры будут подключены в пункте 010."),
        }
        title, description = sections[key]
        if key == "panel:roles":
            markup = roles_keyboard()
        elif key == "panel:users":
            markup = user_lookup_keyboard()
        elif key == "panel:rp":
            can_manage_actions = await has_permission(
                callback.message.chat.id, callback.from_user.id, "manage_actions"
            )
            markup = rp_catalog_keyboard(callback.message.chat.id, can_manage_actions)
        else:
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ В панель", callback_data="admin")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home")],
            ])
        await callback.message.edit_text(f"<b>{title}</b>\n\n{description}", reply_markup=markup)
    elif key == "users:find":
        with db() as con:
            con.execute(
                "INSERT INTO pending_user_actions(chat_id,admin_id,target_id,action,created_at) VALUES(?,?,NULL,'lookup',?) "
                "ON CONFLICT(chat_id,admin_id) DO UPDATE SET target_id=NULL,action='lookup',created_at=excluded.created_at",
                (callback.message.chat.id, callback.from_user.id,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            con.commit()
        await callback.message.edit_text(
            "🔎 <b>Поиск пользователя</b>\n\nОтправьте числовой Telegram ID участника этой группы. "
            "Для отмены отправьте «отмена».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="users:cancel")],
                [InlineKeyboardButton(text="⬅️ К пользователям", callback_data="panel:users")],
            ]),
        )
    elif key == "users:cancel":
        with db() as con:
            con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                        (callback.message.chat.id, callback.from_user.id))
            con.commit()
        await callback.message.edit_text(
            "👥 <b>Управление пользователями</b>\nВыберите действие.",
            reply_markup=user_lookup_keyboard(),
        )
    elif key.startswith("users:card:"):
        with db() as con:
            con.execute("DELETE FROM pending_user_actions WHERE chat_id=? AND admin_id=?",
                        (callback.message.chat.id, callback.from_user.id))
            con.commit()
        try:
            target_id = int(key.split(":")[2])
        except (ValueError, IndexError):
            await callback.answer("Некорректный Telegram ID.", show_alert=True)
            return
        can_moderate = await has_permission(callback.message.chat.id, callback.from_user.id, "moderate")
        card_text, card_markup = await user_card(callback.message.chat.id, target_id, can_moderate)
        await callback.message.edit_text(card_text, reply_markup=card_markup)
    elif key.startswith("users:action:"):
        parts = key.split(":")
        if len(parts) != 4 or parts[2] not in ("ban", "unban", "mute", "unmute", "warn"):
            await callback.answer("Неизвестное действие.", show_alert=True)
            return
        action_name = parts[2]
        try:
            target_id = int(parts[3])
        except ValueError:
            await callback.answer("Некорректный Telegram ID.", show_alert=True)
            return
        try:
            member = await bot.get_chat_member(callback.message.chat.id, target_id)
        except TelegramAPIError:
            await callback.answer("Не удалось проверить участника. Проверьте ID и доступность Telegram.", show_alert=True)
            return
        if member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
            await callback.answer("Нельзя применять эти ограничения к создателю или администратору Telegram.", show_alert=True)
            return
        if action_name in ("unban", "unmute"):
            try:
                if action_name == "unban":
                    await bot.unban_chat_member(callback.message.chat.id, target_id, only_if_banned=True)
                    log_action(callback.message.chat.id, target_id, callback.from_user.id, "unban", "Снятие блокировки из карточки пользователя")
                    result_text = "✅ Блокировка снята."
                else:
                    await bot.restrict_chat_member(
                        callback.message.chat.id, target_id,
                        permissions=ChatPermissions(can_send_messages=True, can_send_audios=True,
                            can_send_documents=True, can_send_photos=True, can_send_videos=True,
                            can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True,
                            can_send_other_messages=True, can_add_web_page_previews=True,
                            can_invite_users=True),
                    )
                    log_action(callback.message.chat.id, target_id, callback.from_user.id, "unmute", "Снятие мута из карточки пользователя")
                    result_text = "✅ Ограничение на отправку сообщений снято."
            except TelegramAPIError:
                await callback.answer("Telegram не выполнил действие. Проверьте права бота и статус пользователя.", show_alert=True)
                return
            card_text, card_markup = await user_card(callback.message.chat.id, target_id, True)
            await callback.message.edit_text(result_text + "\n\n" + card_text, reply_markup=card_markup)
        else:
            with db() as con:
                con.execute(
                    "INSERT INTO pending_user_actions(chat_id,admin_id,target_id,action,created_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(chat_id,admin_id) DO UPDATE SET target_id=excluded.target_id,action=excluded.action,created_at=excluded.created_at",
                    (callback.message.chat.id, callback.from_user.id, target_id, action_name,
                     datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
                con.commit()
            prompts = {
                "ban": "Отправьте срок и причину, например <code>7д спам</code>. Если срок не нужен, отправьте только причину — бан будет без срока.",
                "mute": "Отправьте срок и причину, например <code>30м флуд</code>. Срок обязателен.",
                "warn": "Отправьте причину предупреждения.",
            }
            await callback.message.edit_text(
                f"📝 <b>Действие с пользователем #{target_id}</b>\n\n{prompts[action_name]}\n"
                "Для отмены отправьте «отмена».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=f"users:card:{target_id}")],
                    [InlineKeyboardButton(text="⬅️ К пользователям", callback_data="panel:users")],
                ]),
            )
    elif key == "rp:create":
        with db() as con:
            con.execute("DELETE FROM pending_rp_edits WHERE chat_id=? AND user_id=?",
                        (callback.message.chat.id, callback.from_user.id))
            con.execute(
                "INSERT INTO pending_panel_inputs(chat_id,user_id,kind,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(chat_id,user_id) DO UPDATE SET kind=excluded.kind,created_at=excluded.created_at",
                (callback.message.chat.id, callback.from_user.id, "rp_create",
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            con.commit()
        await callback.message.edit_text(
            "➕ <b>Создание RP-действия</b>\n\n"
            "Отправьте одной строкой в формате:\n"
            "<code>Название | ключ1, ключ2 | Ответ бота</code>\n\n"
            "Пример: <code>Приветствие | привет, здарова | *кивает и приветствует*</code>\n"
            "Ключевые слова должны быть короткими фразами. Отмена: «отмена».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="rp:cancel")],
                [InlineKeyboardButton(text="⬅️ В каталог", callback_data="panel:rp")],
            ]),
        )
    elif key == "rp:cancel":
        with db() as con:
            con.execute("DELETE FROM pending_panel_inputs WHERE chat_id=? AND user_id=?",
                        (callback.message.chat.id, callback.from_user.id))
            con.commit()
        await callback.message.edit_text(
            rp_catalog(callback.message.chat.id),
            reply_markup=rp_catalog_keyboard(callback.message.chat.id, True),
        )
    elif key.startswith("rp:item:"):
        try:
            action_id = int(key.split(":")[2])
        except (ValueError, IndexError):
            await callback.answer("Некорректный ID действия.", show_alert=True)
            return
        detail, enabled = rp_item_view(callback.message.chat.id, action_id)
        if detail is None:
            await callback.answer("Действие не найдено в этом чате.", show_alert=True)
            return
        await callback.message.edit_text(detail, reply_markup=rp_item_keyboard(action_id, enabled))
    elif key.startswith("rp:toggle:"):
        try:
            action_id = int(key.split(":")[2])
        except (ValueError, IndexError):
            await callback.answer("Некорректный ID действия.", show_alert=True)
            return
        with db() as con:
            row = con.execute(
                "SELECT name,is_enabled FROM rp_actions WHERE chat_id=? AND id=?",
                (callback.message.chat.id, action_id),
            ).fetchone()
            if row:
                new_state = 0 if row["is_enabled"] else 1
                con.execute(
                    "UPDATE rp_actions SET is_enabled=? WHERE chat_id=? AND id=?",
                    (new_state, callback.message.chat.id, action_id),
                )
                con.commit()
        if not row:
            await callback.answer("Действие не найдено в этом чате.", show_alert=True)
            return
        log_action(callback.message.chat.id, None, callback.from_user.id,
                   "rp_action_enabled" if new_state else "rp_action_disabled",
                   f"#{action_id}: {row['name']}")
        detail, enabled = rp_item_view(callback.message.chat.id, action_id)
        await callback.message.edit_text(detail, reply_markup=rp_item_keyboard(action_id, enabled))
    elif key.startswith("rp:edit:"):
        parts = key.split(":")
        if len(parts) != 4 or parts[3] not in ("name", "keywords", "response"):
            await callback.answer("Некорректное поле редактирования.", show_alert=True)
            return
        try:
            action_id = int(parts[2])
        except ValueError:
            await callback.answer("Некорректный ID действия.", show_alert=True)
            return
        with db() as con:
            exists = con.execute(
                "SELECT 1 FROM rp_actions WHERE chat_id=? AND id=?",
                (callback.message.chat.id, action_id),
            ).fetchone()
            if exists:
                con.execute("DELETE FROM pending_panel_inputs WHERE chat_id=? AND user_id=?",
                            (callback.message.chat.id, callback.from_user.id))
                con.execute(
                    "INSERT INTO pending_rp_edits(chat_id,user_id,action_id,field,created_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(chat_id,user_id) DO UPDATE SET action_id=excluded.action_id,field=excluded.field,created_at=excluded.created_at",
                    (callback.message.chat.id, callback.from_user.id, action_id, parts[3],
                     datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
                con.commit()
        if not exists:
            await callback.answer("Действие не найдено в этом чате.", show_alert=True)
            return
        labels = {"name": "новое название (до 80 символов)", "keywords": "ключевые слова через запятую (каждое до 60 символов)", "response": "новый ответ (до 1000 символов)"}
        await callback.message.edit_text(
            f"✏️ <b>Редактирование RP-действия #{action_id}</b>\n\n"
            f"Отправьте {labels[parts[3]]}. Чтобы отменить, отправьте «отмена».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"rp:item:{action_id}")],
                [InlineKeyboardButton(text="⬅️ В каталог", callback_data="panel:rp")],
            ]),
        )
    elif key == "rules":
        await callback.message.edit_text(
            "⚖️ <b>Правила чата</b>\n\n1. Уважайте других участников.\n2. Не размещайте спам и не засоряйте чат сообщениями.\n3. Соблюдайте правила сообщества и законные требования администрации.\n4. Не публикуйте чужие личные данные.",
            reply_markup=home_keyboard(),
        )
    elif key == "contact":
        await callback.message.edit_text("📨 Опишите проблему и отправьте сообщение администрации чата. Не указывайте пароли и коды подтверждения.", reply_markup=home_keyboard())
    elif key == "help":
        await callback.message.edit_text(HELP, reply_markup=home_keyboard())
    else:
        await callback.message.edit_text("👋 <b>heradeLegend</b>\nВыберите нужный раздел в меню ниже.", reply_markup=home_keyboard())
    await callback.answer()


@dp.errors()
async def handle_update_error(event: ErrorEvent):
    """Keep one failed Telegram API request from producing an unhandled update traceback."""
    error = event.exception
    if isinstance(error, TelegramNetworkError):
        log.warning(
            "Telegram API request timed out or failed while processing an update. "
            "Check outbound access to api.telegram.org:443 and TELEGRAM_PROXY. "
            "The response could not be delivered because the connection failed."
        )
        return True
    log.error(
        "Unhandled update error (%s). Update processing was stopped for this update.",
        type(error).__name__,
        exc_info=(type(error), error, error.__traceback__),
    )
    return True


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
    except TelegramNetworkError as exc:
        # Do not dump a long connector traceback for a known startup connectivity failure.
        # The bot cannot run until Telegram Bot API is reachable.
        log.error(
            "Startup connection to Telegram Bot API failed (%s). "
            "Check that TELEGRAM_PROXY is a reachable SOCKS5/HTTP proxy and that it "
            "can connect to api.telegram.org:443. The bot was not started.",
            type(exc).__name__,
        )
        raise SystemExit(1) from None
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by keyboard interrupt.")
