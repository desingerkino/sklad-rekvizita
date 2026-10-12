# -*- coding: utf-8 -*-
"""
Инвентаризация — периодическая физическая сверка склада.

Три величины, как на настоящем складе:
  ЧИСЛИТСЯ    — сколько единиц по учёту должно лежать на полке прямо сейчас
                (всего минус то, что у клиентов, минус то, что в ремонте);
  НАЙДЕНО     — сколько единиц сотрудник физически нашёл и отсканировал;
  РАСХОЖДЕНИЕ — разница.

Важная особенность склада реквизита: инвентарный номер (а значит и штрихкод)
принадлежит карточке, а не отдельному стулу. Десять одинаковых стульев — одна
карточка с количеством 10 и один штрихкод. Поэтому «повторное сканирование»
здесь означает не «второй раз тот же предмет», а «отсканировано больше, чем
числится»: до нужного количества сканирования засчитываются, сверх него
программа предупреждает и без подтверждения ничего не пишет.

Инвентаризация никогда не правит остатки склада сама. Она фиксирует факт;
что делать с недостачей — решает человек.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..db.database import get_db
from ..errors import AppError, NotFoundError, ValidationError
from ..utils import db_dt, today
from .audit_service import audit_service
from .auth_service import auth_service
from .barcode_service import barcode_service

log = logging.getLogger(__name__)

STATUS_OPEN, STATUS_DONE, STATUS_CANCELLED = "open", "done", "cancelled"
STATUS_LABELS = {STATUS_OPEN: "Идёт", STATUS_DONE: "Завершена", STATUS_CANCELLED: "Отменена"}

# результаты сканирования
SCAN_COUNTED = "counted"            # засчитано
SCAN_OVER = "over"                  # найдено больше, чем числится
SCAN_UNEXPECTED = "unexpected"      # предмет не входил в снимок инвентаризации
SCAN_NOT_FOUND = "not_found"        # такого штрихкода нет в базе


class InventoryAuditService:
    # --------------------------------------------------------------- список
    def list(self, limit: int = 200) -> list[dict]:
        rows = get_db().query(
            "SELECT a.*, u.full_name AS user_name FROM inventory_audits a "
            "LEFT JOIN users u ON u.id = a.user_id "
            "ORDER BY a.started_at DESC, a.id DESC LIMIT ?", (limit,))
        out = []
        for row in rows:
            data = dict(row)
            data["status_label"] = STATUS_LABELS.get(data["status"], data["status"])
            out.append(data)
        return out

    def active(self) -> Optional[dict]:
        """Незакрытая инвентаризация. Одновременно может идти только одна."""
        row = get_db().query_one(
            "SELECT * FROM inventory_audits WHERE status=? ORDER BY id DESC LIMIT 1",
            (STATUS_OPEN,))
        return dict(row) if row else None

    def get(self, audit_id: int) -> dict:
        row = get_db().query_one(
            "SELECT a.*, u.full_name AS user_name FROM inventory_audits a "
            "LEFT JOIN users u ON u.id = a.user_id WHERE a.id=?", (audit_id,))
        if not row:
            raise NotFoundError("Инвентаризация не найдена.")
        data = dict(row)
        data["status_label"] = STATUS_LABELS.get(data["status"], data["status"])
        return data

    # -------------------------------------------------------------- создание
    def create(self, title: str = "", responsible: str = "", comment: str = "") -> int:
        """
        Открывает инвентаризацию и делает снимок учёта.

        Снимок обязателен: если во время сверки кто-то оформит аренду, числа
        «числится» не поедут задним числом и отчёт останется правдой на момент
        начала пересчёта.
        """
        user = auth_service.require_admin(
            "Начинать инвентаризацию может только администратор.")
        if self.active():
            raise ValidationError(
                "Одна инвентаризация уже идёт. Завершите или отмените её, "
                "прежде чем начинать новую.")

        db = get_db()
        from .inventory_service import inventory_service

        items = db.query(
            "SELECT i.id, i.inv_number, i.name, i.quantity_total, "
            "       ifnull(c.name,'') AS category "
            "FROM inventory_items i LEFT JOIN categories c ON c.id=i.category_id "
            "WHERE i.is_archived=0 ORDER BY i.inv_number")

        snapshot = []
        for item in items:
            rented = inventory_service.reserved_now(item["id"])
            repair = inventory_service.in_repair(item["id"])
            expected = max(0, int(item["quantity_total"]) - rented - repair)
            snapshot.append((item["id"], item["inv_number"], item["name"], item["category"],
                             expected, rented, repair))

        with db.transaction() as conn:
            number = self._next_number(conn)
            cursor = conn.execute(
                "INSERT INTO inventory_audits(number, title, status, started_at, user_id, "
                "responsible, comment) VALUES (?,?,?,?,?,?,?)",
                (number, (title or "").strip(), STATUS_OPEN, db_dt(), user.id,
                 (responsible or user.full_name).strip(), (comment or "").strip()))
            audit_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO inventory_audit_lines(audit_id, item_id, inv_number, name_snap, "
                "category_snap, expected_qty, rented_qty, repair_qty, found_qty, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,0,?)",
                [(audit_id, item_id, inv, name, category, expected, rented, repair, db_dt())
                 for item_id, inv, name, category, expected, rented, repair in snapshot])

        audit_service.log("Начата инвентаризация", entity="audit", entity_id=audit_id,
                          details=f"{number}, позиций в снимке: {len(snapshot)}")
        log.info("Инвентаризация %s открыта, позиций %s", number, len(snapshot))
        return audit_id

    @staticmethod
    def _next_number(conn) -> str:
        year = today().year
        conn.execute("INSERT INTO counters(name, year, value) VALUES ('audit',?,0) "
                     "ON CONFLICT(name, year) DO NOTHING", (year,))
        conn.execute("UPDATE counters SET value = value + 1 WHERE name='audit' AND year=?",
                     (year,))
        value = conn.execute("SELECT value FROM counters WHERE name='audit' AND year=?",
                             (year,)).fetchone()[0]
        return f"ИНВ-{year}-{value:04d}"

    # ---------------------------------------------------------- сканирование
    def scan(self, audit_id: int, code: str, quantity: int = 1,
             allow_over: bool = False) -> dict:
        """
        Отмечает предмет найденным по отсканированному штрихкоду.

        Возвращает словарь с полями status, message и данными строки —
        интерфейс по нему решает, что показать оператору. Ничего не меняется,
        если сканирование не засчитано.
        """
        auth_service.require_login()
        audit = self.get(audit_id)
        if audit["status"] != STATUS_OPEN:
            raise ValidationError("Эта инвентаризация уже закрыта.")

        item = barcode_service.find_item(code)
        if not item:
            return {"status": SCAN_NOT_FOUND, "message":
                    f"Штрихкод «{barcode_service.normalize(code)}» не найден в базе. "
                    "Проверьте этикетку или найдите предмет вручную."}

        db = get_db()
        line = db.query_one(
            "SELECT * FROM inventory_audit_lines WHERE audit_id=? AND item_id=?",
            (audit_id, item["id"]))

        if line is None:
            # предмет появился после начала сверки или был архивным
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO inventory_audit_lines(audit_id, item_id, inv_number, "
                    "name_snap, category_snap, expected_qty, rented_qty, repair_qty, "
                    "found_qty, unexpected, updated_at) VALUES (?,?,?,?,'',0,0,0,?,1,?)",
                    (audit_id, item["id"], item["inv_number"], item["name"],
                     quantity, db_dt()))
            return {"status": SCAN_UNEXPECTED, "item_id": item["id"],
                    "inv_number": item["inv_number"], "name": item["name"],
                    "found": quantity, "expected": 0,
                    "message": f"«{item['name']}» найден, но в инвентаризации не числился. "
                               "Записан как излишек."}

        expected = int(line["expected_qty"])
        found = int(line["found_qty"])
        new_found = found + quantity

        if new_found > expected and not allow_over and not line["unexpected"]:
            return {"status": SCAN_OVER, "item_id": item["id"],
                    "inv_number": item["inv_number"], "name": item["name"],
                    "found": found, "expected": expected,
                    "rented": int(line["rented_qty"]), "repair": int(line["repair_qty"]),
                    "message": self._over_message(line, found, expected)}

        with db.transaction() as conn:
            conn.execute(
                "UPDATE inventory_audit_lines SET found_qty=?, updated_at=? WHERE id=?",
                (new_found, db_dt(), line["id"]))
        return {"status": SCAN_COUNTED, "item_id": item["id"],
                "inv_number": item["inv_number"], "name": item["name"],
                "found": new_found, "expected": expected,
                "message": f"«{item['name']}» — учтено {new_found} из {expected}."
                           if expected else f"«{item['name']}» — учтено {new_found}."}

    @staticmethod
    def _over_message(line, found: int, expected: int) -> str:
        name = line["name_snap"]
        if expected == 0:
            reason = []
            if line["rented_qty"]:
                reason.append(f"{line['rented_qty']} шт. у клиентов")
            if line["repair_qty"]:
                reason.append(f"{line['repair_qty']} шт. в ремонте")
            tail = (", ".join(reason) + " — на складе быть не должно"
                    if reason else "по учёту на складе ничего не числится")
            return f"«{name}»: {tail}. Подтвердите, если предмет действительно на месте."
        if found >= expected:
            return (f"«{name}» уже учтён полностью: {found} из {expected} шт. "
                    "Если экземпляров действительно больше, подтвердите — "
                    "разница попадёт в отчёт как излишек.")
        return f"«{name}»: превышение количества."

    def set_found(self, audit_id: int, line_id: int, quantity: int,
                  comment: Optional[str] = None) -> None:
        """Ручная правка: этикетка повреждена или предмет посчитали глазами."""
        auth_service.require_login()
        audit = self.get(audit_id)
        if audit["status"] != STATUS_OPEN:
            raise ValidationError("Эта инвентаризация уже закрыта.")
        if quantity < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        db = get_db()
        line = db.query_one("SELECT id FROM inventory_audit_lines WHERE id=? AND audit_id=?",
                            (line_id, audit_id))
        if not line:
            raise NotFoundError("Строка инвентаризации не найдена.")
        with db.transaction() as conn:
            if comment is None:
                conn.execute("UPDATE inventory_audit_lines SET found_qty=?, updated_at=? "
                             "WHERE id=?", (quantity, db_dt(), line_id))
            else:
                conn.execute("UPDATE inventory_audit_lines SET found_qty=?, comment=?, "
                             "updated_at=? WHERE id=?",
                             (quantity, comment.strip(), db_dt(), line_id))

    def add_manually(self, audit_id: int, item_id: int, quantity: int = 1) -> dict:
        """Добавление предмета в сверку без сканера — поиском по каталогу."""
        from .inventory_service import inventory_service

        item = inventory_service.get(item_id)
        if not item:
            raise NotFoundError("Предмет не найден.")
        return self.scan(audit_id, item["inv_number"], quantity, allow_over=True)

    # -------------------------------------------------------------- итоги
    def lines(self, audit_id: int, only: str = "all") -> list[dict]:
        """
        Строки сверки. only: all | missing (не найдено) | diff (расхождения) |
        extra (найдено, но не числилось) | ok.
        """
        rows = get_db().query(
            "SELECT * FROM inventory_audit_lines WHERE audit_id=? "
            "ORDER BY unexpected DESC, inv_number", (audit_id,))
        out = []
        for row in rows:
            line = dict(row)
            line["diff"] = int(line["found_qty"]) - int(line["expected_qty"])
            line["state"] = self._line_state(line)
            if only == "all" or line["state"] == only or (
                    only == "diff" and line["state"] in ("missing", "partial", "extra")):
                out.append(line)
        return out

    @staticmethod
    def _line_state(line: dict) -> str:
        if line["unexpected"]:
            return "extra"
        if line["diff"] > 0:
            return "extra"
        if line["found_qty"] == 0 and line["expected_qty"] > 0:
            return "missing"
        if line["diff"] < 0:
            return "partial"
        return "ok"

    def summary(self, audit_id: int) -> dict:
        """Числа для отчёта: числится, найдено, недостача, излишки, процент."""
        lines = self.lines(audit_id)
        expected_units = sum(l["expected_qty"] for l in lines)
        found_units = sum(l["found_qty"] for l in lines)
        missing_units = sum(max(0, l["expected_qty"] - l["found_qty"]) for l in lines)
        extra_units = sum(max(0, l["found_qty"] - l["expected_qty"]) for l in lines)
        states = [l["state"] for l in lines]
        matched = expected_units - missing_units
        return {
            "positions": len([l for l in lines if not l["unexpected"]]),
            "expected_units": expected_units,
            "found_units": found_units,
            "missing_units": missing_units,
            "extra_units": extra_units,
            "positions_ok": states.count("ok"),
            "positions_missing": states.count("missing"),
            "positions_partial": states.count("partial"),
            "positions_extra": states.count("extra"),
            "match_percent": round(matched / expected_units * 100, 1) if expected_units else 100.0,
            "rented_units": sum(l["rented_qty"] for l in lines),
            "repair_units": sum(l["repair_qty"] for l in lines),
        }

    # ------------------------------------------------------------ завершение
    def finish(self, audit_id: int, comment: str = "") -> dict:
        """
        Закрывает инвентаризацию. Остатки склада НЕ правятся: программа
        фиксирует факт расхождения, решение принимает человек.
        """
        auth_service.require_admin("Завершать инвентаризацию может только администратор.")
        audit = self.get(audit_id)
        if audit["status"] != STATUS_OPEN:
            raise ValidationError("Эта инвентаризация уже закрыта.")
        summary = self.summary(audit_id)
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE inventory_audits SET status=?, finished_at=?, comment=? WHERE id=?",
                (STATUS_DONE, db_dt(),
                 (comment or audit["comment"] or "").strip(), audit_id))
        audit_service.log(
            "Завершена инвентаризация", entity="audit", entity_id=audit_id,
            details=(f"{audit['number']}: числится {summary['expected_units']}, "
                     f"найдено {summary['found_units']}, недостача {summary['missing_units']}, "
                     f"излишки {summary['extra_units']}, совпадение {summary['match_percent']}%"))
        log.info("Инвентаризация %s завершена: %s", audit["number"], summary)
        return summary

    def cancel(self, audit_id: int) -> None:
        auth_service.require_admin("Отменять инвентаризацию может только администратор.")
        audit = self.get(audit_id)
        if audit["status"] != STATUS_OPEN:
            raise ValidationError("Эта инвентаризация уже закрыта.")
        with get_db().transaction() as conn:
            conn.execute("UPDATE inventory_audits SET status=?, finished_at=? WHERE id=?",
                         (STATUS_CANCELLED, db_dt(), audit_id))
        audit_service.log("Отменена инвентаризация", entity="audit", entity_id=audit_id,
                          details=audit["number"])

    # ------------------------------------------------------------------ PDF
    def report_pdf(self, audit_id: int, target: Path) -> Path:
        """Отчёт по инвентаризации: сводка, недостача, излишки, полный список."""
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                        TableStyle)

        from .pdf_service import FONT, FONT_BOLD, _register_fonts, _styles
        from .settings_service import settings_service
        from ..utils import fmt_dt

        _register_fonts()
        styles = _styles()
        audit = self.get(audit_id)
        summary = self.summary(audit_id)
        lines = self.lines(audit_id)

        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(target), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
            topMargin=15 * mm, bottomMargin=15 * mm,
            title=f"Инвентаризация {audit['number']}")

        warehouse = settings_service.warehouse()
        story = [
            Paragraph(f"ИНВЕНТАРИЗАЦИЯ {audit['number']}", styles["title"]),
            Paragraph(warehouse.get("name", "") or "Склад реквизита", styles["sub"]),
        ]

        head = [
            ("Начата:", fmt_dt(audit["started_at"])),
            ("Завершена:", fmt_dt(audit["finished_at"]) if audit["finished_at"] else "не завершена"),
            ("Состояние:", audit["status_label"]),
            ("Ответственный:", audit["responsible"] or audit.get("user_name") or "—"),
        ]
        if audit["title"]:
            head.insert(0, ("Название:", audit["title"]))
        if audit["comment"]:
            head.append(("Комментарий:", audit["comment"]))
        info_table = Table([[Paragraph(k, styles["cell"]), Paragraph(str(v), styles["cell"])]
                            for k, v in head], colWidths=[38 * mm, None])
        info_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story += [info_table, Spacer(1, 6 * mm)]

        story.append(Paragraph("Итоги", styles["h"]))
        totals = [
            ["Числится на складе", f"{summary['expected_units']} шт."],
            ["Найдено", f"{summary['found_units']} шт."],
            ["Не найдено (недостача)", f"{summary['missing_units']} шт."],
            ["Найдено сверх учёта (излишки)", f"{summary['extra_units']} шт."],
            ["Позиций совпало полностью", str(summary["positions_ok"])],
            ["Позиций с расхождением", str(summary["positions_missing"]
                                           + summary["positions_partial"])],
            ["Процент соответствия", f"{summary['match_percent']} %"],
            ["Справочно: у клиентов", f"{summary['rented_units']} шт."],
            ["Справочно: в ремонте", f"{summary['repair_units']} шт."],
        ]
        totals_table = Table([[Paragraph(a, styles["cell"]), Paragraph(b, styles["cell"])]
                              for a, b in totals], colWidths=[80 * mm, 40 * mm])
        totals_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story += [totals_table, Spacer(1, 6 * mm)]

        def block(title: str, rows: list[dict], empty: str) -> None:
            story.append(Paragraph(title, styles["h"]))
            if not rows:
                story.append(Paragraph(empty, styles["cell"]))
                story.append(Spacer(1, 4 * mm))
                return
            data = [[Paragraph("Инв. №", styles["cell"]), Paragraph("Наименование", styles["cell"]),
                     Paragraph("Числится", styles["cell"]), Paragraph("Найдено", styles["cell"]),
                     Paragraph("Расхождение", styles["cell"])]]
            for line in rows:
                data.append([
                    Paragraph(line["inv_number"], styles["cell"]),
                    Paragraph(line["name_snap"], styles["cell"]),
                    Paragraph(str(line["expected_qty"]), styles["cell"]),
                    Paragraph(str(line["found_qty"]), styles["cell"]),
                    Paragraph(f"{line['diff']:+d}" if line["diff"] else "0", styles["cell"]),
                ])
            table = Table(data, colWidths=[24 * mm, None, 20 * mm, 20 * mm, 24 * mm],
                          repeatRows=1)
            table.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F1F5F9")),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.extend([table, Spacer(1, 5 * mm)])

        block("НЕ НАЙДЕНО",
              [l for l in lines if l["state"] in ("missing", "partial")],
              "Недостачи нет — всё, что числится, найдено.")
        block("НАЙДЕНО, НО НЕ ЧИСЛИЛОСЬ",
              [l for l in lines if l["state"] == "extra"],
              "Излишков нет.")
        block("ПОЛНЫЙ СПИСОК", lines, "В инвентаризации нет позиций.")

        doc.build(story)
        audit_service.log("Экспорт отчёта инвентаризации", entity="audit", entity_id=audit_id,
                          details=f"{audit['number']} → PDF")
        return target


inventory_audit_service = InventoryAuditService()
