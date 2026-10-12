# -*- coding: utf-8 -*-
"""Категории и подкатегории: хранятся в БД, редактируются пользователем."""
from __future__ import annotations

from typing import Optional

from ..db.database import get_db
from ..errors import AppError, ValidationError


class CategoriesService:
    def all(self) -> list[dict]:
        rows = get_db().query(
            "SELECT id, parent_id, name, sort_order FROM categories ORDER BY sort_order, name"
        )
        return [dict(r) for r in rows]

    def tree(self) -> list[dict]:
        """Дерево категорий с количеством предметов в каждой."""
        counts = {
            r["category_id"]: r["c"]
            for r in get_db().query(
                "SELECT category_id, COUNT(*) c FROM inventory_items "
                "WHERE is_archived=0 GROUP BY category_id"
            )
            if r["category_id"] is not None
        }
        nodes = {c["id"]: {**c, "children": [], "count": counts.get(c["id"], 0)} for c in self.all()}
        roots: list[dict] = []
        for node in nodes.values():
            parent = nodes.get(node["parent_id"])
            (parent["children"] if parent else roots).append(node)
        for node in nodes.values():
            node["total_count"] = node["count"] + sum(c["count"] for c in node["children"])
        roots.sort(key=lambda n: (n["sort_order"], n["name"]))
        for node in nodes.values():
            node["children"].sort(key=lambda n: (n["sort_order"], n["name"]))
        return roots

    def get(self, category_id: int) -> Optional[dict]:
        row = get_db().query_one("SELECT * FROM categories WHERE id=?", (category_id,))
        return dict(row) if row else None

    def path(self, category_id: Optional[int]) -> str:
        """'Мебель / Стулья' — для карточек и документов."""
        if not category_id:
            return ""
        parts, cur, guard = [], self.get(category_id), 0
        while cur and guard < 20:
            parts.append(cur["name"])
            cur = self.get(cur["parent_id"]) if cur["parent_id"] else None
            guard += 1
        return " / ".join(reversed(parts))

    def descendants(self, category_id: int) -> list[int]:
        """Категория и все вложенные — для фильтра каталога."""
        result, stack = [category_id], [category_id]
        by_parent: dict[Optional[int], list[int]] = {}
        for c in self.all():
            by_parent.setdefault(c["parent_id"], []).append(c["id"])
        while stack:
            for child in by_parent.get(stack.pop(), []):
                result.append(child)
                stack.append(child)
        return result

    def add(self, name: str, parent_id: Optional[int] = None) -> int:
        from .auth_service import auth_service
        auth_service.require_login()
        name = (name or "").strip()
        if not name:
            raise ValidationError("Укажите название категории.")
        db = get_db()
        exists = db.query_one(
            "SELECT id FROM categories WHERE name=? AND ifnull(parent_id,-1)=ifnull(?,-1)",
            (name, parent_id),
        )
        if exists:
            raise ValidationError(f"Категория «{name}» здесь уже есть.")
        order = db.query_one(
            "SELECT ifnull(MAX(sort_order),-1)+1 n FROM categories WHERE ifnull(parent_id,-1)=ifnull(?,-1)",
            (parent_id,),
        )["n"]
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO categories(parent_id, name, sort_order) VALUES (?,?,?)",
                (parent_id, name, order),
            )
            return int(cur.lastrowid)

    def rename(self, category_id: int, name: str) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        name = (name or "").strip()
        if not name:
            raise ValidationError("Название категории не может быть пустым.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE categories SET name=? WHERE id=?", (name, category_id))

    def move(self, category_id: int, new_parent_id: Optional[int]) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        if new_parent_id == category_id:
            raise ValidationError("Категорию нельзя поместить внутрь самой себя.")
        if new_parent_id and new_parent_id in self.descendants(category_id):
            raise ValidationError("Категорию нельзя переместить в свою же подкатегорию.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE categories SET parent_id=? WHERE id=?", (new_parent_id, category_id))

    def reorder(self, category_id: int, sort_order: int) -> None:
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE categories SET sort_order=? WHERE id=?", (sort_order, category_id))

    def delete(self, category_id: int) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        db = get_db()
        children = db.query_one("SELECT COUNT(*) c FROM categories WHERE parent_id=?", (category_id,))["c"]
        if children:
            raise AppError("Сначала удалите или перенесите подкатегории.")
        used = db.query_one("SELECT COUNT(*) c FROM inventory_items WHERE category_id=?", (category_id,))["c"]
        if used:
            raise AppError(
                f"В категории {used} предмет(ов). Перенесите их в другую категорию — "
                "удалять категорию вместе с реквизитом нельзя."
            )
        with db.transaction() as conn:
            conn.execute("DELETE FROM categories WHERE id=?", (category_id,))


categories_service = CategoriesService()
