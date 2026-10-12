# -*- coding: utf-8 -*-
"""Ремонт: предмет в ремонте недоступен для аренды, история ремонтов сохраняется."""
from __future__ import annotations

from ..db.database import get_db
from ..errors import AppError, NotFoundError
from .audit_service import audit_service
from .auth_service import auth_service
from .inventory_service import inventory_service


class RepairsService:
    def send_to_repair(self, item_id: int, quantity: int = 1, reason: str = "",
                       description: str = "") -> int:
        auth_service.require_login()
        item = inventory_service.get(item_id)
        if not item:
            raise NotFoundError("Предмет не найден.")
        if quantity <= 0:
            raise AppError("Количество для ремонта должно быть больше нуля.")
        free = item["quantity_total"] - item["reserved_now"] - item["in_repair"]
        if quantity > free:
            raise AppError(
                f"Нельзя отправить в ремонт {quantity} шт.: свободно только {max(0, free)} шт. "
                "Остальное сейчас в аренде или уже в ремонте.")
        db = get_db()
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO repairs(item_id, quantity, reason, description) VALUES (?,?,?,?)",
                (item_id, quantity, reason, description))
            repair_id = int(cur.lastrowid)
            still_free = item["quantity_total"] - item["reserved_now"] - item["in_repair"] - quantity
            if still_free <= 0:
                conn.execute("UPDATE inventory_items SET status='repair' WHERE id=?", (item_id,))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) "
                "VALUES ('item',?,'Отправлен в ремонт',?)", (item_id, f"{quantity} шт. {reason}"))
        audit_service.log("Реквизит отправлен в ремонт", entity="item", entity_id=item_id,
                          details=f"{item['name']} × {quantity}. {reason}".strip())
        return repair_id

    def return_from_repair(self, repair_id: int, comment: str = "") -> None:
        auth_service.require_login()
        db = get_db()
        row = db.query_one("SELECT * FROM repairs WHERE id=?", (repair_id,))
        if not row:
            raise NotFoundError("Запись о ремонте не найдена.")
        if row["status"] == "done":
            raise AppError("Этот ремонт уже закрыт.")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE repairs SET status='done', date_end=datetime('now','localtime'), comment=? WHERE id=?",
                (comment, repair_id))
            remaining = conn.execute(
                "SELECT ifnull(SUM(quantity),0) q FROM repairs WHERE item_id=? AND status='open'",
                (row["item_id"],)).fetchone()["q"]
            if remaining == 0:
                conn.execute(
                    "UPDATE inventory_items SET status='in_stock' WHERE id=? AND status='repair'",
                    (row["item_id"],))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) "
                "VALUES ('item',?,'Возвращён из ремонта',?)", (row["item_id"], comment))
        audit_service.log("Реквизит возвращён из ремонта", entity="item", entity_id=row["item_id"],
                          details=comment)

    def open_repairs(self, item_id: int | None = None) -> list[dict]:
        sql = ("SELECT r.*, i.name item_name, i.inv_number FROM repairs r "
               "JOIN inventory_items i ON i.id=r.item_id WHERE r.status='open'")
        params: list = []
        if item_id:
            sql += " AND r.item_id=?"
            params.append(item_id)
        sql += " ORDER BY r.date_start DESC"
        return [dict(r) for r in get_db().query(sql, params)]

    def history(self, item_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT * FROM repairs WHERE item_id=? ORDER BY date_start DESC", (item_id,))]


repairs_service = RepairsService()
