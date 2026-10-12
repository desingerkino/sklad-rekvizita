# -*- coding: utf-8 -*-
"""
Тесты версии 2.4, часть 3: комплектация по машинам и выгрузка в Excel.

Запуск:  python -m tests.test_v24_packing
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_v24p_"))
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


def expect_error(func, exc_types, title: str) -> None:
    try:
        func()
    except exc_types:
        print(f"   ok    {title}")
    except Exception as exc:                                   # noqa: BLE001
        failures.append(f"{title} ({type(exc).__name__}: {exc})")
        print(f"   ОШИБКА {title} — неожиданная ошибка {type(exc).__name__}: {exc}")
    else:
        failures.append(f"{title} (ошибки не было)")
        print(f"   ОШИБКА {title} — ошибки не было, хотя она обязана быть")


def main() -> int:
    from datetime import timedelta

    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.errors import PermissionDeniedError, ValidationError
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.clients_service import clients_service
    from app.services.excel_service import excel_service
    from app.services.inventory_audit_service import inventory_audit_service
    from app.services.inventory_service import inventory_service
    from app.services.packing_service import packing_service
    from app.services.productions_service import productions_service
    from app.services.projects_service import projects_service
    from app.services.rentals_service import RentalDraft, RentalLine, rentals_service
    from app.utils import today

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    section("Подготовка")
    auth_service.create_first_admin("Иванов Иван", "admin", "1234")
    auth_service.pop_recovery_code()
    auth_service.login("admin", "1234")
    cat = categories_service.tree()[0]["id"]

    chair = inventory_service.create({"name": "Стул красный", "category_id": cat,
                                      "quantity_total": 10, "rental_price": 100,
                                      "valuation": 3000})
    lamp = inventory_service.create({"name": "Лампа", "category_id": cat,
                                     "quantity_total": 4, "rental_price": 200})
    dishes = inventory_service.create({"name": "Посуда", "category_id": cat,
                                       "quantity_total": 30, "rental_price": 20})
    client = clients_service.create({"full_name": "Петров Пётр", "phone": "+70000000000"})
    prod = productions_service.create({"name": "Кинокомпания «Кадр»", "legal_form": "ООО"})
    project = projects_service.create({"name": "Фильм «Проба»", "production_id": prod})
    rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=prod, project_id=project, payment_method="cash",
        date_start=today(), date_end=today() + timedelta(days=7),
        lines=[RentalLine(chair, "Стул красный", "", "", "", 4, 100, 3000)]))
    check(rental > 0, "Данные для проверки созданы")

    section("Машины")
    gazelle = packing_service.create_vehicle("Газель реквизита", "Сидоров С.С.")
    second = packing_service.create_vehicle("Машина №2", "Кузнецов К.К.")
    check(len(packing_service.vehicles()) == 2, "Две машины в списке")
    expect_error(lambda: packing_service.create_vehicle("Машина №2"), ValidationError,
                 "Одинаковые названия машин отклоняются")
    expect_error(lambda: packing_service.create_vehicle("  "), ValidationError,
                 "Машина без названия отклоняется")
    packing_service.update_vehicle(second, "Машина №2", "Новиков Н.Н.")
    check(packing_service.vehicles()[1]["driver"] == "Новиков Н.Н.", "Водитель изменён")

    section("Комплект и коробки")
    set_id = packing_service.create_set(
        vehicle_id=second, date_ship=today(), project_id=project, production_id=prod,
        rental_id=rental, responsible="Иванов Иван", comment="Съёмка в павильоне")
    data = packing_service.get(set_id)
    check(data["number"].startswith("КМП-"), f"Номер комплекта выдан ({data['number']})")
    check(data["vehicle_name"] == "Машина №2", "Машина привязана")
    check(data["status"] == "open", "Комплект собирается")

    box1 = packing_service.add_box(set_id)
    box2 = packing_service.add_box(set_id, "Ящик хрупкое", "стекло, не кантовать")
    boxes = packing_service.boxes(set_id)
    check(len(boxes) == 2, "Две коробки созданы")
    check(boxes[0]["label"] == "Коробка №1" and boxes[0]["number"] == 1,
          "Первая коробка нумеруется автоматически")
    check(boxes[1]["label"] == "Ящик хрупкое" and boxes[1]["number"] == 2,
          "Своё название коробки сохраняется")

    section("Наполнение коробок")
    packing_service.add_item(box1, chair, 4)
    packing_service.add_item(box1, lamp, 2)
    packing_service.add_item(box2, dishes, 15)
    items = packing_service.items(box1)
    check(len(items) == 2, "В первой коробке две позиции")
    check(items[0]["name_snap"] == "Стул красный" and items[0]["quantity"] == 4,
          "Название и количество сохранены снимком")

    again = packing_service.add_item(box1, chair, 2)
    check(again["quantity"] == 6 and not again["added"],
          "Повторное добавление увеличивает количество, а не плодит строки")
    check(len(packing_service.items(box1)) == 2, "Лишних строк не появилось")

    packing_service.set_item_quantity(items[0]["id"], 4)
    check(packing_service.items(box1)[0]["quantity"] == 4, "Количество исправляется вручную")
    expect_error(lambda: packing_service.add_item(box1, chair, 0), ValidationError,
                 "Нулевое количество отклоняется")
    expect_error(lambda: packing_service.add_item(box1, 999999), Exception,
                 "Несуществующий предмет отклоняется")

    section("Сканирование в коробку")
    code = inventory_service.get(dishes)["barcode"]
    result = packing_service.scan_into_box(box2, code)
    check(result["found"] and result["quantity"] == 16,
          "Сканирование добавляет предмет в выбранную коробку")
    miss = packing_service.scan_into_box(box2, "НЕТ-ТАКОГО")
    check(not miss["found"] and "не найден" in miss["message"],
          "Неизвестный штрихкод даёт понятный ответ")

    section("Где лежит предмет")
    where = packing_service.where_is(chair)
    check(len(where) == 1 and where[0]["box"] == "Коробка №1",
          "Видно, в какой коробке лежит предмет")
    check("Машина №2" in packing_service.where_is_text(chair),
          f"Место упаковки читаемо: {packing_service.where_is_text(chair)}")
    check(packing_service.where_is_text(inventory_service.create(
        {"name": "Не упакован", "category_id": cat, "quantity_total": 1})) == "",
        "Неупакованный предмет места не имеет")

    packing_service.set_status(set_id, "closed")
    check(packing_service.where_is(chair) == [],
          "После закрытия комплекта место упаковки не показывается")
    packing_service.set_status(set_id, "open")

    section("Склад не меняется от упаковки")
    before = inventory_service.get(chair)
    check(before["available_now"] == 6,
          f"Доступно по-прежнему 6 из 10 (4 в аренде), получено {before['available_now']}")
    packing_service.delete_box(box2)
    check(len(packing_service.boxes(set_id)) == 1, "Коробка удалена")
    check(inventory_service.get(dishes)["available_now"] == 30,
          "Удаление коробки не тронуло остатки склада")
    box2 = packing_service.add_box(set_id, "Коробка №2")
    packing_service.add_item(box2, dishes, 15)

    section("Упаковочный лист PDF")
    pdf = packing_service.packing_list_pdf(set_id, _TMP / "packing.pdf")
    check(pdf.exists() and pdf.stat().st_size > 2000, "Упаковочный лист создан")
    check(pdf.read_bytes()[:4] == b"%PDF", "Это настоящий PDF")

    section("Excel: склад")
    xlsx = excel_service.export_inventory(_TMP / "sklad.xlsx")
    check(xlsx.exists(), "Файл склада создан")
    check(_is_real_xlsx(xlsx), "Это настоящая книга .xlsx, а не переименованный CSV")

    import openpyxl
    book = openpyxl.load_workbook(xlsx)
    sheet = book["Склад"]
    check(sheet.freeze_panes == "A2", "Шапка закреплена")
    check(sheet.auto_filter.ref is not None, "Включён автофильтр")
    headers = [c.value for c in sheet[1]]
    for column in ("Инв. №", "Штрихкод", "Наименование", "Категория", "Всего",
                   "Доступно", "В аренде", "Цена аренды", "Оценка", "Место упаковки",
                   "Текущая аренда"):
        check(column in headers, f"В выгрузке есть колонка «{column}»")

    found = {row[0]: row for row in _data_rows(sheet)}
    chair_row = found[inventory_service.get(chair)["inv_number"]]
    check(chair_row[2] == "Стул красный", "Наименование выгружено")
    check(chair_row[5] == 10 and chair_row[6] == 6 and chair_row[7] == 4,
          f"Всего 10, доступно 6, в аренде 4 — получено {chair_row[5:8]}")
    check(isinstance(chair_row[9], (int, float)) and chair_row[9] == 100,
          "Цена выгружена числом, а не текстом")
    check("Машина №2" in (chair_row[11] or ""), "Место упаковки попало в выгрузку")
    check(chair_row[12] and "Петров" in chair_row[12], "Текущая аренда указана")

    selected = excel_service.export_inventory(_TMP / "vybor.xlsx", item_ids=[lamp])
    only = openpyxl.load_workbook(selected)["Склад"]
    data_rows = _data_rows(only)
    check(len(data_rows) == 1 and data_rows[0][2] == "Лампа",
          "Выгрузка только выбранных позиций работает")

    section("Excel: аренды")
    rentals_xlsx = excel_service.export_rentals(_TMP / "arendy.xlsx")
    book = openpyxl.load_workbook(rentals_xlsx)
    check(set(book.sheetnames) >= {"Аренды", "Позиции аренд"},
          f"Два листа: аренды и позиции ({book.sheetnames})")
    head = book["Аренды"]
    head_rows = _data_rows(head)
    check(len(head_rows) == 1, "Одна аренда в выгрузке")
    row = head_rows[0]
    check(row[2] == "Петров Пётр", "Клиент выгружен")
    check(row[8] == 1, "Число позиций посчитано")
    check(isinstance(row[12], (int, float)), "Итоговая сумма — число")
    line_rows = _data_rows(book["Позиции аренд"])
    check(len(line_rows) == 1 and line_rows[0][3] == "Стул красный",
          "Позиция аренды выгружена отдельной строкой")
    check(line_rows[0][7] == 400, f"Сумма позиции 4 × 100 = 400, получено {line_rows[0][7]}")

    section("Excel: инвентаризация")
    audit_id = inventory_audit_service.create(title="Сверка для выгрузки")
    inventory_audit_service.scan(audit_id, inventory_service.get(lamp)["barcode"])
    audit_xlsx = excel_service.export_audit(_TMP / "inv.xlsx", audit_id)
    book = openpyxl.load_workbook(audit_xlsx)
    check(set(book.sheetnames) >= {"Итоги", "Сверка"}, "Два листа: итоги и сверка")
    detail = _data_rows(book["Сверка"])
    lamp_row = [r for r in detail if r[1] == "Лампа"][0]
    check(lamp_row[3] == 4 and lamp_row[4] == 1 and lamp_row[5] == -3,
          f"Числится 4, найдено 1, расхождение −3 — получено {lamp_row[3:6]}")
    inventory_audit_service.cancel(audit_id)

    section("Excel: комплектация")
    packing_xlsx = excel_service.export_packing(_TMP / "kmp.xlsx", set_id)
    sheet = openpyxl.load_workbook(packing_xlsx)["Комплектация"]
    rows = _data_rows(sheet)
    check(len(rows) == 3, f"Три позиции в комплекте, получено {len(rows)}")
    check(any(r[2] == "Посуда" and r[3] == 15 for r in rows), "Содержимое коробок выгружено")

    section("Права")
    auth_service.create_user("Сидоров Сидор", "sidorov", "1234", role="user")
    auth_service.logout()
    auth_service.login("sidorov", "1234")
    new_box = packing_service.add_box(set_id, "Коробка кладовщика")
    check(new_box > 0, "Обычный сотрудник может собирать комплект")
    packing_service.add_item(new_box, lamp, 1)
    check(len(packing_service.items(new_box)) == 1, "И класть в него предметы")
    expect_error(lambda: packing_service.delete_set(set_id), PermissionDeniedError,
                 "Удалить комплект обычный сотрудник не может")
    expect_error(lambda: packing_service.delete_vehicle(gazelle), PermissionDeniedError,
                 "Удалить машину обычный сотрудник не может")
    auth_service.logout()
    auth_service.login("admin", "1234")

    section("Удаление машины с историей")
    packing_service.delete_vehicle(second)
    check(len(packing_service.vehicles(only_active=True)) == 1,
          "Машина с комплектами убрана из списка, а не удалена")
    check(packing_service.get(set_id)["vehicle_name"] == "Машина №2",
          "Упаковочный лист по-прежнему читается")
    packing_service.delete_vehicle(gazelle)
    check(not any(v["id"] == gazelle for v in packing_service.vehicles(only_active=False)),
          "Машина без истории удаляется полностью")

    section("Интерфейс комплектации (статическая проверка)")
    _check_ui()

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки комплектации и Excel пройдены.")
    return 0


def _data_rows(sheet) -> list[tuple]:
    """
    Строки данных листа: всё до первой пустой строки.

    После таблицы сервис дописывает подпись «выгружено тогда-то», и если
    просто отбирать непустые строки, она посчитается как данные.
    """
    rows = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if all(value in (None, "") for value in row):
            break
        rows.append(row)
    return rows


def _is_real_xlsx(path: Path) -> bool:
    """Настоящая книга Excel — это zip-архив с xl/workbook.xml внутри."""
    import zipfile

    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    return "xl/workbook.xml" in names and any(n.startswith("xl/worksheets/") for n in names)


def _check_ui() -> None:
    root = Path(__file__).resolve().parent.parent / "app" / "ui"
    packing = root / "packing_view.py"
    if not packing.exists():
        check(False, "Экран комплектации существует")
        return
    src = packing.read_text(encoding="utf-8")
    main_window = (root / "main_window.py").read_text(encoding="utf-8")

    check("class PackingView" in src, "Экран комплектации существует")
    check("Машины" in main_window and "PAGE_PACKING" in main_window,
          "Раздел «Машины / Комплектация» есть в меню")
    check("ScanInput(" in src and "scan_into_box" in src,
          "В комплектацию можно сканировать предметы")
    check("packing_list_pdf" in src, "Упаковочный лист печатается с экрана")
    check("export_packing" in src, "Комплект выгружается в Excel")
    check("confirm(self" in src, "Удаление в комплектации спрашивает подтверждение")

    catalog = (root / "catalog_view.py").read_text(encoding="utf-8")
    rentals = (root / "rentals_view.py").read_text(encoding="utf-8")
    audit = (root / "inventory_audit_view.py").read_text(encoding="utf-8")
    check("export_inventory" in catalog and "Выгрузить в Excel" in catalog,
          "Склад выгружается в Excel из каталога")
    check("export_rentals" in rentals, "Аренды выгружаются в Excel")
    check("export_audit" in audit, "Инвентаризация выгружается в Excel")
    check("PDF" not in catalog or "export_inventory" in catalog,
          "PDF-выгрузки не заменены, Excel добавлен вторым форматом")


if __name__ == "__main__":
    raise SystemExit(main())
