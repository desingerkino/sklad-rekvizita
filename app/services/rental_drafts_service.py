# -*- coding: utf-8 -*-
"""
Незавершённые аренды: несколько оформлений одновременно.

На складе обычная картина: один клиент выбирает реквизит, приходит второй,
оператор начинает оформлять его, потом возвращается к первому. Раньше форма
«Новая аренда» была одна на всех, и второй клиент затирал первого.

Теперь каждое оформление — отдельный черновик со своим номером. Номер
бронируется сразу, из того же счётчика, что и у обычных аренд: двух систем
нумерации быть не должно, а номер должен остаться за арендой до самой выдачи.

Черновик живёт в базе, а не в памяти, поэтому переживает закрытие программы —
после запуска незавершённые оформления возвращаются на место. Черновик привязан
к пользователю: чужие оформления оператору не показываются и не мешают.

Что здесь НЕ происходит: реквизит не резервируется и остатки не двигаются.
Черновик — это заполненная форма, а не выдача. Склад меняется только в момент
«Выдать аренду», как и раньше.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from ..db.database import get_db
from ..errors import NotFoundError, PermissionDeniedError, ValidationError
from ..utils import db_dt
from .audit_service import audit_service
from .auth_service import auth_service

log = logging.getLogger(__name__)

STATUS_OPEN, STATUS_ISSUED, STATUS_CANCELLED = "open", "issued", "cancelled"
MAX_OPEN_DRAFTS = 12          # больше дюжины одновременных оформлений — это уже беда


class RentalDraftsService:
    # ------------------------------------------------------------- создание
    def open_new(self) -> dict:
        """
        Открывает новое оформление и бронирует за ним номер.

        Номер берётся тем же способом, что и у обычной аренды, поэтому
        пересечься они не могут. Отменённый черновик номер не возвращает —
        дырка в нумерации честнее, чем два документа с одним номером.
        """
        user = auth_service.require_login()
        db = get_db()
        open_count = db.query_one(
            "SELECT COUNT(*) c FROM rental_drafts WHERE user_id=? AND status=?",
            (user.id, STATUS_OPEN))["c"]
        if open_count >= MAX_OPEN_DRAFTS:
            raise ValidationError(
                f"Одновременно открыто {open_count} незавершённых аренд. "
                "Завершите или отмените лишние, прежде чем начинать новую.")

        from .rentals_service import rentals_service

        with db.transaction() as conn:
            number = rentals_service._next_number(conn)
            cursor = conn.execute(
                "INSERT INTO rental_drafts(number, user_id, payload, status, "
                "created_at, updated_at) VALUES (?,?,'{}',?,?,?)",
                (number, user.id, STATUS_OPEN, db_dt(), db_dt()))
            draft_id = int(cursor.lastrowid)
        audit_service.log("Начато оформление аренды", entity="rental_draft",
                          entity_id=draft_id, details=number)
        log.info("Открыт черновик аренды %s (пользователь %s)", number, user.login)
        return {"id": draft_id, "number": number, "status": STATUS_OPEN, "payload": {}}

    # --------------------------------------------------------------- чтение
    def list_open(self, user_id: Optional[int] = None) -> list[dict]:
        """Незавершённые оформления текущего (или указанного) пользователя."""
        if user_id is None:
            user_id = auth_service.current_id()
        if not user_id:
            return []
        rows = get_db().query(
            "SELECT id, number, status, created_at, updated_at, payload "
            "FROM rental_drafts WHERE user_id=? AND status=? ORDER BY id",
            (user_id, STATUS_OPEN))
        return [self._row(r) for r in rows]

    def get(self, draft_id: int) -> dict:
        row = get_db().query_one(
            "SELECT id, number, status, user_id, rental_id, created_at, updated_at, "
            "payload FROM rental_drafts WHERE id=?", (draft_id,))
        if not row:
            raise NotFoundError("Незавершённая аренда не найдена.")
        data = self._row(row)
        data["user_id"] = row["user_id"]
        data["rental_id"] = row["rental_id"]
        return data

    @staticmethod
    def _row(row) -> dict:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (ValueError, TypeError):
            payload = {}
        return {"id": row["id"], "number": row["number"], "status": row["status"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "payload": payload}

    def _require_own(self, draft_id: int) -> dict:
        """
        Чужой черновик трогать нельзя: у каждого оператора своё оформление.
        Администратор — не исключение: это не вопрос прав, а вопрос того, что
        два человека не должны править одну форму одновременно.
        """
        draft = self.get(draft_id)
        current = auth_service.current_id()
        if draft["user_id"] and current and draft["user_id"] != current:
            raise PermissionDeniedError(
                "Это оформление начал другой пользователь.")
        return draft

    # ---------------------------------------------------------- сохранение
    def save(self, draft_id: int, payload: dict[str, Any]) -> None:
        """
        Автосохранение состояния формы. Ошибка сохранения не должна мешать
        работе: черновик — вспомогательная вещь, а данные аренды пишутся в
        базу только при выдаче.
        """
        try:
            self._require_own(draft_id)
            db = get_db()
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE rental_drafts SET payload=?, updated_at=? "
                    "WHERE id=? AND status=?",
                    (json.dumps(payload, ensure_ascii=False, default=str),
                     db_dt(), draft_id, STATUS_OPEN))
        except Exception:                                          # noqa: BLE001
            log.exception("Не удалось сохранить черновик аренды %s", draft_id)

    # ----------------------------------------------------------- завершение
    def mark_issued(self, draft_id: int, rental_id: int) -> None:
        """Аренда выдана: черновик уходит из списка, но остаётся в истории."""
        draft = self._require_own(draft_id)
        with get_db().transaction() as conn:
            conn.execute(
                "UPDATE rental_drafts SET status=?, rental_id=?, payload='{}', "
                "updated_at=? WHERE id=?",
                (STATUS_ISSUED, rental_id, db_dt(), draft_id))
        audit_service.log("Оформление завершено выдачей", entity="rental_draft",
                          entity_id=draft_id, details=draft["number"])
        log.info("Черновик %s выдан как аренда %s", draft["number"], rental_id)

    def cancel(self, draft_id: int) -> str:
        """
        Отмена оформления. Строка не удаляется: номер должен остаться занятым,
        иначе он уйдёт другой аренде, а в журнале появится дыра без объяснения.
        """
        draft = self._require_own(draft_id)
        if draft["status"] != STATUS_OPEN:
            raise ValidationError("Это оформление уже закрыто.")
        with get_db().transaction() as conn:
            conn.execute(
                "UPDATE rental_drafts SET status=?, payload='{}', updated_at=? WHERE id=?",
                (STATUS_CANCELLED, db_dt(), draft_id))
        audit_service.log("Оформление аренды отменено", entity="rental_draft",
                          entity_id=draft_id, details=draft["number"])
        log.info("Черновик аренды %s отменён", draft["number"])
        return draft["number"]

    # ------------------------------------------------------------- справка
    def count_open(self, user_id: Optional[int] = None) -> int:
        return len(self.list_open(user_id))

    def number_taken(self, number: str) -> bool:
        return get_db().query_one(
            "SELECT 1 FROM rental_drafts WHERE number=?", (number,)) is not None


rental_drafts_service = RentalDraftsService()
