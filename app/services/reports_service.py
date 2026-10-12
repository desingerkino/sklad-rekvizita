# -*- coding: utf-8 -*-
"""
Отчёты версии 2.3: история аренд, календарь, поиск, фильтры и статистика.

Собственной базы у отчётов нет — всё читается из существующих таблиц аренд,
позиций, начислений и возвратов. Любая новая аренда, продление или возврат
появляются в отчётах сразу, без синхронизации и копий данных.
"""
from __future__ import annotations

import calendar as calendar_module
import logging
from datetime import date, timedelta
from typing import Optional

from ..db.database import get_db
from ..utils import FAR_FUTURE, db_date, fmt_date, money_round, parse_date, today

log = logging.getLogger(__name__)

STATUS_LABELS = {
    "planned": "Запланирована",
    "active": "Активная",
    "overdue": "Просроченная",
    "prolonged": "Продлённая",
    "closed": "Завершённая",
    "cancelled": "Отменённая",
}

QUICK_PERIODS = ("today", "yesterday", "week", "month", "prev_month", "year", "all")


def period_bounds(name: str, reference: Optional[date] = None) -> tuple[Optional[date], Optional[date]]:
    """Границы быстрого периода: сегодня, вчера, неделя, месяц, прошлый месяц, год."""
    ref = reference or today()
    if name == "today":
        return ref, ref
    if name == "yesterday":
        day = ref - timedelta(days=1)
        return day, day
    if name == "week":
        start = ref - timedelta(days=ref.weekday())
        return start, start + timedelta(days=6)
    if name == "month":
        last = calendar_module.monthrange(ref.year, ref.month)[1]
        return date(ref.year, ref.month, 1), date(ref.year, ref.month, last)
    if name == "prev_month":
        first = date(ref.year, ref.month, 1) - timedelta(days=1)
        last = calendar_module.monthrange(first.year, first.month)[1]
        return date(first.year, first.month, 1), date(first.year, first.month, last)
    if name == "year":
        return date(ref.year, 1, 1), date(ref.year, 12, 31)
    return None, None


class ReportsService:
    # ------------------------------------------------------------- выборка
    def search(self, date_from: Optional[date] = None, date_to: Optional[date] = None,
               text: str = "", client_id: Optional[int] = None,
               project_id: Optional[int] = None, production_id: Optional[int] = None,
               category_id: Optional[int] = None, item_id: Optional[int] = None,
               status: str = "", user_id: Optional[int] = None,
               limit: int = 1000) -> list[dict]:
        """
        История аренд с комбинируемыми фильтрами. Все условия применяются
        одновременно: период + клиент + категория + статус и т.д.
        """
        sql = [
            "SELECT DISTINCT r.*, c.full_name client_name, p.name project_name,",
            "  pr.name production_name,",
            "  (SELECT COUNT(*) FROM rental_items ri2 WHERE ri2.rental_id = r.id) positions,",
            "  (SELECT ifnull(SUM(ri3.quantity), 0) FROM rental_items ri3",
            "     WHERE ri3.rental_id = r.id) units",
            "FROM rentals r",
            "LEFT JOIN clients c ON c.id = r.client_id",
            "LEFT JOIN projects p ON p.id = r.project_id",
            "LEFT JOIN productions pr ON pr.id = r.production_id",
        ]
        where: list[str] = ["1=1"]
        params: list = []

        needs_items = bool(category_id or item_id or text)
        if needs_items:
            sql.append("LEFT JOIN rental_items ri ON ri.rental_id = r.id")
            sql.append("LEFT JOIN inventory_items i ON i.id = ri.item_id")

        # период: аренда попадает в отчёт, если её срок пересекается с периодом
        if date_from:
            where.append("ifnull(r.date_end, '9999-12-31') >= ?")
            params.append(db_date(date_from))
        if date_to:
            where.append("r.date_start <= ?")
            params.append(db_date(date_to))

        if text.strip():
            like = f"%{text.strip()}%"
            where.append(
                "(r.number LIKE ? OR c.full_name LIKE ? OR p.name LIKE ? OR pr.name LIKE ? "
                "OR ri.name_snap LIKE ? OR ri.inv_number_snap LIKE ? OR r.note LIKE ?)")
            params += [like] * 7
        if client_id:
            where.append("r.client_id = ?")
            params.append(client_id)
        if project_id:
            where.append("r.project_id = ?")
            params.append(project_id)
        if production_id:
            where.append("r.production_id = ?")
            params.append(production_id)
        if item_id:
            where.append("ri.item_id = ?")
            params.append(item_id)
        if category_id:
            from .categories_service import categories_service
            ids = categories_service.descendants(category_id)
            where.append(f"i.category_id IN ({','.join('?' * len(ids))})")
            params += ids
        if user_id:
            where.append(
                "EXISTS (SELECT 1 FROM audit_log a WHERE a.entity='rental' "
                "AND a.entity_id = r.id AND a.user_id = ?)")
            params.append(user_id)

        sql.append("WHERE " + " AND ".join(where))
        sql.append("ORDER BY r.date_start DESC, r.id DESC LIMIT ?")
        params.append(limit)

        rows = [dict(r) for r in get_db().query("\n".join(sql), params)]
        result = []
        for row in rows:
            row["status_code"] = self.rental_status(row)
            row["status_label"] = STATUS_LABELS[row["status_code"]]
            row["date_start_d"] = parse_date(row["date_start"])
            row["date_end_d"] = parse_date(row["date_end"])
            row["days"] = ((row["date_end_d"] - row["date_start_d"]).days + 1
                           if row["date_end_d"] else None)
            if status and row["status_code"] != status:
                continue
            result.append(row)
        return result

    def rental_status(self, rental: dict) -> str:
        """Состояние аренды для календаря и списка — считается из её же данных."""
        if rental["status"] == "cancelled":
            return "cancelled"
        if rental["status"] == "closed":
            return "closed"
        start = parse_date(rental["date_start"])
        end = parse_date(rental["date_end"])
        current = today()
        if start and start > current:
            return "planned"
        if end and end < current:
            return "overdue"
        prolonged = get_db().query_one(
            "SELECT COUNT(*) c FROM rental_items WHERE rental_id=? AND prolongations > 0",
            (rental["id"],))["c"]
        return "prolonged" if prolonged else "active"

    # ----------------------------------------------------------- статистика
    def stats(self, rentals: list[dict]) -> dict:
        """Показатели за выбранный период — из тех же записей, что и список."""
        revenue = money_round(sum(r["total_final"] for r in rentals))
        deposits = money_round(sum(r["deposit"] for r in rentals))
        by_status: dict[str, int] = {}
        for rental in rentals:
            by_status[rental["status_code"]] = by_status.get(rental["status_code"], 0) + 1
        return {
            "total": len(rentals),
            "active": by_status.get("active", 0) + by_status.get("prolonged", 0),
            "closed": by_status.get("closed", 0),
            "overdue": by_status.get("overdue", 0),
            "planned": by_status.get("planned", 0),
            "revenue": revenue,
            "deposits": deposits,
            "positions": sum(r["positions"] for r in rentals),
            "units": sum(r["units"] for r in rentals),
            "by_status": by_status,
        }

    # ------------------------------------------------------------ календарь
    @staticmethod
    def _calendar_entry(rental: dict, day: date) -> dict:
        return {
            "rental_id": rental["id"],
            "number": rental["number"],
            "client": rental.get("client_name") or "—",
            "project": rental.get("project_name") or "—",
            "status": rental["status_code"],
            "status_label": rental["status_label"],
            "is_start": True,          # в календаре аренда стоит на дне открытия
            "is_end": rental["date_end_d"] == day,
            "date_end": rental["date_end_d"],
            "total": rental["total_final"],
        }

    def calendar_month(self, year: int, month: int, rentals: list[dict]) -> list[list[dict]]:
        """
        Сетка месяца: недели по семь дней.

        Аренда попадает в календарь ОДИН раз — в день своего открытия. Раньше
        она рисовалась в каждом дне периода, а аренда без даты окончания
        размазывалась по всем дням до конца времён: одно и то же название
        дублировалось на каждой дате, и календарь становился нечитаемым.

        Дата окончания при этом никуда не делась — она нужна расчёту срока,
        пролонгации, просрочке и истории. Календарь просто показывает событие
        «аренда открыта», а не занимает ею весь период.
        """
        weeks = calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month)
        by_day: dict[date, list[dict]] = {}
        for rental in rentals:
            start = rental["date_start_d"]
            if not start:
                continue
            by_day.setdefault(start, []).append(self._calendar_entry(rental, start))

        grid: list[list[dict]] = []
        for week in weeks:
            row = []
            for day in week:
                row.append({
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today(),
                    "rentals": by_day.get(day, []),
                })
            grid.append(row)
        return grid

    def day_rentals(self, day: date, rentals: list[dict]) -> list[dict]:
        """
        Аренды, открытые в этот день.

        Совпадает с тем, что нарисовано в клетке календаря: щелчок по дню
        должен показывать ровно то, что в нём видно, иначе список и календарь
        начинают противоречить друг другу.
        """
        return [r for r in rentals if r["date_start_d"] == day]

    def active_on(self, day: date, rentals: list[dict]) -> list[dict]:
        """
        Аренды, действующие в этот день: период включает дату.

        Это уже не про календарь, а про вопрос «что было на руках такого-то
        числа». Аренда без даты окончания считается действующей.
        """
        return [r for r in rentals
                if r["date_start_d"] and r["date_start_d"] <= day
                <= (r["date_end_d"] or FAR_FUTURE)]

    # -------------------------------------------------------------- детали
    def rental_details(self, rental_id: int) -> dict:
        """Подробности аренды для карточки в отчётах — только реальные поля."""
        from .billing_service import billing_service
        from .rentals_service import rentals_service

        rental = rentals_service.get(rental_id)
        if not rental:
            return {}
        rental["status_code"] = self.rental_status(rental)
        rental["status_label"] = STATUS_LABELS[rental["status_code"]]
        rental["returns"] = [r for item in rental["items"] for r in item["returns"]]
        rental["charges_list"] = billing_service.charges_grouped(rental_id)
        rental["responsible"] = self.responsible_users(rental_id)
        return rental

    def responsible_users(self, rental_id: int) -> list[str]:
        """Кто работал с арендой — из журнала действий, без выдуманных полей."""
        rows = get_db().query(
            "SELECT DISTINCT user_name FROM audit_log WHERE entity='rental' AND entity_id=? "
            "AND user_name <> 'Система'", (rental_id,))
        return [row["user_name"] for row in rows]

    # ------------------------------------------------------- справочники
    def filter_options(self) -> dict:
        """Значения для выпадающих фильтров — только то, что есть в данных."""
        db = get_db()
        return {
            "clients": [dict(r) for r in db.query(
                "SELECT DISTINCT c.id, c.full_name FROM clients c "
                "JOIN rentals r ON r.client_id = c.id ORDER BY c.full_name")],
            "projects": [dict(r) for r in db.query(
                "SELECT DISTINCT p.id, p.name FROM projects p "
                "JOIN rentals r ON r.project_id = p.id ORDER BY p.name")],
            "productions": [dict(r) for r in db.query(
                "SELECT DISTINCT pr.id, pr.name FROM productions pr "
                "JOIN rentals r ON r.production_id = pr.id ORDER BY pr.name")],
            "users": [dict(r) for r in db.query(
                "SELECT DISTINCT u.id, u.full_name FROM users u "
                "JOIN audit_log a ON a.user_id = u.id AND a.entity='rental' "
                "ORDER BY u.full_name")],
            "statuses": [{"code": code, "label": label} for code, label in STATUS_LABELS.items()],
        }


reports_service = ReportsService()
