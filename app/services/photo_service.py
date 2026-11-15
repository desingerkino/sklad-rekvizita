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
THUMB_SIZE = (480, 480)      # миниатюра каталога: хватает и для экранов Retina / 150 %
MAX_SIDE = 1920              # рабочая копия для просмотра в программе
ORIGINAL_SUFFIX = "_orig"    # исходный файл лежит рядом и не пересжимается

# Форматы фотографий, которые принимает программа. Один список на все окна
# выбора файла, перетаскивание и импорт из папки. Регистр расширения не важен:
# айфон пишет IMG_0001.HEIC, камера — DSC_0001.JPG.
PHOTO_SUFFIXES = (".jpg", ".jpeg", ".heic", ".heif", ".png", ".webp", ".bmp")


def photo_filter() -> str:
    """Фильтр для окна выбора файла: расширения в обоих регистрах."""
    patterns = []
    for suffix in PHOTO_SUFFIXES:
        patterns += [f"*{suffix}", f"*{suffix.upper()}"]
    return f"Фотографии — JPG, JPEG, HEIC, PNG, WEBP, BMP ({' '.join(patterns)});;Все файлы (*)"


def is_photo_file(path) -> bool:
    return Path(path).suffix.lower() in PHOTO_SUFFIXES


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


def file_sha1(path: Path) -> str:
    """Отпечаток файла, читается кусками: исходники с камеры бывают по 20–50 МБ."""
    digest = hashlib.sha1()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_thumb(rel_path: str) -> Optional[Path]:
    """
    Возвращает путь к миниатюре, создавая её, если файла нет.

    Миниатюра могла пропасть (ручная чистка папки, восстановление из архива
    без миниатюр). Каталог от этого не должен показывать пустые карточки:
    миниатюра — производный файл и всегда восстанавливается из фотографии.
    """
    target = thumb_path(rel_path)
    if target.exists():
        return target
    source = abs_path(rel_path)
    if not source.exists():
        return None
    try:
        from PIL import Image

        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail(THUMB_SIZE)
            image.save(target, "JPEG", quality=82, optimize=True)
        return target
    except Exception:                                              # noqa: BLE001
        log.exception("Не удалось восстановить миниатюру %s", rel_path)
        return None


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
        stem = uuid.uuid4().hex
        rel = f"{rel_dir}/{stem}.jpg"
        dst = abs_path(rel)

        width = height = 0
        try:
            from PIL import ImageOps

            with Image.open(src) as img:
                # Телефон и камера часто пишут снимок «лёжа» и отдельно отметку,
                # как его повернуть. Без этого шага вертикальные кадры попадали
                # в каталог повёрнутыми набок.
                img = ImageOps.exif_transpose(img) or img
                width, height = img.size
                img = img.convert("RGB")
                img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
                img.save(dst, "JPEG", quality=90, optimize=True)
                thumb = img.copy()
                thumb.thumbnail(THUMB_SIZE, Image.LANCZOS)
                thumb.save(thumb_path(rel), "JPEG", quality=82, optimize=True)
        except Exception as exc:
            log.exception("Не удалось обработать изображение")
            for leftover in (dst, thumb_path(rel)):
                leftover.unlink(missing_ok=True)
            formats = "JPG, PNG, BMP, WEBP" + (", HEIC" if enable_heic() else "")
            raise AppError(f"Не удалось прочитать изображение. Поддерживаются {formats}.") from exc

        # Исходный файл сохраняется как есть, байт в байт: рабочая копия выше
        # нужна для быстрого показа, а оригинал — чтобы качество снимка не
        # терялось при хранении. Сбой копирования оригинала не отменяет
        # добавление: рабочая копия уже есть, о проблеме пишется в журнал.
        orig_rel = ""
        src_sha1 = ""
        try:
            src_sha1 = file_sha1(src)
            suffix = src.suffix.lower() if src.suffix else ".jpg"
            orig_rel = f"{rel_dir}/{stem}{ORIGINAL_SUFFIX}{suffix}"
            shutil.copyfile(src, abs_path(orig_rel))
        except OSError:
            log.exception("Не удалось сохранить исходный файл фотографии %s", src)
            orig_rel = ""

        sha1 = file_sha1(dst)
        order = db.query_one(
            "SELECT ifnull(MAX(sort_order),-1)+1 n FROM inventory_photos WHERE item_id=?", (item_id,)
        )["n"]
        has_primary = db.query_one(
            "SELECT COUNT(*) c FROM inventory_photos WHERE item_id=? AND is_primary=1", (item_id,)
        )["c"]
        is_primary = 1 if (make_primary or not has_primary) else 0

        try:
            with db.transaction() as conn:
                if is_primary:
                    conn.execute("UPDATE inventory_photos SET is_primary=0 WHERE item_id=?",
                                 (item_id,))
                cur = conn.execute(
                    "INSERT INTO inventory_photos(item_id, rel_path, kind, is_primary, "
                    "sort_order, sha1, orig_rel_path, src_sha1, width, height) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (item_id, rel, kind, is_primary, order, sha1, orig_rel, src_sha1,
                     width, height),
                )
                photo_id = int(cur.lastrowid)
        except Exception:
            # запись не появилась — файлы без записи не должны копиться на диске
            for leftover in (dst, thumb_path(rel), abs_path(orig_rel) if orig_rel else None):
                if leftover:
                    leftover.unlink(missing_ok=True)
            raise

        # индексация для поиска по фото — вне транзакции, ошибка индексации
        # не должна отменять сохранение самой фотографии
        try:
            from .image_search import image_search
            image_search.index_photo(photo_id, item_id, dst)
        except Exception:
            log.exception("Не удалось проиндексировать фото %s", photo_id)
        return photo_id

    def find_same_file(self, source: Path | str, exclude_item_id: Optional[int] = None) -> list[dict]:
        """
        Где этот же файл уже загружен: [{item_id, inv_number, name}].

        Сравнение по отпечатку исходного файла, поэтому находит именно тот же
        снимок, а не «похожий». Используется для предупреждения о дубле.
        """
        try:
            digest = file_sha1(Path(source))
        except OSError:
            return []
        rows = get_db().query(
            "SELECT DISTINCT i.id item_id, i.inv_number, i.name FROM inventory_photos p "
            "JOIN inventory_items i ON i.id = p.item_id "
            "WHERE p.src_sha1 = ? AND i.is_archived = 0 AND i.id <> ?",
            (digest, exclude_item_id or 0))
        return [dict(row) for row in rows]

    def original_path(self, photo: dict) -> Optional[Path]:
        """Путь к исходному файлу фотографии или None, если он не сохранялся."""
        rel = photo.get("orig_rel_path") or ""
        if not rel:
            return None
        path = abs_path(rel)
        return path if path.exists() else None

    def reorder(self, item_id: int, photo_ids: list[int]) -> None:
        """Задаёт порядок фотографий в карточке."""
        from .auth_service import auth_service
        auth_service.require_login()
        db = get_db()
        with db.transaction() as conn:
            for position, photo_id in enumerate(photo_ids):
                conn.execute(
                    "UPDATE inventory_photos SET sort_order=? WHERE id=? AND item_id=?",
                    (position, photo_id, item_id))

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
        row = get_db().query_one(
            "SELECT * FROM inventory_photos WHERE item_id=? "
            "ORDER BY is_primary DESC, sort_order LIMIT 1", (item_id,))
        if not row:
            return None
        photo = dict(row)
        photo["abs_path"] = str(abs_path(row["rel_path"]))
        photo["thumb_path"] = str(thumb_path(row["rel_path"]))
        return photo

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
            if row["is_primary"]:
                # главной становится следующая по порядку: иначе у предмета с
                # фотографиями в каталоге оставалась бы пустая карточка
                conn.execute(
                    "UPDATE inventory_photos SET is_primary=1 WHERE id = ("
                    "SELECT id FROM inventory_photos WHERE item_id=? "
                    "ORDER BY sort_order LIMIT 1)", (row["item_id"],))
        db.log_history("item", row["item_id"], "Удалена фотография", row["rel_path"])
        files = [abs_path(row["rel_path"]), thumb_path(row["rel_path"])]
        original = (row["orig_rel_path"] if "orig_rel_path" in row.keys() else "") or ""
        if original:
            files.append(abs_path(original))
        for p in files:
            try:
                if p.exists():
                    # файл не стираем безвозвратно, а переносим в корзину хранилища
                    trash = paths.PHOTOS_DIR / "_trash"
                    trash.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(trash / p.name))
            except OSError:
                log.warning("Не удалось убрать файл фотографии %s", p)


photo_service = PhotoService()
