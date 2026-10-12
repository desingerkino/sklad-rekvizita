# -*- coding: utf-8 -*-
"""
Принудительная запись всех данных на диск («Сохранить всё»).

В обычной работе кнопка не нужна: каждая подтверждённая операция фиксируется
в базе сразу. Но перед выключением компьютера, снятием резервной копии или
просто для спокойствия сотрудника полезно одним действием свести журнал WAL
в файл базы и убедиться, что несохранённого не осталось.
"""
from __future__ import annotations

import logging

from ..db.database import get_db
from ..utils import now

log = logging.getLogger(__name__)

_COUNTS = [
    ("Реквизит", "SELECT COUNT(*) c FROM inventory_items WHERE is_archived=0"),
    ("Фотографии", "SELECT COUNT(*) c FROM inventory_photos"),
    ("Клиенты", "SELECT COUNT(*) c FROM clients"),
    ("Продакшены", "SELECT COUNT(*) c FROM productions"),
    ("Проекты", "SELECT COUNT(*) c FROM projects"),
    ("Аренды", "SELECT COUNT(*) c FROM rentals"),
    ("Позиции аренд", "SELECT COUNT(*) c FROM rental_items"),
    ("Начисления", "SELECT COUNT(*) c FROM rental_charges"),
    ("Документы", "SELECT COUNT(*) c FROM documents"),
    ("Записи журнала", "SELECT COUNT(*) c FROM audit_log"),
]


class StorageService:
    def save_all(self) -> dict:
        """Сводит журнал в базу и возвращает отчёт о том, что записано."""
        db = get_db()
        flush = db.flush()
        counts = []
        for label, sql in _COUNTS:
            try:
                counts.append((label, db.query_one(sql)["c"]))
            except Exception:
                log.exception("Не удалось посчитать %s", label)
        report = {
            "at": now(),
            "db_path": str(db.path),
            "db_size_mb": round(flush["db_size"] / 1_048_576, 2),
            "wal_size": flush["wal_size"],
            "pages_written": flush["pages_written"],
            "counts": counts,
            "pending": flush["wal_size"] > 0,
        }
        log.info("Выполнено «Сохранить всё»: база %s МБ, журнал %s байт",
                 report["db_size_mb"], report["wal_size"])
        return report

    def last_change(self) -> str:
        """Время последнего изменения данных — для подписи «Все изменения сохранены»."""
        row = get_db().query_one(
            "SELECT MAX(t) t FROM (SELECT MAX(updated_at) t FROM inventory_items "
            "UNION ALL SELECT MAX(updated_at) FROM rentals "
            "UNION ALL SELECT MAX(created_at) FROM audit_log "
            "UNION ALL SELECT MAX(updated_at) FROM settings)")
        return row["t"] if row and row["t"] else ""


storage_service = StorageService()
