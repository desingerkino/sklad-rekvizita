# -*- coding: utf-8 -*-
"""
Финансовая логика аренды версии 2.0.

Главное правило: цена позиции — это стоимость аренды на срок до 14 календарных
дней. Один период стоит полную цену независимо от того, вернули предмет на
первый день или на четырнадцатый. Если позиция не возвращена к концу периода,
автоматически начисляется следующий период — тоже по полной цене.

Каждая позиция (RentalItem) живёт своим циклом: своя дата выдачи, свой номер
периода, своя дата пролонгации, свой возврат. Аренда — контейнер таких позиций,
внутри одной аренды спокойно уживаются группы с разными датами начала.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from ..db.database import get_db
from ..errors import AppError, NotFoundError, ValidationError
from ..utils import (add_days, db_date, db_dt, fmt_date, money_round, now, parse_date,
                     parse_dt, today)
from ..version import RENTAL_PERIOD_DAYS
from .audit_service import audit_service
from .auth_service import auth_service

log = logging.getLogger(__name__)

CHARGE_LABELS = {
    "initial": "Первоначальная аренда",
    "addition": "Добор реквизита",
    "prolongation": "Пролонгация",
}

STATUS_RENTED = "rented"
STATUS_RETURNED = "returned"


# ------------------------------------------------------------------ периоды
def period_bounds(date_start: date, period_number: int) -> tuple[date, date]:
    """Границы N-го периода: период 1 — дни 1..14 от даты выдачи, период 2 — 15..28 и т.д."""
    offset = (period_number - 1) * RENTAL_PERIOD_DAYS
    start = add_days(date_start, offset)
    end = add_days(start, RENTAL_PERIOD_DAYS - 1)
    return start, end


def period_for_date(date_start: date, on_date: date) -> int:
    """Номер периода, в который попадает указанная дата (не меньше 1)."""
    days = (on_date - date_start).days
    if days < 0:
        return 1
    return days // RENTAL_PERIOD_DAYS + 1


def describe_period(item: dict) -> str:
    start = parse_date(item.get("period_start"))
    end = parse_date(item.get("period_end"))
    if not start or not end:
        return "—"
    return f"{fmt_date(start)}–{fmt_date(end)}"


def item_status_label(item: dict, reference: Optional[date] = None) -> tuple[str, str]:
    """
    Состояние позиции аренды по единой системе статусов 2.3.1.
    Текст обязателен, цвет подбирает интерфейс по коду состояния.
    """
    from .statuses import FINISHED, PROLONGED, RENTED, LABELS

    reference = reference or today()
    if item["status"] == STATUS_RETURNED:
        return FINISHED, "Возвращено"
    if item.get("prolongations", 0) > 0:
        return PROLONGED, LABELS[PROLONGED]
    end = parse_date(item.get("period_end"))
    if end and (end - reference).days <= 2:
        return "soon", "Скоро пролонгация"
    return RENTED, LABELS[RENTED]


class BillingService:
    # ------------------------------------------------------------ начисления
    def add_charge(self, conn, rental_id: int, rental_item_id: Optional[int], charge_type: str,
                   period_number: int, quantity: int, price: float, amount: float,
                   charge_date: date, description: str) -> Optional[int]:
        """
        Записывает начисление. Уникальный ключ (позиция, тип, номер периода)
        гарантирует, что одна и та же пролонгация не начислится дважды.
        """
        cur = conn.execute(
            "INSERT OR IGNORE INTO rental_charges(rental_id, rental_item_id, charge_type, "
            "period_number, quantity, price, amount, charge_date, description, user_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rental_id, rental_item_id, charge_type, period_number, quantity, price, amount,
             db_date(charge_date), description, auth_service.current_id(), db_dt()))
        return int(cur.lastrowid) if cur.rowcount else None

    def charges(self, rental_id: int) -> list[dict]:
        rows = get_db().query(
            "SELECT c.*, ri.name_snap, ri.inv_number_snap FROM rental_charges c "
            "LEFT JOIN rental_items ri ON ri.id = c.rental_item_id "
            "WHERE c.rental_id=? ORDER BY c.charge_date, c.id", (rental_id,))
        out = []
        for row in rows:
            charge = dict(row)
            charge["type_label"] = CHARGE_LABELS.get(charge["charge_type"], charge["charge_type"])
            out.append(charge)
        return out

    def charges_grouped(self, rental_id: int) -> list[dict]:
        """История начислений так, как её читает человек: по дате и виду начисления."""
        groups: dict[tuple, dict] = {}
        for charge in self.charges(rental_id):
            key = (charge["charge_date"], charge["charge_type"], charge["period_number"])
            group = groups.setdefault(key, {
                "date": charge["charge_date"], "type": charge["charge_type"],
                "type_label": charge["type_label"], "period_number": charge["period_number"],
                "amount": 0.0, "positions": [], "quantity": 0})
            group["amount"] += charge["amount"]
            group["quantity"] += charge["quantity"]
            if charge["name_snap"]:
                group["positions"].append(f"{charge['name_snap']} × {charge['quantity']}")
        return sorted(groups.values(), key=lambda g: (g["date"], g["type"]))

    # -------------------------------------------------------------- позиции
    def init_item_period(self, conn, rental_item_id: int, date_start: datetime,
                         quantity: int, price: float, amount: float, rental_id: int,
                         charge_type: str = "initial", description: str = "") -> None:
        """Задаёт позиции её собственный первый 14-дневный период и делает начисление."""
        start_date = date_start.date()
        period_start, period_end = period_bounds(start_date, 1)
        conn.execute(
            "UPDATE rental_items SET date_start=?, status='rented', period_number=1, "
            "period_start=?, period_end=?, next_prolongation=?, prolongations=0, "
            "initial_amount=?, prolongation_amount=0, total_amount=?, added_at=?, added_by=? "
            "WHERE id=?",
            (db_dt(date_start), db_date(period_start), db_date(period_end),
             db_date(add_days(period_end, 1)), amount, amount, db_dt(date_start),
             auth_service.current_id(), rental_item_id))
        self.add_charge(conn, rental_id, rental_item_id, charge_type, 1, quantity, price, amount,
                        start_date, description or CHARGE_LABELS.get(charge_type, ""))

    def items(self, rental_id: int, reference: Optional[date] = None) -> list[dict]:
        """Позиции аренды с рассчитанным состоянием периода."""
        reference = reference or today()
        rows = get_db().query(
            "SELECT * FROM rental_items WHERE rental_id=? ORDER BY date_start, id", (rental_id,))
        out = []
        for row in rows:
            item = dict(row)
            item["remaining_qty"] = max(0, item["quantity"] - item["returned_qty"])
            item["date_start_ui"] = fmt_date(item["date_start"])
            item["period_label"] = describe_period(item)
            code, label = item_status_label(item, reference)
            item["state"] = code
            item["state_label"] = label
            item["returns"] = self.returns_of(item["id"])
            out.append(item)
        return out

    def returns_of(self, rental_item_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT * FROM rental_returns WHERE rental_item_id=? ORDER BY returned_at",
            (rental_item_id,))]

    # --------------------------------------------------- автопролонгация
    def run_prolongations(self, reference: Optional[date] = None) -> list[dict]:
        """
        Начисляет новые периоды по всем позициям, которые не вернули вовремя.

        Запускается при старте программы и при открытии аренды. Повторный запуск
        в тот же день ничего не дублирует: номер периода хранится у позиции,
        а уникальный ключ начисления не даёт записать одну пролонгацию дважды.
        Дата берётся из системных часов Windows.
        """
        reference = reference or today()
        db = get_db()
        rows = db.query(
            "SELECT ri.*, r.status rental_status FROM rental_items ri "
            "JOIN rentals r ON r.id = ri.rental_id "
            "WHERE ri.status='rented' AND r.status='active'")
        created: list[dict] = []
        for row in rows:
            start = parse_date(row["date_start"])
            if not start:
                continue
            remaining = max(0, row["quantity"] - row["returned_qty"])
            if remaining <= 0:
                continue
            target_period = period_for_date(start, reference)
            current_period = row["period_number"] or 1
            if target_period <= current_period:
                continue
            with db.transaction() as conn:
                total_added = 0.0
                for number in range(current_period + 1, target_period + 1):
                    period_start, period_end = period_bounds(start, number)
                    amount = money_round(remaining * row["price"])
                    charge_id = self.add_charge(
                        conn, row["rental_id"], row["id"], "prolongation", number, remaining,
                        row["price"], amount, period_start,
                        f"Период №{number}: {fmt_date(period_start)}–{fmt_date(period_end)}")
                    if charge_id is None:
                        continue          # уже начислялось ранее
                    total_added += amount
                    created.append({
                        "rental_id": row["rental_id"], "rental_item_id": row["id"],
                        "name": row["name_snap"], "period_number": number, "amount": amount,
                        "quantity": remaining, "period_start": period_start, "period_end": period_end})
                period_start, period_end = period_bounds(start, target_period)
                conn.execute(
                    "UPDATE rental_items SET period_number=?, period_start=?, period_end=?, "
                    "next_prolongation=?, prolongations=?, prolongation_amount=prolongation_amount+?, "
                    "total_amount=total_amount+? WHERE id=?",
                    (target_period, db_date(period_start), db_date(period_end),
                     db_date(add_days(period_end, 1)), target_period - 1, total_added,
                     total_added, row["id"]))
            for entry in created[-max(1, target_period - current_period):]:
                if entry["rental_item_id"] != row["id"]:
                    continue
                audit_service.log(
                    "Автоматическая пролонгация", entity="rental_item", entity_id=row["id"],
                    details=f"{row['name_snap']} × {entry['quantity']}, период №{entry['period_number']} "
                            f"({fmt_date(entry['period_start'])}–{fmt_date(entry['period_end'])})",
                    amount=entry["amount"], user_name="Система", user_id=None)
        for rental_id in {entry["rental_id"] for entry in created}:
            self.recalc_rental(rental_id)
        if created:
            log.info("Начислено пролонгаций: %s", len(created))
        return created

    # ------------------------------------------------------------- возврат
    def return_item(self, rental_item_id: int, quantity: Optional[int] = None,
                    comment: str = "") -> dict:
        """
        Принимает позицию обратно (полностью или частично).

        Возврат фиксирует дату и время, останавливает будущие пролонгации по
        возвращённому количеству и не уменьшает уже начисленные периоды.
        """
        auth_service.require_login()
        db = get_db()
        row = db.query_one("SELECT * FROM rental_items WHERE id=?", (rental_item_id,))
        if not row:
            raise NotFoundError("Позиция аренды не найдена.")
        remaining = row["quantity"] - row["returned_qty"]
        if remaining <= 0:
            raise ValidationError("Эта позиция уже полностью возвращена.")
        quantity = remaining if quantity is None else int(quantity)
        if quantity <= 0:
            raise ValidationError("Количество для возврата должно быть больше нуля.")
        if quantity > remaining:
            raise ValidationError(
                f"Нельзя принять {quantity} шт.: в аренде осталось {remaining} шт.")

        moment = now()
        fully = (row["returned_qty"] + quantity) >= row["quantity"]
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO rental_returns(rental_id, rental_item_id, quantity, returned_at, "
                "user_id, comment) VALUES (?,?,?,?,?,?)",
                (row["rental_id"], rental_item_id, quantity, db_dt(moment),
                 auth_service.current_id(), comment))
            conn.execute(
                "UPDATE rental_items SET returned_qty=returned_qty+?, "
                "status=CASE WHEN ? THEN 'returned' ELSE status END, "
                "date_returned=CASE WHEN ? THEN ? ELSE date_returned END WHERE id=?",
                (quantity, 1 if fully else 0, 1 if fully else 0, db_dt(moment), rental_item_id))
        audit_service.log(
            "Возврат позиции", entity="rental_item", entity_id=rental_item_id,
            details=f"{row['name_snap']} × {quantity}"
                    f"{' — позиция закрыта' if fully else ' (частичный возврат)'}"
                    f"{'. ' + comment if comment else ''}")
        self.recalc_rental(row["rental_id"])
        self._close_rental_if_all_returned(row["rental_id"])
        return {"rental_item_id": rental_item_id, "quantity": quantity, "fully": fully,
                "returned_at": moment}

    def _close_rental_if_all_returned(self, rental_id: int) -> None:
        db = get_db()
        left = db.query_one(
            "SELECT COUNT(*) c FROM rental_items WHERE rental_id=? AND status='rented'",
            (rental_id,))["c"]
        rental = db.query_one("SELECT status FROM rentals WHERE id=?", (rental_id,))
        if left == 0 and rental and rental["status"] == "active":
            with db.transaction() as conn:
                conn.execute(
                    "UPDATE rentals SET status='closed', closed_at=?, updated_at=datetime('now','localtime') "
                    "WHERE id=?", (db_dt(), rental_id))
            audit_service.log("Аренда закрыта", entity="rental", entity_id=rental_id,
                              details="весь реквизит возвращён")

    # ----------------------------------------------------------- пересчёт
    def recalc_rental(self, rental_id: int) -> dict:
        """
        Итоги аренды = сумма всех начислений (первоначальных, доборов и пролонгаций)
        минус скидка. Скидка применяется к общей начисленной сумме.
        """
        db = get_db()
        rental = db.query_one("SELECT * FROM rentals WHERE id=?", (rental_id,))
        if not rental:
            raise NotFoundError("Аренда не найдена.")
        totals = db.query_one(
            "SELECT ifnull(SUM(amount),0) total, "
            "ifnull(SUM(CASE WHEN charge_type='prolongation' THEN amount ELSE 0 END),0) prolong "
            "FROM rental_charges WHERE rental_id=?", (rental_id,))
        valuation = db.query_one(
            "SELECT ifnull(SUM((quantity - returned_qty) * valuation),0) v FROM rental_items "
            "WHERE rental_id=?", (rental_id,))["v"]
        before = money_round(totals["total"])
        discount_amount = money_round(before * (rental["discount_percent"] or 0) / 100.0)
        final = money_round(before - discount_amount)
        with db.transaction() as conn:
            conn.execute(
                "UPDATE rentals SET total_before_discount=?, discount_amount=?, total_final=?, "
                "total_valuation=?, updated_at=datetime('now','localtime') WHERE id=?",
                (before, discount_amount, final, money_round(valuation), rental_id))
        return {"total_before_discount": before, "discount_amount": discount_amount,
                "total_final": final, "prolongation_total": money_round(totals["prolong"])}

    def rental_summary(self, rental_id: int) -> dict:
        """Сводка для карточки действующей аренды."""
        db = get_db()
        items = self.items(rental_id)
        charges = db.query_one(
            "SELECT ifnull(SUM(amount),0) total, "
            "ifnull(SUM(CASE WHEN charge_type='initial' THEN amount ELSE 0 END),0) initial, "
            "ifnull(SUM(CASE WHEN charge_type='addition' THEN amount ELSE 0 END),0) addition, "
            "ifnull(SUM(CASE WHEN charge_type='prolongation' THEN amount ELSE 0 END),0) prolong "
            "FROM rental_charges WHERE rental_id=?", (rental_id,))
        rented = [i for i in items if i["status"] == "rented"]
        returned = [i for i in items if i["status"] == "returned"]
        return {
            "positions": len(items),
            "positions_rented": len(rented),
            "positions_returned": len(returned),
            "units_rented": sum(i["remaining_qty"] for i in rented),
            "units_returned": sum(i["returned_qty"] for i in items),
            "initial_total": money_round(charges["initial"]),
            "addition_total": money_round(charges["addition"]),
            "prolongation_total": money_round(charges["prolong"]),
            "accrued_total": money_round(charges["total"]),
            "prolonged_positions": [i for i in items if i["prolongations"] > 0],
        }


billing_service = BillingService()
