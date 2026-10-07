# -*- coding: utf-8 -*-
"""
sqlite_storage.py — ذخیرهٔ پایدار وضعیت FSM در SQLite (جایگزین MemoryStorage).

با MemoryStorage، هر ری‌استارت ربات (آپدیت کد، قطعی سرور، ...) همهٔ کاربرانی
را که وسط فرم چندمرحله‌ای بودند به نقطهٔ صفر برمی‌گرداند. این storage وضعیت و
دادهٔ هر کاربر را در جدول fsm نگه می‌دارد.

دادهٔ فرم باید قابل تبدیل به JSON باشد (رشته/عدد/بولی/None/لیست/دیکشنری).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

import db

logger = logging.getLogger(__name__)


def _key(key: StorageKey) -> str:
    parts = [str(key.bot_id), str(key.chat_id), str(key.user_id)]
    if getattr(key, "thread_id", None):
        parts.append(f"t{key.thread_id}")
    if getattr(key, "business_connection_id", None):
        parts.append(f"b{key.business_connection_id}")
    if getattr(key, "destiny", "default") != "default":
        parts.append(f"d{key.destiny}")
    return ":".join(parts)


def _dumps(data: Dict[str, Any]) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)
    except TypeError as e:
        # نباید رخ دهد؛ اگر رخ داد لاگ می‌شود و مقدار به رشته تبدیل می‌شود تا ربات متوقف نشود
        logger.error(f"[FSM] دادهٔ غیرقابل‌ذخیره در وضعیت فرم: {e}")
        return json.dumps(data, ensure_ascii=False, default=str)


class SQLiteStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        s = state.state if isinstance(state, State) else state
        k = _key(key)
        if s is None:
            # اگر داده هم خالی است ردیف حذف شود تا جدول بی‌دلیل بزرگ نشود
            db.execute("UPDATE fsm SET state=NULL WHERE key=?", (k,))
            db.execute("DELETE FROM fsm WHERE key=? AND (data IS NULL OR data='{}')", (k,))
            return
        db.execute("INSERT INTO fsm(key, state, data) VALUES (?,?,'{}') "
                   "ON CONFLICT(key) DO UPDATE SET state=excluded.state", (k, s))

    async def get_state(self, key: StorageKey) -> Optional[str]:
        r = db.query_one("SELECT state FROM fsm WHERE key=?", (_key(key),))
        return r["state"] if r else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        k = _key(key)
        if not data:
            db.execute("UPDATE fsm SET data='{}' WHERE key=?", (k,))
            db.execute("DELETE FROM fsm WHERE key=? AND state IS NULL", (k,))
            return
        db.execute("INSERT INTO fsm(key, state, data) VALUES (?,NULL,?) "
                   "ON CONFLICT(key) DO UPDATE SET data=excluded.data", (k, _dumps(data)))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        r = db.query_one("SELECT data FROM fsm WHERE key=?", (_key(key),))
        if not r or not r["data"]:
            return {}
        try:
            return json.loads(r["data"])
        except ValueError:
            logger.error(f"[FSM] دادهٔ خراب برای {_key(key)} — پاک شد")
            return {}

    async def close(self) -> None:
        pass
