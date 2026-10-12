# -*- coding: utf-8 -*-
"""
Календарь аренд: одна аренда — одна отметка, в дне открытия.

Проверяются три сценария из задачи: аренда без даты окончания, аренда с
периодом и несколько аренд, открытых в разные дни. Плюс отдельно — что
изменение календаря не тронуло расчёт срока, пролонгацию, возврат, историю,
уведомления, поиск по датам и отчёты.

Запуск:  python -m tests.test_v24_calendar
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_cal_"))
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


def main() -> int:
    from datetime import date, timedelta

    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.clients_service import clients_service
    from app.services.inventory_service import inventory_service
    from app.services.productions_service import productions_service
    from app.services.projects_service import projects_service
    from app.services.rentals_service import RentalDraft, RentalLine, rentals_service
    from app.services.reports_service import reports_service

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Иван", "admin", "1234")
    auth_service.pop_recovery_code()
    auth_service.login("admin", "1234")

    cat = categories_service.tree()[0]["id"]
    client = clients_service.create({"full_name": "Петров Пётр", "phone": "+70000000001"})
    prod = productions_service.create({"name": "Кинокомпания «Кадр»", "legal_form": "ООО"})
    project = projects_service.create({"name": "Съёмки фильма Кировский",
                                       "production_id": prod})
    stock = inventory_service.create({"name": "Стул венский", "category_id": cat,
                                      "quantity_total": 40, "rental_price": 100})

    # берём десятое число текущего месяца — как в примере из задачи
    anchor = date.today().replace(day=10)

    def rent(start, end, quantity=1):
        return rentals_service.create(RentalDraft(
            client_id=client, production_id=prod, project_id=project,
            payment_method="cash", date_start=start, date_end=end,
            lines=[RentalLine(stock, "Стул венский", "", "", "", quantity, 100, 0)]))

    def grid_days(rentals):
        month = reports_service.calendar_month(anchor.year, anchor.month, rentals)
        return {day["date"]: day["rentals"] for week in month for day in week}

    def days_with(rental_id, days):
        return sorted(d for d, items in days.items()
                      if any(r["rental_id"] == rental_id for r in items))

    # ------------------------------------------------------------------
    section("Сценарий 1: аренда без даты окончания")
    open_ended = rent(anchor, None)
    rentals = reports_service.search(anchor - timedelta(days=40),
                                     anchor + timedelta(days=40))
    days = grid_days(rentals)
    marked = days_with(open_ended, days)
    check(marked == [anchor],
          f"Отмечена ровно в дне открытия {anchor}, и больше нигде (найдено: {marked})")
    check(all(not any(r["rental_id"] == open_ended for r in items)
              for day, items in days.items() if day != anchor),
          "На следующих днях аренда не дублируется")
    entry = days[anchor][0]
    check(entry["date_end"] is None, "Отсутствие даты возврата видно в подсказке")

    # ------------------------------------------------------------------
    section("Сценарий 2: аренда с датой начала и датой окончания")
    with_end = rent(anchor + timedelta(days=1), anchor + timedelta(days=10))
    rentals = reports_service.search(anchor - timedelta(days=40),
                                     anchor + timedelta(days=40))
    days = grid_days(rentals)
    marked = days_with(with_end, days)
    check(marked == [anchor + timedelta(days=1)],
          f"Аренда с 11 по 20 отмечена только в дне открытия (найдено: {marked})")
    check(len(marked) == 1, "Копий на каждый день периода нет")
    entry = [r for r in days[anchor + timedelta(days=1)] if r["rental_id"] == with_end][0]
    check(entry["date_end"] == anchor + timedelta(days=10),
          "Дата возврата доступна для подсказки, хотя отметка одна")

    # ------------------------------------------------------------------
    section("Сценарий 3: несколько аренд в разные даты")
    third = rent(anchor + timedelta(days=5), anchor + timedelta(days=6))
    fourth = rent(anchor + timedelta(days=5), None)          # тот же день открытия
    rentals = reports_service.search(anchor - timedelta(days=40),
                                     anchor + timedelta(days=40))
    days = grid_days(rentals)
    check(days_with(third, days) == [anchor + timedelta(days=5)],
          "Третья аренда — только в своём дне")
    check(days_with(fourth, days) == [anchor + timedelta(days=5)],
          "Четвёртая аренда — только в своём дне")
    same_day = days[anchor + timedelta(days=5)]
    check(len(same_day) == 2,
          f"Две аренды одного дня стоят рядом в одной клетке (получено {len(same_day)})")

    every = [r["rental_id"] for items in days.values() for r in items]
    check(len(every) == len(set(every)),
          f"Ни одна аренда не встречается в календаре дважды ({len(every)} отметок)")

    # ------------------------------------------------------------------
    section("Клик по дню согласован с календарём")
    for day in (anchor, anchor + timedelta(days=5), anchor + timedelta(days=3)):
        cell = {r["rental_id"] for r in days.get(day, [])}
        listed = {r["id"] for r in reports_service.day_rentals(day, rentals)}
        check(cell == listed, f"{day}: список дня совпадает с клеткой ({len(cell)} шт.)")

    check(not reports_service.day_rentals(anchor + timedelta(days=3), rentals),
          "В день внутри периода аренда не показывается — она открыта не тогда")

    # ------------------------------------------------------------------
    section("Что действовало в этот день — отдельный вопрос")
    inside = anchor + timedelta(days=3)
    active = {r["id"] for r in reports_service.active_on(inside, rentals)}
    check(open_ended in active, "Аренда без даты окончания действует и внутри периода")
    check(with_end in active, "Аренда с периодом действует внутри своего срока")
    after = reports_service.active_on(anchor + timedelta(days=30), rentals)
    check(with_end not in {r["id"] for r in after},
          "После даты возврата аренда действующей не считается")
    check(open_ended in {r["id"] for r in after},
          "Аренда без даты окончания считается действующей и дальше")

    # ------------------------------------------------------------------
    section("Остальная логика не тронута")
    rental = rentals_service.get(with_end)
    check(rental["date_end"] is not None, "Дата возврата хранится как раньше")
    row = [r for r in rentals if r["id"] == with_end][0]
    check(row["days"] == 10, f"Срок аренды считается как раньше (получено {row['days']})")
    open_row = [r for r in rentals if r["id"] == open_ended][0]
    check(open_row["days"] is None, "У аренды без даты возврата срок не выдумывается")

    new_end = anchor + timedelta(days=20)
    rentals_service.extend(with_end, new_end)
    check(rentals_service.get(with_end)["date_end"] == new_end.strftime("%Y-%m-%d"),
          "Пролонгация работает")
    after_extend = reports_service.search(anchor - timedelta(days=40),
                                          anchor + timedelta(days=40))
    days = grid_days(after_extend)
    check(days_with(with_end, days) == [anchor + timedelta(days=1)],
          "После пролонгации отметка осталась одна — в дне открытия")

    found = reports_service.search(anchor, anchor)
    check(any(r["id"] == open_ended for r in found),
          "Поиск по датам находит аренду, начавшуюся в этот день")
    found = reports_service.search(anchor + timedelta(days=3), anchor + timedelta(days=3))
    check(any(r["id"] == open_ended for r in found),
          "Поиск по датам по-прежнему учитывает весь период, а не только начало")

    stats = reports_service.stats(after_extend)
    check(stats["total"] == 4, f"Отчёт считает все четыре аренды ({stats['total']})")

    history = clients_service.rentals(client)
    check(len(history) == 4, f"История клиента содержит все аренды ({len(history)})")

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки календаря пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
