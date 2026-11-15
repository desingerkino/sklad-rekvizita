# -*- coding: utf-8 -*-
"""Каталог реквизита: карточки, поиск, количество, доступность."""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from ..db.database import get_db
from ..errors import AppError, NotFoundError, ValidationError
from ..utils import FAR_FUTURE, parse_date, today

log = logging.getLogger(__name__)

STATUS_LABELS = {"in_stock": "На складе", "repair": "В ремонте", "archived": "Списан"}


def _fts_query(text: str) -> str:
    """Готовит запрос для FTS5: каждое слово ищется как префикс."""
    words = [w for w in re.split(r"[^\w\u0400-\u04FF]+", text) if w]
    return " ".join(f'"{w}"*' for w in words)


class InventoryService:
    # ------------------------------------------------------------- нумерация
    def next_inv_number(self) -> str:
        row = get_db().query_one(
            "SELECT inv_number FROM inventory_items "
            "WHERE inv_number GLOB '[0-9]*' ORDER BY CAST(inv_number AS INTEGER) DESC LIMIT 1"
        )
        nxt = (int(row["inv_number"]) + 1) if row and row["inv_number"].isdigit() else 1
        return f"{nxt:06d}"

    # ------------------------------------------------------------------ CRUD
    def create(self, data: dict) -> int:
        from .auth_service import auth_service
        auth_service.require_login()
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Укажите название предмета.")
        qty = int(data.get("quantity_total") or 0)
        if qty < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        inv_number = (data.get("inv_number") or "").strip() or self.next_inv_number()

        db = get_db()
        if db.query_one("SELECT id FROM inventory_items WHERE inv_number=?", (inv_number,)):
            raise ValidationError(f"Инвентарный номер {inv_number} уже занят.")
        # штрихкод = инвентарный номер (версия 2.4). Если номер задан кириллицей,
        # предмет создаётся без штрихкода — работать это не мешает.
        from .barcode_service import barcode_service
        try:
            barcode = barcode_service.value_for(inv_number)
        except ValidationError:
            barcode = ""

        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO inventory_items(inv_number, name, category_id, description, keywords, "
                "note, quantity_total, rental_price, valuation, status, barcode, barcode_type) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (inv_number, name, data.get("category_id"), data.get("description", ""),
                 data.get("keywords", ""), data.get("note", ""), qty,
                 float(data.get("rental_price") or 0), float(data.get("valuation") or 0),
                 data.get("status") or "in_stock", barcode, "code128"),
            )
            item_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) VALUES ('item',?,?,?)",
                (item_id, "Создан", f"{inv_number} — {name}"),
            )
        from .audit_service import audit_service
        audit_service.log("Добавлен реквизит", entity="item", entity_id=item_id,
                          details=f"{inv_number} — {name}, {qty} шт.")
        return item_id

    def update(self, item_id: int, data: dict) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        db = get_db()
        item = self.get(item_id)
        if not item:
            raise NotFoundError("Предмет не найден.")
        qty = int(data.get("quantity_total", item["quantity_total"]))
        if qty < 0:
            raise ValidationError("Количество не может быть отрицательным.")
        busy = self.reserved_now(item_id)
        if qty < busy:
            raise ValidationError(
                f"Нельзя установить количество {qty}: сейчас в аренде {busy} шт. "
                "Сначала закройте аренды или верните реквизит."
            )
        with db.transaction() as conn:
            conn.execute(
                "UPDATE inventory_items SET name=?, category_id=?, description=?, keywords=?, note=?, "
                "quantity_total=?, rental_price=?, valuation=?, status=?, updated_at=datetime('now','localtime') "
                "WHERE id=?",
                (data.get("name", item["name"]).strip(), data.get("category_id", item["category_id"]),
                 data.get("description", item["description"]), data.get("keywords", item["keywords"]),
                 data.get("note", item["note"]), qty,
                 float(data.get("rental_price", item["rental_price"])),
                 float(data.get("valuation", item["valuation"])),
                 data.get("status", item["status"]), item_id),
            )
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) VALUES ('item',?,?,?)",
                (item_id, "Изменён", ""),
            )
        from .audit_service import audit_service
        audit_service.log("Изменён реквизит", entity="item", entity_id=item_id,
                          details=f"{item['inv_number']} — {data.get('name', item['name'])}")

    def archive(self, item_id: int) -> None:
        """Реквизит не удаляется физически — история аренд должна остаться."""
        from .auth_service import auth_service
        auth_service.require_login()

        if self.reserved_now(item_id):
            raise AppError("Предмет сейчас в аренде, его нельзя списать.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE inventory_items SET is_archived=1, status='archived', "
                "updated_at=datetime('now','localtime') WHERE id=?", (item_id,))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action) VALUES ('item',?,'Списан')",
                (item_id,))

    def get(self, item_id: int) -> Optional[dict]:
        row = get_db().query_one("SELECT * FROM inventory_items WHERE id=?", (item_id,))
        if not row:
            return None
        item = dict(row)
        from .categories_service import categories_service
        item["category_path"] = categories_service.path(item["category_id"])
        item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
        item["in_repair"] = self.in_repair(item_id)
        item["reserved_now"] = self.reserved_now(item_id)
        item["available_now"] = max(0, item["quantity_total"] - item["reserved_now"] - item["in_repair"])
        return item

    def get_by_inv_number(self, inv_number: str) -> Optional[dict]:
        row = get_db().query_one("SELECT id FROM inventory_items WHERE inv_number=?", (inv_number.strip(),))
        return self.get(row["id"]) if row else None

    # ----------------------------------------------------------------- поиск
    # Варианты сортировки каталога. Ключ хранится в настройках интерфейса,
    # поэтому переименовывать ключи нельзя — только добавлять новые.
    SORTS = {
        "name": ("По названию (А → Я)", "i.name COLLATE NOCASE, i.id"),
        "name_desc": ("По названию (Я → А)", "i.name COLLATE NOCASE DESC, i.id DESC"),
        "number": ("По инвентарному номеру", "CAST(i.inv_number AS INTEGER), i.inv_number"),
        "newest": ("Сначала новые", "i.created_at DESC, i.id DESC"),
        "updated": ("Недавно изменённые", "i.updated_at DESC, i.id DESC"),
        "price": ("Сначала дешёвые", "i.rental_price, i.name"),
        "price_desc": ("Сначала дорогие", "i.rental_price DESC, i.name"),
    }
    AVAILABILITY = {"": "Любая доступность", "available": "Есть свободные",
                    "rented": "Есть в аренде", "repair": "В ремонте",
                    "unavailable": "Нет свободных"}

    def busy_map(self) -> dict[int, int]:
        """
        Сколько единиц каждого предмета занято арендами сегодня — одним запросом.

        Раньше занятость считалась отдельным запросом на каждую карточку
        каталога. Правила те же, что в peak_reserved(): учитывается собственная
        дата выдачи позиции и фактические возвраты.
        """
        rows = get_db().query(
            "SELECT ri.item_id, ri.date_start, ri.date_returned, ri.quantity, ri.returned_qty, "
            "ri.status, r.date_start rental_start, r.date_end, r.id rental_id, r.number "
            "FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id "
            "WHERE r.status = 'active' AND ri.item_id IS NOT NULL")
        grouped: dict[int, list] = {}
        for row in rows:
            grouped.setdefault(row["item_id"], []).append(row)
        day = today()
        return {item_id: self._peak(self._intervals(item_rows), day, day)
                for item_id, item_rows in grouped.items()}

    def repair_map(self) -> dict[int, int]:
        """Сколько единиц каждого предмета сейчас в ремонте — одним запросом."""
        return {row["item_id"]: int(row["q"]) for row in get_db().query(
            "SELECT item_id, SUM(quantity) q FROM repairs WHERE status='open' GROUP BY item_id")}

    def _enrich(self, rows, busy: Optional[dict] = None, repair: Optional[dict] = None,
                with_extras: bool = True) -> list[dict]:
        """Дополняет строки каталога остатками, категорией, фото и отметками."""
        busy = self.busy_map() if busy is None else busy
        repair = self.repair_map() if repair is None else repair
        items = []
        for row in rows:
            item = dict(row)
            item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
            item["reserved_now"] = busy.get(item["id"], 0)
            item["in_repair"] = repair.get(item["id"], 0)
            item["available_now"] = max(
                0, item["quantity_total"] - item["reserved_now"] - item["in_repair"])
            items.append(item)
        if with_extras and items:
            self._attach_extras(items)
        return items

    def _attach_extras(self, items: list[dict]) -> None:
        """Путь категории, главная фотография, «в избранном» — пачкой, без запроса на карточку."""
        from .auth_service import auth_service
        from .categories_service import categories_service
        from .photo_service import thumb_path

        db = get_db()
        paths_by_id = categories_service.path_map()
        ids = [item["id"] for item in items]
        photos: dict[int, str] = {}
        counts: dict[int, int] = {}
        favorites: set[int] = set()
        user_id = auth_service.current_id()
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            marks = ",".join("?" * len(chunk))
            for row in db.query(
                    f"SELECT item_id, rel_path, COUNT(*) c FROM (SELECT item_id, rel_path "
                    f"FROM inventory_photos WHERE item_id IN ({marks}) "
                    f"ORDER BY is_primary DESC, sort_order) GROUP BY item_id", chunk):
                photos[row["item_id"]] = row["rel_path"]
                counts[row["item_id"]] = row["c"]
            if user_id:
                favorites.update(row["item_id"] for row in db.query(
                    f"SELECT item_id FROM item_favorites WHERE user_id=? "
                    f"AND item_id IN ({marks})", [user_id] + chunk))
        for item in items:
            item["category_path"] = paths_by_id.get(item["category_id"], "")
            rel = photos.get(item["id"])
            item["thumb_path"] = str(thumb_path(rel)) if rel else ""
            item["photo_rel_path"] = rel or ""
            item["photo_count"] = counts.get(item["id"], 0)
            item["is_favorite"] = item["id"] in favorites

    def search_page(self, text: str = "", category_id: Optional[int] = None,
                    availability: str = "", sort: str = "name", limit: int = 200,
                    offset: int = 0, favorites_only: bool = False,
                    has_photo: Optional[bool] = None, price_min: Optional[float] = None,
                    price_max: Optional[float] = None, archived: bool = False,
                    recent_only: bool = False) -> tuple[list[dict], int]:
        """
        Страница каталога и общее число найденного.

        Возвращает (предметы, сколько всего подходит под запрос). Каталог
        подгружает страницы по мере прокрутки, поэтому на складе в десять
        тысяч позиций в память попадает только то, что на экране.
        """
        from .auth_service import auth_service

        db = get_db()
        where = ["i.is_archived = ?"]
        params: list = [1 if archived else 0]
        joins = ""
        user_id = auth_service.current_id() or 0

        if category_id:
            from .categories_service import categories_service
            ids = categories_service.descendants(category_id)
            where.append(f"i.category_id IN ({','.join('?' * len(ids))})")
            params += ids
        if favorites_only:
            where.append("i.id IN (SELECT item_id FROM item_favorites WHERE user_id=?)")
            params.append(user_id)
        if has_photo is True:
            where.append("EXISTS (SELECT 1 FROM inventory_photos p WHERE p.item_id = i.id)")
        elif has_photo is False:
            where.append("NOT EXISTS (SELECT 1 FROM inventory_photos p WHERE p.item_id = i.id)")
        if price_min is not None:
            where.append("i.rental_price >= ?")
            params.append(float(price_min))
        if price_max is not None:
            where.append("i.rental_price <= ?")
            params.append(float(price_max))

        text = (text or "").strip()
        if text and not archived:
            # отсканированный штрихкод или точный инвентарный номер — сразу предмет
            from .barcode_service import barcode_service
            exact = barcode_service.find_item(text)
            if exact and not exact.get("is_archived"):
                row = db.query_one(
                    f"SELECT i.* FROM inventory_items i WHERE i.id=? AND {' AND '.join(where)}",
                    [exact["id"]] + params)
                if row:
                    found = self._enrich([row])
                    if self._availability_ok(found[0], availability):
                        return found, 1

        order = self.SORTS.get(sort, self.SORTS["name"])[1]
        if recent_only:
            joins += " JOIN item_views v ON v.item_id = i.id AND v.user_id = ?"
            params = [user_id] + params
            order = "v.viewed_at DESC"

        base = f"FROM inventory_items i{joins} WHERE {' AND '.join(where)}"
        if text:
            fts = _fts_query(text)
            like = f"%{text.lower()}%"
            # Полнотекстовый индекс ищет по началу слов; подстрока внутри слова
            # («машинка» по «шинк») добирается обычным сравнением без учёта
            # регистра кириллицы. Сначала идут совпадения по индексу.
            matched = set()
            if fts:
                try:
                    matched = {row["rowid"] for row in db.query(
                        "SELECT rowid FROM items_fts WHERE items_fts MATCH ?", (fts,))}
                except Exception:                                  # noqa: BLE001
                    log.warning("Полнотекстовый запрос не разобран: %r", text)
            candidates = db.query(
                f"SELECT i.id, i.quantity_total, i.status, rulower(i.name) n, "
                f"rulower(i.inv_number) num, rulower(i.keywords) k, rulower(i.description) d, "
                f"rulower(i.note) nt {base} ORDER BY {order}", params)
            ranked = []
            for position, row in enumerate(candidates):
                if row["id"] in matched:
                    rank = 0 if like.strip("%") in (row["n"] or "") else 1
                elif like.strip("%") in (row["n"] or "") or like.strip("%") in (row["num"] or ""):
                    rank = 2
                elif any(like.strip("%") in (row[key] or "") for key in ("k", "d", "nt")):
                    rank = 3
                else:
                    continue
                ranked.append((rank, position, row))
            if sort in ("", "relevance", "name"):
                ranked.sort(key=lambda entry: (entry[0], entry[1]))
            else:
                ranked.sort(key=lambda entry: entry[1])
            light = [entry[2] for entry in ranked]
        elif availability:
            light = db.query(f"SELECT i.id, i.quantity_total, i.status {base} ORDER BY {order}",
                             params)
        else:
            total = db.query_one(f"SELECT COUNT(*) c {base}", params)["c"]
            rows = db.query(f"SELECT i.* {base} ORDER BY {order} LIMIT ? OFFSET ?",
                            params + [limit, offset])
            return self._enrich(rows), int(total)

        busy = repair = None
        if availability:
            busy, repair = self.busy_map(), self.repair_map()
            light = [row for row in light if self._availability_ok({
                "quantity_total": row["quantity_total"], "status": row["status"],
                "reserved_now": busy.get(row["id"], 0), "in_repair": repair.get(row["id"], 0),
                "available_now": max(0, row["quantity_total"] - busy.get(row["id"], 0)
                                     - repair.get(row["id"], 0))}, availability)]
        total = len(light)
        page_ids = [row["id"] for row in light[offset:offset + limit]]
        if not page_ids:
            return [], total
        full = {row["id"]: row for row in db.query(
            f"SELECT * FROM inventory_items WHERE id IN ({','.join('?' * len(page_ids))})",
            page_ids)}
        return self._enrich([full[i] for i in page_ids if i in full], busy, repair), total

    @staticmethod
    def _availability_ok(item: dict, availability: str) -> bool:
        if not availability:
            return True
        if availability == "available":
            return item["available_now"] > 0
        if availability == "unavailable":
            return item["available_now"] <= 0
        if availability == "rented":
            return item["reserved_now"] > 0
        if availability == "repair":
            return item["in_repair"] > 0 or item.get("status") == "repair"
        return True

    def search(self, text: str = "", category_id: Optional[int] = None,
               only_available: bool = False, limit: int = 300, offset: int = 0) -> list[dict]:
        """Поиск по названию, номеру, описанию, примечанию и ключевым словам."""
        items, _total = self.search_page(
            text, category_id, "available" if only_available else "",
            sort="name", limit=limit, offset=offset)
        return items

    def matching_ids(self, **filters) -> list[int]:
        """Все id под текущий запрос каталога — для «выделить всё» и выгрузки."""
        filters.pop("limit", None)
        filters.pop("offset", None)
        ids: list[int] = []
        offset = 0
        while True:
            items, total = self.search_page(limit=2000, offset=offset, **filters)
            ids += [item["id"] for item in items]
            offset += 2000
            if offset >= total or not items:
                return ids

    # --------------------------------------------------- избранное и недавние
    def set_favorite(self, item_id: int, favorite: bool) -> None:
        from .auth_service import auth_service
        user = auth_service.require_login()
        db = get_db()
        if favorite:
            db.execute("INSERT OR IGNORE INTO item_favorites(user_id, item_id) VALUES (?,?)",
                       (user.id, item_id))
        else:
            db.execute("DELETE FROM item_favorites WHERE user_id=? AND item_id=?",
                       (user.id, item_id))

    def is_favorite(self, item_id: int) -> bool:
        from .auth_service import auth_service
        user_id = auth_service.current_id()
        if not user_id:
            return False
        return bool(get_db().query_one(
            "SELECT 1 FROM item_favorites WHERE user_id=? AND item_id=?", (user_id, item_id)))

    def favorites_count(self) -> int:
        from .auth_service import auth_service
        return get_db().query_one(
            "SELECT COUNT(*) c FROM item_favorites f JOIN inventory_items i ON i.id=f.item_id "
            "WHERE f.user_id=? AND i.is_archived=0", (auth_service.current_id() or 0,))["c"]

    RECENT_LIMIT = 60

    def mark_viewed(self, item_id: int) -> None:
        """Запоминает, что сотрудник открывал карточку. Хранятся последние 60."""
        from .auth_service import auth_service
        user_id = auth_service.current_id()
        if not user_id:
            return
        db = get_db()
        try:
            db.execute(
                "INSERT INTO item_views(user_id, item_id, viewed_at) "
                "VALUES (?,?,datetime('now','localtime')) "
                "ON CONFLICT(user_id, item_id) DO UPDATE SET viewed_at=excluded.viewed_at",
                (user_id, item_id))
            db.execute(
                "DELETE FROM item_views WHERE user_id=? AND item_id NOT IN ("
                "SELECT item_id FROM item_views WHERE user_id=? "
                "ORDER BY viewed_at DESC LIMIT ?)", (user_id, user_id, self.RECENT_LIMIT))
        except Exception:                                          # noqa: BLE001
            log.exception("Не удалось записать просмотр карточки")

    def recent(self, limit: int = 12) -> list[dict]:
        items, _ = self.search_page(recent_only=True, limit=limit)
        return items

    # ------------------------------------------------------ массовые действия
    def bulk_move(self, item_ids: list[int], category_id: Optional[int]) -> int:
        """Переносит предметы в другую категорию одной операцией."""
        from .audit_service import audit_service
        from .auth_service import auth_service
        from .categories_service import categories_service

        auth_service.require_login()
        ids = [int(i) for i in item_ids]
        if not ids:
            return 0
        if category_id and not categories_service.get(category_id):
            raise NotFoundError("Категория не найдена.")
        db = get_db()
        with db.transaction() as conn:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                conn.execute(
                    f"UPDATE inventory_items SET category_id=?, "
                    f"updated_at=datetime('now','localtime') "
                    f"WHERE id IN ({','.join('?' * len(chunk))})", [category_id] + chunk)
        target = categories_service.path(category_id) or "без категории"
        audit_service.log("Массовый перенос реквизита", entity="item",
                          details=f"{len(ids)} поз. → {target}")
        return len(ids)

    def bulk_update(self, item_ids: list[int], rental_price: Optional[float] = None,
                    valuation: Optional[float] = None, add_keywords: str = "") -> int:
        """Массовая правка: цена, оценка, добавление ключевых слов."""
        from .audit_service import audit_service
        from .auth_service import auth_service

        auth_service.require_login()
        ids = [int(i) for i in item_ids]
        if not ids:
            return 0
        if rental_price is not None and rental_price < 0:
            raise ValidationError("Цена не может быть отрицательной.")
        if valuation is not None and valuation < 0:
            raise ValidationError("Оценочная стоимость не может быть отрицательной.")
        extra = " ".join((add_keywords or "").split())
        db = get_db()
        with db.transaction() as conn:
            for item_id in ids:
                row = conn.execute("SELECT keywords FROM inventory_items WHERE id=?",
                                   (item_id,)).fetchone()
                if not row:
                    continue
                keywords = row["keywords"] or ""
                if extra:
                    have = keywords.split()
                    known = {word.lower() for word in have}
                    keywords = " ".join(
                        have + [w for w in extra.split() if w.lower() not in known])
                # незаданные поля остаются прежними: COALESCE берёт старое значение
                conn.execute(
                    "UPDATE inventory_items SET rental_price=COALESCE(?, rental_price), "
                    "valuation=COALESCE(?, valuation), keywords=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (None if rental_price is None else float(rental_price),
                     None if valuation is None else float(valuation), keywords, item_id))
        changes = [name for name, value in (("цена", rental_price), ("оценка", valuation),
                                            ("ключевые слова", extra)) if value not in (None, "")]
        audit_service.log("Массовая правка реквизита", entity="item",
                          details=f"{len(ids)} поз.: {', '.join(changes)}")
        return len(ids)

    def bulk_archive(self, item_ids: list[int]) -> tuple[int, list[str]]:
        """Списывает несколько предметов. Занятые в аренде пропускаются с объяснением."""
        done, skipped = 0, []
        for item_id in item_ids:
            try:
                self.archive(item_id)
                done += 1
            except AppError as exc:
                item = get_db().query_one(
                    "SELECT inv_number, name FROM inventory_items WHERE id=?", (item_id,))
                skipped.append(f"{item['inv_number']} {item['name']}: {exc}" if item else str(exc))
        return done, skipped

    def restore(self, item_id: int) -> None:
        """Возвращает списанный предмет в каталог."""
        from .audit_service import audit_service
        from .auth_service import auth_service

        auth_service.require_login()
        db = get_db()
        row = db.query_one("SELECT inv_number, name, is_archived FROM inventory_items WHERE id=?",
                           (item_id,))
        if not row:
            raise NotFoundError("Предмет не найден.")
        if not row["is_archived"]:
            return
        with db.transaction() as conn:
            conn.execute(
                "UPDATE inventory_items SET is_archived=0, status='in_stock', "
                "updated_at=datetime('now','localtime') WHERE id=?", (item_id,))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action) "
                "VALUES ('item',?,'Возвращён из списанных')", (item_id,))
        audit_service.log("Реквизит возвращён из списанных", entity="item", entity_id=item_id,
                          details=f"{row['inv_number']} — {row['name']}")

    def archived_count(self) -> int:
        return get_db().query_one(
            "SELECT COUNT(*) c FROM inventory_items WHERE is_archived=1")["c"]

    # ----------------------------------------------------------------- дубли
    def similar_by_name(self, name: str, exclude_id: Optional[int] = None,
                        limit: int = 5) -> list[dict]:
        """
        Предметы с таким же названием (без учёта регистра и лишних пробелов).

        Нужна, чтобы при добавлении реквизита предупредить: «такой уже есть».
        Одинаковые названия не запрещены — на складе бывает десять одинаковых
        стульев отдельными карточками, — поэтому это предупреждение, а не отказ.
        """
        key = self._name_key(name)
        if not key:
            return []
        rows = get_db().query(
            "SELECT id, inv_number, name, quantity_total FROM inventory_items "
            "WHERE is_archived=0 AND id <> ?", (exclude_id or 0,))
        return [dict(row) for row in rows if self._name_key(row["name"]) == key][:limit]

    @staticmethod
    def _name_key(name: str) -> str:
        """Название для сравнения: без регистра, лишних пробелов и «ё/е»."""
        return " ".join((name or "").lower().replace("ё", "е").split())

    def duplicate_groups(self, limit: int = 200) -> list[dict]:
        """Группы карточек с одинаковым названием — для проверки дублей."""
        groups: dict[str, list[dict]] = {}
        for row in get_db().query(
                "SELECT id, inv_number, name, quantity_total, category_id "
                "FROM inventory_items WHERE is_archived=0 ORDER BY inv_number"):
            groups.setdefault(self._name_key(row["name"]), []).append(dict(row))
        found = [{"reason": "Одинаковое название", "items": items}
                 for key, items in groups.items() if key and len(items) > 1]
        found.sort(key=lambda group: len(group["items"]), reverse=True)
        return found[:limit]

    def available_units(self) -> tuple[int, int]:
        """(всего экземпляров, свободно сейчас) — для сводки на главном экране."""
        db = get_db()
        total = db.query_one(
            "SELECT ifnull(SUM(quantity_total),0) q FROM inventory_items WHERE is_archived=0")["q"]
        rented = db.query_one(
            "SELECT ifnull(SUM(ri.quantity - ri.returned_qty),0) q FROM rental_items ri "
            "JOIN rentals r ON r.id = ri.rental_id WHERE r.status='active'")["q"]
        repair = db.query_one(
            "SELECT ifnull(SUM(quantity),0) q FROM repairs WHERE status='open'")["q"]
        return int(total), max(0, int(total) - int(rented) - int(repair))

    def count(self) -> int:
        return get_db().query_one("SELECT COUNT(*) c FROM inventory_items WHERE is_archived=0")["c"]

    # ---------------------------------------------------------- доступность
    def in_repair(self, item_id: int) -> int:
        row = get_db().query_one(
            "SELECT ifnull(SUM(quantity),0) q FROM repairs WHERE item_id=? AND status='open'",
            (item_id,))
        return int(row["q"])

    def reserved_now(self, item_id: int) -> int:
        """Сколько единиц занято действующими арендами на сегодня."""
        return self.peak_reserved(item_id, today(), today())

    def busy_intervals(self, item_id: int, exclude_rental_id: Optional[int] = None) -> list[tuple]:
        """
        Интервалы занятости предмета действующими арендами.

        Учитывается собственная дата выдачи каждой позиции (в версии 2.0 внутри
        одной аренды позиции могут быть выданы в разные дни) и фактические
        возвраты: возвращённое количество освобождается с даты возврата.
        """
        sql = ("SELECT ri.date_start, ri.date_returned, ri.quantity, ri.returned_qty, "
               "ri.status, r.date_start rental_start, r.date_end, r.id rental_id, r.number "
               "FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id "
               "WHERE ri.item_id = ? AND r.status = 'active'")
        params: list = [item_id]
        if exclude_rental_id:
            sql += " AND r.id <> ?"
            params.append(exclude_rental_id)
        return self._intervals(get_db().query(sql, params))

    @staticmethod
    def _intervals(rows) -> list[tuple]:
        """Строки позиций аренды → интервалы (начало, конец, количество, аренда, номер)."""
        out = []
        for row in rows:
            start = parse_date(row["date_start"]) or parse_date(row["rental_start"])
            planned_end = parse_date(row["date_end"]) or FAR_FUTURE
            remaining = max(0, row["quantity"] - row["returned_qty"])
            if remaining > 0:
                out.append((start, planned_end, remaining, row["rental_id"], row["number"]))
            returned = row["returned_qty"]
            if returned > 0:
                # возвращённая часть была занята только до дня возврата
                end = parse_date(row["date_returned"]) or planned_end
                out.append((start, min(end, planned_end), returned, row["rental_id"], row["number"]))
        return out

    def peak_reserved(self, item_id: int, date_start: date, date_end: Optional[date],
                      exclude_rental_id: Optional[int] = None) -> int:
        """
        Максимальное одновременное количество, занятое арендами внутри периода.

        Считается методом заметающей прямой по дням: если две аренды не пересекаются
        между собой, они не складываются — блокируется только реальный пик.
        """
        return self._peak(self.busy_intervals(item_id, exclude_rental_id), date_start, date_end)

    @staticmethod
    def _peak(intervals: list[tuple], date_start: date, date_end: Optional[date]) -> int:
        from ..utils import add_days

        end = date_end or FAR_FUTURE
        deltas: dict[date, int] = {}
        for s, e, qty, _rid, _num in intervals:
            s2, e2 = max(s, date_start), min(e, end)
            if s2 > e2:
                continue
            deltas[s2] = deltas.get(s2, 0) + qty            # занято с этого дня
            free_day = add_days(e2, 1) if e2 < FAR_FUTURE else FAR_FUTURE
            deltas[free_day] = deltas.get(free_day, 0) - qty  # освободилось на следующий день
        current = peak = 0
        for day in sorted(deltas):
            current += deltas[day]
            peak = max(peak, current)
        return peak

    def available_for_period(self, item_id: int, date_start: date, date_end: Optional[date],
                             exclude_rental_id: Optional[int] = None) -> int:
        item = get_db().query_one(
            "SELECT quantity_total, status FROM inventory_items WHERE id=?", (item_id,))
        if not item:
            raise NotFoundError("Предмет не найден.")
        if item["status"] == "archived":
            return 0
        busy = self.peak_reserved(item_id, date_start, date_end, exclude_rental_id)
        return max(0, item["quantity_total"] - busy - self.in_repair(item_id))

    def conflicting_rentals(self, item_id: int, date_start: date, date_end: Optional[date],
                            exclude_rental_id: Optional[int] = None) -> list[dict]:
        """Аренды, из-за которых предмет занят в этот период (для понятного сообщения)."""
        from ..utils import overlaps
        out = []
        for s, e, qty, rid, num in self.busy_intervals(item_id, exclude_rental_id):
            if overlaps(s, e, date_start, date_end):
                out.append({"rental_id": rid, "number": num, "quantity": qty,
                            "date_start": s, "date_end": None if e == FAR_FUTURE else e})
        return out

    # -------------------------------------------------------------- история
    def history(self, item_id: int) -> list[dict]:
        """Полная история предмета: аренды, ремонты, изменения карточки."""
        db = get_db()
        rows = db.query(
            "SELECT r.number, r.date_start, r.date_end, r.status, ri.quantity, ri.price, "
            "ri.amount, ri.note, c.full_name client, p.name project, pr.name production, r.id rental_id "
            "FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id "
            "LEFT JOIN clients c ON c.id = r.client_id "
            "LEFT JOIN projects p ON p.id = r.project_id "
            "LEFT JOIN productions pr ON pr.id = r.production_id "
            "WHERE ri.item_id = ? ORDER BY r.date_start DESC", (item_id,))
        return [dict(r) for r in rows]


inventory_service = InventoryService()
