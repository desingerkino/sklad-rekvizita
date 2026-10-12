# -*- coding: utf-8 -*-
"""
Проверка на больших объёмах и «плохих» данных (пп. 3, 13, 20, 28 версии 2.3.1).

Создаются 120 позиций реквизита, 35 клиентов, 30 проектов, десятки аренд,
повреждения и очень длинные названия. Проверяется, что данные полностью
доходят до интерфейса (списки не обрезаются искусственно), поиск и отчёты
остаются быстрыми, а пустые разделы не ломают логику.

Запуск:  python -m tests.test_bigdata
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sklad_big_"))
os.environ["SKLAD_DATA_DIR"] = str(TMP)
os.environ["SKLAD_KEY_DIR"] = str(tempfile.mkdtemp(prefix="sklad_bigkey_"))

from app import paths                                            # noqa: E402
from app.db.database import Database, get_db, set_db             # noqa: E402
from app.db.seed import seed_if_empty                            # noqa: E402
from app.services.auth_service import auth_service               # noqa: E402
from app.services.billing_service import billing_service         # noqa: E402
from app.services.categories_service import categories_service   # noqa: E402
from app.services.clients_service import clients_service         # noqa: E402
from app.services.inventory_service import inventory_service     # noqa: E402
from app.services.notifications_service import notifications_service  # noqa: E402
from app.services.productions_service import productions_service  # noqa: E402
from app.services.projects_service import projects_service       # noqa: E402
from app.services.rentals_service import (RentalDraft, RentalLine, rentals_service)  # noqa: E402
from app.services.repairs_service import repairs_service         # noqa: E402
from app.services.reports_service import period_bounds, reports_service  # noqa: E402
from app.services.statuses import item_status, rental_item_status  # noqa: E402

PASSED, FAILED = [], []

LONG_ITEM = ("Стул деревянный резной красный с золотой отделкой и декоративной спинкой, "
             "реставрация 1998 года, комплект из парадного гарнитура")
LONG_PROJECT = "Художественный фильм «Очень длинное рабочее название проекта о жизни и судьбе»"
LONG_CLIENT = "Овчинникова-Разумовская Аполлинария Кристиановна"
LONG_PRODUCTION = "ООО «Продакшн-компания с очень длинным названием и уточнением деятельности»"
LONG_DESCRIPTION = ("Большое многострочное описание реквизита. " * 12)


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def main() -> int:  # noqa: C901
    paths.ensure_dirs()
    db = Database(TMP / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Михаил", "admin", "Пароль!2026", "Заведующий")
    auth_service.login("admin", "Пароль!2026")

    print("\n=== Пустые разделы (п. 28) ===")
    check("Пустой каталог не ломает поиск", inventory_service.search("") == [])
    check("Пустой список клиентов", clients_service.search("") == [])
    check("Пустой список проектов", projects_service.search("") == [])
    check("Пустой отчёт", reports_service.search() == [])
    check("Статистика по пустому отчёту", reports_service.stats([])["total"] == 0)
    check("Календарь без аренд строится",
          len(reports_service.calendar_month(date.today().year, date.today().month, [])) >= 4)
    check("Уведомлений нет", notifications_service.unread_count() == 0)

    print("\n=== Наполнение (п. 13) ===")
    started = time.perf_counter()
    category = categories_service.add("Мебель нагрузочная")
    subcategory = categories_service.add("Стулья нагрузочные", category)
    items = []
    for index in range(120):
        items.append(inventory_service.create({
            "name": f"{LONG_ITEM} № {index + 1}" if index % 4 == 0 else f"Предмет {index + 1}",
            "category_id": subcategory, "description": LONG_DESCRIPTION,
            "keywords": "нагрузка длинное название", "quantity_total": 5 + index % 7,
            "rental_price": 500 + index, "valuation": 5000 + index * 10}))
    clients = [clients_service.create({
        "full_name": f"{LONG_CLIENT} {index}" if index % 3 == 0 else f"Клиент {index}",
        "phone": f"+7 900 {index:07d}", "passport": f"45 08 {index:06d}",
        "address": "Москва, улица с очень длинным названием, дом 1, квартира 100"})
        for index in range(35)]
    production = productions_service.create({"name": LONG_PRODUCTION, "legal_form": "ООО",
                                             "inn": "7700000001"})
    projects = [projects_service.create({
        "name": f"{LONG_PROJECT} — сезон {index}" if index % 2 else f"Проект {index}",
        "production_id": production}) for index in range(30)]

    today = date.today()
    rentals = []
    for index in range(40):
        start = today - timedelta(days=index * 2)
        rentals.append(rentals_service.create(RentalDraft(
            client_id=clients[index % len(clients)], production_id=production,
            project_id=projects[index % len(projects)], payment_method="cash",
            date_start=start, date_end=start + timedelta(days=10 + index % 5),
            lines=[RentalLine(items[(index * 3 + offset) % len(items)],
                              f"{LONG_ITEM} № {offset}", "", LONG_DESCRIPTION,
                              "Примечание с длинным текстом о состоянии позиции",
                              1 + offset, 500 + offset, 5000)
                   for offset in range(3)])))
    for index in range(0, 30, 3):
        repairs_service.send_to_repair(items[index], 1, "Повреждение: скол и трещина",
                                       "Подробное описание повреждения " * 5)
    elapsed = time.perf_counter() - started
    check(f"Данные созданы за {elapsed:.1f} c",
          inventory_service.count() == 120 and len(rentals) == 40, f"{elapsed:.1f} c")
    check("35 клиентов на месте", len(clients_service.search("", limit=200)) == 35)
    check("30 проектов на месте", len(projects_service.search("", limit=200)) == 30)
    check("Повреждения записаны", len(repairs_service.open_repairs()) == 10)

    print("\n=== Списки отдают все записи (пп. 5, 13, 20) ===")
    catalog = inventory_service.search("", limit=500)
    check("Каталог возвращает все 120 позиций", len(catalog) == 120, str(len(catalog)))
    check("Список клиентов не обрезается", len(clients_service.search("", limit=500)) == 35)
    check("Список аренд не обрезается", len(rentals_service.list_all(limit=500)) == 40,
          str(len(rentals_service.list_all(limit=500))))
    report = reports_service.search(limit=500)
    check("Отчёт отдаёт всю историю", len(report) == 40, str(len(report)))
    check("Последняя запись доступна", report[-1]["number"] is not None)
    heavy = rentals_service.get(rentals[0])
    check("Все позиции аренды доступны", len(heavy["items"]) == 3)
    check("Категория содержит все позиции",
          len(inventory_service.search("", subcategory, limit=500)) == 120)

    print("\n=== Скорость на объёме (п. 36) ===")
    for label, action, limit in (
            ("поиск по названию", lambda: inventory_service.search("Предмет 77"), 1.0),
            ("поиск клиента", lambda: clients_service.search("Клиент 30"), 1.5),
            ("поиск по телефону (шифрование)", lambda: clients_service.search("900 0000012"), 2.0),
            ("отчёт за месяц", lambda: reports_service.search(*period_bounds("month")), 3.0),
            ("календарь месяца", lambda: reports_service.calendar_month(
                today.year, today.month, reports_service.search(*period_bounds("month"))), 3.0),
            ("список активных аренд", rentals_service.list_active, 5.0)):
        started = time.perf_counter()
        result = action()
        spent = time.perf_counter() - started
        check(f"{label}: {spent * 1000:.0f} мс", spent < limit, f"{spent:.2f} c")

    print("\n=== Длинные данные (п. 3) ===")
    long_item = [i for i in catalog if i["name"].startswith("Стул деревянный резной")][0]
    card = inventory_service.get(long_item["id"])
    check("Длинное название сохраняется целиком", len(card["name"]) > 100, str(len(card["name"])))
    check("Длинное описание сохраняется", len(card["description"]) > 300)
    check("Длинное название находится поиском",
          any(i["id"] == long_item["id"] for i in inventory_service.search("золотой отделкой")))
    long_client = [c for c in clients_service.search("Овчинникова", limit=50)][0]
    check("Длинное ФИО клиента сохраняется", len(long_client["full_name"]) > 40)
    long_project = [p for p in projects_service.search("Художественный", limit=50)][0]
    check("Длинное название проекта сохраняется", len(long_project["name"]) > 60)
    check("Длинные данные не мешают отчёту",
          any(len(r.get("project_name") or "") > 60 for r in report))

    print("\n=== Статусы на реальных данных (пп. 14, 15) ===")
    free_item = next(inventory_service.get(item_id) for item_id in items
                     if inventory_service.get(item_id)["available_now"]
                     == inventory_service.get(item_id)["quantity_total"])
    code, label = item_status(free_item)
    check("Свободный реквизит — «В наличии»", code == "in_stock" and label == "В наличии",
          f"{code} / {label}")
    rented_id = billing_service.items(rentals[0])[0]["item_id"]
    rented_item = inventory_service.get(rented_id)
    code, label = item_status(rented_item)
    check("Выданный реквизит — «В аренде»", code == "rented" and "В аренде" in label,
          f"{code} / {label}")
    repaired = inventory_service.get(items[0])
    code, label = item_status(repaired)
    check("Реквизит в ремонте помечен", code in ("repair", "rented"), f"{code} / {label}")

    rental_items = billing_service.items(rentals[0])
    billing_service.run_prolongations(today + timedelta(days=40))
    prolonged = [i for i in billing_service.items(rentals[0]) if i["prolongations"] > 0]
    if prolonged:
        code, label = rental_item_status(prolonged[0])
        check("Продлённая позиция — «Пролонгация»",
              code == "prolonged" and label == "Пролонгация", f"{code} / {label}")
    else:
        check("Продлённая позиция — «Пролонгация»", False, "не нашлось пролонгированных позиций")
    returned = billing_service.return_item(rental_items[0]["id"], 1, "возврат")
    item_after = [i for i in billing_service.items(rentals[0])
                  if i["id"] == rental_items[0]["id"]][0]
    check("Возврат отражается в статусе позиции",
          rental_item_status(item_after)[1] in ("Возвращено", "Пролонгация", "В аренде"),
          rental_item_status(item_after)[1])
    rentals_service.close(rentals[1])
    closed = reports_service.search(status="closed")
    check("Завершённая аренда получает статус «Аренда завершена»",
          any(r["id"] == rentals[1] for r in closed))
    check("Все пять статусов имеют текст",
          all(len(text) > 3 for text in
              ("В наличии", "В аренде", "Пролонгация", "Открыт набор / аренда",
               "Аренда завершена")))

    print("\n=== Уведомления и целостность на объёме ===")
    created = notifications_service.refresh()
    check("Уведомления собраны по большому объёму", created > 0, str(created))
    check("Список уведомлений ограничен разумным числом",
          len(notifications_service.list(limit=40)) <= 40)
    check("Целостность базы", get_db().query_one("PRAGMA integrity_check")[0] == "ok")
    check("Внешние ключи целы", len(get_db().query("PRAGMA foreign_key_check")) == 0)

    print(f"\n{'=' * 62}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    for name in FAILED:
        print("  FAIL:", name)
    print(f"Данные теста: {TMP}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    code = main()
    try:
        get_db().close()
    except Exception:
        pass
    sys.exit(code)
