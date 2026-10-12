# -*- coding: utf-8 -*-
"""
Хранилище фотографий.

Файлы лежат в пользовательской папке данных (%LOCALAPPDATA%), в БД — только
относительные пути вида 2026/000123/ab12cd.jpg. Перенос или переименование
папки приложения ссылки не ломает: абсолютный путь вычисляется в runtime.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path
from typing import Optional

from .. import paths
from ..db.database import get_db
from ..errors import AppError, NotFoundError, ValidationError

log = logging.getLogger(__name__)

# Ракурсы фотографии. Интерфейс берёт подписи отсюда же, чтобы список
# нельзя было рассинхронизировать между экраном и базой.
PHOTO_KINDS = {
    "main": "Основное",
    "front": "Вид спереди",
    "side": "Вид сбоку",
    "detail": "Детали",
    "damage": "Повреждения",
    "other": "Прочее",
}
THUMB_SIZE = (320, 320)
MAX_SIDE = 1920


_HEIF_READY: Optional[bool] = None


def enable_heic() -> bool:
    """
    Подключает поддержку HEIC/HEIF — формата, в котором снимает айфон.

    Pillow сам такие файлы не открывает, нужна библиотека pillow-heif. Она
    входит в зависимости программы; если её почему-то нет, HEIC просто не
    откроется, а остальные форматы продолжат работать как раньше.
    Регистрация делается один раз за запуск.
    """
    global _HEIF_READY
    if _HEIF_READY is not None:
        return _HEIF_READY
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
        _HEIF_READY = True
        log.info("Поддержка HEIC включена")
    except Exception:                                          # noqa: BLE001
        _HEIF_READY = False
        log.warning("Библиотека pillow-heif недоступна — файлы HEIC читаться не будут")
    return _HEIF_READY


def abs_path(rel_path: str) -> Path:
    return paths.PHOTOS_DIR / rel_path


def thumb_path(rel_path: str) -> Path:
    p = abs_path(rel_path)
    return p.with_name(p.stem + "_thumb" + p.suffix)


class PhotoService:
    def add_photo(self, item_id: int, source: Path | str, kind: str = "other",
                  make_primary: bool = False) -> int:
        """Копирует файл в хранилище, ужимает, делает миниатюру и считает вектор."""
        from .auth_service import auth_service
        auth_service.require_login()
        from PIL import Image  # локальный импорт: PIL нужен только при работе с фото

        src = Path(source)
        if not src.exists():
            raise AppError(f"Файл не найден: {src}")
        if src.suffix.lower() in (".heic", ".heif") and not enable_heic():
            raise AppError(
                "Файлы HEIC (снимки с айфона) сейчас не читаются: не установлена "
                "библиотека pillow-heif. Пересохраните фотографию в JPEG или "
                "переустановите программу.")
        enable_heic()

        db = get_db()
        item = db.query_one("SELECT inv_number FROM inventory_items WHERE id=?", (item_id,))
        if not item:
            raise AppError("Предмет не найден.")

        from ..utils import today
        rel_dir = f"{today().year}/{item['inv_number']}"
        (paths.PHOTOS_DIR / rel_dir).mkdir(parents=True, exist_ok=True)
        rel = f"{rel_dir}/{uuid.uuid4().hex}.jpg"
        dst = abs_path(rel)

        try:
            with Image.open(src) as img:
                img = img.convert("RGB")
                img.thumbnail((MAX_SIDE, MAX_SIDE))
                img.save(dst, "JPEG", quality=88, optimize=True)
                thumb = img.copy()
                thumb.thumbnail(THUMB_SIZE)
                thumb.save(thumb_path(rel), "JPEG", quality=80, optimize=True)
        except Exception as exc:
            log.exception("Не удалось обработать изображение")
            formats = "JPG, PNG, BMP, WEBP" + (", HEIC" if enable_heic() else "")
            raise AppError(f"Не удалось прочитать изображение. Поддерживаются {formats}.") from exc

        sha1 = hashlib.sha1(dst.read_bytes()).hexdigest()
        order = db.query_one(
            "SELECT ifnull(MAX(sort_order),-1)+1 n FROM inventory_photos WHERE item_id=?", (item_id,)
        )["n"]
        has_primary = db.query_one(
            "SELECT COUNT(*) c FROM inventory_photos WHERE item_id=? AND is_primary=1", (item_id,)
        )["c"]
        is_primary = 1 if (make_primary or not has_primary) else 0

        with db.transaction() as conn:
            if is_primary:
                conn.execute("UPDATE inventory_photos SET is_primary=0 WHERE item_id=?", (item_id,))
            cur = conn.execute(
                "INSERT INTO inventory_photos(item_id, rel_path, kind, is_primary, sort_order, sha1) "
                "VALUES (?,?,?,?,?,?)",
                (item_id, rel, kind, is_primary, order, sha1),
            )
            photo_id = int(cur.lastrowid)

        # индексация для поиска по фото — вне транзакции, ошибка индексации
        # не должна отменять сохранение самой фотографии
        try:
            from .image_search import image_search
            image_search.index_photo(photo_id, item_id, dst)
        except Exception:
            log.exception("Не удалось проиндексировать фото %s", photo_id)
        return photo_id

    def photos_of(self, item_id: int) -> list[dict]:
        rows = get_db().query(
            "SELECT * FROM inventory_photos WHERE item_id=? ORDER BY is_primary DESC, sort_order",
            (item_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["abs_path"] = str(abs_path(r["rel_path"]))
            d["thumb_path"] = str(thumb_path(r["rel_path"]))
            out.append(d)
        return out

    def primary_photo(self, item_id: int) -> Optional[dict]:
        photos = self.photos_of(item_id)
        return photos[0] if photos else None

    def set_primary(self, photo_id: int) -> None:
        db = get_db()
        row = db.query_one("SELECT item_id FROM inventory_photos WHERE id=?", (photo_id,))
        if not row:
            return
        with db.transaction() as conn:
            conn.execute("UPDATE inventory_photos SET is_primary=0 WHERE item_id=?", (row["item_id"],))
            conn.execute("UPDATE inventory_photos SET is_primary=1 WHERE id=?", (photo_id,))

    def set_kind(self, photo_id: int, kind: str) -> None:
        """
        Меняет ракурс уже загруженной фотографии.

        Ракурс проставляют при загрузке, но чаще всего фотографии заливают
        пачкой, а разбирают потом. Без этого метода кнопки «Детали» и
        «Повреждения» в карточке оставались бы навсегда серыми.
        """
        from .auth_service import auth_service

        auth_service.require_login()
        if kind not in PHOTO_KINDS:
            raise ValidationError(f"Неизвестный ракурс: {kind}")
        db = get_db()
        row = db.query_one("SELECT item_id FROM inventory_photos WHERE id=?", (photo_id,))
        if not row:
            raise NotFoundError("Фотография не найдена.")
        with db.transaction() as conn:
            conn.execute("UPDATE inventory_photos SET kind=? WHERE id=?", (kind, photo_id))
        db.log_history("item", row["item_id"], "Изменён ракурс фотографии", kind)

    def delete_photo(self, photo_id: int) -> None:
        from .auth_service import auth_service
        auth_service.require_login()
        db = get_db()
        row = db.query_one("SELECT * FROM inventory_photos WHERE id=?", (photo_id,))
        if not row:
            return
        with db.transaction() as conn:
            conn.execute("DELETE FROM inventory_photos WHERE id=?", (photo_id,))
        for p in (abs_path(row["rel_path"]), thumb_path(row["rel_path"])):
            try:
                if p.exists():
                    # файл не стираем безвозвратно, а переносим в корзину хранилища
                    trash = paths.PHOTOS_DIR / "_trash"
                    trash.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(trash / p.name))
            except OSError:
                log.warning("Не удалось убрать файл фотографии %s", p)


photo_service = PhotoService()
