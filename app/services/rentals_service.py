# -*- coding: utf-8 -*-
"""
Аренда — ядро системы.

Здесь: расчёт сумм, скидка, залог, проверка доступности с учётом пересечения дат,
генерация номера, создание аренды одной транзакцией, продление, закрытие.
Ни одна проверка не может быть обойдена из интерфейса — UI вызывает эти методы.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from ..db.database import get_db
from ..errors import AvailabilityError, NotFoundError, ValidationError
from ..utils import (FAR_FUTURE, db_date, db_dt, fmt_date, money_round, now,
                     parse_date, today)
from ..version import RENTAL_PERIOD_DAYS
from .audit_service import audit_service
from .auth_service import auth_service
from .billing_service import billing_service
from .inventory_service import inventory_service
from .settings_service import settings_service

log = logging.getLogger(__name__)

PAYMENT_LABELS = {"cash": "Наличные", "bank": "Безналичные"}


@dataclass
class RentalLine:
    """Строка аренды в форме до сохранения."""
    item_id: int
    name: str = ""
    inv_number: str = ""
    description: str = ""
    note: str = ""
    quantity: int = 1
    price: float = 0.0
    valuation: float = 0.0

    @property
    def amount(self) -> float:
        return money_round(self.quantity * self.price)

    @property
    def valuation_amount(self) -> float:
        return money_round(self.quantity * self.valuation)


@dataclass
class RentalDraft:
    """Черновик аренды: то, что собрано в форме «Новая аренда»."""
    client_id: Optional[int] = None
    production_id: Optional[int] = None
    project_id: Optional[int] = None
    payment_method: str = "cash"
    invoice_production_id: Optional[int] = None
    date_start: Optional[date] = None
    date_end: Optional[date] = None
    discount_percent: float = 0.0
    deposit: float = 0.0
    note: str = ""
    lines: list[RentalLine] = field(default_factory=list)


def calculate(lines: list[RentalLine], discount_percent: float, deposit: float) -> dict:
    """Расчёт итогов. Залог никогда не смешивается со стоимостью аренды."""
    total_before = money_round(sum(line.amount for line in lines))
    discount_percent = float(discount_percent or 0)
    discount_amount = money_round(total_before * discount_percent / 100.0)
    total_final = money_round(total_before - discount_amount)
    total_valuation = money_round(sum(line.valuation_amount for line in lines))
    return {
        "total_before_discount": total_before,
        "discount_percent": discount_percent,
        "discount_amount": discount_amount,
        "total_final": total_final,
        "total_valuation": total_valuation,
        "deposit": money_round(deposit),
        "positions": len(lines),
        "units": sum(line.quantity for line in lines),
    }


class RentalsService:
    # ------------------------------------------------------------- нумерация
    def next_number(self, year: Optional[int] = None) -> str:
        """АР-2026-0001, номер уникален и хранится в БД."""
        db = get_db()
        with db.transaction() as conn:
            return self._next_number(conn, year)

    def _next_number(self, conn, year: Optional[int] = None) -> str:
        """Выдача номера внутри уже открытой транзакции — без вложенных BEGIN."""
        prefix = settings_service.get("rental.number_prefix", "АР")
        year = year or today().year
        conn.execute(
            "INSERT INTO counters(name, year, value) VALUES ('rental',?,0) "
            "ON CONFLICT(name, year) DO NOTHING", (year,))
        conn.execute(
            "UPDATE counters SET value = value + 1 WHERE name='rental' AND year=?", (year,))
        value = conn.execute(
            "SELECT value FROM counters WHERE name='rental' AND year=?", (year,)).fetchone()[0]
        return f"{prefix}-{year}-{value:04d}"

    # ------------------------------------------------------------- проверки
    def validate(self, draft: RentalDraft) -> None:
        if not draft.client_id:
            raise ValidationError("Не выбрано физическое лицо, которое берёт реквизит.")
        if not draft.production_id:
            raise ValidationError("Не выбран продакшен.")
        if not draft.project_id:
            raise ValidationError("Не выбран проект.")
        if draft.payment_method not in PAYMENT_LABELS:
            raise ValidationError("Не указан способ оплаты: наличные или безналичные.")
        if draft.payment_method == "bank" and not (draft.invoice_production_id or draft.production_id):
            raise ValidationError("Для безналичной оплаты укажите, на какое юр. лицо выставляется счёт.")
        if not draft.date_start:
            raise ValidationError("Укажите дату начала аренды.")
        if draft.date_end and draft.date_end < draft.date_start:
            raise ValidationError("Дата окончания аренды раньше даты начала.")
        if not draft.lines:
            raise ValidationError("В аренду не добавлено ни одной позиции реквизита.")
        if not 0 <= float(draft.discount_percent or 0) <= 100:
            raise ValidationError("Скидка должна быть от 0 до 100 %.")
        if float(draft.deposit or 0) < 0:
            raise ValidationError("Залог не может быть отрицательным.")
        for line in draft.lines:
            if line.quantity <= 0:
                raise ValidationError(f"«{line.name}»: количество должно быть больше нуля.")
            if line.price < 0:
                raise ValidationError(f"«{line.name}»: цена аренды не может быть отрицательной.")
        self.check_availability(draft)

    def check_availability(self, draft: RentalDraft, exclude_rental_id: Optional[int] = None) -> None:
        """Один и тот же реквизит нельзя выдать дважды на пересекающиеся даты."""
        # если одна позиция добавлена несколько раз — суммируем
        wanted: dict[int, int] = {}
        for line in draft.lines:
            wanted[line.item_id] = wanted.get(line.item_id, 0) + line.quantity

        for item_id, qty in wanted.items():
            item = inventory_service.get(item_id)
            if not item:
                raise NotFoundError("Один из предметов не найден в каталоге.")
            available = inventory_service.available_for_period(
                item_id, draft.date_start, draft.date_end, exclude_rental_id)
            if qty > available:
                conflicts = inventory_service.conflicting_rentals(
                    item_id, draft.date_start, draft.date_end, exclude_rental_id)
                detail = ""
                if conflicts:
                    parts = [
                        f"{c['number']} ({fmt_date(c['date_start'])}–"
                        f"{fmt_date(c['date_end']) if c['date_end'] else 'без даты'}, {c['quantity']} шт.)"
                        for c in conflicts[:4]
                    ]
                    detail = " Заняты арендами: " + "; ".join(parts) + "."
                repair = item["in_repair"]
                repair_text = f" В ремонте: {repair} шт." if repair else ""
                raise AvailabilityError(
                    f"Нельзя оформить аренду «{item['name']}»: на выбранные даты доступно "
                    f"{available} шт., а выбрано {qty}.{detail}{repair_text}"
                )

    # -------------------------------------------------------------- создание
    def create(self, draft: RentalDraft, number: Optional[str] = None) -> int:
        """
        Создаёт аренду. Каждая позиция получает собственную дату выдачи и свой
        первый 14-дневный период; на каждую делается начисление типа initial.

        Если номер уже забронирован за незавершённым оформлением, он передаётся
        сюда и используется как есть — иначе аренда, которую оператор всё время
        видел под номером АР-2026-0047, при выдаче стала бы другой.
        """
        auth_service.require_login()
        if float(draft.discount_percent or 0) > 0:
            auth_service.require_admin("Скидку может установить только администратор.")
        self.validate(draft)
        totals = calculate(draft.lines, draft.discount_percent, draft.deposit)
        moment = now()
        start_dt = datetime.combine(draft.date_start, moment.time()) \
            if draft.date_start != moment.date() else moment

        db = get_db()
        with db.transaction() as conn:
            # повторная проверка остатков уже под блокировкой БД: между проверкой
            # и записью никто не успеет забрать тот же реквизит
            self.check_availability(draft)
            if number:
                clash = conn.execute("SELECT 1 FROM rentals WHERE number=?",
                                     (number,)).fetchone()
                if clash:
                    raise ValidationError(
                        f"Аренда с номером {number} уже существует.")
            else:
                number = self._next_number(conn, draft.date_start.year)
            cur = conn.execute(
                "INSERT INTO rentals(number, client_id, production_id, project_id, payment_method, "
                "invoice_production_id, date_start, date_end, discount_percent, deposit, "
                "total_before_discount, discount_amount, total_final, total_valuation, status, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'active', ?)",
                (number, draft.client_id, draft.production_id, draft.project_id,
                 draft.payment_method, draft.invoice_production_id or draft.production_id,
                 db_date(draft.date_start), db_date(draft.date_end),
                 totals["discount_percent"], totals["deposit"], totals["total_before_discount"],
                 totals["discount_amount"], totals["total_final"], totals["total_valuation"],
                 draft.note))
            rental_id = int(cur.lastrowid)
            for line in draft.lines:
                item_cur = conn.execute(
                    "INSERT INTO rental_items(rental_id, item_id, name_snap, inv_number_snap, "
                    "description_snap, note, quantity, price, valuation, amount) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rental_id, line.item_id, line.name, line.inv_number, line.description,
                     line.note, line.quantity, line.price, line.valuation, line.amount))
                billing_service.init_item_period(
                    conn, int(item_cur.lastrowid), start_dt, line.quantity, line.price,
                    line.amount, rental_id, "initial",
                    f"Первоначальная аренда, период №1 ({RENTAL_PERIOD_DAYS} дн.)")
            if totals["deposit"] > 0:
                conn.execute(
                    "INSERT INTO deposits(rental_id, amount, received_at) VALUES (?,?,datetime('now','localtime'))",
                    (rental_id, totals["deposit"]))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) "
                "VALUES ('rental',?,'Создана аренда',?)", (rental_id, number))
        billing_service.recalc_rental(rental_id)
        audit_service.log("Создана аренда", entity="rental", entity_id=rental_id,
                          details=f"{number}, позиций: {len(draft.lines)}",
                          amount=totals["total_final"])
        if totals["discount_percent"]:
            audit_service.log("Назначена скидка", entity="rental", entity_id=rental_id,
                              details=f"{totals['discount_percent']} % при создании аренды {number}")
        log.info("Создана аренда %s (id=%s), позиций: %s", number, rental_id, len(draft.lines))
        return rental_id

    # --------------------------------------------- добор в действующую аренду
    def add_items(self, rental_id: int, lines: list[RentalLine]) -> list[int]:
        """
        Добор реквизита в уже действующую аренду.

        Новые позиции получают дату и время фактической выдачи — не дату создания
        аренды. Их первый 14-дневный период и дата пролонгации считаются от этого
        момента; сроки ранее выданных позиций не меняются.
        """
        auth_service.require_login()
        rental = self.get(rental_id)
        if not rental:
            raise NotFoundError("Аренда не найдена.")
        if rental["status"] != "active":
            raise ValidationError("Добавить реквизит можно только в действующую аренду.")
        if not lines:
            raise ValidationError("Не выбрано ни одной позиции.")

        moment = now()
        start_date = moment.date()
        # проверяем доступность на срок нового периода
        check = RentalDraft(
            client_id=rental["client_id"], production_id=rental["production_id"],
            project_id=rental["project_id"], payment_method=rental["payment_method"],
            date_start=start_date, date_end=rental["date_end"] and parse_date(rental["date_end"]),
            lines=lines)
        self.check_availability(check, exclude_rental_id=None)

        db = get_db()
        created: list[int] = []
        total_added = 0.0
        with db.transaction() as conn:
            self.check_availability(check)     # повторная проверка под блокировкой
            for line in lines:
                if line.quantity <= 0:
                    raise ValidationError(f"«{line.name}»: количество должно быть больше нуля.")
                cur = conn.execute(
                    "INSERT INTO rental_items(rental_id, item_id, name_snap, inv_number_snap, "
                    "description_snap, note, quantity, price, valuation, amount) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rental_id, line.item_id, line.name, line.inv_number, line.description,
                     line.note, line.quantity, line.price, line.valuation, line.amount))
                rental_item_id = int(cur.lastrowid)
                billing_service.init_item_period(
                    conn, rental_item_id, moment, line.quantity, line.price, line.amount,
                    rental_id, "addition",
                    f"Добор реквизита {fmt_date(start_date)}, период №1")
                created.append(rental_item_id)
                total_added += line.amount
        billing_service.recalc_rental(rental_id)
        audit_service.log(
            "Добор реквизита в аренду", entity="rental", entity_id=rental_id,
            details=f"{rental['number']}: добавлено позиций {len(created)} "
                    f"({', '.join(l.name for l in lines[:5])}{'…' if len(lines) > 5 else ''}), "
                    f"дата выдачи {fmt_date(start_date)}",
            amount=round(total_added, 2))
        return created

    # ------------------------------------------------- защищённые операции
    def set_discount(self, rental_id: int, percent: float) -> dict:
        """Скидку может установить или изменить только администратор."""
        auth_service.require_admin("Скидку может установить только администратор.")
        if not 0 <= float(percent) <= 100:
            raise ValidationError("Скидка должна быть от 0 до 100 %.")
        rental = self.get(rental_id)
        if not rental:
            raise NotFoundError("Аренда не найдена.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE rentals SET discount_percent=?, updated_at=datetime('now','localtime') "
                         "WHERE id=?", (float(percent), rental_id))
        totals = billing_service.recalc_rental(rental_id)
        audit_service.log("Назначена скидка", entity="rental", entity_id=rental_id,
                          details=f"{rental['number']}: {rental['discount_percent']} % → {percent} %",
                          amount=totals["discount_amount"])
        return totals

    def remove_item(self, rental_item_id: int) -> None:
        """Удалить позицию из аренды может только администратор."""
        auth_service.require_admin("Удалить реквизит из аренды может только администратор.")
        db = get_db()
        row = db.query_one("SELECT * FROM rental_items WHERE id=?", (rental_item_id,))
        if not row:
            raise NotFoundError("Позиция аренды не найдена.")
        with db.transaction() as conn:
            conn.execute("DELETE FROM rental_charges WHERE rental_item_id=?", (rental_item_id,))
            conn.execute("DELETE FROM rental_returns WHERE rental_item_id=?", (rental_item_id,))
            conn.execute("DELETE FROM rental_items WHERE id=?", (rental_item_id,))
        billing_service.recalc_rental(row["rental_id"])
        audit_service.log("Удалена позиция из аренды", entity="rental", entity_id=row["rental_id"],
                          details=f"{row['name_snap']} × {row['quantity']}",
                          amount=row["total_amount"])

    # --------------------------------------------------------------- чтение
    def get(self, rental_id: int) -> Optional[dict]:
        row = get_db().query_one(
            "SELECT r.*, c.full_name client_name, c.passport client_passport, c.phone client_phone, "
            "c.address client_address, c.email client_email, p.name project_name, "
            "pr.name production_name FROM rentals r "
            "LEFT JOIN clients c ON c.id=r.client_id "
            "LEFT JOIN projects p ON p.id=r.project_id "
            "LEFT JOIN productions pr ON pr.id=r.production_id WHERE r.id=?", (rental_id,))
        if not row:
            return None
        rental = dict(row)
        rental["items"] = billing_service.items(rental_id)
        rental["summary"] = billing_service.rental_summary(rental_id)
        rental["charges"] = billing_service.charges_grouped(rental_id)
        rental["extensions"] = self.extensions(rental_id)
        rental["payment_label"] = PAYMENT_LABELS.get(rental["payment_method"], rental["payment_method"])
        rental["days_left"] = (parse_date(rental["date_end"]) - today()).days if rental["date_end"] else None
        return rental

    def items(self, rental_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT * FROM rental_items WHERE rental_id=? ORDER BY id", (rental_id,))]

    def extensions(self, rental_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT * FROM extensions WHERE rental_id=? ORDER BY created_at", (rental_id,))]

    def list_active(self, text: str = "") -> list[dict]:
        sql = ("SELECT r.*, c.full_name client_name, p.name project_name, pr.name production_name, "
               "(SELECT COUNT(*) FROM rental_items ri WHERE ri.rental_id=r.id) positions "
               "FROM rentals r LEFT JOIN clients c ON c.id=r.client_id "
               "LEFT JOIN projects p ON p.id=r.project_id "
               "LEFT JOIN productions pr ON pr.id=r.production_id WHERE r.status='active'")
        params: list = []
        if text.strip():
            sql += " AND (r.number LIKE ? OR c.full_name LIKE ? OR p.name LIKE ? OR pr.name LIKE ?)"
            params += [f"%{text.strip()}%"] * 4
        sql += " ORDER BY ifnull(r.date_end,'9999-12-31'), r.date_start"
        rows = [dict(r) for r in get_db().query(sql, params)]
        for r in rows:
            end = parse_date(r["date_end"])
            r["days_left"] = (end - today()).days if end else None
        return rows

    def list_all(self, text: str = "", limit: int = 500) -> list[dict]:
        sql = ("SELECT r.*, c.full_name client_name, p.name project_name, pr.name production_name "
               "FROM rentals r LEFT JOIN clients c ON c.id=r.client_id "
               "LEFT JOIN projects p ON p.id=r.project_id "
               "LEFT JOIN productions pr ON pr.id=r.production_id WHERE 1=1")
        params: list = []
        if text.strip():
            sql += " AND (r.number LIKE ? OR c.full_name LIKE ? OR p.name LIKE ? OR pr.name LIKE ?)"
            params += [f"%{text.strip()}%"] * 4
        sql += " ORDER BY r.created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in get_db().query(sql, params)]

    def expiring_soon(self, days: Optional[int] = None) -> list[dict]:
        """Аренды, которые заканчиваются в ближайшие N дней (по системной дате Windows)."""
        days = days if days is not None else settings_service.get_int("notify.days_before_end", 2)
        out = []
        for rental in self.list_active():
            left = rental["days_left"]
            if left is not None and left <= days:
                out.append(rental)
        return out

    # ------------------------------------------------------------ продление
    def extend(self, rental_id: int, new_date_end: date, comment: str = "") -> int:
        rental = self.get(rental_id)
        if not rental:
            raise NotFoundError("Аренда не найдена.")
        if rental["status"] != "active":
            raise ValidationError("Продлить можно только действующую аренду.")
        old_end = parse_date(rental["date_end"])
        start = parse_date(rental["date_start"])
        if new_date_end < start:
            raise ValidationError("Новая дата окончания раньше даты начала аренды.")
        if old_end and new_date_end <= old_end:
            raise ValidationError(
                f"Новая дата окончания должна быть позже текущей ({fmt_date(old_end)}).")

        # проверяем, что на новый период реквизит свободен (сама эта аренда не мешает)
        draft = RentalDraft(
            client_id=rental["client_id"], production_id=rental["production_id"],
            project_id=rental["project_id"], payment_method=rental["payment_method"],
            date_start=old_end or start, date_end=new_date_end,
            lines=[RentalLine(item_id=i["item_id"], name=i["name_snap"],
                              quantity=i["quantity"] - i["returned_qty"])
                   for i in rental["items"]
                   if i["item_id"] and i["quantity"] - i["returned_qty"] > 0],
        )
        self.check_availability(draft, exclude_rental_id=rental_id)

        db = get_db()
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO extensions(rental_id, old_date_end, new_date_end, comment) VALUES (?,?,?,?)",
                (rental_id, rental["date_end"], db_date(new_date_end), comment))
            extension_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE rentals SET date_end=?, updated_at=datetime('now','localtime') WHERE id=?",
                (db_date(new_date_end), rental_id))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) "
                "VALUES ('rental',?,'Продление',?)",
                (rental_id, f"{fmt_date(old_end) if old_end else '—'} → {fmt_date(new_date_end)}"))
        audit_service.log("Изменена аренда", entity="rental", entity_id=rental_id,
                          details=f"{rental['number']}: срок продлён до {fmt_date(new_date_end)}")
        return extension_id

    # ------------------------------------------------------------- закрытие
    def close(self, rental_id: int, comment: str = "") -> None:
        auth_service.require_login()
        rental = self.get(rental_id)
        if not rental:
            raise NotFoundError("Аренда не найдена.")
        if rental["status"] != "active":
            raise ValidationError("Аренда уже закрыта.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE rentals SET status='closed', closed_at=datetime('now','localtime'), "
                "updated_at=datetime('now','localtime') WHERE id=?", (rental_id,))
            conn.execute(
                "UPDATE rental_items SET returned_qty=quantity, status='returned', "
                "date_returned=COALESCE(date_returned, ?) WHERE rental_id=?", (db_dt(), rental_id))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) "
                "VALUES ('rental',?,'Закрыта',?)", (rental_id, comment))
        audit_service.log("Аренда закрыта", entity="rental", entity_id=rental_id,
                          details=f"{rental['number']}. {comment}".strip())

    def return_deposit(self, rental_id: int, returned_amount: float,
                       withheld_amount: float = 0.0, reason: str = "") -> None:
        db = get_db()
        row = db.query_one("SELECT id FROM deposits WHERE rental_id=?", (rental_id,))
        if not row:
            raise NotFoundError("По этой аренде залог не оформлялся.")
        with db.transaction() as conn:
            conn.execute(
                "UPDATE deposits SET returned_at=datetime('now','localtime'), returned_amount=?, "
                "withheld_amount=?, withheld_reason=? WHERE id=?",
                (returned_amount, withheld_amount, reason, row["id"]))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) "
                "VALUES ('rental',?,'Возврат залога',?)", (rental_id, reason))


rentals_service = RentalsService()
