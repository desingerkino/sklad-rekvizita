# -*- coding: utf-8 -*-
"""
Автосохранение незавершённых форм.

Всё, что уже подтверждено (карточка, аренда, возврат, начисление), пишется в базу
сразу и отдельной кнопки «Сохранить» не требует. Незаконченный ввод в форме
«Новая аренда» подтверждением не является, поэтому он сохраняется черновиком:
при аварийном закрытии программы набранный список позиций не пропадёт.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..db.database import get_db
from ..utils import db_dt

log = logging.getLogger(__name__)

DRAFT_NEW_RENTAL = "new_rental"


class DraftService:
    def save(self, key: str, payload: dict[str, Any]) -> None:
        from .auth_service import auth_service
        try:
            db = get_db()
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO form_drafts(key, user_id, payload, updated_at) VALUES (?,?,?,?) "
                    "ON CONFLICT(key, user_id) DO UPDATE SET payload=excluded.payload, "
                    "updated_at=excluded.updated_at",
                    (key, auth_service.current_id() or 0,
                     json.dumps(payload, ensure_ascii=False, default=str), db_dt()))
        except Exception:
            # черновик — вспомогательная вещь, ошибка не должна мешать работе
            log.exception("Не удалось сохранить черновик %s", key)

    def load(self, key: str) -> Optional[dict]:
        from .auth_service import auth_service
        row = get_db().query_one(
            "SELECT payload, updated_at FROM form_drafts WHERE key=? AND user_id=?",
            (key, auth_service.current_id() or 0))
        if not row:
            return None
        try:
            data = json.loads(row["payload"])
            data["_updated_at"] = row["updated_at"]
            return data
        except (ValueError, TypeError):
            return None

    def clear(self, key: str) -> None:
        from .auth_service import auth_service
        try:
            db = get_db()
            with db.transaction() as conn:
                conn.execute("DELETE FROM form_drafts WHERE key=? AND user_id=?",
                             (key, auth_service.current_id() or 0))
        except Exception:
            log.exception("Не удалось очистить черновик %s", key)


draft_service = DraftService()
