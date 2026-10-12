# -*- coding: utf-8 -*-
"""
Тесты импорта реквизита из папки с фотографиями.

Запуск:  python -m tests.test_v24_import
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_v24i_"))
os.environ["SKLAD_DATA_DIR"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

failures: list[str] = []


def check(condition: bool, title: str) -> None:
    if condition:
        print(f"   ok    {title}")
    else:
        failures.append(title)
        print(f"   ОШИБКА {title}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def make_photo(path: Path, color=(120, 90, 60)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), color).save(path)
    return path


def main() -> int:
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.errors import ValidationError
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.import_service import import_service
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import photo_service

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Иван", "admin", "1234")
    auth_service.pop_recovery_code()
    auth_service.login("admin", "1234")

    section("Подготовка папки с фотографиями")
    album = _TMP / "Альбомы склада"
    make_photo(album / "Мебель" / "Стулья" / "стул венский.jpg")
    make_photo(album / "Мебель" / "Стулья" / "стул венский_2.jpg")
    make_photo(album / "Мебель" / "Стулья" / "стул венский (3).jpg")
    make_photo(album / "Мебель" / "Стулья" / "кресло кожаное.jpg")
    make_photo(album / "Мебель" / "стол письменный.jpg")
    make_photo(album / "Посуда" / "сервиз кузнецовский.png")
    make_photo(album / "Посуда" / "IMG_4821.jpg")            # непонятное имя
    (album / "Посуда" / "заметки.doc").write_text("не фотография", encoding="utf-8")
    check(True, "Папка с альбомами собрана")

    section("Разбор папки")
    plan = import_service.scan(album)
    names = {item.name for item in plan.items}
    check("Стул венский" in names, "Название взято из имени файла")
    check("Кресло кожаное" in names and "Стол письменный" in names,
          "Каждый файл стал отдельным предметом")
    check("Сервиз кузнецовский" in names, "Файлы .png тоже читаются")

    chair = [i for i in plan.items if i.name == "Стул венский"][0]
    check(len(chair.photos) == 3,
          f"Три кадра одного предмета собраны вместе (получено {len(chair.photos)})")
    check(chair.category_path == "Мебель / Стулья",
          f"Категория из вложенных папок: {chair.category_path}")
    desk = [i for i in plan.items if i.name == "Стол письменный"][0]
    check(desk.category_path == "Мебель", "Файл в корне папки категории")

    check(not any(i.name.startswith("IMG") for i in plan.items),
          "Файл с именем IMG_4821 не стал предметом")
    check(any("IMG_4821" in s for s in plan.skipped_files),
          "И попал в список пропущенных с объяснением")
    check(any("заметки.doc" in s for s in plan.skipped_files),
          "Не-фотография пропущена")
    check(len(plan.items) == 4, f"Всего 4 предмета, получено {len(plan.items)}")
    check(plan.photo_count == 6, f"Всего 6 фотографий, получено {plan.photo_count}")

    section("План ничего не меняет до подтверждения")
    before = inventory_service.count()
    plan2 = import_service.scan(album)
    check(inventory_service.count() == before, "Разбор папки не создал ни одной карточки")
    check(plan2.new_categories == [],
          "«Мебель», «Стулья» и «Посуда» уже есть в каталоге — новых не будет")

    # папка с заведомо новой категорией: план должен её показать заранее
    fresh = _TMP / "Новый раздел"
    make_photo(fresh / "Пиротехника" / "дымовая шашка.jpg")
    plan_new = import_service.scan(fresh)
    check(plan_new.new_categories == ["Пиротехника"],
          f"Новая категория показана до импорта: {plan_new.new_categories}")
    check(not any(c["name"] == "Пиротехника" for c in categories_service.all()),
          "И при этом ещё не создана")

    section("Импорт")
    result = import_service.apply(plan)
    check(result.created == 4, f"Создано 4 карточки, получено {result.created}")
    check(result.photos == 6, f"Загружено 6 фотографий, получено {result.photos}")
    check(not result.errors, f"Ошибок нет ({result.errors})")

    found = inventory_service.search("Стул венский")
    check(len(found) == 1, "Предмет найден в каталоге")
    item = inventory_service.get(found[0]["id"])
    check(item["category_path"] == "Мебель / Стулья",
          f"Категория проставлена: {item['category_path']}")
    check(item["inv_number"] and item["barcode"] == item["inv_number"],
          "Инвентарный номер и штрихкод выданы")
    photos = photo_service.photos_of(item["id"])
    check(len(photos) == 3, f"Три фотографии привязаны, получено {len(photos)}")
    check(any(p["is_primary"] for p in photos), "Одна из фотографий стала основной")

    paths = categories_service.all()
    check(any(c["name"] == "Стулья" for c in paths), "Вложенная категория создана")
    check(sum(1 for c in paths if c["name"] == "Мебель") == 1,
          "Существующая категория «Мебель» не продублирована")

    section("Повторный импорт не плодит дубли")
    plan3 = import_service.scan(album)
    check(len(plan3.existing_items) == 4, "Все предметы опознаны как уже существующие")
    check(not plan3.new_items, "Новых предметов нет")
    result2 = import_service.apply(plan3, add_photos_to_existing=False)
    check(result2.created == 0 and result2.skipped == 4,
          f"Ничего не создано, пропущено 4 (получено {result2.created}/{result2.skipped})")
    check(inventory_service.count() == before + 4, "Каталог не вырос")
    check(len(photo_service.photos_of(item["id"])) == 3, "Фотографии не задвоились")

    section("Файл подписей")
    captions = _TMP / "С подписями"
    make_photo(captions / "IMG_0001.jpg")
    make_photo(captions / "IMG_0002.jpg")
    (captions / "подписи.txt").write_text(
        "IMG_0001.jpg; Телефон дисковый чёрный; 3; Рабочий, с проводом\n"
        "IMG_0002.jpg; Пишущая машинка «Ундервуд»\n",
        encoding="utf-8")
    plan4 = import_service.scan(captions)
    by_name = {i.name: i for i in plan4.items}
    check("Телефон дисковый чёрный" in by_name,
          "Подпись заменила непонятное имя файла")
    check(by_name["Телефон дисковый чёрный"].quantity == 3, "Количество из подписи")
    check(by_name["Телефон дисковый чёрный"].description == "Рабочий, с проводом",
          "Описание из подписи")
    check("Пишущая машинка «Ундервуд»" in by_name, "Строка без количества тоже работает")
    import_service.apply(plan4)
    phone = inventory_service.search("Телефон дисковый")
    check(len(phone) == 1 and inventory_service.get(phone[0]["id"])["quantity_total"] == 3,
          "Количество попало в карточку")

    section("Устойчивость к мусору")
    broken = _TMP / "Битые"
    broken.mkdir()
    (broken / "сломанное фото.jpg").write_bytes("это не картинка".encode("utf-8"))
    make_photo(broken / "нормальное фото.jpg")
    plan5 = import_service.scan(broken)
    result3 = import_service.apply(plan5)
    check(result3.created == 2, "Обе карточки созданы")
    check(result3.photos == 1, f"Загружена только целая фотография ({result3.photos})")
    check(len(result3.errors) == 1 and "сломанное фото" in result3.errors[0],
          f"Битый файл попал в список ошибок: {result3.errors}")
    check(len(inventory_service.search("Нормальное фото")) == 1,
          "Битый файл не сорвал импорт остальных")

    empty = _TMP / "Пустая"
    empty.mkdir()
    check(len(import_service.scan(empty).items) == 0, "Пустая папка даёт пустой план")
    try:
        import_service.scan(_TMP / "нет такой папки")
        missing_ok = False
    except ValidationError:
        missing_ok = True
    check(missing_ok, "Несуществующая папка отклоняется понятной ошибкой")

    section("Интерфейс импорта")
    ui = (Path(__file__).resolve().parent.parent / "app" / "ui"
          / "import_dialog.py")
    check(ui.exists(), "Диалог импорта существует")
    if ui.exists():
        src = ui.read_text(encoding="utf-8")
        catalog = (ui.parent / "catalog_view.py").read_text(encoding="utf-8")
        check("getExistingDirectory" in src, "Папка выбирается через проводник")
        check("import_service.scan" in src and "import_service.apply" in src,
              "Сначала разбор, потом импорт")
        check("ImportDialog" in catalog, "Импорт запускается из каталога")

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки импорта пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
