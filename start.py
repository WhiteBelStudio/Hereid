"""Welcome and help handlers for the Hereid Telegram moderation bot."""
from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

router = Router(name="start")


def start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📖 Помощь", callback_data="start:help"),
                InlineKeyboardButton(text="📋 Команды", callback_data="start:commands"),
            ]
        ]
    )


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ На главную", callback_data="start:home")]
        ]
    )


def welcome_text(name: str) -> str:
    safe_name = html.escape(name or "участник")
    return (
        f"👋 <b>Привет, {safe_name}!</b>\n\n"
        "Я — <b>heradeLegend</b>, бот для помощи в модерации "
        "и поддержании порядка в Telegram-сообществах.\n\n"
        "🛡️ <b>Что я умею?</b>\n"
        "• Помогать с модерацией чатов.\n"
        "• Поддерживать систему предупреждений.\n"
        "• Показывать справку и доступные команды.\n"
        "• Предоставлять инструменты администраторам в рамках их прав.\n\n"
        "👇 <b>Выбери нужный раздел ниже.</b>"
    )


HELP_TEXT = (
    "📖 <b>Помощь по heradeLegend</b>\n\n"
    "Добавь бота в группу и выдай ему необходимые права администратора. "
    "Доступность действий зависит от разрешений бота, твоих прав "
    "и настроек сообщества.\n\n"
    "Используй раздел «Команды», чтобы посмотреть базовые команды."
)

COMMANDS_TEXT = (
    "📋 <b>Основные команды</b>\n\n"
    "<code>/start</code> — открыть главное меню.\n"
    "<code>/help</code> — открыть справку.\n\n"
    "Другие команды модерации зависят от настроек чата и твоих прав."
)


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    name = message.from_user.first_name if message.from_user else "участник"
    await message.answer(welcome_text(name), reply_markup=start_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=back_keyboard())


@router.callback_query(F.data == "start:home")
async def callback_home(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.edit_text(
            welcome_text(callback.from_user.first_name),
            reply_markup=start_keyboard(),
        )
    await callback.answer()


@router.callback_query(F.data == "start:help")
async def callback_help(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.edit_text(HELP_TEXT, reply_markup=back_keyboard())
    await callback.answer()


@router.callback_query(F.data == "start:commands")
async def callback_commands(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await callback.message.edit_text(COMMANDS_TEXT, reply_markup=back_keyboard())
    await callback.answer()
