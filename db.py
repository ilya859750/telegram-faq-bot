"""Хранилище заявок на SQLite (стандартная библиотека, без доп. зависимостей)."""
import csv
import io
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB_PATH = "leads.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at  TEXT NOT NULL,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                name        TEXT NOT NULL,
                phone       TEXT NOT NULL,
                message     TEXT NOT NULL
            )
            """
        )


def add_lead(user_id: int, username: str | None, name: str, phone: str, message: str) -> tuple[int, str]:
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO leads (created_at, user_id, username, name, phone, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (created_at, user_id, username, name, phone, message),
        )
        return cur.lastrowid, created_at


def get_leads(limit: int = 10) -> list[sqlite3.Row]:
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def export_csv() -> str:
    """Все заявки в виде CSV-текста (utf-8-sig, чтобы Excel корректно открыл кириллицу)."""
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM leads ORDER BY id").fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "created_at_utc", "user_id", "username", "name", "phone", "message"])
    for r in rows:
        writer.writerow([r["id"], r["created_at"], r["user_id"], r["username"], r["name"], r["phone"], r["message"]])
    return buf.getvalue()
