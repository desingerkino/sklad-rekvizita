# -*- coding: utf-8 -*-
"""
Журнал значимых действий (AuditLog).

Пишет: кто, когда (системное время Windows), что сделал, с каким объектом и на
какую сумму. Автоматические операции записываются от имени пользователя «Система».
"""
from __future__ import annotations

import logging
from typing import Optional

from ..db.database import get_db
from ..utils import db_dt

log = logging.getLogger(__name__)


class AuditService:
    def log(self, action: str, entity: str = "", entity_id: Optional[int] = None,
            details: str = "", amount: Optional[float] = None,
            user_name: Optional[str] = None, user_id: Optional[int] = None) -> None:
        from .auth_service import auth_service
        name = user_name or auth_service.current_name()
        uid = user_id if user_id is not None else auth_service.current_id()
        try:
            db = get_db()
            db.conn.execute(
                "INSERT INTO audit_log(user_id, user_name, action, entity, entity_id, details, "
                "amount, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uid, name, action, entity, entity_id, details, amount, db_dt()))
        except Exception:
            # журнал не должен ломать основную операцию
            log.exception("Не удалось записать действие в журнал: %s", action)

    def search(self, text: str = "", entity: str = "", limit: int = 500) -> list[dict]:
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if text.strip():
            sql += " AND (user_name LIKE ? OR action LIKE ? OR details LIKE ? OR entity LIKE ?)"
            params += [f"%{text.strip()}%"] * 4
        if entity:
            sql += " AND entity=?"
            params.append(entity)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in get_db().query(sql, params)]

    def for_entity(self, entity: str, entity_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT * FROM audit_log WHERE entity=? AND entity_id=? ORDER BY created_at DESC",
            (entity, entity_id))]


audit_service = AuditService()
