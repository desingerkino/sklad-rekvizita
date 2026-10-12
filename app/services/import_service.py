# -*- coding: utf-8 -*-
"""
Импорт реквизита из папки с фотографиями.

Задача простая и очень частая: до программы склад жил в виде альбомов с
фотографиями, у каждой фотографии — подпись, альбомы разложены по темам.
Этот модуль превращает такую папку в карточки реквизита: подпапки становятся
категориями, файлы — предметами с фотографиями, подписи — названиями.

Правила разбора (намеренно простые, чтобы их можно было объяснить словами):

  • Вложенные папки → дерево категорий. «Мебель/Стулья/венский.jpg» попадёт
    в категорию «Мебель / Стулья». Существующие категории переиспользуются,
    новые создаются.
  • Имя файла → название предмета. Служебные хвосты вида «_1», «-2», « (3)»
    отбрасываются, и такие файлы считаются фотографиями одного предмета:
    «стул венский.jpg», «стул венский_2.jpg» — один предмет с двумя фото.
  • Файл подписей (captions.txt / подписи.txt / *.csv) рядом с фотографиями
    переопределяет названия и может задать количество и описание.

Импорт ничего не удаляет и не перезаписывает. Предмет с таким же названием в
той же категории считается уже существующим: к нему можно добавить фотографии
или пропустить его целиком — это решает вызывающий код.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from ..db.database import get_db
from ..errors import AppError, ValidationError
from .audit_service import audit_service
from .auth_service import auth_service
from .categories_service import categories_service
from .inventory_service import inventory_service
from .photo_service import photo_service

log = logging.getLogger(__name__)

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif",
                  ".tif", ".tiff"}
CAPTION_NAMES = {"captions.txt", "подписи.txt", "captions.csv", "подписи.csv",
                 "названия.txt", "названия.csv"}
MAX_PHOTOS_PER_ITEM = 8

# хвосты, которыми обычно нумеруют кадры одного предмета
_TAIL = re.compile(r"[\s_\-–—]*(?:\(\d{1,3}\)|\[\d{1,3}\]|\d{1,3})$")
# мусор от камер и мессенджеров: IMG_1234, photo_2024-05-01, DSC00123
_CAMERA_NAME = re.compile(
    r"^(?:img|image|photo|foto|dsc|dscn|p|pxl|screenshot|снимок|фото)[\s_\-]*[\d\-_]*$",
    re.IGNORECASE)


@dataclass
class ImportItem:
    """Один будущий предмет: название, категория и его фотографии."""
    name: str
    category_path: str
    photos: list[Path] = field(default_factory=list)
    quantity: int = 1
    description: str = ""
    exists: bool = False           # такой предмет уже есть в базе
    item_id: Optional[int] = None


@dataclass
class ImportPlan:
    """
    Что программа собирается сделать. Показывается пользователю ДО импорта:
    массовое создание карточек вслепую — прямой путь к разгребанию вручную.
    """
    items: list[ImportItem] = field(default_factory=list)
    new_categories: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    root: Optional[Path] = None

    @property
    def new_items(self) -> list[ImportItem]:
        return [i for i in self.items if not i.exists]

    @property
    def existing_items(self) -> list[ImportItem]:
        return [i for i in self.items if i.exists]

    @property
    def photo_count(self) -> int:
        return sum(len(i.photos) for i in self.items)


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    photos: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class ImportService:
    # ------------------------------------------------------------- разбор
    def scan(self, folder: Path | str, use_folder_names: bool = True) -> ImportPlan:
        """
        Читает папку и составляет план импорта, ничего не меняя в базе.
        """
        root = Path(folder)
        if not root.is_dir():
            raise ValidationError("Такой папки нет. Выберите папку с фотографиями.")

        plan = ImportPlan(root=root)
        known_categories = {self._normalize(c["path"]): c["id"]
                            for c in self._category_paths()}
        grouped: dict[tuple[str, str], ImportItem] = {}
        # Подписи читаются до разбора имён: файл может называться IMG_0001, и
        # только подпись говорит, что это за предмет. Если разбирать имена
        # первыми, такой файл отсеется раньше, чем до него дойдёт подпись.
        captions, caption_files = self._read_captions(root)
        used_captions: set[str] = set()

        for path in sorted(root.rglob("*")):
            if path.is_dir() or path.name.startswith("."):
                continue
            if path.name.lower() in CAPTION_NAMES:
                continue
            if path.suffix.lower() not in PHOTO_SUFFIXES:
                plan.skipped_files.append(f"{path.name} — не фотография")
                continue

            relative = path.parent.relative_to(root)
            category_path = self.SEPARATOR.join(relative.parts) if use_folder_names else ""
            caption = captions.get(path.name.lower())
            if caption:
                used_captions.add(path.name.lower())
                name, quantity, description = caption
            else:
                name, quantity, description = self._item_name(path), 1, ""
            if not name:
                plan.skipped_files.append(
                    f"{path.name} — из имени файла не понять, что это за предмет. "
                    "Переименуйте файл или добавьте подпись.")
                continue

            key = (self._normalize(category_path), self._normalize(name))
            item = grouped.get(key)
            if item is None:
                item = ImportItem(name=name, category_path=category_path,
                                  quantity=quantity, description=description)
                grouped[key] = item
                plan.items.append(item)
            if len(item.photos) < MAX_PHOTOS_PER_ITEM:
                item.photos.append(path)
            else:
                plan.skipped_files.append(
                    f"{path.name} — у предмета «{item.name}» уже "
                    f"{MAX_PHOTOS_PER_ITEM} фотографий")

        for name in sorted(set(captions) - used_captions):
            plan.skipped_files.append(
                f"подпись «{name}» — такой фотографии в папке нет")
        plan.skipped_files.extend(caption_files)

        # какие категории придётся создать и что уже есть в базе
        needed = {i.category_path for i in plan.items if i.category_path}
        for category_path in sorted(needed):
            if self._normalize(category_path) not in known_categories:
                plan.new_categories.append(category_path)
        for item in plan.items:
            item.exists, item.item_id = self._find_existing(item, known_categories)

        log.info("План импорта из %s: предметов %s, фотографий %s, новых категорий %s",
                 root, len(plan.items), plan.photo_count, len(plan.new_categories))
        return plan

    # ------------------------------------------------------------- импорт
    def apply(self, plan: ImportPlan, add_photos_to_existing: bool = True,
              progress: Optional[Callable[[int, int, str], None]] = None) -> ImportResult:
        """
        Выполняет план. Каждый предмет — отдельная операция: если один файл
        окажется битым, остальные всё равно импортируются, а сбой попадёт в
        список ошибок, а не оборвёт весь импорт на середине.
        """
        auth_service.require_login()
        result = ImportResult()
        total = len(plan.items)
        categories: dict[str, Optional[int]] = {}

        for index, item in enumerate(plan.items, start=1):
            if progress:
                progress(index, total, item.name)
            try:
                category_id = self._ensure_category(item.category_path, categories)
                if item.exists and item.item_id:
                    if not add_photos_to_existing:
                        result.skipped += 1
                        continue
                    item_id = item.item_id
                    result.updated += 1
                else:
                    item_id = inventory_service.create({
                        "name": item.name,
                        "category_id": category_id,
                        "quantity_total": max(1, item.quantity),
                        "description": item.description,
                    })
                    result.created += 1

                existing_photos = len(photo_service.photos_of(item_id))
                for number, photo in enumerate(item.photos):
                    try:
                        photo_service.add_photo(
                            item_id, photo, kind="main" if number == 0 else "other",
                            make_primary=(number == 0 and existing_photos == 0))
                        result.photos += 1
                    except Exception as exc:                        # noqa: BLE001
                        result.errors.append(f"{photo.name}: {exc}")
            except Exception as exc:                                # noqa: BLE001
                result.errors.append(f"«{item.name}»: {exc}")
                log.exception("Импорт предмета «%s» не удался", item.name)

        audit_service.log(
            "Импорт реквизита из папки",
            details=(f"создано {result.created}, дополнено {result.updated}, "
                     f"фотографий {result.photos}"
                     + (f", ошибок {len(result.errors)}" if result.errors else "")))
        log.info("Импорт завершён: %s", result)
        return result

    # ------------------------------------------------------- вспомогательное
    SEPARATOR = " / "          # так categories_service.path() собирает путь

    @classmethod
    def _normalize(cls, text: str) -> str:
        """
        Приведение пути или названия к виду для сравнения: без регистра, без
        «ё», с одним и тем же разделителем. Иначе «Мебель / Стулья» из базы и
        «Мебель → Стулья» из папки выглядели бы разными категориями, и импорт
        создавал бы дубли рядом с существующими.
        """
        text = (text or "").replace("→", "/").replace("\\", "/")
        parts = [part.strip() for part in text.split("/") if part.strip()]
        joined = cls.SEPARATOR.join(parts) if parts else text
        return " ".join(joined.lower().replace("ё", "е").split())

    def _item_name(self, path: Path) -> str:
        """
        Название предмета из имени файла: убираем расширение, номер кадра и
        приводим первую букву к заглавной. Имена вроде IMG_1234 не годятся —
        по ним не понять, что за предмет, и такой файл лучше пропустить.
        """
        stem = path.stem.strip()
        if _CAMERA_NAME.match(stem):
            return ""
        stem = _TAIL.sub("", stem).strip(" _-–—")
        stem = " ".join(stem.replace("_", " ").split())
        if not stem or len(stem) < 2:
            return ""
        return stem[0].upper() + stem[1:]

    def _read_captions(self, root: Path) -> tuple[dict[str, tuple[str, int, str]], list[str]]:
        """
        Читает файлы подписей. Возвращает соответствие «имя файла → (название,
        количество, описание)» и список замечаний.

        Формат — по одной записи в строке: «имя файла; название; количество;
        описание». Разделитель — точка с запятой, запятая или табуляция;
        количество и описание необязательны.
        """
        captions: dict[str, tuple[str, int, str]] = {}
        notes: list[str] = []
        for caption_file in sorted(root.rglob("*")):
            if caption_file.name.lower() not in CAPTION_NAMES:
                continue
            try:
                text = caption_file.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                notes.append(f"{caption_file.name} — файл подписей не читается")
                continue
            delimiter = ";" if ";" in text else ("\t" if "\t" in text else ",")
            for row in csv.reader(text.splitlines(), delimiter=delimiter):
                cells = [c.strip() for c in row if c is not None]
                if len(cells) < 2 or not cells[0] or not cells[1]:
                    continue
                quantity = int(cells[2]) if len(cells) > 2 and cells[2].isdigit() else 1
                description = cells[3] if len(cells) > 3 else ""
                captions[cells[0].lower()] = (cells[1], max(1, quantity), description)
        return captions, notes

    def _category_paths(self) -> list[dict]:
        """Плоский список категорий с полным путём вида «Мебель / Стулья»."""
        out = []
        for category in categories_service.all():
            out.append({"id": category["id"],
                        "path": categories_service.path(category["id"])})
        return out

    def _find_existing(self, item: ImportItem,
                       known: dict[str, int]) -> tuple[bool, Optional[int]]:
        """Есть ли уже такой предмет: то же название в той же категории."""
        category_id = known.get(self._normalize(item.category_path))
        row = get_db().query_one(
            "SELECT id FROM inventory_items WHERE lower(name)=lower(?) "
            "AND ifnull(category_id,0)=ifnull(?,0) AND is_archived=0",
            (item.name, category_id))
        return (True, row["id"]) if row else (False, None)

    def _ensure_category(self, category_path: str,
                         cache: dict[str, Optional[int]]) -> Optional[int]:
        """Находит категорию по пути, создавая недостающие уровни."""
        if not category_path:
            return None
        if category_path in cache:
            return cache[category_path]

        known = {self._normalize(c["path"]): c["id"] for c in self._category_paths()}
        parent_id: Optional[int] = None
        walked: list[str] = []
        for part in [p.strip() for p in category_path.split("/") if p.strip()]:
            walked.append(part)
            key = self._normalize(self.SEPARATOR.join(walked))
            if key in known:
                parent_id = known[key]
                continue
            parent_id = categories_service.add(part, parent_id)
            known[key] = parent_id
        cache[category_path] = parent_id
        return parent_id


import_service = ImportService()
