# -*- coding: utf-8 -*-
"""
Тесты выгрузки в Excel: склад, аренды, инвентаризация.

Запуск:  python -m tests.test_v24_excel
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
                   "Доступно", "В аренде", "Цена аренды", "Оценка", "Текущая аренда"):
        check(column in headers, f"В выгрузке есть колонка «{column}»")

    found = {row[0]: row for row in _data_rows(sheet)}
    chair_row = found[inventory_service.get(chair)["inv_number"]]
    check(chair_row[2] == "Стул красный", "Наименование выгружено")
    check(chair_row[5] == 10 and chair_row[6] == 6 and chair_row[7] == 4,
          f"Всего 10, доступно 6, в аренде 4 — получено {chair_row[5:8]}")
    check(isinstance(chair_row[9], (int, float)) and chair_row[9] == 100,
          "Цена выгружена числом, а не текстом")
    check(chair_row[11] and "Петров" in chair_row[11], "Текущая аренда указана")
    check("Место упаковки" not in headers, "Колонки раздела «Машины» в выгрузке больше нет")

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

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки выгрузки в Excel пройдены.")
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


if __name__ == "__main__":
    raise SystemExit(main())
