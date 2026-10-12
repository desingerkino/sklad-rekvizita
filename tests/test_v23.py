# -*- coding: utf-8 -*-
"""
Проверка функций версии 2.3: профиль сотрудника с аватаром, уведомления
из реальных данных, отчёты (история аренд, календарь, поиск, фильтры,
статистика) и сохранение всего этого после перезапуска.

Запуск:  python -m tests.test_v23
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sklad_v23_"))
os.environ["SKLAD_DATA_DIR"] = str(TMP)

from app import paths                                              # noqa: E402
from app.db.database import Database, get_db, set_db               # noqa: E402
from app.db.seed import seed_if_empty                              # noqa: E402
from app.errors import AppError, PermissionDeniedError             # noqa: E402
from app.services.auth_service import auth_service                 # noqa: E402
from app.services.billing_service import billing_service           # noqa: E402
from app.services.categories_service import categories_service     # noqa: E402
from app.services.clients_service import clients_service           # noqa: E402
from app.services.inventory_service import inventory_service       # noqa: E402
from app.services.notifications_service import notifications_service  # noqa: E402
from app.services.productions_service import productions_service   # noqa: E402
from app.services.projects_service import projects_service         # noqa: E402
from app.services.rentals_service import (RentalDraft, RentalLine, rentals_service)  # noqa: E402
from app.services.repairs_service import repairs_service           # noqa: E402
from app.services.reports_service import period_bounds, reports_service  # noqa: E402
from app.version import DISPLAY_NAME, VERSION                      # noqa: E402

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def raises(action, exc=AppError, part: str = "") -> bool:
    try:
        action()
    except exc as error:
        return part in str(error) if part else True
    except Exception:
        return False
    return False


def main() -> int:  # noqa: C901
    paths.ensure_dirs()
    db = Database(TMP / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    print(f"\n=== {DISPLAY_NAME} (версия {VERSION}) ===")
    from app.version import DB_SCHEMA_VERSION
    check("Версия приложения задана", VERSION >= "2.3", VERSION)
    check(f"Схема базы обновлена до {DB_SCHEMA_VERSION}",
          get_db().query_one("PRAGMA user_version")[0] == DB_SCHEMA_VERSION)

    # ------------------------------------------------------------- профиль
    print("\n=== Профиль сотрудника (пп. 7, 29) ===")
    admin_id = auth_service.create_first_admin("Иванов Михаил", "admin", "admin123",
                                               "Заведующий складом")
    admin = auth_service.login("admin", "admin123")
    check("Имя и фамилия разобраны из ФИО",
          admin.last_name == "Иванов" and admin.first_name == "Михаил",
          f"{admin.last_name} / {admin.first_name}")
    check("Должность сохранена", admin.position == "Заведующий складом", admin.position)
    check("Роль и должность — разные сущности",
          admin.role_label == "Администратор" and admin.position != admin.role_label)
    check("Инициалы для аватара", admin.initials == "МИ", admin.initials)

    auth_service.update_profile(admin_id, first_name="Михаил", last_name="Иванов",
                                position="Художник по реквизиту")
    admin = auth_service.refresh_current()
    check("Должность меняется", admin.position == "Художник по реквизиту", admin.position)
    check("Отображаемое имя «Имя Фамилия»", admin.display_name == "Михаил Иванов",
          admin.display_name)

    from PIL import Image
    avatar_src = TMP / "avatar.png"
    Image.new("RGB", (400, 300), (37, 99, 235)).save(avatar_src)
    rel = auth_service.set_avatar(admin_id, avatar_src)
    check("Аватар загружен", bool(rel) and auth_service.avatar_file(admin_id) is not None)
    with Image.open(auth_service.avatar_file(admin_id)) as img:
        check("Аватар обрезан в квадрат без искажений", img.size == (256, 256), str(img.size))
    old_file = auth_service.avatar_file(admin_id)
    auth_service.set_avatar(admin_id, avatar_src)
    check("Замена аватара удаляет прежний файл", not old_file.exists())
    auth_service.delete_avatar(admin_id)
    check("Аватар удаляется", auth_service.avatar_file(admin_id) is None)
    auth_service.set_avatar(admin_id, avatar_src)

    worker_id = auth_service.create_user("Сидорова Мария", "maria", "maria123", "user",
                                         "Реквизитор")
    check("Пользователь создан с должностью",
          auth_service.get_user(worker_id)["position"] == "Реквизитор")
    auth_service.logout()
    auth_service.login("maria", "maria123")
    check("Свой профиль сотрудник менять может",
          auth_service.update_profile(worker_id, position="Старший реквизитор") is None)
    check("Чужой профиль — только администратор",
          raises(lambda: auth_service.update_profile(admin_id, position="Взлом"),
                 PermissionDeniedError))
    check("Чужой аватар — только администратор",
          raises(lambda: auth_service.set_avatar(admin_id, avatar_src), PermissionDeniedError))
    auth_service.logout()
    auth_service.login("admin", "admin123")

    # -------------------------------------------------------------- данные
    cat = categories_service.add("Мебель QA")
    chairs = inventory_service.create({"name": "Стул венский", "category_id": cat,
                                       "quantity_total": 20, "rental_price": 1000,
                                       "valuation": 7000})
    lamps = inventory_service.create({"name": "Лампа настольная", "category_id": cat,
                                      "quantity_total": 10, "rental_price": 800,
                                      "valuation": 5000})
    client_a = clients_service.create({"full_name": "Петров Пётр", "phone": "+7 900"})
    client_b = clients_service.create({"full_name": "Кузнецова Анна", "phone": "+7 901"})
    production = productions_service.create({"name": "Кинокомпания «Кадр»", "legal_form": "ООО"})
    project_a = projects_service.create({"name": "Фильм «Тишина»", "production_id": production})
    project_b = projects_service.create({"name": "Сериал «Осень»", "production_id": production})

    current = date.today()
    rental_active = rentals_service.create(RentalDraft(
        client_id=client_a, production_id=production, project_id=project_a,
        payment_method="cash", date_start=current - timedelta(days=2),
        date_end=current + timedelta(days=1),
        lines=[RentalLine(chairs, "Стул венский", "", "", "", 6, 1000, 7000)]))
    rental_overdue = rentals_service.create(RentalDraft(
        client_id=client_b, production_id=production, project_id=project_b,
        payment_method="bank", date_start=current - timedelta(days=20),
        date_end=current - timedelta(days=15),
        lines=[RentalLine(lamps, "Лампа настольная", "", "", "", 3, 800, 5000)]))
    rental_planned = rentals_service.create(RentalDraft(
        client_id=client_a, production_id=production, project_id=project_a,
        payment_method="cash", date_start=current + timedelta(days=10),
        date_end=current + timedelta(days=15),
        lines=[RentalLine(chairs, "Стул венский", "", "", "", 4, 1000, 7000)]))
    rental_closed = rentals_service.create(RentalDraft(
        client_id=client_b, production_id=production, project_id=project_b,
        payment_method="cash", date_start=current - timedelta(days=60),
        date_end=current - timedelta(days=50),
        lines=[RentalLine(lamps, "Лампа настольная", "", "", "", 2, 800, 5000)]))
    rentals_service.close(rental_closed)
    # пересекающаяся по датам аренда — проверяем, что календарь покажет обе
    rental_overlap = rentals_service.create(RentalDraft(
        client_id=client_b, production_id=production, project_id=project_b,
        payment_method="cash", date_start=current - timedelta(days=1),
        date_end=current + timedelta(days=3),
        lines=[RentalLine(lamps, "Лампа настольная", "", "", "", 2, 800, 5000)]))

    # --------------------------------------------------------- уведомления
    print("\n=== Уведомления (п. 11) ===")
    created = notifications_service.refresh()
    check("Уведомления собраны из реальных аренд", created > 0, str(created))
    items = notifications_service.list()
    kinds = {n["kind"] for n in items}
    check("Есть уведомление о просрочке", "overdue" in kinds, str(kinds))
    check("Есть уведомление о скором окончании", "expiring" in kinds, str(kinds))
    overdue = [n for n in items if n["kind"] == "overdue"][0]
    check("Уведомление ведёт к конкретной аренде",
          overdue["entity"] == "rental" and overdue["entity_id"] == rental_overdue,
          f"{overdue['entity']} {overdue['entity_id']}")
    check("В уведомлении есть заголовок, текст и дата",
          overdue["title"] and overdue["body"] and overdue["created_at"])
    before = notifications_service.unread_count()
    check("Счётчик непрочитанных больше нуля", before > 0, str(before))

    again = notifications_service.refresh()
    check("Повторный сбор не дублирует уведомления", again == 0, str(again))
    check("Счётчик не изменился", notifications_service.unread_count() == before)

    notifications_service.mark_read(overdue["id"])
    check("Одно уведомление отмечается прочитанным",
          notifications_service.unread_count() == before - 1)
    check("Прочитанное помечено в списке",
          [n for n in notifications_service.list() if n["id"] == overdue["id"]][0]["is_read"])
    check("Фильтр «только непрочитанные» работает",
          all(not n["is_read"] for n in notifications_service.list(unread_only=True)))

    repairs_service.send_to_repair(chairs, 2, "сломана ножка")
    notifications_service.refresh()
    check("Ремонт попадает в уведомления",
          any(n["kind"] == "repair" for n in notifications_service.list()))

    marked = notifications_service.mark_all_read()
    check("Отметить все прочитанными", marked > 0 and notifications_service.unread_count() == 0)

    # --------------------------------------------------------------- отчёты
    print("\n=== Отчёты: история, поиск, фильтры (пп. 19–25) ===")
    all_rentals = reports_service.search()
    check("В отчёты попали все аренды", len(all_rentals) == 5, str(len(all_rentals)))
    statuses = {r["id"]: r["status_code"] for r in all_rentals}
    check("Статус «активная» определён", statuses[rental_active] in ("active", "prolonged"),
          statuses[rental_active])
    check("Статус «просроченная» определён", statuses[rental_overdue] == "overdue",
          statuses[rental_overdue])
    check("Статус «запланированная» определён", statuses[rental_planned] == "planned",
          statuses[rental_planned])
    check("Статус «завершённая» определён", statuses[rental_closed] == "closed",
          statuses[rental_closed])

    check("Поиск по ФИО клиента",
          {r["id"] for r in reports_service.search(text="Кузнецова")} ==
          {rental_overdue, rental_closed, rental_overlap},
          str([r["number"] for r in reports_service.search(text="Кузнецова")]))
    check("Поиск по проекту",
          {r["id"] for r in reports_service.search(text="Тишина")} ==
          {rental_active, rental_planned})
    check("Поиск по названию реквизита",
          {r["id"] for r in reports_service.search(text="Лампа")} ==
          {rental_overdue, rental_closed, rental_overlap})
    number = rentals_service.get(rental_active)["number"]
    check("Поиск по номеру аренды",
          [r["id"] for r in reports_service.search(text=number)] == [rental_active])
    check("Ничего не найдено — пустой список, без ошибки",
          reports_service.search(text="щщщ") == [])

    check("Фильтр по клиенту",
          {r["id"] for r in reports_service.search(client_id=client_a)} ==
          {rental_active, rental_planned})
    check("Фильтр по проекту",
          {r["id"] for r in reports_service.search(project_id=project_b)} ==
          {rental_overdue, rental_closed, rental_overlap})
    check("Фильтр по категории",
          len(reports_service.search(category_id=cat)) == 5)
    check("Фильтр по реквизиту",
          {r["id"] for r in reports_service.search(item_id=lamps)} ==
          {rental_overdue, rental_closed, rental_overlap})
    check("Фильтр по статусу",
          [r["id"] for r in reports_service.search(status="overdue")] == [rental_overdue])
    check("Фильтр по ответственному пользователю",
          len(reports_service.search(user_id=admin_id)) == 5,
          str(len(reports_service.search(user_id=admin_id))))
    check("Комбинация фильтров: клиент + статус",
          [r["id"] for r in reports_service.search(client_id=client_a, status="planned")] ==
          [rental_planned])
    check("Комбинация: период + категория + текст",
          [r["id"] for r in reports_service.search(
              date_from=current - timedelta(days=3), date_to=current + timedelta(days=3),
              category_id=cat, text="Стул")] == [rental_active])

    print("\n=== Отчёты: периоды и календарь (пп. 20, 23) ===")
    start, end = period_bounds("today")
    check("Быстрый период «Сегодня»", start == current and end == current)
    start, end = period_bounds("yesterday")
    check("Быстрый период «Вчера»", start == current - timedelta(days=1))
    start, end = period_bounds("month")
    check("Быстрый период «Этот месяц»", start.day == 1 and start.month == current.month)
    start, end = period_bounds("prev_month")
    check("Быстрый период «Прошлый месяц»", (end - start).days >= 27 and end < date(
        current.year, current.month, 1))
    start, end = period_bounds("year")
    check("Быстрый период «Этот год»", start == date(current.year, 1, 1))

    month_rentals = reports_service.search(*period_bounds("month"))
    grid = reports_service.calendar_month(current.year, current.month, month_rentals)
    check("Календарь построен по неделям", len(grid) >= 4 and all(len(w) == 7 for w in grid))
    today_cell = [d for week in grid for d in week if d["is_today"]]
    check("Сегодняшний день отмечен", len(today_cell) == 1)

    # Версия 2.4: аренда стоит в календаре один раз — в дне открытия, а не в
    # каждом дне периода. Иначе аренда без даты возврата размазывалась по всем
    # дням до конца времён и дублировала своё название на каждой дате.
    start_day = current - timedelta(days=2)
    start_cell = [d for week in grid for d in week if d["date"] == start_day]
    check("Аренда отмечена в дне открытия",
          start_cell and any(r["rental_id"] == rental_active
                             for r in start_cell[0]["rentals"]),
          str(start_cell[0]["rentals"]) if start_cell else "нет клетки")
    check("В следующих днях периода аренда не дублируется",
          not any(r["rental_id"] == rental_active for r in today_cell[0]["rentals"]),
          str(today_cell[0]["rentals"]))
    marks = [d["date"] for week in grid for d in week
             for r in d["rentals"] if r["rental_id"] == rental_active]
    check("Одна аренда — одна отметка в месяце", len(marks) == 1, str(marks))
    check("Дни соседних месяцев помечены",
          any(not d["in_month"] for week in grid for d in week))

    day_list = reports_service.day_rentals(start_day, month_rentals)
    check("Список дня показывает аренды, открытые в этот день",
          any(r["id"] == rental_active for r in day_list))
    active_now = reports_service.active_on(current, month_rentals)
    check("Действующие в этот день считаются отдельно",
          any(r["id"] == rental_active for r in active_now))

    print("\n=== Отчёты: статистика (п. 26) ===")
    stats = reports_service.stats(all_rentals)
    expected_revenue = sum(r["total_final"] for r in all_rentals)
    check("Аренд за период", stats["total"] == 5, str(stats["total"]))
    check("Активных", stats["active"] >= 1, str(stats["active"]))
    check("Завершённых", stats["closed"] == 1, str(stats["closed"]))
    check("Просроченных", stats["overdue"] == 1, str(stats["overdue"]))
    check("Выручка считается из реальных сумм",
          abs(stats["revenue"] - expected_revenue) < 0.01, str(stats["revenue"]))
    narrow = reports_service.search(*period_bounds("today"))
    check("При смене периода статистика пересчитывается",
          reports_service.stats(narrow)["total"] < stats["total"],
          f"{reports_service.stats(narrow)['total']} против {stats['total']}")

    print("\n=== Отчёты: детали и связь с арендами (пп. 21, 28) ===")
    details = reports_service.rental_details(rental_active)
    check("В деталях есть номер, клиент, проект",
          details["number"] and details["client_name"] == "Петров Пётр"
          and details["project_name"] == "Фильм «Тишина»")
    check("В деталях есть позиции и суммы",
          details["items"] and details["total_final"] > 0)
    check("В деталях есть ответственный пользователь",
          "Иванов Михаил" in details["responsible"], str(details["responsible"]))
    check("В деталях есть история начислений", len(details["charges_list"]) >= 1)

    before_count = len(reports_service.search())
    new_rental = rentals_service.create(RentalDraft(
        client_id=client_a, production_id=production, project_id=project_a,
        payment_method="cash", date_start=current, date_end=current + timedelta(days=5),
        lines=[RentalLine(chairs, "Стул венский", "", "", "", 1, 1000, 7000)]))
    check("Новая аренда сразу видна в отчётах",
          len(reports_service.search()) == before_count + 1)
    items = billing_service.items(new_rental)
    billing_service.return_item(items[0]["id"], 1, "возврат")
    check("Возврат отражается в отчётах: аренда закрыта",
          reports_service.search(status="closed") and
          any(r["id"] == new_rental for r in reports_service.search(status="closed")))
    rentals_service.extend(rental_active, current + timedelta(days=12), "продление")
    updated = [r for r in reports_service.search() if r["id"] == rental_active][0]
    check("Продление отражается в отчётах",
          updated["date_end"] == (current + timedelta(days=12)).isoformat(),
          updated["date_end"])
    check("Отчёты не создают своей копии данных",
          get_db().query_one("SELECT COUNT(*) c FROM sqlite_master WHERE type='table' "
                             "AND name LIKE '%report%'")["c"] == 0)

    options = reports_service.filter_options()
    check("Списки фильтров заполняются реальными данными",
          len(options["clients"]) == 2 and len(options["projects"]) == 2
          and len(options["users"]) >= 1, str({k: len(v) for k, v in options.items()}))

    # ------------------------------------------------- сохранность данных
    print("\n=== Сохранение после перезапуска (п. 30) ===")
    unread_before = notifications_service.unread_count()
    notifications_service.refresh()
    unread_before = notifications_service.unread_count()
    get_db().close()
    db2 = Database(TMP / "data.db")
    db2.initialize()
    set_db(db2)
    auth_service.logout()
    reopened = auth_service.login("admin", "admin123")
    check("Профиль сохранился", reopened.position == "Художник по реквизиту")
    check("Аватар сохранился", auth_service.avatar_file(admin_id) is not None)
    check("Состояние уведомлений сохранилось",
          notifications_service.unread_count() == unread_before, str(unread_before))
    check("Прочитанное осталось прочитанным",
          [n for n in notifications_service.list() if n["id"] == overdue["id"]][0]["is_read"])
    check("История аренд в отчётах сохранилась", len(reports_service.search()) == before_count + 1)
    check("Целостность базы", get_db().query_one("PRAGMA integrity_check")[0] == "ok")

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
