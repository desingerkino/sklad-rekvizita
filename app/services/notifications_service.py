# -*- coding: utf-8 -*-
"""
Уведомления версии 2.3.

Строятся из реальных данных приложения, а не из отдельной таблицы-справочника:
сроки аренд, просрочки, наступившие пролонгации, реквизит в ремонте. Каждое
событие получает устойчивый ключ (dedup_key), поэтому при каждом обновлении
одно и то же событие не дублируется, а отметка «прочитано» сохраняется в базе
и переживает перезапуск программы.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..db.database import get_db
from ..utils import db_dt, fmt_date, money, parse_date, today

log = logging.getLogger(__name__)

KIND_LABELS = {
    "overdue": "Просроченная аренда",
    "expiring": "Скоро окончание аренды",
    "prolonged": "Автоматическая пролонгация",
    "repair": "Реквизит в ремонте",
    "system": "Событие программы",
}


class NotificationsService:
    # --------------------------------------------------------------- сборка
    def refresh(self) -> int:
        """
        Пересобирает уведомления по текущему состоянию склада.
        Возвращает количество новых. Уже существующие не трогаются.
        """
        from .rentals_service import rentals_service
        from .settings_service import settings_service

        days = settings_service.get_int("notify.days_before_end", 2)
        created = 0
        for rental in rentals_service.list_active():
            left = rental.get("days_left")
            if left is None:
                continue
            project = rental.get("project_name") or "без проекта"
            client = rental.get("client_name") or "—"
            if left < 0:
                created += self._add(
                    key=f"overdue:{rental['id']}:{rental['date_end']}",
                    kind="overdue",
                    title=f"Аренда {rental['number']} просрочена",
                    body=f"Срок истёк {abs(left)} дн. назад ({fmt_date(rental['date_end'])}). "
                         f"{project} · {client}. Нужно принять реквизит или продлить аренду.",
                    entity="rental", entity_id=rental["id"])
            elif left <= days:
                created += self._add(
                    key=f"expiring:{rental['id']}:{rental['date_end']}",
                    kind="expiring",
                    title=f"Аренда {rental['number']} заканчивается через {left} дн.",
                    body=f"Окончание {fmt_date(rental['date_end'])}. {project} · {client}. "
                         f"Продлите срок или подготовьте приём реквизита.",
                    entity="rental", entity_id=rental["id"])

        # наступившие пролонгации за последние 30 дней
        rows = get_db().query(
            "SELECT c.id, c.rental_id, c.amount, c.period_number, c.charge_date, "
            "ri.name_snap, r.number FROM rental_charges c "
            "JOIN rental_items ri ON ri.id = c.rental_item_id "
            "JOIN rentals r ON r.id = c.rental_id "
            "WHERE c.charge_type='prolongation' AND c.charge_date >= date('now','-30 day') "
            "ORDER BY c.charge_date DESC LIMIT 50")
        for row in rows:
            created += self._add(
                key=f"prolongation:{row['id']}",
                kind="prolonged",
                title=f"Начислен новый период по аренде {row['number']}",
                body=f"{row['name_snap']} — период №{row['period_number']} "
                     f"от {fmt_date(row['charge_date'])}, начислено {money(row['amount'])}.",
                entity="rental", entity_id=row["rental_id"])

        for row in get_db().query(
                "SELECT r.id, r.quantity, r.reason, i.name, i.id item_id FROM repairs r "
                "JOIN inventory_items i ON i.id = r.item_id WHERE r.status='open'"):
            created += self._add(
                key=f"repair:{row['id']}",
                kind="repair",
                title=f"В ремонте: {row['name']}",
                body=f"{row['quantity']} шт. недоступны для аренды. "
                     f"{row['reason'] or 'Причина не указана'}.",
                entity="item", entity_id=row["item_id"])
        if created:
            log.info("Новых уведомлений: %s", created)
        return created

    def _add(self, key: str, kind: str, title: str, body: str,
             entity: str = "", entity_id: Optional[int] = None) -> int:
        db = get_db()
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO notifications(dedup_key, kind, title, body, entity, "
                "entity_id, created_at) VALUES (?,?,?,?,?,?,?)",
                (key, kind, title, body, entity, entity_id, db_dt()))
            return 1 if cur.rowcount else 0

    def add_system(self, title: str, body: str = "", entity: str = "",
                   entity_id: Optional[int] = None, key: Optional[str] = None) -> int:
        """Системное уведомление (например, о создании резервной копии)."""
        return self._add(key or f"system:{title}:{db_dt()}", "system", title, body,
                         entity, entity_id)

    # --------------------------------------------------------------- чтение
    def list(self, unread_only: bool = False, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM notifications"
        if unread_only:
            sql += " WHERE read_at IS NULL"
        sql += " ORDER BY (read_at IS NOT NULL), created_at DESC LIMIT ?"
        rows = get_db().query(sql, (limit,))
        out = []
        for row in rows:
            item = dict(row)
            item["is_read"] = bool(item["read_at"])
            item["kind_label"] = KIND_LABELS.get(item["kind"], item["kind"])
            out.append(item)
        return out

    def unread_count(self) -> int:
        return get_db().query_one(
            "SELECT COUNT(*) c FROM notifications WHERE read_at IS NULL")["c"]

    # -------------------------------------------------------------- отметки
    def mark_read(self, notification_id: int) -> None:
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE notifications SET read_at=? WHERE id=? AND read_at IS NULL",
                         (db_dt(), notification_id))

    def mark_all_read(self) -> int:
        db = get_db()
        count = self.unread_count()
        with db.transaction() as conn:
            conn.execute("UPDATE notifications SET read_at=? WHERE read_at IS NULL", (db_dt(),))
        return count

    def cleanup(self, keep_days: int = 180) -> None:
        """Убирает прочитанные уведомления старше полугода — история аренд не трогается."""
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "DELETE FROM notifications WHERE read_at IS NOT NULL "
                "AND created_at < date('now', ?)", (f"-{keep_days} day",))


notifications_service = NotificationsService()
