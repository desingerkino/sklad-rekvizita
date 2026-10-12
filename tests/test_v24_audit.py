# -*- coding: utf-8 -*-
"""
Тесты версии 2.4, часть 2: инвентаризация и сканирование.

Запуск:  python -m tests.test_v24_audit
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_v24a_"))
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
    except Exception as exc:                                       # noqa: BLE001
        failures.append(f"{title} ({type(exc).__name__}: {exc})")
        print(f"   ОШИБКА {title} — неожиданная ошибка {type(exc).__name__}: {exc}")
    else:
        failures.append(f"{title} (ошибки не было)")
        print(f"   ОШИБКА {title} — ошибки не было, хотя она обязана быть")


def main() -> int:
    from datetime import timedelta

    from app.db.database import Database, get_db, set_db
    from app.db.seed import seed_if_empty
    from app.errors import PermissionDeniedError, ValidationError
    from app.services import inventory_audit_service as module
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.inventory_audit_service import inventory_audit_service as audits
    from app.services.inventory_service import inventory_service
    from app.services.rentals_service import RentalDraft, RentalLine, rentals_service
    from app.services.repairs_service import repairs_service
    from app.services.clients_service import clients_service
    from app.services.productions_service import productions_service
    from app.services.projects_service import projects_service
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

    chairs = inventory_service.create({"name": "Стул венский", "category_id": cat,
                                       "quantity_total": 10, "rental_price": 100})
    lamps = inventory_service.create({"name": "Лампа банкирская", "category_id": cat,
                                      "quantity_total": 3, "rental_price": 200})
    books = inventory_service.create({"name": "Книги стопкой", "category_id": cat,
                                      "quantity_total": 20, "rental_price": 50})
    check(all(inventory_service.get(i)["barcode"] for i in (chairs, lamps, books)),
          "У всех предметов есть штрихкоды")

    client_id = clients_service.create({"full_name": "Петров Пётр", "phone": "+70000000000"})
    prod_id = productions_service.create({"name": "Кинокомпания «Кадр»", "legal_form": "ООО"})
    project_id = projects_service.create({"name": "Фильм «Проба»", "production_id": prod_id})
    draft = RentalDraft(client_id=client_id, production_id=prod_id, project_id=project_id,
                        date_start=today(), date_end=today() + timedelta(days=7),
                        lines=[RentalLine(chairs, "Стул венский", "", "", "", 4, 100, 0)])
    rental_id = rentals_service.create(draft)
    check(rental_id > 0, "Оформлена аренда на 4 стула")

    repairs_service.send_to_repair(lamps, 1, "скол плафона", "")
    check(inventory_service.in_repair(lamps) == 1, "Одна лампа отправлена в ремонт")

    section("Создание инвентаризации")
    audit_id = audits.create(title="Годовая сверка", responsible="Иванов Иван")
    audit = audits.get(audit_id)
    check(audit["number"].startswith("ИНВ-"), f"Номер выдан ({audit['number']})")
    check(audit["status"] == "open", "Инвентаризация открыта")
    check(audits.active() is not None, "Активная инвентаризация видна")
    expect_error(lambda: audits.create(), ValidationError,
                 "Вторую инвентаризацию одновременно начать нельзя")

    lines = {l["item_id"]: l for l in audits.lines(audit_id)}
    check(lines[chairs]["expected_qty"] == 6,
          f"Стулья: числится 6 из 10 (4 у клиента) — получено {lines[chairs]['expected_qty']}")
    check(lines[chairs]["rented_qty"] == 4, "Стулья: аренда учтена отдельно")
    check(lines[lamps]["expected_qty"] == 2,
          f"Лампы: числится 2 из 3 (1 в ремонте) — получено {lines[lamps]['expected_qty']}")
    check(lines[books]["expected_qty"] == 20, "Книги: числится 20")

    section("Снимок не меняется задним числом")
    draft2 = RentalDraft(client_id=client_id, production_id=prod_id, project_id=project_id,
                         date_start=today(), date_end=today() + timedelta(days=3),
                         lines=[RentalLine(books, "Книги стопкой", "", "", "", 5, 50, 0)])
    rentals_service.create(draft2)
    again = {l["item_id"]: l for l in audits.lines(audit_id)}
    check(again[books]["expected_qty"] == 20,
          "Новая аренда во время сверки не изменила «числится»")

    section("Сканирование")
    chair_code = inventory_service.get(chairs)["barcode"]
    result = audits.scan(audit_id, chair_code)
    check(result["status"] == module.SCAN_COUNTED, "Первое сканирование засчитано")
    check(result["found"] == 1, "Найдено 1")

    for _ in range(5):
        audits.scan(audit_id, chair_code)
    line = {l["item_id"]: l for l in audits.lines(audit_id)}[chairs]
    check(line["found_qty"] == 6, f"Шесть стульев учтены (получено {line['found_qty']})")

    over = audits.scan(audit_id, chair_code)
    check(over["status"] == module.SCAN_OVER, "Седьмое сканирование не засчитано автоматически")
    check("уже учтён полностью" in over["message"], "Оператору объяснено, почему")
    line = {l["item_id"]: l for l in audits.lines(audit_id)}[chairs]
    check(line["found_qty"] == 6, "Отклонённое сканирование ничего не записало")

    confirmed = audits.scan(audit_id, chair_code, allow_over=True)
    check(confirmed["status"] == module.SCAN_COUNTED and confirmed["found"] == 7,
          "После подтверждения излишек записан")
    audits.set_found(audit_id, line["id"], 6)
    check({l["item_id"]: l for l in audits.lines(audit_id)}[chairs]["found_qty"] == 6,
          "Ручная правка количества работает")

    check(audits.scan(audit_id, "НЕТ-ТАКОГО-КОДА")["status"] == module.SCAN_NOT_FOUND,
          "Неизвестный штрихкод не найден")
    check("не найден" in audits.scan(audit_id, "0000999")["message"],
          "Сообщение о ненайденном коде понятное")

    section("Излишек: предмет появился после начала сверки")
    late = inventory_service.create({"name": "Телефон дисковый", "category_id": cat,
                                     "quantity_total": 2})
    late_code = inventory_service.get(late)["barcode"]
    result = audits.scan(audit_id, late_code)
    check(result["status"] == module.SCAN_UNEXPECTED, "Новый предмет отмечен как излишек")
    extra = [l for l in audits.lines(audit_id) if l["item_id"] == late][0]
    check(extra["unexpected"] == 1 and extra["expected_qty"] == 0,
          "Строка излишка заведена корректно")
    check(audits.scan(audit_id, late_code)["found"] == 2,
          "Повторное сканирование излишка увеличивает счёт")

    section("Расхождения и итоги")
    lamp_code = inventory_service.get(lamps)["barcode"]
    audits.scan(audit_id, lamp_code)                    # 1 из 2 — недостача 1
    # книги не сканируем вовсе — недостача 20

    summary = audits.summary(audit_id)
    check(summary["expected_units"] == 28,
          f"Числится всего 28 шт. (получено {summary['expected_units']})")
    check(summary["found_units"] == 9, f"Найдено 9 шт. (получено {summary['found_units']})")
    check(summary["missing_units"] == 21,
          f"Недостача 21 шт. (получено {summary['missing_units']})")
    check(summary["extra_units"] == 2, f"Излишки 2 шт. (получено {summary['extra_units']})")
    check(summary["positions_ok"] == 1, "Полностью совпала одна позиция (стулья)")
    check(summary["positions_missing"] == 1, "Совсем не найдена одна позиция (книги)")
    check(summary["positions_partial"] == 1, "Частично найдена одна позиция (лампы)")
    check(abs(summary["match_percent"] - 25.0) < 0.1,
          f"Процент соответствия 25% (получено {summary['match_percent']})")
    check(summary["rented_units"] == 4 and summary["repair_units"] == 1,
          "Справочные числа по аренде и ремонту верны")

    missing = audits.lines(audit_id, "missing")
    check(len(missing) == 1 and missing[0]["item_id"] == books, "Фильтр «не найдено» работает")
    extra_lines = audits.lines(audit_id, "extra")
    check(any(l["item_id"] == late for l in extra_lines), "Фильтр «излишки» работает")

    section("Инвентаризация не трогает склад")
    before = inventory_service.get(books)["quantity_total"]
    audits.finish(audit_id, comment="Недостачу разбираем отдельно")
    check(inventory_service.get(books)["quantity_total"] == before,
          "Количество на складе после завершения не изменилось")
    check(audits.get(audit_id)["status"] == "done", "Инвентаризация завершена")
    check(audits.active() is None, "Активной инвентаризации больше нет")
    expect_error(lambda: audits.scan(audit_id, chair_code), ValidationError,
                 "В закрытую инвентаризацию сканировать нельзя")
    expect_error(lambda: audits.finish(audit_id), ValidationError,
                 "Завершить дважды нельзя")

    section("Отчёт PDF")
    report = audits.report_pdf(audit_id, _TMP / "report.pdf")
    check(report.exists() and report.stat().st_size > 3000, "Отчёт PDF сформирован")
    data = report.read_bytes()
    check(b"%PDF" == data[:4], "Файл — настоящий PDF")

    section("История и журнал")
    check(len(audits.list()) == 1, "Инвентаризация видна в списке")
    log_rows = get_db().query(
        "SELECT action FROM audit_log WHERE entity='audit' ORDER BY id")
    actions = [r["action"] for r in log_rows]
    for expected_action in ("Начата инвентаризация", "Завершена инвентаризация",
                            "Экспорт отчёта инвентаризации"):
        check(expected_action in actions, f"В журнале есть «{expected_action}»")

    section("Права")
    auth_service.create_user("Сидоров Сидор", "sidorov", "1234", role="user")
    auth_service.logout()
    auth_service.login("sidorov", "1234")
    expect_error(lambda: audits.create(title="Своя сверка"), PermissionDeniedError,
                 "Обычный пользователь не может начать инвентаризацию")
    auth_service.logout()
    auth_service.login("admin", "1234")
    second = audits.create(title="Вторая сверка")
    auth_service.logout()
    auth_service.login("sidorov", "1234")
    scan_ok = audits.scan(second, chair_code)
    check(scan_ok["status"] == module.SCAN_COUNTED,
          "Обычный пользователь может сканировать в открытой сверке")
    expect_error(lambda: audits.finish(second), PermissionDeniedError,
                 "Обычный пользователь не может завершить инвентаризацию")
    auth_service.logout()
    auth_service.login("admin", "1234")
    audits.cancel(second)
    check(audits.get(second)["status"] == "cancelled", "Отмена инвентаризации работает")

    section("Интерфейс: сканирование и раздел инвентаризации")
    _check_ui()

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки инвентаризации пройдены.")
    return 0


def _check_ui() -> None:
    """
    Проверка по исходникам: PySide6 в этой среде не запускается, но убедиться,
    что сканирование действительно подключено к экранам выдачи, возврата и
    инвентаризации, а раздел виден в меню, можно и так.
    """
    root = Path(__file__).resolve().parent.parent / "app" / "ui"
    src = {name: (root / name).read_text(encoding="utf-8")
           for name in ("main_window.py", "rental_new_view.py", "returns_view.py",
                        "inventory_audit_view.py")}
    src["scan_input.py"] = (root / "widgets" / "scan_input.py").read_text(encoding="utf-8")

    check("class ScanInput" in src["scan_input.py"], "Виджет сканирования существует")
    check("def take_focus" in src["scan_input.py"],
          "Поле само возвращает себе фокус — сканеру нужен курсор в поле")
    check("returnPressed" in src["scan_input.py"],
          "Enter от сканера обрабатывается как подтверждение кода")

    check("Инвентаризация" in src["main_window.py"] and "PAGE_AUDIT" in src["main_window.py"],
          "Раздел «Инвентаризация» есть в меню")
    check("InventoryAuditView()" in src["main_window.py"],
          "Экран инвентаризации создаётся в главном окне")
    check("self.audit.changed.connect" in src["main_window.py"],
          "Изменения инвентаризации обновляют сводку")

    check("ScanInput(" in src["rental_new_view.py"] and "def _scanned" in src["rental_new_view.py"],
          "На выдаче есть режим сканирования")
    check("barcode_service.find_item" in src["rental_new_view.py"],
          "Выдача ищет предмет по штрихкоду")
    check("не найден" in src["rental_new_view.py"],
          "Ненайденный код на выдаче даёт понятное сообщение")

    check("ScanInput(" in src["returns_view.py"] and "def _scanned" in src["returns_view.py"],
          "На возврате есть режим сканирования")
    check("quantity > ri.returned_qty" in src["returns_view.py"],
          "Возврат ищет только невозвращённые позиции")
    check("self.accept_return()" in src["returns_view.py"]
          and "QInputDialog" in src["returns_view.py"],
          "Возврат по сканированию требует подтверждения человеком")
    check("ни в одной аренде не числится" in src["returns_view.py"],
          "Предмет вне аренды не меняет данные, а вызывает предупреждение")
    check("self.scan.take_focus()" in src["returns_view.py"],
          "После приёма курсор возвращается в поле сканирования")

    check("def _scanned" in src["inventory_audit_view.py"]
          and "SCAN_OVER" in src["inventory_audit_view.py"],
          "Инвентаризация обрабатывает превышение количества")
    check("confirm(self" in src["inventory_audit_view.py"],
          "Лишний экземпляр записывается только после подтверждения")
    check("Добавить вручную" in src["inventory_audit_view.py"],
          "Есть ручное добавление на случай повреждённой этикетки")
    check("is_admin" in src["inventory_audit_view.py"],
          "Завершение инвентаризации ограничено правами")


if __name__ == "__main__":
    raise SystemExit(main())
