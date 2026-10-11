"""Запись заявок в Google Sheets через сервисный аккаунт (библиотека gspread).

Настройки читаются из .env лениво (внутри функций), потому что load_dotenv()
вызывается в bot.py уже после импорта этого модуля.
"""
import os
from pathlib import Path

import gspread

HEADER = ["ID", "Дата (UTC)", "Имя", "Telegram", "Телефон", "Запрос"]

_worksheet = None


def _creds_path() -> str:
    return os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")


def is_configured() -> bool:
    """Интеграция включена, если задан ID таблицы и есть файл с ключом."""
    return bool(os.getenv("GOOGLE_SHEET_ID")) and Path(_creds_path()).exists()


def _get_worksheet():
    global _worksheet
    if _worksheet is None:
        client = gspread.service_account(filename=_creds_path())
        ws = client.open_by_key(os.environ["GOOGLE_SHEET_ID"]).sheet1
        if not ws.row_values(1):  # пустая таблица -> добавляем заголовки
            ws.append_row(HEADER)
        _worksheet = ws
    return _worksheet


def append_lead(lead_id: int, created_at: str, name: str, username: str | None, phone: str, message: str) -> None:
    """Добавляет заявку строкой в конец таблицы. Синхронная функция (вызывать через asyncio.to_thread)."""
    global _worksheet
    row = [lead_id, created_at, name, f"@{username}" if username else "", phone, message]
    try:
        # RAW: значения записываются как есть (телефон +7... не превратится в число/формулу)
        _get_worksheet().append_row(row, value_input_option="RAW")
    except Exception:
        _worksheet = None  # при ошибке в следующий раз переподключимся заново
        raise
