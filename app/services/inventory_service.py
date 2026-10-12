# -*- coding: utf-8 -*-
"""Каталог реквизита: карточки, поиск, количество, доступность."""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from ..db.database import get_db
from ..errors import AppError, NotFoundError, ValidationError
from ..utils import FAR_FUTURE, parse_date, today

log = logging.getLogger(__name__)

STATUS_LABELS = {"in_stock": "На складе", "repair": "В ремонте", "archived": "Списан"}


def _fts_query(text: str) -> str:
    """Готовит запрос для FTS5: каждое слово ищется как префикс."""
    words = [w for w in re.split(r"[^\w\u0400-\u04FF]+", text) if w]
    return " ".join(f'"{w}"*' for w in words)


class InventoryService:
    # ------------------------------------------------------------- нумерация
    def next_inv_number(self) -> str:
        row = get_db().query_one(
            "SELECT inv_number FROM inventory_items "
            "WHERE inv_number GLOB '[0-9]*' ORDER BY CAST(inv_number AS INTEGER) DESC LIMIT 1"
        )
        nxt = (int(row["inv_number"]) + 1) if row and row["inv_number"].isdigit() else 1
        return f"{nxt:06d}"

    # ------------------------------------------------------------------ CRUD
    def create(self, data: dict) -> int:
        from .auth_service import auth_service
        auth_service.require_login()
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Укажите название предмета.")
        qty = int(data.get("quantity_total") or 0)
        if qty < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        inv_number = (data.get("inv_number") or "").strip() or self.next_inv_number()

        db = get_db()
        if db.query_one("SELECT id FROM inventory_items WHERE inv_number=?", (inv_number,)):
            raise ValidationError(f"Инвентарный номер {inv_number} уже занят.")
        # штрихкод = инвентарный номер (версия 2.4). Если номер задан кириллицей,
        # предмет создаётся без штрихкода — работать это не мешает.
        from .barcode_service import barcode_service
        try:
            barcode = barcode_service.value_for(inv_number)
        except ValidationError:
            barcode = ""

        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO inventory_items(inv_number, name, category_id, description, keywords, "
                "note, quantity_total, rental_price, valuation, status, barcode, barcode_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (inv_number, name, data.get("category_id"), data.get("description", ""),
                 data.get("keywords", ""), data.get("note", ""), qty,
                 float(data.get("rental_price") or 0), float(data.get("valuation") or 0),
                 data.get("status") or "in_stock", barcode, "code128"),
            )
            item_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) VALUES ('item',?,?,?)",
                (item_id, "Создан", f"{inv_number} — {name}"),
            )
        from .audit_service import audit_service
        audit_service.log("Добавлен реквизит", entity="item", entity_id=item_id,
                          details=f"{inv_number} — {name}, {qty} шт.")
        return item_id

    def update(self, item_id: int, data: dict) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        db = get_db()
        item = self.get(item_id)
        if not item:
            raise NotFoundError("Предмет не найден.")
        qty = int(data.get("quantity_total", item["quantity_total"]))
        if qty < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        busy = self.reserved_now(item_id)
        if qty < busy:
            raise ValidationError(
                f"Нельзя установить количество {qty}: сейчас в аренде {busy} шт. "
                "Сначала закройте аренды или верните реквизит."
            )
        with db.transaction() as conn:
            conn.execute(
                "UPDATE inventory_items SET name=?, category_id=?, description=?, keywords=?, note=?, "
                "quantity_total=?, rental_price=?, valuation=?, status=?, updated_at=datetime('now','localtime') "
                "WHERE id=?",
                (data.get("name", item["name"]).strip(), data.get("category_id", item["category_id"]),
                 data.get("description", item["description"]), data.get("keywords", item["keywords"]),
                 data.get("note", item["note"]), qty,
                 float(data.get("rental_price", item["rental_price"])),
                 float(data.get("valuation", item["valuation"])),
                 data.get("status", item["status"]), item_id),
            )
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) VALUES ('item',?,?,?)",
                (item_id, "Изменён", ""),
            )
        from .audit_service import audit_service
        audit_service.log("Изменён реквизит", entity="item", entity_id=item_id,
                          details=f"{item['inv_number']} — {data.get('name', item['name'])}")

    def archive(self, item_id: int) -> None:
        """Реквизит не удаляется физически — история аренд должна остаться."""
        from .auth_service import auth_service
        auth_service.require_login()

        if self.reserved_now(item_id):
            raise AppError("Предмет сейчас в аренде, его нельзя списать.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE inventory_items SET is_archived=1, status='archived', "
                "updated_at=datetime('now','localtime') WHERE id=?", (item_id,))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action) VALUES ('item',?,'Списан')",
                (item_id,))

    def get(self, item_id: int) -> Optional[dict]:
        row = get_db().query_one("SELECT * FROM inventory_items WHERE id=?", (item_id,))
        if not row:
            return None
        item = dict(row)
        from .categories_service import categories_service
        item["category_path"] = categories_service.path(item["category_id"])
        item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
        item["in_repair"] = self.in_repair(item_id)
        item["reserved_now"] = self.reserved_now(item_id)
        item["available_now"] = max(0, item["quantity_total"] - item["reserved_now"] - item["in_repair"])
        return item

    def get_by_inv_number(self, inv_number: str) -> Optional[dict]:
        row = get_db().query_one("SELECT id FROM inventory_items WHERE inv_number=?", (inv_number.strip(),))
        return self.get(row["id"]) if row else None

    # ----------------------------------------------------------------- поиск
    def search(self, text: str = "", category_id: Optional[int] = None,
               only_available: bool = False, limit: int = 300, offset: int = 0) -> list[dict]:
        """Поиск по названию, номеру, описанию, примечанию и ключевым словам."""
        db = get_db()
        where = ["i.is_archived = 0"]
        params: list = []

        if category_id:
            from .categories_service import categories_service
            ids = categories_service.descendants(category_id)
            where.append(f"i.category_id IN ({','.join('?' * len(ids))})")
            params += ids

        text = (text or "").strip()

        # Версия 2.4: отсканированный штрихкод или инвентарный номер должны
        # находить предмет мгновенно и точно, а не «похожие по словам».
        # Проверка идёт до полнотекстового поиска: сканер отдаёт код и сразу
        # нажимает Enter, оператору некогда разбираться в списке из десяти
        # позиций. Если точного совпадения нет — работает обычный поиск.
        if text:
            from .barcode_service import barcode_service
            exact = barcode_service.find_item(text)
            if exact and not exact.get("is_archived"):
                if not category_id or exact.get("category_id") in set(params):
                    found = self.get(exact["id"])
                    if found and not (only_available and found["available_now"] <= 0):
                        return [found]

        if text:
            q = _fts_query(text)
            try:
                rows = db.query(
                    f"SELECT i.* FROM items_fts f JOIN inventory_items i ON i.id = f.rowid "
                    f"WHERE items_fts MATCH ? AND {' AND '.join(where)} "
                    f"ORDER BY bm25(items_fts), i.name LIMIT ? OFFSET ?",
                    [q] + params + [limit, offset],
                )
            except Exception:
                like = f"%{text}%"
                rows = db.query(
                    f"SELECT i.* FROM inventory_items i WHERE {' AND '.join(where)} AND ("
                    "i.name LIKE ? OR i.inv_number LIKE ? OR i.description LIKE ? OR "
                    "i.keywords LIKE ? OR i.note LIKE ?) ORDER BY i.name LIMIT ? OFFSET ?",
                    params + [like] * 5 + [limit, offset],
                )
        else:
            rows = db.query(
                f"SELECT i.* FROM inventory_items i WHERE {' AND '.join(where)} "
                f"ORDER BY i.name LIMIT ? OFFSET ?", params + [limit, offset])

        result = []
        for row in rows:
            item = dict(row)
            item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
            item["reserved_now"] = self.reserved_now(item["id"])
            item["in_repair"] = self.in_repair(item["id"])
            item["available_now"] = max(
                0, item["quantity_total"] - item["reserved_now"] - item["in_repair"])
            if only_available and item["available_now"] <= 0:
                continue
            result.append(item)
        return result

    def available_units(self) -> tuple[int, int]:
        """(всего экземпляров, свободно сейчас) — для сводки на главном экране."""
        db = get_db()
        total = db.query_one(
            "SELECT ifnull(SUM(quantity_total),0) q FROM inventory_items WHERE is_archived=0")["q"]
        rented = db.query_one(
            "SELECT ifnull(SUM(ri.quantity - ri.returned_qty),0) q FROM rental_items ri "
            "JOIN rentals r ON r.id = ri.rental_id WHERE r.status='active'")["q"]
        repair = db.query_one(
            "SELECT ifnull(SUM(quantity),0) q FROM repairs WHERE status='open'")["q"]
        return int(total), max(0, int(total) - int(rented) - int(repair))

    def count(self) -> int:
        return get_db().query_one("SELECT COUNT(*) c FROM inventory_items WHERE is_archived=0")["c"]

    # ---------------------------------------------------------- доступность
    def in_repair(self, item_id: int) -> int:
        row = get_db().query_one(
            "SELECT ifnull(SUM(quantity),0) q FROM repairs WHERE item_id=? AND status='open'",
            (item_id,))
        return int(row["q"])

    def reserved_now(self, item_id: int) -> int:
        """Сколько единиц занято действующими арендами на сегодня."""
        return self.peak_reserved(item_id, today(), today())

    def busy_intervals(self, item_id: int, exclude_rental_id: Optional[int] = None) -> list[tuple]:
        """
        Интервалы занятости предмета действующими арендами.

        Учитывается собственная дата выдачи каждой позиции (в версии 2.0 внутри
        одной аренды позиции могут быть выданы в разные дни) и фактические
        возвраты: возвращённое количество освобождается с даты возврата.
        """
        sql = ("SELECT ri.date_start, ri.date_returned, ri.quantity, ri.returned_qty, "
               "ri.status, r.date_start rental_start, r.date_end, r.id rental_id, r.number "
               "FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id "
               "WHERE ri.item_id = ? AND r.status = 'active'")
        params: list = [item_id]
        if exclude_rental_id:
            sql += " AND r.id <> ?"
            params.append(exclude_rental_id)
        out = []
        for row in get_db().query(sql, params):
            start = parse_date(row["date_start"]) or parse_date(row["rental_start"])
            planned_end = parse_date(row["date_end"]) or FAR_FUTURE
            remaining = max(0, row["quantity"] - row["returned_qty"])
            if remaining > 0:
                out.append((start, planned_end, remaining, row["rental_id"], row["number"]))
            returned = row["returned_qty"]
            if returned > 0:
                # возвращённая часть была занята только до дня возврата
                end = parse_date(row["date_returned"]) or planned_end
                out.append((start, min(end, planned_end), returned, row["rental_id"], row["number"]))
        return out

    def peak_reserved(self, item_id: int, date_start: date, date_end: Optional[date],
                      exclude_rental_id: Optional[int] = None) -> int:
        """
        Максимальное одновременное количество, занятое арендами внутри периода.

        Считается методом заметающей прямой по дням: если две аренды не пересекаются
        между собой, они не складываются — блокируется только реальный пик.
        """
        from ..utils import add_days

        end = date_end or FAR_FUTURE
        deltas: dict[date, int] = {}
        for s, e, qty, _rid, _num in self.busy_intervals(item_id, exclude_rental_id):
            s2, e2 = max(s, date_start), min(e, end)
            if s2 > e2:
                continue
            deltas[s2] = deltas.get(s2, 0) + qty            # занято с этого дня
            free_day = add_days(e2, 1) if e2 < FAR_FUTURE else FAR_FUTURE
            deltas[free_day] = deltas.get(free_day, 0) - qty  # освободилось на следующий день
        current = peak = 0
        for day in sorted(deltas):
            current += deltas[day]
            peak = max(peak, current)
        return peak

    def available_for_period(self, item_id: int, date_start: date, date_end: Optional[date],
                             exclude_rental_id: Optional[int] = None) -> int:
        item = get_db().query_one(
            "SELECT quantity_total, status FROM inventory_items WHERE id=?", (item_id,))
        if not item:
            raise NotFoundError("Предмет не найден.")
        if item["status"] == "archived":
            return 0
        busy = self.peak_reserved(item_id, date_start, date_end, exclude_rental_id)
        return max(0, item["quantity_total"] - busy - self.in_repair(item_id))

    def conflicting_rentals(self, item_id: int, date_start: date, date_end: Optional[date],
                            exclude_rental_id: Optional[int] = None) -> list[dict]:
        """Аренды, из-за которых предмет занят в этот период (для понятного сообщения)."""
        from ..utils import overlaps
        out = []
        for s, e, qty, rid, num in self.busy_intervals(item_id, exclude_rental_id):
            if overlaps(s, e, date_start, date_end):
                out.append({"rental_id": rid, "number": num, "quantity": qty,
                            "date_start": s, "date_end": None if e == FAR_FUTURE else e})
        return out

    # -------------------------------------------------------------- история
    def history(self, item_id: int) -> list[dict]:
        """Полная история предмета: аренды, ремонты, изменения карточки."""
        db = get_db()
        rows = db.query(
            "SELECT r.number, r.date_start, r.date_end, r.status, ri.quantity, ri.price, "
            "ri.amount, ri.note, c.full_name client, p.name project, pr.name production, r.id rental_id "
            "FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id "
            "LEFT JOIN clients c ON c.id = r.client_id "
            "LEFT JOIN projects p ON p.id = r.project_id "
            "LEFT JOIN productions pr ON pr.id = r.production_id "
            "WHERE ri.item_id = ? ORDER BY r.date_start DESC", (item_id,))
        return [dict(r) for r in rows]


inventory_service = InventoryService()
