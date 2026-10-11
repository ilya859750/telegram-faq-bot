"""Telegram-бот: FAQ для малого бизнеса + сбор заявок.

Запуск: python bot.py
"""
import asyncio
import html
import json
import logging
import os
import re
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

import db
import sheets

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
CONFIG = json.loads(Path(__file__).with_name("faq.json").read_text(encoding="utf-8"))
FAQ = CONFIG["faq"]

router = Router()


# ---------- Состояния формы заявки ----------
class LeadForm(StatesGroup):
    name = State()
    phone = State()
    message = State()


# ---------- Клавиатуры ----------
def main_menu():
    kb = InlineKeyboardBuilder()
    for i, item in enumerate(FAQ):
        kb.button(text=item["button"], callback_data=f"faq:{i}")
    kb.button(text=CONFIG.get("lead_button", "📝 Оставить заявку"), callback_data="lead")
    kb.adjust(1)
    return kb.as_markup()


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def normalize_phone(raw: str) -> str | None:
    """Оставляем цифры и ведущий +. Допустимо 10-15 цифр."""
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 15:
        return None
    return ("+" if raw.strip().startswith("+") else "") + digits


def find_answer(text: str) -> str | None:
    """Простой поиск по ключевым словам из faq.json."""
    text = text.lower()
    for item in FAQ:
        if any(kw in text for kw in item.get("keywords", [])):
            return item["answer"]
    return None


# ---------- Команды ----------
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(CONFIG["welcome"], reply_markup=main_menu())


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Отменено. Чем ещё могу помочь?", reply_markup=ReplyKeyboardRemove())
    await message.answer("Выберите тему:", reply_markup=main_menu())


@router.message(Command("myid"))
async def cmd_myid(message: Message):
    # Нужна один раз, чтобы узнать свой ID и вписать его в ADMIN_IDS
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("leads"))
async def cmd_leads(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = db.get_leads(10)
    if not rows:
        await message.answer("Заявок пока нет.")
        return
    parts = []
    for r in rows:
        username = f"@{html.escape(r['username'])}" if r["username"] else "—"
        parts.append(
            f"<b>#{r['id']}</b> · {r['created_at']} UTC\n"
            f"👤 {html.escape(r['name'])} ({username})\n"
            f"📞 {html.escape(r['phone'])}\n"
            f"💬 {html.escape(r['message'])}"
        )
    await message.answer("\n\n".join(parts))


@router.message(Command("export"))
async def cmd_export(message: Message):
    if not is_admin(message.from_user.id):
        return
    data = db.export_csv().encode("utf-8-sig")
    await message.answer_document(BufferedInputFile(data, filename="leads.csv"))


# ---------- FAQ (кнопки) ----------
@router.callback_query(F.data.startswith("faq:"))
async def show_answer(cb: CallbackQuery):
    try:
        item = FAQ[int(cb.data.split(":")[1])]
    except (ValueError, IndexError):
        await cb.answer("Раздел не найден", show_alert=True)
        return
    await cb.message.answer(item["answer"], reply_markup=main_menu())
    await cb.answer()


# ---------- Форма заявки ----------
@router.callback_query(F.data == "lead")
async def lead_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(LeadForm.name)
    await cb.message.answer("Как к вам обращаться? (для отмены — /cancel)")
    await cb.answer()


@router.message(LeadForm.name, F.text)
async def lead_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not 2 <= len(name) <= 60:
        await message.answer("Пожалуйста, введите имя (от 2 до 60 символов).")
        return
    await state.update_data(name=name)
    await state.set_state(LeadForm.phone)
    await message.answer(
        "Оставьте номер телефона — нажмите кнопку ниже или напишите вручную:",
        reply_markup=phone_keyboard(),
    )


@router.message(LeadForm.phone, F.contact)
async def lead_phone_contact(message: Message, state: FSMContext):
    await _save_phone(message, state, message.contact.phone_number)


@router.message(LeadForm.phone, F.text)
async def lead_phone_text(message: Message, state: FSMContext):
    await _save_phone(message, state, message.text)


async def _save_phone(message: Message, state: FSMContext, raw: str):
    phone = normalize_phone(raw)
    if phone is None:
        await message.answer("Не похоже на номер телефона. Пример: +7 900 123-45-67")
        return
    await state.update_data(phone=phone)
    await state.set_state(LeadForm.message)
    await message.answer(
        "Коротко опишите, что вам нужно:", reply_markup=ReplyKeyboardRemove()
    )


@router.message(LeadForm.message, F.text)
async def lead_message(message: Message, state: FSMContext, bot: Bot):
    text = message.text.strip()
    if not 3 <= len(text) <= 1000:
        await message.answer("Опишите запрос чуть подробнее (до 1000 символов).")
        return
    data = await state.get_data()
    await state.clear()

    user = message.from_user
    lead_id, created_at = db.add_lead(user.id, user.username, data["name"], data["phone"], text)

    await message.answer(
        f"Спасибо, {html.escape(data['name'])}! Заявка №{lead_id} принята — "
        "мы свяжемся с вами в ближайшее время. 🙌",
        reply_markup=main_menu(),
    )

    # Дублируем заявку в Google Sheets (если настроено). Ошибка таблицы не должна ломать бота:
    # заявка уже сохранена в SQLite, а владелец получит уведомление ниже.
    if sheets.is_configured():
        try:
            await asyncio.to_thread(
                sheets.append_lead, lead_id, created_at, data["name"], user.username, data["phone"], text
            )
        except Exception as e:
            logging.error("Не удалось записать заявку #%s в Google Sheets: %s", lead_id, e)

    # Уведомление владельцу бизнеса
    username = f"@{html.escape(user.username)}" if user.username else "—"
    notice = (
        f"🔔 <b>Новая заявка #{lead_id}</b>\n"
        f"👤 {html.escape(data['name'])} ({username})\n"
        f"📞 {html.escape(data['phone'])}\n"
        f"💬 {html.escape(text)}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, notice)
        except Exception as e:  # админ мог не запускать бота
            logging.warning("Не удалось уведомить админа %s: %s", admin_id, e)


# ---------- Свободный текст (в самом конце!) ----------
@router.message(F.text)
async def free_text(message: Message):
    answer = find_answer(message.text)
    if answer:
        await message.answer(answer, reply_markup=main_menu())
    else:
        await message.answer(CONFIG["fallback"], reply_markup=main_menu())


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Скопируйте .env.example в .env и впишите токен.")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS пуст: уведомления о заявках и /leads работать не будут.")
    db.init_db()
    if sheets.is_configured():
        logging.info("Google Sheets подключены: заявки будут дублироваться в таблицу.")
    else:
        logging.info("Google Sheets не настроены (нужны GOOGLE_SHEET_ID и credentials.json) — пропускаю.")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
