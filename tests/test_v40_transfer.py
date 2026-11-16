# -*- coding: utf-8 -*-
"""
Перенос склада между компьютерами: выгрузка в файл и загрузка из файла.

Запуск:  python -m tests.test_v40_transfer

«Другой компьютер» изображается честно: после выгрузки стирается ключ
шифрования (на новом компьютере он другой), база и фотографии заменяются
чужими — и склад поднимается только из файла переноса и пароля.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["SKLAD_DATA_DIR"] = tempfile.mkdtemp(prefix="sklad_tr_")
os.environ["SKLAD_KEY_DIR"] = tempfile.mkdtemp(prefix="sklad_trkey_")

PASSED = FAILED = 0


def check(name: str, condition: bool, details: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {details}")


def main() -> int:
    from PIL import Image

    from app import paths
    from app.db.database import Database, get_db, set_db
    from app.db.seed import seed_if_empty
    from app.errors import AppError
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.clients_service import clients_service
    from app.services.crypto_service import _KEY_FILE, crypto_service, key_dir
    from app.services.image_search import image_search
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import abs_path, photo_service
    from app.services.transfer_service import transfer_service

    def fresh_database() -> None:
        try:
            get_db().close()
        except Exception:                                          # noqa: BLE001
            pass
        for suffix in ("", "-wal", "-shm"):
            Path(str(paths.DB_PATH) + suffix).unlink(missing_ok=True)
        db = Database()
        db.initialize()
        set_db(db)
        seed_if_empty(db)

    work = Path(tempfile.mkdtemp(prefix="sklad_trsrc_"))
    fresh_database()
    auth_service.create_first_admin("Иванов Михаил", "admin", "Пароль!2026", "Заведующий")
    auth_service.login("admin", "Пароль!2026")

    print("\n=== Исходный склад («компьютер А») ===")
    category = categories_service.add("Перенос: мебель")
    ids = []
    for index in range(12):
        item = inventory_service.create({
            "name": f"Кресло «Ёлочка» №{index} — тест переноса", "quantity_total": 2,
            "category_id": category, "rental_price": 300 + index,
            "description": "Описание с кириллицей, ё и символами: «» № ₽"})
        ids.append(item)
        source = work / f"фото предмета {index}.jpg"
        Image.new("RGB", (400, 300), (40 + index * 15, 120, 200 - index * 10)).save(source)
        photo_service.add_photo(item, source, "main")
    inventory_service.set_favorite(ids[0], True)
    client = clients_service.create({"full_name": "Петров Пётр Петрович",
                                     "phone": "+7 900 000-00-00"})
    (paths.DOCS_DIR / "Договор АР-0001.pdf").write_bytes(b"%PDF-1.4 test document")
    (paths.AVATARS_DIR / "1.jpg").write_bytes(b"avatar")
    before = {
        "items": [(r["id"], r["inv_number"], r["name"], r["category_id"]) for r in get_db().query(
            "SELECT id, inv_number, name, category_id FROM inventory_items ORDER BY id")],
        "photos": [(r["id"], r["item_id"], r["rel_path"], r["orig_rel_path"])
                   for r in get_db().query("SELECT * FROM inventory_photos ORDER BY id")],
        "categories": get_db().query_one("SELECT COUNT(*) c FROM categories")["c"],
        "phone": clients_service.get(client)["phone"],
    }
    old_key = crypto_service._load_key()                           # noqa: SLF001

    archive = work / "Мой склад (перенос).skladpack"
    try:
        transfer_service.export_archive(archive, "123", None)
        check("Короткий пароль отклоняется", False)
    except AppError:
        check("Короткий пароль отклоняется", True)
    progress_calls = []
    info = transfer_service.export_archive(
        archive, "Перенос-2026", lambda done, total, name: progress_calls.append((done, total)))
    check("Файл переноса создан", archive.exists() and info["inventory_items"] == 12)
    check("Ход выгрузки сообщается", bool(progress_calls))
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
        raw_db = z.read("data.db.enc")
    check("В файле есть база, ключ, фотографии, аватары и документы",
          {"manifest.json", "data.db.enc", "master.key.enc"} <= set(names)
          and sum(n.startswith("photos/") for n in names) == 36     # копия, миниатюра, исходник
          and any(n.startswith("avatars/") for n in names)
          and any(n.startswith("documents/") and n.endswith(".enc") for n in names),
          str(len(names)))
    check("База в файле зашифрована (не читается как SQLite)",
          not raw_db.startswith(b"SQLite format 3"))
    check("Пути в архиве — с прямой косой чертой, без абсолютных",
          all("\\" not in n and not n.startswith("/") for n in names))
    manifest = transfer_service.inspect(archive)
    check("Описание файла читается без пароля",
          manifest["counts"]["inventory_items"] == 12 and manifest["app_version"] == "4.0")

    print("\n=== Другой компьютер («компьютер Б»): чужой ключ, чужой склад ===")
    (key_dir() / _KEY_FILE).unlink()
    crypto_service._key = None                                     # noqa: SLF001
    shutil.rmtree(paths.PHOTOS_DIR)
    shutil.rmtree(paths.DOCS_DIR)
    paths.ensure_dirs()
    fresh_database()
    auth_service.logout()
    auth_service.create_first_admin("Сидоров Сидор", "boss", "Другой!2026", "Админ")
    auth_service.login("boss", "Другой!2026")
    foreign = inventory_service.create({"name": "Чужой предмет", "quantity_total": 1})
    source = work / "чужое.jpg"
    Image.new("RGB", (200, 200), (9, 9, 9)).save(source)
    photo_service.add_photo(foreign, source, "main")
    new_key = crypto_service._load_key()                           # noqa: SLF001
    check("У второго компьютера свой ключ шифрования", new_key != old_key)

    try:
        transfer_service.import_archive(archive, "неверный пароль")
        check("Неверный пароль отклоняется", False)
    except AppError as exc:
        check("Неверный пароль отклоняется", "пароль" in str(exc).lower(), str(exc))
    check("После отказа склад не изменился",
          inventory_service.get(foreign) is not None and inventory_service.count() == 1)

    damaged = work / "damaged.skladpack"
    data = bytearray(archive.read_bytes())
    middle = len(data) // 2
    data[middle:middle + 64] = b"\x00" * 64
    damaged.write_bytes(bytes(data))
    try:
        transfer_service.import_archive(damaged, "Перенос-2026")
        check("Повреждённый файл отклоняется", False)
    except AppError:
        check("Повреждённый файл отклоняется", True)
    check("После повреждённого файла склад цел, фотографии на месте",
          inventory_service.count() == 1
          and all(Path(p["abs_path"]).exists() for p in photo_service.photos_of(foreign)))
    try:
        transfer_service.inspect(source)
        check("Посторонний файл не принимается за файл переноса", False)
    except AppError:
        check("Посторонний файл не принимается за файл переноса", True)

    print("\n=== Загрузка ===")
    result = transfer_service.import_archive(archive, "Перенос-2026")
    db = get_db()
    after_items = [(r["id"], r["inv_number"], r["name"], r["category_id"]) for r in db.query(
        "SELECT id, inv_number, name, category_id FROM inventory_items ORDER BY id")]
    after_photos = [(r["id"], r["item_id"], r["rel_path"], r["orig_rel_path"])
                    for r in db.query("SELECT * FROM inventory_photos ORDER BY id")]
    check("Все предметы на месте: те же номера, названия, категории и идентификаторы",
          after_items == before["items"])
    check("Категории перенесены",
          db.query_one("SELECT COUNT(*) c FROM categories")["c"] == before["categories"])
    check("Связи предметов с фотографиями сохранены", after_photos == before["photos"])
    check("Каждая фотография из базы есть на диске",
          not result["missing_photos"]
          and all(abs_path(rel).exists() and abs_path(orig).exists()
                  for _i, _item, rel, orig in after_photos), str(result["missing_photos"][:3]))
    check("Дублей нет: чужой предмет не подмешан", "Чужой предмет" not in
          [name for _i, _n, name, _c in after_items] and len(after_items) == 12)
    auth_service.logout()
    check("Сотрудники перенесены: вход прежним логином работает",
          bool(auth_service.login("admin", "Пароль!2026")))
    check("Личные данные клиента расшифровываются на новом компьютере",
          clients_service.get(client)["phone"] == before["phone"],
          str(clients_service.get(client)["phone"]))
    check("Избранное сотрудника перенесено", inventory_service.is_favorite(ids[0]))
    check("Документ перенесён и расшифрован",
          (paths.DOCS_DIR / "Договор АР-0001.pdf").read_bytes() == b"%PDF-1.4 test document")
    check("Аватар перенесён", (paths.AVATARS_DIR / "1.jpg").exists())
    check("Поиск по фото работает на перенесённом складе",
          bool(image_search.search(Path(abs_path(after_photos[0][2])), limit=3, min_score=0.0)))
    check("База цела", db.query_one("PRAGMA integrity_check")[0] == "ok")
    kept = [Path(p) for p in result["previous_kept"]]
    check("Прежние фотографии этого компьютера не удалены, а отложены",
          any(p.exists() and any(p.rglob("*.jpg")) for p in kept), str(kept))
    check("Прежняя база сохранена в папке копий",
          any(p.name.startswith("before_transfer_") for p in paths.BACKUPS_DIR.iterdir()))
    again = work / "again.skladpack"
    transfer_service.export_archive(again, "Перенос-2026")
    check("С нового компьютера склад выгружается обратно",
          transfer_service.inspect(again)["counts"]["inventory_items"] == 12)

    print("\n=== Откат при сбое на середине ===")
    original_seal = crypto_service._seal                           # noqa: SLF001
    crypto_service._seal = staticmethod(                           # noqa: SLF001
        lambda key: (_ for _ in ()).throw(OSError("диск недоступен")))
    marker = inventory_service.create({"name": "Отметка перед сбоем", "quantity_total": 1})
    try:
        transfer_service.import_archive(archive, "Перенос-2026")
        check("Сбой при загрузке поднимает ошибку", False)
    except Exception:                                              # noqa: BLE001
        check("Сбой при загрузке поднимает ошибку", True)
    crypto_service._seal = original_seal                           # noqa: SLF001
    check("После сбоя склад возвращён в прежнее состояние",
          inventory_service.get(marker) is not None and inventory_service.count() == 13)
    check("Фотографии после отката на месте",
          all(abs_path(rel).exists() for _i, _item, rel, _o in after_photos))
    check("Личные данные после отката читаются",
          clients_service.get(client)["phone"] == before["phone"])

    print(f"\nИтог: пройдено {PASSED}, не пройдено {FAILED}")
    get_db().close()
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
