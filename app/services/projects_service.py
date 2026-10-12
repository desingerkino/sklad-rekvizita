# -*- coding: utf-8 -*-
"""Проекты (фильмы, сериалы, рекламы) и их история аренд."""
from __future__ import annotations

from typing import Optional

from ..db.database import get_db
from ..errors import NotFoundError, ValidationError


class ProjectsService:
    def create(self, data: dict) -> int:
        from .auth_service import auth_service
        auth_service.require_login()
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Укажите название проекта.")
        db = get_db()
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO projects(name, production_id, contacts, date_start, date_end, extra) "
                "VALUES (?,?,?,?,?,?)",
                (name, data.get("production_id"), data.get("contacts", ""),
                 data.get("date_start"), data.get("date_end"), data.get("extra", "")))
            return int(cur.lastrowid)

    def update(self, project_id: int, data: dict) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        cur = self.get(project_id)
        if not cur:
            raise NotFoundError("Проект не найден.")
        fields = ("name", "production_id", "contacts", "date_start", "date_end", "extra")
        values = [data.get(f, cur[f]) for f in fields]
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE projects SET name=?, production_id=?, contacts=?, date_start=?, date_end=?, "
                "extra=?, updated_at=datetime('now','localtime') WHERE id=?", values + [project_id])

    def get(self, project_id: int) -> Optional[dict]:
        row = get_db().query_one(
            "SELECT p.*, pr.name production_name FROM projects p "
            "LEFT JOIN productions pr ON pr.id = p.production_id WHERE p.id=?", (project_id,))
        return dict(row) if row else None

    def search(self, text: str = "", production_id: Optional[int] = None, limit: int = 200) -> list[dict]:
        sql = ("SELECT p.*, pr.name production_name, "
               "(SELECT COUNT(*) FROM rentals r WHERE r.project_id=p.id) rentals_count "
               "FROM projects p LEFT JOIN productions pr ON pr.id=p.production_id WHERE 1=1")
        params: list = []
        if text.strip():
            sql += " AND (p.name LIKE ? OR pr.name LIKE ?)"
            params += [f"%{text.strip()}%"] * 2
        if production_id:
            sql += " AND p.production_id=?"
            params.append(production_id)
        sql += " ORDER BY p.name LIMIT ?"
        params.append(limit)
        return [dict(r) for r in get_db().query(sql, params)]

    def rentals(self, project_id: int) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT r.*, c.full_name client, pr.name production, "
            "(SELECT COUNT(*) FROM rental_items ri WHERE ri.rental_id=r.id) positions "
            "FROM rentals r LEFT JOIN clients c ON c.id=r.client_id "
            "LEFT JOIN productions pr ON pr.id=r.production_id "
            "WHERE r.project_id=? ORDER BY r.date_start DESC", (project_id,))]


projects_service = ProjectsService()
