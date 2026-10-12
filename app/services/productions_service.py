# -*- coding: utf-8 -*-
"""Продакшены: юридические реквизиты, проекты, аренды, история."""
from __future__ import annotations

from typing import Optional

from ..db.database import get_db
from ..errors import NotFoundError, ValidationError
from .crypto_service import crypto_service

FIELDS = ("name", "legal_form", "inn", "kpp", "ogrn", "legal_address", "actual_address",
          "bank_name", "bank_account", "bank_bik", "bank_corr", "phone", "email",
          "contact_person", "contact_position", "extra")
# Название и ИНН нужны для поиска, остальное — конфиденциальные реквизиты.
SECRET_FIELDS = ("legal_address", "actual_address", "bank_account", "bank_corr",
                 "phone", "email", "contact_person", "extra")


def _decrypt_row(row) -> dict:
    data = dict(row)
    for field in SECRET_FIELDS:
        if field in data:
            data[field] = crypto_service.decrypt(data[field])
    return data


def _encrypt_values(values: list) -> list:
    return [crypto_service.encrypt(value) if FIELDS[index] in SECRET_FIELDS else value
            for index, value in enumerate(values)]


class ProductionsService:
    def create(self, data: dict) -> int:
        from .auth_service import auth_service
        auth_service.require_login()
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Укажите название продакшена.")
        values = [data.get(f, "") for f in FIELDS]
        values[0] = name
        db = get_db()
        with db.transaction() as conn:
            cur = conn.execute(
                f"INSERT INTO productions({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
                _encrypt_values(values))
            return int(cur.lastrowid)

    def update(self, production_id: int, data: dict) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        current = self.get(production_id)
        if not current:
            raise NotFoundError("Продакшен не найден.")
        values = [data.get(f, current[f]) for f in FIELDS]
        if not (values[0] or "").strip():
            raise ValidationError("Название продакшена не может быть пустым.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                f"UPDATE productions SET {','.join(f + '=?' for f in FIELDS)}, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                _encrypt_values(values) + [production_id])

    def get(self, production_id: int) -> Optional[dict]:
        row = get_db().query_one("SELECT * FROM productions WHERE id=?", (production_id,))
        return _decrypt_row(row) if row else None

    def search(self, text: str = "", limit: int = 100) -> list[dict]:
        if text.strip():
            like = f"%{text.strip()}%"
            rows = get_db().query(
                "SELECT * FROM productions WHERE name LIKE ? OR inn LIKE ? "
                "ORDER BY name LIMIT ?", (like, like, limit))
            found = {row["id"]: _decrypt_row(row) for row in rows}
            needle = text.strip().lower()
            for row in get_db().query("SELECT * FROM productions ORDER BY name"):
                if row["id"] in found or len(found) >= limit:
                    continue
                production = _decrypt_row(row)
                if any(needle in (production.get(field) or "").lower()
                       for field in ("contact_person", "phone", "email")):
                    found[production["id"]] = production
            return sorted(found.values(), key=lambda p: p["name"])
        rows = get_db().query("SELECT * FROM productions ORDER BY name LIMIT ?", (limit,))
        return [_decrypt_row(r) for r in rows]

    def projects(self, production_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT * FROM projects WHERE production_id=? ORDER BY name", (production_id,))]

    def rentals(self, production_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT r.*, p.name project, c.full_name client FROM rentals r "
            "LEFT JOIN projects p ON p.id=r.project_id LEFT JOIN clients c ON c.id=r.client_id "
            "WHERE r.production_id=? ORDER BY r.date_start DESC", (production_id,))]

    def totals(self, production_id: int) -> dict:
        row = get_db().query_one(
            "SELECT COUNT(*) rentals, ifnull(SUM(total_final),0) amount FROM rentals "
            "WHERE production_id=?", (production_id,))
        return dict(row)


productions_service = ProductionsService()
