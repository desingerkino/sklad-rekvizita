# -*- coding: utf-8 -*-
"""Физические лица: карточка, поиск с автоподстановкой, история аренд."""
from __future__ import annotations

from typing import Optional

from ..db.database import get_db
from ..errors import NotFoundError, ValidationError
from .crypto_service import crypto_service

FIELDS = ("full_name", "passport", "phone", "address", "email", "extra")
# ФИО остаётся открытым: по нему идёт поиск и автоподстановка.
# Всё остальное — персональные данные, они шифруются.
SECRET_FIELDS = ("passport", "phone", "address", "email", "extra")


def _decrypt_row(row) -> dict:
    data = dict(row)
    for field in SECRET_FIELDS:
        if field in data:
            data[field] = crypto_service.decrypt(data[field])
    return data


class ClientsService:
    def create(self, data: dict) -> int:
        from .auth_service import auth_service
        auth_service.require_login()
        name = (data.get("full_name") or "").strip()
        if not name:
            raise ValidationError("Укажите ФИО.")
        db = get_db()
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO clients(full_name, passport, phone, address, email, extra) "
                "VALUES (?,?,?,?,?,?)",
                (name, *(crypto_service.encrypt(data.get(field, ""))
                         for field in SECRET_FIELDS)),
            )
            return int(cur.lastrowid)

    def update(self, client_id: int, data: dict) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        cur = self.get(client_id)
        if not cur:
            raise NotFoundError("Клиент не найден.")
        merged = {f: data.get(f, cur[f]) for f in FIELDS}
        if not (merged["full_name"] or "").strip():
            raise ValidationError("ФИО не может быть пустым.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE clients SET full_name=?, passport=?, phone=?, address=?, email=?, extra=?, "
                "updated_at=datetime('now','localtime') WHERE id=?",
                (merged["full_name"],
                 *(crypto_service.encrypt(merged[field]) for field in SECRET_FIELDS),
                 client_id),
            )
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action) VALUES ('client',?,'Изменена карточка')",
                (client_id,))

    def get(self, client_id: int) -> Optional[dict]:
        row = get_db().query_one("SELECT * FROM clients WHERE id=?", (client_id,))
        return _decrypt_row(row) if row else None

    def search(self, text: str = "", limit: int = 100) -> list[dict]:
        """
        Поиск по ФИО идёт в базе, по зашифрованным полям (телефон, e-mail,
        паспорт) — после расшифровки в памяти: искать по шифртексту нельзя.
        """
        text = (text or "").strip()
        if not text:
            rows = get_db().query("SELECT * FROM clients ORDER BY full_name LIMIT ?", (limit,))
            return [_decrypt_row(r) for r in rows]

        like = f"%{text.lower()}%"
        by_name = [_decrypt_row(r) for r in get_db().query(
            "SELECT * FROM clients WHERE rulower(full_name) LIKE ? ORDER BY full_name LIMIT ?",
            (like, limit))]
        found = {client["id"]: client for client in by_name}
        if len(found) < limit:
            needle = text.lower()
            for row in get_db().query("SELECT * FROM clients ORDER BY full_name"):
                if row["id"] in found:
                    continue
                client = _decrypt_row(row)
                if any(needle in (client.get(field) or "").lower()
                       for field in ("phone", "email", "passport", "address")):
                    found[client["id"]] = client
                if len(found) >= limit:
                    break
        return sorted(found.values(), key=lambda c: c["full_name"])

    def suggest(self, prefix: str, limit: int = 10) -> list[dict]:
        """Автозаполнение при вводе ФИО в новой аренде."""
        if not prefix.strip():
            return []
        # Ищем не только по началу строки: люди набирают «петров», а в базе
        # «Петров Пётр Петрович», и по фамилии в середине тоже надо находить.
        # Совпадения с начала показываем первыми — так привычнее.
        needle = prefix.strip().lower()
        rows = get_db().query(
            "SELECT * FROM clients WHERE rulower(full_name) LIKE ? ORDER BY "
            "  CASE WHEN rulower(full_name) LIKE ? THEN 0 ELSE 1 END, full_name LIMIT ?",
            (f"%{needle}%", f"{needle}%", limit))
        return [_decrypt_row(r) for r in rows]

    def rentals(self, client_id: int, only_active: bool = False) -> list[dict]:
        sql = ("SELECT r.*, p.name project, pr.name production, "
               "(SELECT COUNT(*) FROM rental_items ri WHERE ri.rental_id=r.id) positions "
               "FROM rentals r LEFT JOIN projects p ON p.id=r.project_id "
               "LEFT JOIN productions pr ON pr.id=r.production_id WHERE r.client_id=?")
        if only_active:
            sql += " AND r.status='active'"
        sql += " ORDER BY r.date_start DESC"
        return [dict(r) for r in get_db().query(sql, (client_id,))]


clients_service = ClientsService()
