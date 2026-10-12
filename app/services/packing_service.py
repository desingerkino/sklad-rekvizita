# -*- coding: utf-8 -*-
"""
Комплектация по машинам: что и в каких коробках уезжает на съёмку.

Это отдельный слой поверх склада, а не движение остатков. Аренда отвечает на
вопрос «кому выдано и до какого числа», комплектация — «в какой машине и в
какой коробке это лежит физически». Поэтому упаковка ничего не резервирует и
не списывает: положить предмет в коробку можно и до оформления аренды, и
после, а сам предмет от этого не перестаёт числиться на складе.

Названия и инвентарные номера сохраняются снимком на момент упаковки — если
предмет потом переименуют, уже напечатанный упаковочный лист останется верным.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..db.database import get_db
from ..errors import AppError, NotFoundError, ValidationError
from ..utils import db_date, db_dt, today
from .audit_service import audit_service
from .auth_service import auth_service
from .barcode_service import barcode_service

log = logging.getLogger(__name__)

STATUS_OPEN, STATUS_SHIPPED, STATUS_CLOSED, STATUS_CANCELLED = \
    "open", "shipped", "closed", "cancelled"
STATUS_LABELS = {STATUS_OPEN: "Собирается", STATUS_SHIPPED: "Уехал",
                 STATUS_CLOSED: "Вернулся", STATUS_CANCELLED: "Отменён"}


class PackingService:
    # ---------------------------------------------------------------- машины
    def vehicles(self, only_active: bool = True) -> list[dict]:
        sql = "SELECT * FROM vehicles"
        if only_active:
            sql += " WHERE is_active=1"
        sql += " ORDER BY name"
        return [dict(r) for r in get_db().query(sql)]

    def create_vehicle(self, name: str, driver: str = "", note: str = "") -> int:
        auth_service.require_login()
        name = (name or "").strip()
        if not name:
            raise ValidationError("Укажите название машины.")
        db = get_db()
        if db.query_one("SELECT 1 FROM vehicles WHERE name=?", (name,)):
            raise ValidationError(f"Машина «{name}» уже есть в списке.")
        with db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO vehicles(name, driver, note) VALUES (?,?,?)",
                (name, (driver or "").strip(), (note or "").strip()))
            vehicle_id = int(cursor.lastrowid)
        audit_service.log("Добавлена машина", entity="vehicle", entity_id=vehicle_id,
                          details=name)
        return vehicle_id

    def update_vehicle(self, vehicle_id: int, name: str, driver: str = "",
                       note: str = "", is_active: bool = True) -> None:
        auth_service.require_login()
        name = (name or "").strip()
        if not name:
            raise ValidationError("Укажите название машины.")
        db = get_db()
        clash = db.query_one("SELECT id FROM vehicles WHERE name=? AND id<>?",
                             (name, vehicle_id))
        if clash:
            raise ValidationError(f"Машина «{name}» уже есть в списке.")
        with db.transaction() as conn:
            conn.execute("UPDATE vehicles SET name=?, driver=?, note=?, is_active=? WHERE id=?",
                         (name, (driver or "").strip(), (note or "").strip(),
                          1 if is_active else 0, vehicle_id))
        audit_service.log("Изменена машина", entity="vehicle", entity_id=vehicle_id,
                          details=name)

    def delete_vehicle(self, vehicle_id: int) -> None:
        """
        Машину, по которой уже есть комплекты, не удаляем: упаковочные листы
        должны остаться читаемыми. Такая машина просто убирается из списка.
        """
        auth_service.require_admin("Удалять машины может только администратор.")
        db = get_db()
        used = db.query_one("SELECT COUNT(*) c FROM packing_sets WHERE vehicle_id=?",
                            (vehicle_id,))["c"]
        row = db.query_one("SELECT name FROM vehicles WHERE id=?", (vehicle_id,))
        if not row:
            raise NotFoundError("Машина не найдена.")
        with db.transaction() as conn:
            if used:
                conn.execute("UPDATE vehicles SET is_active=0 WHERE id=?", (vehicle_id,))
            else:
                conn.execute("DELETE FROM vehicles WHERE id=?", (vehicle_id,))
        audit_service.log("Машина убрана из списка" if used else "Удалена машина",
                          entity="vehicle", entity_id=vehicle_id, details=row["name"])

    # -------------------------------------------------------------- комплекты
    def sets(self, status: str = "", limit: int = 200) -> list[dict]:
        sql = ("SELECT s.*, v.name AS vehicle_name, v.driver AS vehicle_driver, "
               "       ifnull(p.name,'') AS project_name, ifnull(pr.name,'') AS production_name, "
               "       ifnull(r.number,'') AS rental_number, "
               "       (SELECT COUNT(*) FROM packing_boxes b WHERE b.set_id=s.id) AS boxes, "
               "       (SELECT ifnull(SUM(pi.quantity),0) FROM packing_items pi "
               "        JOIN packing_boxes b ON b.id=pi.box_id WHERE b.set_id=s.id) AS units "
               "FROM packing_sets s "
               "LEFT JOIN vehicles v ON v.id=s.vehicle_id "
               "LEFT JOIN projects p ON p.id=s.project_id "
               "LEFT JOIN productions pr ON pr.id=s.production_id "
               "LEFT JOIN rentals r ON r.id=s.rental_id ")
        params: list = []
        if status:
            sql += "WHERE s.status=? "
            params.append(status)
        sql += "ORDER BY s.date_ship DESC, s.id DESC LIMIT ?"
        params.append(limit)
        out = []
        for row in get_db().query(sql, params):
            data = dict(row)
            data["status_label"] = STATUS_LABELS.get(data["status"], data["status"])
            out.append(data)
        return out

    def get(self, set_id: int) -> dict:
        rows = self.sets(limit=100000)
        for row in rows:
            if row["id"] == set_id:
                return row
        raise NotFoundError("Комплект не найден.")

    def create_set(self, vehicle_id: Optional[int], date_ship=None, project_id=None,
                   production_id=None, rental_id=None, responsible: str = "",
                   comment: str = "") -> int:
        user = auth_service.require_login()
        db = get_db()
        if vehicle_id and not db.query_one("SELECT 1 FROM vehicles WHERE id=?", (vehicle_id,)):
            raise ValidationError("Машина не найдена.")
        with db.transaction() as conn:
            number = self._next_number(conn)
            cursor = conn.execute(
                "INSERT INTO packing_sets(number, vehicle_id, project_id, production_id, "
                "rental_id, date_ship, responsible, comment, status, user_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (number, vehicle_id, project_id, production_id, rental_id,
                 db_date(date_ship or today()), (responsible or user.full_name).strip(),
                 (comment or "").strip(), STATUS_OPEN, user.id))
            set_id = int(cursor.lastrowid)
        audit_service.log("Создан комплект", entity="packing", entity_id=set_id,
                          details=number)
        return set_id

    @staticmethod
    def _next_number(conn) -> str:
        year = today().year
        conn.execute("INSERT INTO counters(name, year, value) VALUES ('packing',?,0) "
                     "ON CONFLICT(name, year) DO NOTHING", (year,))
        conn.execute("UPDATE counters SET value = value + 1 WHERE name='packing' AND year=?",
                     (year,))
        value = conn.execute("SELECT value FROM counters WHERE name='packing' AND year=?",
                             (year,)).fetchone()[0]
        return f"КМП-{year}-{value:04d}"

    # Каждое поле — свой готовый запрос. Собирать SQL из строк, даже по белому
    # списку, здесь не нужно: полей мало, а текст запроса так остаётся
    # неизменяемым и его нельзя испортить чем-то, что придёт извне.
    _SET_UPDATES = {
        "vehicle_id": "UPDATE packing_sets SET vehicle_id=? WHERE id=?",
        "project_id": "UPDATE packing_sets SET project_id=? WHERE id=?",
        "production_id": "UPDATE packing_sets SET production_id=? WHERE id=?",
        "rental_id": "UPDATE packing_sets SET rental_id=? WHERE id=?",
        "responsible": "UPDATE packing_sets SET responsible=? WHERE id=?",
        "comment": "UPDATE packing_sets SET comment=? WHERE id=?",
        "date_ship": "UPDATE packing_sets SET date_ship=? WHERE id=?",
    }

    def update_set(self, set_id: int, **fields) -> None:
        auth_service.require_login()
        updates = {key: value for key, value in fields.items() if key in self._SET_UPDATES}
        if "date_ship" in updates:
            updates["date_ship"] = db_date(updates["date_ship"])
        if not updates:
            return
        with get_db().transaction() as conn:
            for key, value in updates.items():
                conn.execute(self._SET_UPDATES[key], (value, set_id))
        audit_service.log("Изменена комплектация", entity="packing", entity_id=set_id)

    def set_status(self, set_id: int, status: str) -> None:
        if status not in STATUS_LABELS:
            raise ValidationError("Неизвестное состояние комплекта.")
        auth_service.require_login()
        data = self.get(set_id)
        shipped = db_dt() if status == STATUS_SHIPPED else data.get("shipped_at")
        with get_db().transaction() as conn:
            conn.execute("UPDATE packing_sets SET status=?, shipped_at=? WHERE id=?",
                         (status, shipped, set_id))
        audit_service.log("Изменена комплектация", entity="packing", entity_id=set_id,
                          details=f"{data['number']}: {STATUS_LABELS[status]}")

    def delete_set(self, set_id: int) -> None:
        auth_service.require_admin("Удалять комплекты может только администратор.")
        data = self.get(set_id)
        with get_db().transaction() as conn:
            conn.execute("DELETE FROM packing_sets WHERE id=?", (set_id,))
        audit_service.log("Удалён комплект", entity="packing", entity_id=set_id,
                          details=data["number"])

    # ---------------------------------------------------------------- коробки
    def boxes(self, set_id: int) -> list[dict]:
        rows = get_db().query(
            "SELECT b.*, (SELECT ifnull(SUM(pi.quantity),0) FROM packing_items pi "
            "             WHERE pi.box_id=b.id) AS units "
            "FROM packing_boxes b WHERE b.set_id=? ORDER BY b.number", (set_id,))
        return [dict(r) for r in rows]

    def add_box(self, set_id: int, label: str = "", note: str = "") -> int:
        auth_service.require_login()
        db = get_db()
        self.get(set_id)
        with db.transaction() as conn:
            last = conn.execute("SELECT ifnull(MAX(number),0) FROM packing_boxes WHERE set_id=?",
                                (set_id,)).fetchone()[0]
            number = int(last) + 1
            cursor = conn.execute(
                "INSERT INTO packing_boxes(set_id, number, label, note) VALUES (?,?,?,?)",
                (set_id, number, (label or f"Коробка №{number}").strip(),
                 (note or "").strip()))
            return int(cursor.lastrowid)

    def update_box(self, box_id: int, label: str, note: str = "") -> None:
        auth_service.require_login()
        with get_db().transaction() as conn:
            conn.execute("UPDATE packing_boxes SET label=?, note=? WHERE id=?",
                         ((label or "").strip(), (note or "").strip(), box_id))

    def delete_box(self, box_id: int) -> None:
        """Удаление коробки уносит и её содержимое — предметы остаются на складе."""
        auth_service.require_login()
        db = get_db()
        row = db.query_one("SELECT set_id, label FROM packing_boxes WHERE id=?", (box_id,))
        if not row:
            raise NotFoundError("Коробка не найдена.")
        with db.transaction() as conn:
            conn.execute("DELETE FROM packing_boxes WHERE id=?", (box_id,))
        audit_service.log("Изменена комплектация", entity="packing",
                          entity_id=row["set_id"], details=f"удалена {row['label']}")

    # --------------------------------------------------------------- предметы
    def items(self, box_id: int) -> list[dict]:
        rows = get_db().query(
            "SELECT * FROM packing_items WHERE box_id=? ORDER BY id", (box_id,))
        return [dict(r) for r in rows]

    def contents(self, set_id: int) -> list[dict]:
        """Полное содержимое комплекта: коробки со списком предметов."""
        result = []
        for box in self.boxes(set_id):
            box["items"] = self.items(box["id"])
            result.append(box)
        return result

    def add_item(self, box_id: int, item_id: int, quantity: int = 1,
                 note: str = "") -> dict:
        """
        Кладёт предмет в коробку. Повторное добавление того же предмета
        увеличивает количество, а не плодит строки.
        """
        auth_service.require_login()
        if quantity < 1:
            raise ValidationError("Количество должно быть не меньше единицы.")
        from .inventory_service import inventory_service

        item = inventory_service.get(item_id)
        if not item:
            raise NotFoundError("Предмет не найден.")

        db = get_db()
        box = db.query_one("SELECT id, set_id, label FROM packing_boxes WHERE id=?", (box_id,))
        if not box:
            raise NotFoundError("Коробка не найдена.")

        existing = db.query_one(
            "SELECT id, quantity FROM packing_items WHERE box_id=? AND item_id=?",
            (box_id, item_id))
        with db.transaction() as conn:
            if existing:
                total = int(existing["quantity"]) + quantity
                conn.execute("UPDATE packing_items SET quantity=? WHERE id=?",
                             (total, existing["id"]))
            else:
                total = quantity
                conn.execute(
                    "INSERT INTO packing_items(box_id, item_id, inv_number, name_snap, "
                    "quantity, note) VALUES (?,?,?,?,?,?)",
                    (box_id, item_id, item["inv_number"], item["name"], quantity,
                     (note or "").strip()))
        return {"item_id": item_id, "name": item["name"], "quantity": total,
                "box": box["label"], "added": not existing}

    def scan_into_box(self, box_id: int, code: str, quantity: int = 1) -> dict:
        """Добавление в коробку сканером: находит предмет по штрихкоду."""
        item = barcode_service.find_item(code)
        if not item:
            return {"found": False,
                    "message": f"Штрихкод «{barcode_service.normalize(code)}» "
                               "не найден в базе."}
        result = self.add_item(box_id, item["id"], quantity)
        result["found"] = True
        result["message"] = (
            f"«{result['name']}» добавлен в {result['box']}."
            if result["added"]
            else f"«{result['name']}» уже в {result['box']} — теперь {result['quantity']} шт.")
        return result

    def set_item_quantity(self, packing_item_id: int, quantity: int) -> None:
        auth_service.require_login()
        if quantity < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        with get_db().transaction() as conn:
            if quantity == 0:
                conn.execute("DELETE FROM packing_items WHERE id=?", (packing_item_id,))
            else:
                conn.execute("UPDATE packing_items SET quantity=? WHERE id=?",
                             (quantity, packing_item_id))

    def remove_item(self, packing_item_id: int) -> None:
        self.set_item_quantity(packing_item_id, 0)

    # ----------------------------------------------------- где лежит предмет
    def where_is(self, item_id: int) -> list[dict]:
        """
        Где предмет упакован сейчас: «Машина №2 → Коробка №4».

        Смотрим только по комплектам, которые ещё собираются или уехали, —
        закрытые и отменённые уже неактуальны. Отдельной колонки у предмета
        нет намеренно: место упаковки выводится из самих коробок и не может
        разойтись с ними.
        """
        rows = get_db().query(
            "SELECT s.id AS set_id, s.number, s.status, ifnull(v.name,'без машины') AS vehicle, "
            "       b.label AS box, b.number AS box_number, pi.quantity "
            "FROM packing_items pi "
            "JOIN packing_boxes b ON b.id = pi.box_id "
            "JOIN packing_sets s ON s.id = b.set_id "
            "LEFT JOIN vehicles v ON v.id = s.vehicle_id "
            "WHERE pi.item_id=? AND s.status IN (?,?) "
            "ORDER BY s.date_ship DESC, b.number",
            (item_id, STATUS_OPEN, STATUS_SHIPPED))
        return [dict(r) for r in rows]

    def where_is_text(self, item_id: int) -> str:
        places = self.where_is(item_id)
        if not places:
            return ""
        return "; ".join(f"{p['vehicle']} → {p['box']} ({p['quantity']} шт.)"
                         for p in places)

    # --------------------------------------------------------- упаковочный лист
    def packing_list_pdf(self, set_id: int, target: Path) -> Path:
        """Упаковочный лист: что в какой коробке. Для водителя и группы."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                        TableStyle)

        from ..utils import fmt_date
        from .pdf_service import _register_fonts, _styles
        from .settings_service import settings_service

        _register_fonts()
        styles = _styles()
        data = self.get(set_id)
        boxes = self.contents(set_id)

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(target), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
            topMargin=15 * mm, bottomMargin=15 * mm,
            title=f"Упаковочный лист {data['number']}")

        warehouse = settings_service.warehouse()
        story = [
            Paragraph("УПАКОВОЧНЫЙ ЛИСТ", styles["title"]),
            Paragraph(f"{data['number']} · {warehouse.get('name', '') or 'Склад реквизита'}",
                      styles["sub"]),
        ]

        head = [("Машина:", data["vehicle_name"] or "не указана")]
        if data["vehicle_driver"]:
            head.append(("Водитель:", data["vehicle_driver"]))
        if data["responsible"]:
            head.append(("Ответственный:", data["responsible"]))
        head.append(("Дата выезда:", fmt_date(data["date_ship"]) if data["date_ship"] else "—"))
        if data["project_name"]:
            head.append(("Проект:", data["project_name"]))
        if data["production_name"]:
            head.append(("Продакшен:", data["production_name"]))
        if data["rental_number"]:
            head.append(("Аренда:", data["rental_number"]))
        head.append(("Коробок:", str(len(boxes))))
        head.append(("Всего предметов:", str(sum(
            item["quantity"] for box in boxes for item in box["items"]))))
        if data["comment"]:
            head.append(("Комментарий:", data["comment"]))

        info_table = Table([[Paragraph(k, styles["cell"]), Paragraph(str(v), styles["cell"])]
                            for k, v in head], colWidths=[38 * mm, None])
        info_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                        ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
        story.extend([info_table, Spacer(1, 6 * mm)])

        if not boxes:
            story.append(Paragraph("Коробок пока нет.", styles["cell"]))
        for box in boxes:
            caption = box["label"]
            if box["note"]:
                caption += f" — {box['note']}"
            story.append(Paragraph(caption, styles["h"]))
            if not box["items"]:
                story.extend([Paragraph("пусто", styles["cell"]), Spacer(1, 4 * mm)])
                continue
            rows = [[Paragraph("Инв. №", styles["cell"]),
                     Paragraph("Наименование", styles["cell"]),
                     Paragraph("Кол-во", styles["cell"]),
                     Paragraph("Примечание", styles["cell"])]]
            for item in box["items"]:
                rows.append([Paragraph(item["inv_number"], styles["cell"]),
                             Paragraph(item["name_snap"], styles["cell"]),
                             Paragraph(str(item["quantity"]), styles["cell"]),
                             Paragraph(item["note"], styles["cell"])])
            table = Table(rows, colWidths=[24 * mm, None, 18 * mm, 42 * mm], repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.extend([table, Spacer(1, 5 * mm)])

        story.append(Spacer(1, 8 * mm))
        signatures = Table([[Paragraph("Собрал: ______________________", styles["cell"]),
                             Paragraph("Принял: ______________________", styles["cell"])]],
                           colWidths=[None, None])
        story.append(signatures)

        doc.build(story)
        audit_service.log("Печать упаковочного листа", entity="packing", entity_id=set_id,
                          details=data["number"])
        log.info("Упаковочный лист %s -> %s", data["number"], target)
        return target


packing_service = PackingService()
