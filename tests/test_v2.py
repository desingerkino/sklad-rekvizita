# -*- coding: utf-8 -*-
"""
Проверка функций версии 2.0: авторизация и роли, 14-дневные периоды,
автоматические пролонгации, частичный возврат, добор в действующую аренду,
журнал действий, миграция базы с версии 1.x.

Запуск:  python -m tests.test_v2
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sklad_v2_"))
os.environ["SKLAD_DATA_DIR"] = str(TMP)

from app import paths                                          # noqa: E402
from app.db.database import Database, get_db, set_db           # noqa: E402
from app.db.seed import seed_if_empty                          # noqa: E402
from app.errors import AppError, AuthError, PermissionDeniedError  # noqa: E402
from app.services.audit_service import audit_service           # noqa: E402
from app.services.auth_service import auth_service             # noqa: E402
from app.services.backup_service import backup_service         # noqa: E402
from app.services.billing_service import (billing_service, period_bounds,  # noqa: E402
                                          period_for_date)
from app.services.clients_service import clients_service       # noqa: E402
from app.services.documents_service import documents_service   # noqa: E402
from app.services.inventory_service import inventory_service   # noqa: E402
from app.services.productions_service import productions_service  # noqa: E402
from app.services.projects_service import projects_service     # noqa: E402
from app.services.rentals_service import (RentalDraft, RentalLine, rentals_service)  # noqa: E402
from app.services.settings_service import settings_service     # noqa: E402
from app.version import (DB_SCHEMA_VERSION, DISPLAY_NAME, RENTAL_PERIOD_DAYS,  # noqa: E402
                         VERSION)

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def audit_has(action: str, contains: str = "") -> bool:
    for row in audit_service.search(limit=1000):
        if row["action"] == action and (not contains or contains in (row["details"] or "")):
            return True
    return False


def main() -> int:  # noqa: C901 — тестовый сценарий целиком
    paths.ensure_dirs()
    db = Database(TMP / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    print(f"\n=== Версия: {DISPLAY_NAME} (v{VERSION}), период аренды {RENTAL_PERIOD_DAYS} дн. ===")
    check(f"Название версии — {DISPLAY_NAME}",
          DISPLAY_NAME == f"СКЛАД РЕКВИЗИТА {VERSION}", DISPLAY_NAME)
    check("Номер версии задан", bool(VERSION) and VERSION[0].isdigit(), VERSION)
    check(f"Схема БД обновлена до v{DB_SCHEMA_VERSION}",
          get_db().query_one("PRAGMA user_version")[0] == DB_SCHEMA_VERSION)

    # ---------------------------------------------------------- Авторизация
    print("\n=== Авторизация (сценарии 1–8) ===")
    check("1. Первый запуск: пользователей нет", not auth_service.has_users())
    admin_id = auth_service.create_first_admin("Иван Петров", "admin", "admin123")
    check("2. Создан первоначальный администратор", admin_id > 0)
    stored = auth_service.get_user(admin_id)["password_hash"]
    check("   Пароль хранится только хэшем",
          "admin123" not in stored and stored.startswith(("scrypt$", "pbkdf2_sha256$", "$argon2")),
          stored.split("$")[0])
    check("   Мастер больше недоступен",
          _raises(lambda: auth_service.create_first_admin("X", "x", "12345"), PermissionDeniedError))

    admin = auth_service.login("admin", "admin123")
    check("3. Вход администратора", admin.is_admin and auth_service.is_admin())
    user_id = auth_service.create_user("Мария Сидорова", "maria", "maria123", "user")
    check("   Администратор создал обычного пользователя", user_id > 0)

    auth_service.logout()
    check("7. Выход: текущего пользователя нет", auth_service.current_user is None)
    check("5. Неверный пароль отклонён",
          _raises(lambda: auth_service.login("maria", "wrong"), AuthError))
    worker = auth_service.login("maria", "maria123")
    check("4. Вход обычного пользователя", worker.role == "user" and not worker.is_admin)
    check("8. Повторный вход работает", auth_service.current_user.login == "maria")

    auth_service.logout()
    auth_service.login("admin", "admin123")
    auth_service.update_user(user_id, is_active=False)
    check("6. Отключённый пользователь не может войти",
          _raises(lambda: auth_service.login("maria", "maria123"), AuthError))
    auth_service.logout()
    auth_service.login("admin", "admin123")
    auth_service.update_user(user_id, is_active=True)
    check("   Последнего администратора нельзя удалить",
          _raises(lambda: auth_service.delete_user(admin_id), AppError))

    # ------------------------------------------------------------ Подготовка
    settings_service.set_many({"warehouse.name": "Склад Реквизита «Кадр»"})
    chairs = inventory_service.create({"name": "Стул венский", "quantity_total": 40,
                                       "rental_price": 1000, "valuation": 7000})
    lamps = inventory_service.create({"name": "Лампа настольная", "quantity_total": 30,
                                      "rental_price": 1000, "valuation": 9000})
    props = inventory_service.create({"name": "Печатная машинка", "quantity_total": 20,
                                      "rental_price": 1000, "valuation": 20000})
    client_id = clients_service.create({"full_name": "Смирнов Пётр", "phone": "+7 900 000-00-00"})
    prod_id = productions_service.create({"name": "Продакшен «Кадр»", "legal_form": "ООО"})
    project_id = projects_service.create({"name": "Сериал «Осень»", "production_id": prod_id})

    # ---------------------------------------------------------------- Права
    print("\n=== Права (сценарии 9–14) ===")
    start = date(2026, 9, 1)
    lines = [RentalLine(chairs, "Стул венский", "", "", "", 20, 1000, 7000)]
    rental_id = rentals_service.create(RentalDraft(
        client_id=client_id, production_id=prod_id, project_id=project_id,
        payment_method="cash", date_start=start, date_end=None, lines=lines))
    rental = rentals_service.get(rental_id)
    check("15. Аренда на 20 000 ₽ создана", rental["total_final"] == 20000, str(rental["total_final"]))

    totals = rentals_service.set_discount(rental_id, 10)
    check("9. Администратор установил скидку", totals["discount_amount"] == 2000, str(totals))
    check("   Итог со скидкой 18 000 ₽", totals["total_final"] == 18000, str(totals["total_final"]))
    rentals_service.set_discount(rental_id, 0)

    auth_service.logout()
    auth_service.login("maria", "maria123")
    check("10/11. Обычный пользователь не может менять скидку",
          _raises(lambda: rentals_service.set_discount(rental_id, 15), PermissionDeniedError,
                  "Скидку может установить только администратор."))
    check("14. Защита именно в бизнес-логике (вызов сервиса напрямую)",
          rentals_service.get(rental_id)["discount_percent"] == 0)
    check("13. Обычный пользователь не может удалить позицию",
          _raises(lambda: rentals_service.remove_item(rental["items"][0]["id"]), PermissionDeniedError,
                  "Удалить реквизит из аренды может только администратор."))
    check("   Обычный пользователь не может создать аренду со скидкой",
          _raises(lambda: rentals_service.create(RentalDraft(
              client_id=client_id, production_id=prod_id, project_id=project_id,
              payment_method="cash", date_start=start, discount_percent=5,
              lines=[RentalLine(lamps, "Лампа", "", "", "", 1, 1000, 9000)])),
              PermissionDeniedError))
    check("   Обычный пользователь не может менять настройки склада",
          _raises(lambda: settings_service.set_protected({"warehouse.name": "Взлом"}),
                  PermissionDeniedError))
    check("   Обычный пользователь не может управлять пользователями",
          _raises(lambda: auth_service.create_user("Хакер", "hack", "12345"), PermissionDeniedError))
    check("   Обычный пользователь МОЖЕТ создать обычную аренду", rentals_service.create(RentalDraft(
        client_id=client_id, production_id=prod_id, project_id=project_id, payment_method="cash",
        date_start=start, lines=[RentalLine(lamps, "Лампа", "", "", "", 1, 1000, 9000)])) > 0)

    auth_service.logout()
    auth_service.login("admin", "admin123")

    # ------------------------------------------------- 14 дней и пролонгация
    print("\n=== 14-дневные периоды и пролонгация (сценарии 16–25) ===")
    items = billing_service.items(rental_id)
    first = items[0]
    p_start, p_end = period_bounds(start, 1)
    check("Период №1 = 01.09–14.09", (p_start, p_end) == (date(2026, 9, 1), date(2026, 9, 14)),
          f"{p_start}–{p_end}")
    check("Позиция знает свой период", first["period_start"] == "2026-09-01"
          and first["period_end"] == "2026-09-14", f"{first['period_start']}–{first['period_end']}")
    check("Следующая пролонгация 15.09", first["next_prolongation"] == "2026-09-15",
          first["next_prolongation"])
    check("Цена не зависит от числа дней (1 день = полный период)",
          first["initial_amount"] == 20000)

    # 16–17. частичный возврат 5 позиций 08.09
    billing_service.return_item(first["id"], 5, "возврат 5 стульев")
    after_return = billing_service.items(rental_id)[0]
    check("16. Частичный возврат зафиксирован", after_return["returned_qty"] == 5)
    check("   Дата возврата сохранена в истории",
          len(after_return["returns"]) == 1 and after_return["returns"][0]["quantity"] == 5)
    check("   Возврат не уменьшил начисленный период",
          rentals_service.get(rental_id)["total_before_discount"] == 20000,
          str(rentals_service.get(rental_id)["total_before_discount"]))

    # 18. автоматическая пролонгация на 15.09 — только на оставшиеся 15
    created_all = billing_service.run_prolongations(date(2026, 9, 15))
    created = [c for c in created_all if c["rental_id"] == rental_id]
    check("18. Автопролонгация начислена", len(created) == 1, str(created_all))
    check("17. Пролонгация только на оставшиеся 15 шт. = 15 000 ₽",
          created and created[0]["amount"] == 15000 and created[0]["quantity"] == 15,
          str(created))
    totals = rentals_service.get(rental_id)
    check("   Общая начисленная сумма 35 000 ₽", totals["total_before_discount"] == 35000,
          str(totals["total_before_discount"]))
    item = billing_service.items(rental_id)[0]
    check("   Период позиции стал №2 (15.09–28.09)",
          item["period_number"] == 2 and item["period_end"] == "2026-09-28",
          f"{item['period_number']} {item['period_start']}–{item['period_end']}")
    check("   Статус позиции 🔴 Пролонгировано", item["state"] == "prolonged", item["state_label"])

    # 25. повторный запуск не дублирует
    again = billing_service.run_prolongations(date(2026, 9, 15))
    again2 = billing_service.run_prolongations(date(2026, 9, 20))
    check("25. Повторная пролонгация не начисляется", len(again) == 0 and len(again2) == 0)
    check("   Сумма не изменилась",
          rentals_service.get(rental_id)["total_before_discount"] == 35000)

    # ------------------------------------------------------- добор реквизита
    print("\n=== Добор в действующую аренду (сценарии 19–23) ===")
    import app.utils as utils_module
    real_now = utils_module.now

    def fake_now_factory(moment: datetime):
        return lambda: moment

    # 05.09 добор на 10 000 ₽
    utils_module.now = fake_now_factory(datetime(2026, 9, 5, 14, 30))
    import app.services.billing_service as billing_module
    import app.services.rentals_service as rentals_module
    billing_module.now = utils_module.now
    rentals_module.now = utils_module.now
    added = rentals_service.add_items(rental_id, [
        RentalLine(lamps, "Лампа настольная", "", "", "", 10, 1000, 9000)])
    check("19. Добор на 10 000 ₽ выполнен", len(added) == 1)
    group_b = [i for i in billing_service.items(rental_id) if i["id"] == added[0]][0]
    check("20. Дата начала новых позиций — день добора (05.09)",
          group_b["date_start"][:10] == "2026-09-05", group_b["date_start"])
    check("21. Их период 05.09–18.09, пролонгация 19.09",
          group_b["period_start"] == "2026-09-05" and group_b["period_end"] == "2026-09-18"
          and group_b["next_prolongation"] == "2026-09-19",
          f"{group_b['period_start']}–{group_b['period_end']} → {group_b['next_prolongation']}")
    old_group = [i for i in billing_service.items(rental_id) if i["id"] == first["id"]][0]
    check("6. Сроки старых позиций не сдвинулись",
          old_group["period_start"] == "2026-09-15" and old_group["date_start"][:10] == "2026-09-01",
          f"{old_group['date_start']} / {old_group['period_start']}")
    check("   Сумма аренды выросла до 45 000 ₽",
          rentals_service.get(rental_id)["total_before_discount"] == 45000,
          str(rentals_service.get(rental_id)["total_before_discount"]))

    # 22. вторая группа добора 12.09
    utils_module.now = fake_now_factory(datetime(2026, 9, 12, 11, 0))
    billing_module.now = utils_module.now
    rentals_module.now = utils_module.now
    added2 = rentals_service.add_items(rental_id, [
        RentalLine(props, "Печатная машинка", "", "", "", 5, 1000, 20000)])
    group_c = [i for i in billing_service.items(rental_id) if i["id"] == added2[0]][0]
    check("22. Группа от 12.09 имеет свой срок 12.09–25.09, пролонгация 26.09",
          group_c["period_start"] == "2026-09-12" and group_c["next_prolongation"] == "2026-09-26",
          f"{group_c['period_start']}–{group_c['period_end']} → {group_c['next_prolongation']}")

    # 23. три независимых периода внутри одной аренды
    all_items = billing_service.items(rental_id)
    starts = sorted({i["date_start"][:10] for i in all_items})
    check("23. Внутри одной аренды три группы с разными датами начала",
          starts == ["2026-09-01", "2026-09-05", "2026-09-12"], str(starts))

    # пролонгация 19.09 — только группа Б
    created_b = [c for c in billing_service.run_prolongations(date(2026, 9, 19))
                 if c["rental_id"] == rental_id]
    check("   19.09 пролонгирована только группа от 05.09 (10 000 ₽)",
          len(created_b) == 1 and created_b[0]["amount"] == 10000
          and created_b[0]["rental_item_id"] == added[0], str(created_b))
    check("   Группа от 12.09 ещё не пролонгирована",
          [i for i in billing_service.items(rental_id) if i["id"] == added2[0]][0]["prolongations"] == 0)

    # 24. частичный возврат после пролонгации
    billing_service.return_item(first["id"], 10, "вернули ещё 10 после пролонгации")
    after = [i for i in billing_service.items(rental_id) if i["id"] == first["id"]][0]
    check("24. Частичный возврат после пролонгации принят", after["returned_qty"] == 15)
    # 20 000 (01.09) + 10 000 (добор 05.09) + 5 000 (добор 12.09)
    # + 15 000 (пролонгация 15.09) + 10 000 (пролонгация 19.09)
    check("   Уже начисленная пролонгация не отменена",
          rentals_service.get(rental_id)["total_before_discount"] == 60000,
          str(rentals_service.get(rental_id)["total_before_discount"]))
    created_after = billing_service.run_prolongations(date(2026, 9, 29))

    chair_charge = [c for c in created_after if c["rental_item_id"] == first["id"]]
    check("17. После возврата пролонгируются только оставшиеся 5 шт.",
          chair_charge and chair_charge[0]["amount"] == 5000, str(chair_charge))

    # полный возврат — прекращение пролонгаций
    billing_service.return_item(first["id"], 5, "возврат остатка")
    closed = [i for i in billing_service.items(rental_id) if i["id"] == first["id"]][0]
    check("   Полностью возвращённая позиция получила статус «Возвращено»",
          closed["status"] == "returned" and closed["state_label"] == "Возвращено",
          closed["state_label"])
    before_total = rentals_service.get(rental_id)["total_before_discount"]
    billing_service.run_prolongations(date(2026, 12, 31))
    after_total = rentals_service.get(rental_id)["total_before_discount"]
    chair_now = [i for i in billing_service.items(rental_id) if i["id"] == first["id"]][0]
    check("17. Возвращённая позиция больше не пролонгируется",
          chair_now["period_number"] == 3, f"период {chair_now['period_number']}")
    check("   Остальные позиции пролонгируются дальше", after_total > before_total)

    utils_module.now = real_now
    billing_module.now = real_now
    rentals_module.now = real_now

    # --------------------------------------------------- история начислений
    print("\n=== История начислений (пункт 8) ===")
    charges = billing_service.charges_grouped(rental_id)
    kinds = {c["type"] for c in charges}
    check("Есть начисления всех трёх видов", kinds == {"initial", "addition", "prolongation"},
          str(kinds))
    first_line = charges[0]
    check("Первая запись — первоначальная аренда 01.09 на 20 000 ₽",
          first_line["date"] == "2026-09-01" and first_line["amount"] == 20000, str(first_line))
    addition = [c for c in charges if c["type"] == "addition" and c["date"] == "2026-09-05"]
    check("Есть запись «Добор реквизита» 05.09 на 10 000 ₽",
          addition and addition[0]["amount"] == 10000, str(addition))
    prolong_15 = [c for c in charges if c["type"] == "prolongation" and c["date"] == "2026-09-15"]
    check("Есть запись «Пролонгация» 15.09 на 15 000 ₽",
          prolong_15 and prolong_15[0]["amount"] == 15000, str(prolong_15))
    prolong_19 = [c for c in charges if c["type"] == "prolongation" and c["date"] == "2026-09-19"]
    check("Есть запись «Пролонгация» 19.09 на 10 000 ₽",
          prolong_19 and prolong_19[0]["amount"] == 10000, str(prolong_19))
    check("Каждое начисление связано с позицией",
          all(c["rental_item_id"] for c in billing_service.charges(rental_id)))

    summary = billing_service.rental_summary(rental_id)
    check("Сводка показывает пролонгированные позиции", len(summary["prolonged_positions"]) >= 2)
    check("Сводка считает возвращённые позиции", summary["positions_returned"] >= 1)

    # -------------------------------------------------- скидка и пролонгация
    print("\n=== Скидка поверх всех начислений (пункт 23) ===")
    accrued = rentals_service.get(rental_id)["total_before_discount"]
    totals = rentals_service.set_discount(rental_id, 20)
    check("Скидка применяется ко всей начисленной сумме",
          abs(totals["discount_amount"] - round(accrued * 0.2, 2)) < 0.01, str(totals))
    check("Итог = начислено − скидка",
          abs(totals["total_final"] - (accrued - totals["discount_amount"])) < 0.01)

    # ------------------------------------------------------------- документы
    print("\n=== Документы и совместимость ===")
    doc_id = documents_service.create_document(rental_id)
    doc = documents_service.get(doc_id)
    check("PDF документа v2 создан", doc["abs_path"] and Path(doc["abs_path"]).exists())
    check("PDF настоящий", Path(doc["abs_path"]).read_bytes()[:4] == b"%PDF")
    snap = documents_service.get_snapshot(doc_id)
    check("В снимке есть периоды позиций",
          all("period_end" in i for i in snap["items"]))
    check("В снимке есть история начислений", len(snap["charges"]) >= 4)
    html = documents_service.render_html(doc_id)
    check("HTML документа содержит колонку периода", "Период" in html)

    # ----------------------------------------------------------- доступность
    free_later = inventory_service.available_for_period(chairs, date(2026, 10, 1), date(2026, 10, 5))
    check("Возвращённый реквизит свободен на даты после возврата",
          free_later == 40, str(free_later))
    busy_during = inventory_service.available_for_period(chairs, date(2026, 9, 2), date(2026, 9, 3))
    check("В дни, когда реквизит был у клиента, он занят", busy_during == 20, str(busy_during))

    # ------------------------------------------------------------- AuditLog
    print("\n=== AuditLog (сценарии 26–34) ===")
    check("26. Вход пользователя записан", audit_has("Вход в программу"))
    check("27. Неудачный вход записан", audit_has("Неудачный вход"))
    check("   Выход записан", audit_has("Выход из программы"))
    check("28. Создание аренды записано", audit_has("Создана аренда"))
    check("29. Добор реквизита записан", audit_has("Добор реквизита в аренду"))
    check("30. Возврат позиции записан", audit_has("Возврат позиции"))
    check("31. Автоматическая пролонгация записана", audit_has("Автоматическая пролонгация"))
    auto = [r for r in audit_service.search(limit=1000) if r["action"] == "Автоматическая пролонгация"]
    check("   Пролонгация записана от имени «Система» с суммой и номером периода",
          auto and auto[0]["user_name"] == "Система" and auto[0]["amount"]
          and "период №" in auto[0]["details"], str(auto[:1]))
    check("32. Назначение скидки записано", audit_has("Назначена скидка"))
    check("34. Действия с пользователями записаны",
          audit_has("Создан пользователь") and audit_has("Изменён пользователь"))
    check("   Отказ в доступе записан", audit_has("Отказано в доступе"))
    check("   Реквизит и ремонт пишутся в журнал", audit_has("Добавлен реквизит"))

    # 33. удаление позиции администратором
    victim = rentals_service.create(RentalDraft(
        client_id=client_id, production_id=prod_id, project_id=project_id, payment_method="cash",
        date_start=date.today(), lines=[RentalLine(props, "Печатная машинка", "", "", "", 2, 1000, 20000)]))
    victim_item = billing_service.items(victim)[0]["id"]
    rentals_service.remove_item(victim_item)
    check("12. Администратор удалил позицию из аренды", len(billing_service.items(victim)) == 0)
    check("33. Удаление позиции записано в журнал", audit_has("Удалена позиция из аренды"))
    check("   Итоги пересчитаны после удаления",
          rentals_service.get(victim)["total_before_discount"] == 0)

    # ---------------------------------------------------- данные и миграция
    print("\n=== Данные, backup, миграция (сценарии 35–38) ===")
    backup_path = backup_service.create(tag="v2test")
    check("37. Резервная копия создана", backup_path.exists())
    tmp_item = inventory_service.create({"name": "Временный", "quantity_total": 1})
    backup_service.restore(backup_path)
    check("38. Восстановление работает", inventory_service.get(tmp_item) is None)
    check("   Аренда и её начисления сохранились после восстановления",
          rentals_service.get(rental_id) is not None
          and len(billing_service.charges(rental_id)) >= 5)
    check("   Целостность базы после всех операций",
          get_db().query_one("PRAGMA integrity_check")[0] == "ok")

    print(f"\n{'=' * 62}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    for name in FAILED:
        print("  FAIL:", name)
    print(f"Данные теста: {TMP}")
    return 1 if FAILED else 0


def _raises(action, exception_type, message_part: str = "") -> bool:
    try:
        action()
    except exception_type as exc:
        return message_part in str(exc) if message_part else True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    code = main()
    try:
        get_db().close()
    except Exception:
        pass
    sys.exit(code)
