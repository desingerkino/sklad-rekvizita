# -*- coding: utf-8 -*-
"""
Проверки версии 4.0 — сервисный слой (без окна).

Запуск:  python -m tests.test_v40

Каждая проверка работает на настоящей базе SQLite во временной папке и на
настоящих файлах изображений; данные пользователя не затрагиваются.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SKLAD_DATA_DIR"] = tempfile.mkdtemp(prefix="sklad_v40_")
os.environ["SKLAD_KEY_DIR"] = tempfile.mkdtemp(prefix="sklad_v40key_")

PASSED = FAILED = 0


def check(name: str, condition: bool, details: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {details}")


def make_image(path: Path, size=(640, 480), color=(180, 60, 40), fmt=None, exif_orientation=None):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, (230, 230, 225))
    draw = ImageDraw.Draw(image)
    w, h = size
    draw.rectangle([w * 0.2, h * 0.2, w * 0.6, h * 0.8], fill=color)
    draw.ellipse([w * 0.55, h * 0.1, w * 0.9, h * 0.5], fill=(40, 40, 60))
    kwargs = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    image.save(path, fmt, **kwargs) if fmt else image.save(path, **kwargs)
    return path


def main() -> int:
    from app import paths
    from app.db.database import Database, get_db, set_db
    from app.db.seed import seed_if_empty
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.image_search import (dominant_colors, image_search, similarity_level,
                                           similarity_percent)
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import (abs_path, enable_heic, ensure_thumb, is_photo_file,
                                            photo_filter, photo_service, thumb_path)
    from app.version import DB_SCHEMA_VERSION, VERSION

    paths.ensure_dirs()
    db = Database(paths.DATA_DIR / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Михаил", "admin", "Пароль!2026", "Заведующий")
    auth_service.login("admin", "Пароль!2026")
    work = Path(tempfile.mkdtemp(prefix="sklad_v40src_"))

    print("\n=== Версия и схема ===")
    check("Версия программы — 4.0", VERSION == "4.0", VERSION)
    check("Версия схемы базы — 9", DB_SCHEMA_VERSION == 9
          and db.query_one("PRAGMA user_version")[0] == 9)
    tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    check("Таблицы избранного и недавних созданы",
          {"item_favorites", "item_views"} <= tables)
    columns = {r["name"] for r in db.query("PRAGMA table_info(inventory_photos)")}
    check("У фотографии есть поля исходного файла",
          {"orig_rel_path", "src_sha1", "width", "height"} <= columns)

    print("\n=== Форматы фотографий: JPG, JPEG, HEIC ===")
    furniture = categories_service.add("Тест мебель")
    chairs = categories_service.add("Тест стулья", furniture)
    vienna = categories_service.add("Тест венские", chairs)
    item = inventory_service.create({"name": "Стул венский", "quantity_total": 3,
                                     "category_id": vienna, "rental_price": 500})
    check("HEIC поддерживается библиотекой", enable_heic())
    check("Фильтр выбора файла называет JPG, JPEG и HEIC",
          all(x in photo_filter() for x in ("*.jpg", "*.jpeg", "*.heic", "*.JPG", "*.HEIC")))
    check("Расширение в верхнем регистре считается фотографией",
          is_photo_file("IMG_0001.HEIC") and is_photo_file("DSC_1.JPG")
          and is_photo_file("a.jpeg") and not is_photo_file("a.txt"))
    added = {}
    for name, fmt in (("снимок один.jpg", "JPEG"), ("ФОТО 2.JPEG", "JPEG"),
                      ("IMG_0003.HEIC", "HEIF"), ("фото 4.png", "PNG")):
        source = work / name
        try:
            make_image(source, fmt=fmt)
            added[name] = photo_service.add_photo(item, source, "other")
        except Exception as exc:                                   # noqa: BLE001
            added[name] = None
            print("     ", name, "→", exc)
    for name in ("снимок один.jpg", "ФОТО 2.JPEG", "IMG_0003.HEIC", "фото 4.png"):
        check(f"Файл «{name}» загружается", bool(added.get(name)))
    photos = photo_service.photos_of(item)
    check("Все четыре фотографии в карточке", len(photos) == 4, str(len(photos)))
    check("У каждой есть рабочая копия и миниатюра",
          all(Path(p["abs_path"]).exists() and Path(p["thumb_path"]).exists() for p in photos))

    print("\n=== Исходные файлы сохраняются без изменений ===")
    heic = next(p for p in photos if p["orig_rel_path"].lower().endswith(".heic"))
    original = photo_service.original_path(heic)
    check("Исходный HEIC лежит рядом с рабочей копией", original is not None)
    check("Исходник совпадает с загруженным файлом байт в байт",
          original is not None
          and original.read_bytes() == (work / "IMG_0003.HEIC").read_bytes())
    check("Размер исходного снимка записан", heic["width"] == 640 and heic["height"] == 480)

    print("\n=== Поворот снимка с телефона ===")
    rotated = make_image(work / "portrait.jpg", size=(640, 480), exif_orientation=6)
    photo_id = photo_service.add_photo(item, rotated, "other")
    from PIL import Image
    row = db.query_one("SELECT rel_path FROM inventory_photos WHERE id=?", (photo_id,))
    with Image.open(abs_path(row["rel_path"])) as stored:
        check("Кадр с отметкой «повернуть» сохраняется вертикальным",
              stored.size == (480, 640), str(stored.size))

    print("\n=== Дубли ===")
    other = inventory_service.create({"name": "стул  ВЕНСКИЙ", "quantity_total": 1})
    same = photo_service.find_same_file(work / "снимок один.jpg", exclude_item_id=other)
    check("Тот же файл у другого предмета распознаётся", bool(same) and same[0]["item_id"] == item)
    check("Свой же предмет дублем не считается",
          not photo_service.find_same_file(work / "снимок один.jpg", exclude_item_id=item))
    similar = inventory_service.similar_by_name("Стул Венский", exclude_id=item)
    check("Одинаковое название находится без учёта регистра и пробелов",
          [s["id"] for s in similar] == [other])
    groups = inventory_service.duplicate_groups()
    check("Группа дублей по названию собрана",
          any({i["id"] for i in g["items"]} == {item, other} for g in groups))

    print("\n=== Удаление фотографии ===")
    primary = next(p for p in photo_service.photos_of(item) if p["is_primary"])
    photo_service.delete_photo(primary["id"])
    left = photo_service.photos_of(item)
    check("После удаления главной главной становится следующая",
          bool(left) and left[0]["is_primary"] == 1)
    check("Файлы удалённой фотографии убраны из папки склада",
          not Path(primary["abs_path"]).exists())
    trash = list((paths.DATA_DIR).rglob(Path(primary["abs_path"]).name))
    check("Удалённая фотография лежит в корзине, а не стёрта", bool(trash))
    thumb = thumb_path(left[0]["rel_path"])
    thumb.unlink()
    check("Пропавшая миниатюра восстанавливается из фотографии",
          ensure_thumb(left[0]["rel_path"]) == thumb and thumb.exists())

    print("\n=== Каталог: страницы, фильтры, сортировка ===")
    ids = [inventory_service.create({
        "name": f"Лампа настольная {i:03d}", "quantity_total": 1 + i % 3,
        "category_id": furniture if i % 2 else None,
        "rental_price": 100 + i * 10, "keywords": "ретро" if i % 5 == 0 else ""})
        for i in range(60)]
    page1, total = inventory_service.search_page("лампа", limit=25, offset=0)
    page2, _ = inventory_service.search_page("лампа", limit=25, offset=25)
    page3, _ = inventory_service.search_page("лампа", limit=25, offset=50)
    check("Общее число найденного верно", total == 60, str(total))
    check("Страницы не пересекаются и покрывают всё",
          len({i["id"] for i in page1 + page2 + page3}) == 60)
    check("Поиск по части слова находит предмет",
          inventory_service.search_page("астольн")[1] == 60)
    check("Поиск по ключевому слову", inventory_service.search_page("ретро")[1] == 12)
    cheap, _ = inventory_service.search_page("лампа", sort="price", limit=3)
    dear, _ = inventory_service.search_page("лампа", sort="price_desc", limit=3)
    check("Сортировка по цене в обе стороны",
          cheap[0]["rental_price"] == 100 and dear[0]["rental_price"] == 690)
    _, in_range = inventory_service.search_page(price_min=200, price_max=290)
    check("Фильтр по цене", in_range == 10, str(in_range))
    _, with_photo = inventory_service.search_page(has_photo=True)
    _, without = inventory_service.search_page(has_photo=False)
    check("Фильтр «с фото / без фото»", with_photo == 1 and without == 61,
          f"{with_photo}/{without}")
    tree = {n["id"]: n for n in categories_service.tree()}
    check("Счётчик категории считает предметы на всю глубину вложенности",
          tree[furniture]["total_count"] == 31, str(tree[furniture]["total_count"]))
    first = page1[0]
    check("В карточке каталога есть путь категории, фото и остатки",
          all(key in first for key in ("category_path", "thumb_path", "available_now",
                                       "photo_count", "is_favorite")))
    check("Старый вызов search() работает как раньше",
          len(inventory_service.search("лампа", limit=300)) == 60)
    check("Все id под запрос отдаются целиком",
          len(inventory_service.matching_ids(text="лампа")) == 60)

    print("\n=== Избранное и недавние ===")
    inventory_service.set_favorite(ids[0], True)
    inventory_service.set_favorite(ids[1], True)
    inventory_service.set_favorite(ids[1], True)        # повтор не создаёт вторую запись
    check("Избранное сохраняется", inventory_service.favorites_count() == 2)
    favorites, n = inventory_service.search_page(favorites_only=True)
    check("Список избранного", n == 2 and all(i["is_favorite"] for i in favorites))
    inventory_service.set_favorite(ids[1], False)
    check("Из избранного можно убрать", inventory_service.favorites_count() == 1)
    for item_id in (ids[5], ids[6], ids[5]):
        inventory_service.mark_viewed(item_id)
    recent = inventory_service.recent()
    check("Недавние: без повторов", sorted(i["id"] for i in recent) == sorted([ids[5], ids[6]]))

    print("\n=== Массовые действия ===")
    moved = inventory_service.bulk_move(ids[:10], vienna)
    check("Массовый перенос в категорию", moved == 10
          and inventory_service.search_page(category_id=vienna)[1] == 11)
    before = inventory_service.get(ids[20])
    inventory_service.bulk_update(ids[20:23], rental_price=777, add_keywords="кино ретро")
    after = inventory_service.get(ids[20])
    check("Массовая правка меняет цену", after["rental_price"] == 777)
    check("Незаданные поля остаются прежними", after["valuation"] == before["valuation"]
          and after["name"] == before["name"])
    check("Ключевые слова добавляются без повторов",
          after["keywords"].split().count("ретро") == 1 and "кино" in after["keywords"])
    done, skipped = inventory_service.bulk_archive(ids[30:33])
    check("Массовое списание", done == 3 and not skipped)
    _, archived = inventory_service.search_page(archived=True)
    check("Списанные видны в отдельном списке", archived == 3)
    inventory_service.restore(ids[30])
    check("Списанный предмет возвращается в каталог",
          inventory_service.archived_count() == 2
          and inventory_service.get(ids[30])["is_archived"] == 0)
    check("Данные списанного предмета не потеряны",
          inventory_service.get(ids[31])["name"].startswith("Лампа"))

    print("\n=== Поиск по фотографии ===")
    red = inventory_service.create({"name": "Чемодан красный", "quantity_total": 1})
    blue = inventory_service.create({"name": "Телефон дисковый", "quantity_total": 1})
    make_image(work / "red.jpg", color=(200, 30, 30))
    from PIL import ImageDraw
    phone = Image.new("RGB", (640, 480), (240, 240, 240))
    draw = ImageDraw.Draw(phone)
    draw.ellipse([180, 100, 460, 380], fill=(30, 30, 30))
    draw.ellipse([260, 180, 380, 300], fill=(240, 240, 240))
    phone.save(work / "phone.jpg")
    photo_service.add_photo(red, work / "red.jpg", "main")
    photo_service.add_photo(blue, work / "phone.jpg", "main")
    found = image_search.search(work / "phone.jpg", limit=5, min_score=0.0)
    check("Тот же снимок находится первым", bool(found) and found[0]["item_id"] == blue)
    check("У точного совпадения высокая оценка сходства",
          found[0]["percent"] >= 90 and found[0]["level"] == "Очень похоже",
          str(found[0]))
    check("Результаты идут от более похожих к менее похожим",
          [r["score"] for r in found] == sorted((r["score"] for r in found), reverse=True))
    darker = work / "phone_dark.jpg"
    Image.open(work / "phone.jpg").point(lambda v: int(v * 0.6)).save(darker)
    check("Тёмный снимок того же предмета находит его первым",
          image_search.search(darker, limit=3, min_score=0.0)[0]["item_id"] == blue)
    narrowed = image_search.search(work / "phone.jpg", limit=5, min_score=0.0, text="чемодан")
    check("Уточнение словами сужает выдачу",
          [r["item_id"] for r in narrowed] == [red])
    check("Уточнение без совпадений даёт пустую выдачу, а не ошибку",
          image_search.search(work / "phone.jpg", text="несуществующее") == [])
    region = image_search.search(work / "phone.jpg", limit=3, min_score=0.0,
                                 box=(0.2, 0.1, 0.8, 0.9))
    check("Поиск по выделенной области работает", bool(region) and region[0]["item_id"] == blue)
    similar = image_search.similar_to_item(blue, min_percent=0)
    check("«Похожие предметы» не включают сам предмет",
          blue not in [r["item_id"] for r in similar])
    check("Шкала сходства: случайный уровень — 0 %, тот же предмет — 100 %",
          similarity_percent(0.5, "local-v2") == 0 and similarity_percent(0.99, "local-v2") == 100
          and similarity_level(10) == "Отдалённо")
    check("Основной цвет определяется словами",
          "красный" in dominant_colors(work / "red.jpg"), str(dominant_colors(work / "red.jpg")))
    twin = inventory_service.create({"name": "Телефон дисковый второй", "quantity_total": 1})
    photo_service.add_photo(twin, work / "phone.jpg", "main")
    pairs = image_search.duplicate_pairs()
    check("Одинаковые фотографии у разных предметов попадают в проверку дублей",
          any({i["id"] for i in p["items"]} == {blue, twin} for p in pairs))
    check("Все фотографии проиндексированы", image_search.missing_count() == 0)
    db.execute("DELETE FROM photo_embeddings WHERE item_id=?", (red,))
    image_search.invalidate()
    check("Пропуск в индексе обнаруживается", image_search.missing_count() == 1)
    check("Доиндексация добирает только недостающее",
          image_search.index_missing() == 1 and image_search.missing_count() == 0)

    print("\n=== Обновление базы версии 2.4 ===")
    old_dir = Path(tempfile.mkdtemp(prefix="sklad_v40old_"))
    old_file = old_dir / "data.db"
    shutil.copy(paths.DATA_DIR / "data.db", old_file)
    import sqlite3
    raw = sqlite3.connect(old_file)
    raw.executescript(
        "DROP TABLE item_favorites; DROP TABLE item_views;"
        "ALTER TABLE inventory_photos DROP COLUMN orig_rel_path;"
        "ALTER TABLE inventory_photos DROP COLUMN src_sha1;"
        "ALTER TABLE inventory_photos DROP COLUMN width;"
        "ALTER TABLE inventory_photos DROP COLUMN height;"
        "PRAGMA user_version = 8;")
    counts_before = {t: raw.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]   # noqa: S608
                     for t in ("inventory_items", "inventory_photos", "categories", "users")}
    ids_before = [r[0] for r in raw.execute("SELECT id FROM inventory_items ORDER BY id")]
    raw.commit()
    raw.close()
    upgraded = Database(old_file)
    upgraded.initialize()
    counts_after = {t: upgraded.query_one(f"SELECT COUNT(*) c FROM {t}")["c"]    # noqa: S608
                    for t in counts_before}
    check("После обновления записи, фото, категории и сотрудники на месте",
          counts_after == counts_before, f"{counts_before} → {counts_after}")
    check("Идентификаторы предметов не изменились",
          ids_before == [r["id"] for r in upgraded.query(
              "SELECT id FROM inventory_items ORDER BY id")])
    check("Схема обновлена до 9", upgraded.query_one("PRAGMA user_version")[0] == 9)
    check("Перед обновлением сделана резервная копия",
          any("before" in p.name or "migration" in p.name
              for p in paths.BACKUPS_DIR.rglob("*") if p.is_file()))
    check("Целостность базы после обновления",
          upgraded.query_one("PRAGMA integrity_check")[0] == "ok")
    upgraded.close()

    print(f"\nИтог: пройдено {PASSED}, не пройдено {FAILED}")
    get_db().close()
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
