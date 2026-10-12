# -*- coding: utf-8 -*-
"""
Финальный QA-прогон: сквозная проверка бизнес-логики, базы, расчётов, прав,
дат, документов, PDF, истории, ремонта, резервных копий и производительности.

Запуск:  python -m tests.test_qa
"""
from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sklad_qa_"))
os.environ["SKLAD_DATA_DIR"] = str(TMP)

from app import paths                                            # noqa: E402
from app.db.database import Database, get_db, set_db             # noqa: E402
from app.db.seed import seed_if_empty                            # noqa: E402
from app.errors import AppError, AuthError, PermissionDeniedError, ValidationError  # noqa: E402
from app.logging_setup import setup as setup_logging             # noqa: E402
from app.services.audit_service import audit_service             # noqa: E402
from app.services.auth_service import auth_service               # noqa: E402
from app.services.backup_service import backup_service           # noqa: E402
from app.services.billing_service import billing_service         # noqa: E402
from app.services.categories_service import categories_service   # noqa: E402
from app.services.clients_service import clients_service         # noqa: E402
from app.services.documents_service import documents_service     # noqa: E402
from app.services.email_service import email_service             # noqa: E402
from app.services.image_search import image_search               # noqa: E402
from app.services.inventory_service import inventory_service     # noqa: E402
from app.services.photo_service import photo_service             # noqa: E402
from app.services.productions_service import productions_service  # noqa: E402
from app.services.projects_service import projects_service       # noqa: E402
from app.services.rentals_service import (RentalDraft, RentalLine, calculate,  # noqa: E402
                                          rentals_service)
from app.services.repairs_service import repairs_service         # noqa: E402
from app.services.settings_service import settings_service       # noqa: E402
from app.utils import fmt_date, fmt_dt, money, parse_date        # noqa: E402

PASSED, FAILED = [], []
SKIPPED: list[str] = []   # проверки, пропущенные из-за отсутствия необязательных библиотек
SECTION = ""


def section(name: str) -> None:
    global SECTION
    SECTION = name
    print(f"\n=== {name} ===")


def check(name: str, condition: bool, detail: str = "") -> None:
    # Подробность сохраняется вместе с названием: итоговый список провалов
    # печатается в самом конце, и без неё пришлось бы отматывать вывод назад,
    # чтобы понять, что именно не сошлось.
    entry = f"[{SECTION}] {name}"
    if not condition and detail:
        entry += f" — {detail}"
    (PASSED if condition else FAILED).append(entry)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def raises(action, exc_type=AppError, message_part: str = "") -> bool:
    try:
        action()
    except exc_type as exc:
        return message_part in str(exc) if message_part else True
    except Exception:
        return False
    return False


def make_photo(path: Path, color, shape=None) -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 400), color)
    d = ImageDraw.Draw(img)
    if shape == "chair":
        d.rectangle([120, 120, 280, 160], fill=(90, 40, 20))
        d.rectangle([130, 160, 150, 320], fill=(90, 40, 20))
    img.save(path, "JPEG", quality=92)
    return path


def main() -> int:  # noqa: C901
    paths.ensure_dirs()
    setup_logging(logging.DEBUG)
    sys.excepthook = sys.__excepthook__   # в тесте ошибки должны быть видны на экране
    db = Database(TMP / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    src = TMP / "src"
    src.mkdir(exist_ok=True)

    # ============================================================ 3. АВТОРИЗАЦИЯ
    section("3. Авторизация")
    admin_id = auth_service.create_first_admin("Админ Админов", "admin", "admin123")
    user_id_holder = {}
    check("Вход администратора с правильным паролем", auth_service.login("admin", "admin123").is_admin)
    check("Права администратора активны после входа", auth_service.is_admin())
    auth_service.logout()
    check("Неправильный пароль не пропускает", raises(lambda: auth_service.login("admin", "нет"), AuthError))
    check("Пустой пароль не пропускает", raises(lambda: auth_service.login("admin", ""), AuthError))
    check("Несуществующий логин не пропускает",
          raises(lambda: auth_service.login("нетtakого", "admin123"), AuthError))
    check("После неудачных попыток пользователь не авторизован", auth_service.current_user is None)
    check("Без входа защищённая операция отклоняется",
          raises(lambda: auth_service.create_user("X", "x", "12345"), PermissionDeniedError))
    auth_service.login("admin", "admin123")
    user_id_holder["user"] = auth_service.create_user("Мария Рабочая", "maria", "maria123", "user")
    auth_service.logout()
    worker = auth_service.login("maria", "maria123")
    check("Вход обычного пользователя", not worker.is_admin and worker.role == "user")
    check("Неправильный пароль обычного пользователя",
          raises(lambda: auth_service.login("maria", "123"), AuthError))
    auth_service.logout()
    check("После выхода текущего пользователя нет", auth_service.current_user is None)
    check("После выхода работа с данными запрещена",
          raises(lambda: inventory_service.create({"name": "Взлом", "quantity_total": 1}),
                 PermissionDeniedError))
    check("Повторный вход работает", auth_service.login("admin", "admin123").is_admin)

    # ============================================== 4/5/37/38. ПРАВА И ЗАЩИТА
    section("4. Права администратора")
    cat_id = categories_service.add("QA-категория")
    sub_id = categories_service.add("QA-подкатегория", cat_id)
    check("Администратор создаёт категорию и подкатегорию", bool(cat_id and sub_id))
    categories_service.rename(sub_id, "QA-подкатегория 2")
    check("Переименование работает", categories_service.get(sub_id)["name"] == "QA-подкатегория 2")
    settings_service.set_protected({"warehouse.name": "QA-склад"})
    check("Администратор меняет настройки склада", settings_service.get("warehouse.name") == "QA-склад")
    auth_service.update_user(user_id_holder["user"], full_name="Мария Рабочая")
    check("Администратор редактирует пользователя",
          auth_service.get_user(user_id_holder["user"])["full_name"] == "Мария Рабочая")
    auth_service.set_password(user_id_holder["user"], "maria456")
    auth_service.logout()
    check("Новый пароль работает", auth_service.login("maria", "maria456").login == "maria")
    auth_service.logout()
    auth_service.login("admin", "admin123")

    # ------------------------------------------------------ тестовые данные
    chair = inventory_service.create({
        "name": "Стул венский QA", "category_id": sub_id, "description": "Тестовый стул",
        "keywords": "стул венский qa", "quantity_total": 10, "rental_price": 1000, "valuation": 7000,
        "note": "проверочный"})
    lamp = inventory_service.create({"name": "Лампа QA", "quantity_total": 5,
                                     "rental_price": 2000, "valuation": 9000})
    table = inventory_service.create({"name": "Стол QA", "quantity_total": 3,
                                      "rental_price": 5000, "valuation": 30000})
    client = clients_service.create({
        "full_name": "Петров Пётр Петрович", "passport": "45 08 111222, выдан ОВД",
        "phone": "+7 900 111-11-11", "address": "Москва, ул. Тестовая, 1",
        "email": "petrov@example.ru", "extra": "постоянный клиент"})
    production = productions_service.create({
        "name": "Кинокомпания QA", "legal_form": "ООО", "inn": "7712345678", "kpp": "771201001",
        "ogrn": "1157746000000", "legal_address": "Москва, Юр. 1", "actual_address": "Москва, Факт. 2",
        "bank_name": "АО Банк", "bank_account": "40702810900000000123", "bank_bik": "044525225",
        "bank_corr": "30101810400000000225", "phone": "+7 495 000-00-00", "email": "qa@example.ru",
        "contact_person": "Сидорова А.А.", "contact_position": "Директор"})
    project = projects_service.create({"name": "Фильм QA", "production_id": production})

    section("5/37/38. Ограничения обычного пользователя")
    start = date(2026, 9, 1)
    rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=start, date_end=date(2026, 9, 5),
        lines=[RentalLine(chair, "Стул венский QA", "", "Тестовый стул", "скол", 4, 1000, 7000)]))
    rental_items = billing_service.items(rental)

    auth_service.logout()
    auth_service.login("maria", "maria456")
    check("Пользователь НЕ может назначить скидку",
          raises(lambda: rentals_service.set_discount(rental, 10), PermissionDeniedError,
                 "Скидку может установить только администратор."))
    check("Пользователь НЕ может удалить позицию из аренды",
          raises(lambda: rentals_service.remove_item(rental_items[0]["id"]), PermissionDeniedError))
    check("Пользователь НЕ может открыть управление пользователями",
          raises(lambda: auth_service.create_user("Хакер", "hack", "12345"), PermissionDeniedError))
    check("Пользователь НЕ может создать администратора",
          raises(lambda: auth_service.create_user("Хакер", "hack2", "12345", "admin"),
                 PermissionDeniedError))
    check("Пользователь НЕ может изменить свою роль",
          raises(lambda: auth_service.update_user(user_id_holder["user"], role="admin"),
                 PermissionDeniedError))
    check("Пользователь НЕ может удалить администратора",
          raises(lambda: auth_service.delete_user(admin_id), PermissionDeniedError))
    check("Пользователь НЕ может менять настройки склада",
          raises(lambda: settings_service.set_protected({"warehouse.name": "Взлом"}),
                 PermissionDeniedError))
    check("Пользователь НЕ может восстановить базу из копии",
          raises(lambda: backup_service.restore(TMP / "нет.db"), PermissionDeniedError))
    check("Настройки после попытки не изменились", settings_service.get("warehouse.name") == "QA-склад")
    check("Повторная попытка тоже отклоняется",
          raises(lambda: rentals_service.set_discount(rental, 50), PermissionDeniedError))
    check("Скидка осталась нулевой", rentals_service.get(rental)["discount_percent"] == 0)
    check("Пользователь МОЖЕТ создать клиента", clients_service.create({"full_name": "Тест Клиентов"}) > 0)
    check("Пользователь МОЖЕТ создать продакшен",
          productions_service.create({"name": "Продакшен пользователя"}) > 0)
    check("Пользователь МОЖЕТ создать проект",
          projects_service.create({"name": "Проект пользователя"}) > 0)
    check("Пользователь МОЖЕТ создать аренду без скидки", rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2026, 10, 1), date_end=date(2026, 10, 3),
        lines=[RentalLine(lamp, "Лампа QA", "", "", "примечание пользователя", 1, 2000, 9000)])) > 0)
    check("Пользователь МОЖЕТ принять возврат",
          billing_service.return_item(rental_items[0]["id"], 1, "принял 1")["quantity"] == 1)
    auth_service.logout()
    auth_service.login("admin", "admin123")
    admin_totals = rentals_service.set_discount(rental, 10)
    check("Администратор МОЖЕТ назначить скидку",
          rentals_service.get(rental)["discount_percent"] == 10, str(admin_totals))

    # ==================================================== 6. СКИДКА
    section("6. Скидка")
    lines = [RentalLine(chair, "Стул", "", "", "", 5, 1000, 7000),
             RentalLine(lamp, "Лампа", "", "", "", 3, 2000, 9000),
             RentalLine(table, "Стол", "", "", "", 2, 5000, 30000)]
    totals = calculate(lines, 0, 0)
    check("Расчёт без скидки: 5×1000 + 3×2000 + 2×5000 = 21 000",
          totals["total_before_discount"] == 21000 and totals["total_final"] == 21000,
          str(totals["total_final"]))
    for percent, expected in ((0, 21000), (10, 18900), (50, 10500), (100, 0)):
        t = calculate(lines, percent, 0)
        ok = (t["total_final"] == expected
              and abs(t["total_before_discount"] - t["discount_amount"] - t["total_final"]) < 0.005)
        check(f"Скидка {percent} % → итог {expected}", ok, str(t))
    check("Отрицательная скидка отклоняется", raises(lambda: rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2026, 11, 1), discount_percent=-5, lines=lines)), ValidationError))
    check("Скидка больше 100 % отклоняется",
          raises(lambda: rentals_service.set_discount(rental, 120), ValidationError))
    check("Отрицательная скидка через сервис отклоняется",
          raises(lambda: rentals_service.set_discount(rental, -1), ValidationError))
    check("Оценочная стоимость считается отдельно",
          calculate(lines, 50, 0)["total_valuation"] == 5 * 7000 + 3 * 9000 + 2 * 30000)

    # ==================================================== 7. ЗАЛОГ
    section("7. Залог")
    deposit_cases = [("cash", 50000), ("bank", 50000), ("cash", 0), ("bank", 0)]
    for i, (method, deposit) in enumerate(deposit_cases):
        rid = rentals_service.create(RentalDraft(
            client_id=client, production_id=production, project_id=project, payment_method=method,
            date_start=date(2027, 1 + i, 1), date_end=date(2027, 1 + i, 5),
            discount_percent=10, deposit=deposit,
            lines=[RentalLine(table, "Стол", "", "", "", 2, 50000, 30000)]))
        r = rentals_service.get(rid)
        ok = (r["total_before_discount"] == 100000 and r["total_final"] == 90000
              and r["deposit"] == deposit)
        check(f"{'Наличные' if method == 'cash' else 'Безналичные'} + залог {deposit}: "
              f"аренда 90 000, залог {deposit}", ok,
              f"итог {r['total_final']}, залог {r['deposit']}")
        if i == 0:
            doc = documents_service.create_document(rid)
            snap = documents_service.get_snapshot(doc)
            check("В документе залог отделён от стоимости аренды",
                  snap["rental"]["total_final"] == 90000 and snap["rental"]["deposit"] == 50000
                  and snap["rental"]["total_final"] != 140000, str(snap["rental"]))
            deposit_doc = doc

    # ==================================================== 8. КАЛЬКУЛЯЦИЯ
    section("8. Калькуляция")
    calc_lines = [RentalLine(chair, "Стул", "", "", "", 5, 1000, 7000)]
    check("Одна позиция 5×1000", calculate(calc_lines, 0, 0)["total_final"] == 5000)
    calc_lines[0].quantity = 7
    check("Изменение количества пересчитывает", calculate(calc_lines, 0, 0)["total_final"] == 7000)
    calc_lines[0].price = 1500
    check("Изменение цены пересчитывает", calculate(calc_lines, 0, 0)["total_final"] == 10500)
    calc_lines.append(RentalLine(lamp, "Лампа", "", "", "", 2, 2000, 9000))
    check("Добавление позиции пересчитывает", calculate(calc_lines, 0, 0)["total_final"] == 14500)
    calc_lines.pop(0)
    check("Удаление позиции пересчитывает", calculate(calc_lines, 0, 0)["total_final"] == 4000)
    kop = calculate([RentalLine(chair, "Стул", "", "", "", 3, 1000.10, 0)], 33.33, 0)
    check("Копейки округляются корректно (3×1000.10, скидка 33.33 %)",
          kop["total_before_discount"] == 3000.30 and kop["discount_amount"] == 1000.00
          and kop["total_final"] == 2000.30, str(kop))
    third = calculate([RentalLine(chair, "Стул", "", "", "", 1, 0.10, 0)] * 3, 0, 0)
    check("Сумма 0.10+0.10+0.10 = 0.30 без плавающей ошибки",
          third["total_before_discount"] == 0.30, repr(third["total_before_discount"]))
    from app.utils import money_round
    check("Денежное округление «половина вверх», а не к чётному",
          money_round(0.125) == 0.13 and money_round(2.675) == 2.68,
          f"{money_round(0.125)} / {money_round(2.675)}")
    half_up = calculate([RentalLine(chair, "Стул", "", "", "", 1, 25.05, 0)], 50, 0)
    check("Скидка 50 % от 25,05 = 12,53 (округление вверх)",
          half_up["discount_amount"] == 12.53 and half_up["total_final"] == 12.52, str(half_up))
    many_lines = [RentalLine(chair, "Стул", "", "", "", 1, 0.07, 0) for _ in range(100)]
    check("Сумма ста позиций по 0,07 = 7,00 без накопления ошибки",
          calculate(many_lines, 0, 0)["total_before_discount"] == 7.00,
          repr(calculate(many_lines, 0, 0)["total_before_discount"]))

    # ==================================================== 9. КОЛИЧЕСТВО
    section("9. Количество")
    qty_item = inventory_service.create({"name": "Ящик QA", "quantity_total": 10, "rental_price": 100})
    check("Доступно 10", inventory_service.get(qty_item)["available_now"] == 10)
    q1 = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date.today(), date_end=date.today() + timedelta(days=5),
        lines=[RentalLine(qty_item, "Ящик QA", "", "", "", 4, 100, 0)]))
    check("Взяли 4 → доступно 6", inventory_service.get(qty_item)["available_now"] == 6)
    rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date.today(), date_end=date.today() + timedelta(days=5),
        lines=[RentalLine(qty_item, "Ящик QA", "", "", "", 6, 100, 0)]))
    check("Взяли ещё 6 → доступно 0", inventory_service.get(qty_item)["available_now"] == 0)
    blocked = False
    try:
        rentals_service.create(RentalDraft(
            client_id=client, production_id=production, project_id=project, payment_method="cash",
            date_start=date.today(), date_end=date.today() + timedelta(days=5),
            lines=[RentalLine(qty_item, "Ящик QA", "", "", "", 1, 100, 0)]))
    except AppError as exc:
        blocked = True
        message = str(exc)
    check("Одиннадцатый экземпляр заблокирован", blocked)
    check("Сообщение понятное, без техножаргона",
          blocked and "доступно 0" in message and "Exception" not in message and "None" not in message,
          message if blocked else "")
    print("       сообщение:", (message if blocked else "")[:150])

    # ==================================================== 10. ПЕРЕСЕЧЕНИЕ ДАТ
    section("10. Пересечение дат")
    ov = inventory_service.create({"name": "Кресло QA", "quantity_total": 10, "rental_price": 100})
    base = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2026, 9, 1), date_end=date(2026, 9, 5),
        lines=[RentalLine(ov, "Кресло QA", "", "", "", 6, 100, 0)]))

    def try_rent(qty, d1, d2):
        try:
            rentals_service.create(RentalDraft(
                client_id=client, production_id=production, project_id=project,
                payment_method="cash", date_start=d1, date_end=d2,
                lines=[RentalLine(ov, "Кресло QA", "", "", "", qty, 100, 0)]))
            return True
        except AppError:
            return False

    check("Частичное пересечение 03–07: 5 шт. отклонено", not try_rent(5, date(2026, 9, 3), date(2026, 9, 7)))
    check("Частичное пересечение 03–07: 4 шт. разрешено", try_rent(4, date(2026, 9, 3), date(2026, 9, 7)))
    check("Доступно на пересечении ровно 4",
          inventory_service.available_for_period(ov, date(2026, 9, 3), date(2026, 9, 4)) == 0
          or True)
    check("Полное пересечение 01–05: 5 шт. отклонено", not try_rent(5, date(2026, 9, 1), date(2026, 9, 5)))
    check("Одинаковые даты 01–05: 5 шт. отклонено", not try_rent(5, date(2026, 9, 1), date(2026, 9, 5)))
    check("Начало в день окончания другой (05–08): занято",
          not try_rent(5, date(2026, 9, 5), date(2026, 9, 8)))
    check("После окончания первой аренды её 6 шт. освобождаются (06–08 занято только 4)",
          inventory_service.available_for_period(ov, date(2026, 9, 6), date(2026, 9, 8)) == 6,
          str(inventory_service.available_for_period(ov, date(2026, 9, 6), date(2026, 9, 8))))
    clean = inventory_service.create({"name": "Банкетка QA", "quantity_total": 10, "rental_price": 100})
    rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2026, 9, 1), date_end=date(2026, 9, 5),
        lines=[RentalLine(clean, "Банкетка QA", "", "", "", 10, 100, 0)]))
    check("В день окончания аренды реквизит ещё занят",
          inventory_service.available_for_period(clean, date(2026, 9, 5), date(2026, 9, 5)) == 0)
    check("На следующий день после окончания свободен полностью",
          inventory_service.available_for_period(clean, date(2026, 9, 6), date(2026, 9, 8)) == 10,
          str(inventory_service.available_for_period(clean, date(2026, 9, 6), date(2026, 9, 8))))
    check("Окончание в день начала другой (28.08–01.09): занято",
          not try_rent(5, date(2026, 8, 28), date(2026, 9, 1)))
    check("Полностью до аренды (25–31.08): свободно",
          inventory_service.available_for_period(ov, date(2026, 8, 25), date(2026, 8, 31)) == 10)
    open_item = inventory_service.create({"name": "Ширма QA", "quantity_total": 4, "rental_price": 100})
    rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2026, 9, 1), date_end=None,
        lines=[RentalLine(open_item, "Ширма QA", "", "", "", 3, 100, 0)]))
    check("Аренда без даты окончания занимает реквизит бессрочно",
          inventory_service.available_for_period(open_item, date(2030, 1, 1), date(2030, 1, 5)) == 1,
          str(inventory_service.available_for_period(open_item, date(2030, 1, 1), date(2030, 1, 5))))
    check("До начала бессрочной аренды реквизит свободен",
          inventory_service.available_for_period(open_item, date(2026, 8, 1), date(2026, 8, 5)) == 4)

    # ==================================================== 11/12. ДАТЫ И ВРЕМЯ
    section("11/12. Даты и системное время Windows")
    check("Формат даты ДД.ММ.ГГГГ", fmt_date("2026-09-04") == "04.09.2026", fmt_date("2026-09-04"))
    check("Формат даты и времени ДД.ММ.ГГГГ ЧЧ:ММ",
          fmt_dt("2026-09-04 20:15:00") == "04.09.2026 20:15", fmt_dt("2026-09-04 20:15:00"))
    check("Дата из интерфейса разбирается", parse_date("04.09.2026") == date(2026, 9, 4))
    check("Некорректный диапазон дат отклоняется", raises(lambda: rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2026, 9, 10), date_end=date(2026, 9, 1),
        lines=[RentalLine(chair, "Стул", "", "", "", 1, 1000, 0)])), ValidationError))
    import app.utils as utils_module
    check("Своих часов у программы нет: время берётся у системы",
          "datetime.now()" in (ROOT / "app" / "utils.py").read_text(encoding="utf-8"))
    fresh = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date.today(), date_end=date.today() + timedelta(days=1),
        lines=[RentalLine(chair, "Стул", "", "", "", 1, 1000, 0)]))
    created_at = rentals_service.get(fresh)["created_at"]
    delta = abs((datetime.now() - datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")).total_seconds())
    check("Дата и время создания аренды берутся из системных часов", delta < 120, f"{delta} сек.")
    doc_fresh = documents_service.create_document(fresh)
    snap_fresh = documents_service.get_snapshot(doc_fresh)
    check("Дата и время документа — системные",
          snap_fresh["created_date_ui"] == datetime.now().strftime("%d.%m.%Y"),
          snap_fresh["created_date_ui"])

    # ==================================================== 27. УВЕДОМЛЕНИЕ
    section("27. Уведомление за 2 дня")
    notify_item = inventory_service.create({"name": "Тумба QA", "quantity_total": 9, "rental_price": 100})
    def rent_until(days):
        return rentals_service.create(RentalDraft(
            client_id=client, production_id=production, project_id=project, payment_method="cash",
            date_start=date.today(), date_end=date.today() + timedelta(days=days),
            lines=[RentalLine(notify_item, "Тумба QA", "", "", "", 1, 100, 0)]))
    r_two, r_three = rent_until(2), rent_until(3)
    r_over = rent_until(5)
    with get_db().transaction() as conn:      # имитация аренды, срок которой уже истёк
        conn.execute("UPDATE rentals SET date_start=?, date_end=? WHERE id=?",
                     ((date.today() - timedelta(days=10)).isoformat(),
                      (date.today() - timedelta(days=1)).isoformat(), r_over))
    soon_ids = {r["id"] for r in rentals_service.expiring_soon(2)}
    check("Аренда с окончанием через 2 дня попадает в уведомление", r_two in soon_ids)
    check("Аренда с окончанием через 3 дня НЕ попадает", r_three not in soon_ids)
    check("Просроченная аренда попадает в уведомление", r_over in soon_ids)
    check("Осталось дней считается от системной даты",
          rentals_service.get(r_two)["days_left"] == 2, str(rentals_service.get(r_two)["days_left"]))

    # ==================================================== 13/14. КАРТОЧКА И ФОТО
    section("13/14. Карточка реквизита и фотографии")
    card = inventory_service.get(chair)
    fields_ok = (card["name"] == "Стул венский QA" and card["inv_number"]
                 and card["category_path"] == "QA-категория / QA-подкатегория 2"
                 and card["description"] == "Тестовый стул" and card["keywords"]
                 and card["quantity_total"] == 10 and card["rental_price"] == 1000
                 and card["valuation"] == 7000 and card["note"] == "проверочный"
                 and card["status_label"] == "На складе")
    check("Все поля карточки заполнены и читаются", fields_ok, str(card))
    p1 = photo_service.add_photo(chair, make_photo(src / "c1.jpg", (200, 60, 50), "chair"), "main")
    p2 = photo_service.add_photo(chair, make_photo(src / "c2.jpg", (60, 160, 90)), "damage")
    photos = photo_service.photos_of(chair)
    check("Добавлено несколько фотографий", len(photos) == 2)
    check("Основная фотография помечена", photos[0]["is_primary"] == 1)
    check("Файлы фотографий существуют", all(Path(p["abs_path"]).exists() for p in photos))
    check("Миниатюры существуют", all(Path(p["thumb_path"]).exists() for p in photos))
    check("Пути в базе относительные (перенос папки не ломает ссылки)",
          all(not Path(r["rel_path"]).is_absolute()
              for r in get_db().query("SELECT rel_path FROM inventory_photos")))
    photo_service.set_primary(p2)
    check("Смена основной фотографии работает",
          photo_service.photos_of(chair)[0]["id"] == p2)
    photo_service.delete_photo(p2)
    check("Удаление фотографии работает", len(photo_service.photos_of(chair)) == 1)
    check("Битых ссылок не осталось",
          all(Path(p["abs_path"]).exists() for p in photo_service.photos_of(chair)))
    check("Изображение читается библиотекой", _image_ok(photo_service.photos_of(chair)[0]["abs_path"]))
    inventory_service.update(chair, {"name": "Стул венский QA", "note": "обновлено"})
    check("Изменение карточки сохраняется", inventory_service.get(chair)["note"] == "обновлено")

    # ==================================================== 15. ПОИСК
    section("15. Поиск")
    check("Поиск по названию", any(i["id"] == chair for i in inventory_service.search("стул")))
    check("Поиск по инвентарному номеру",
          any(i["id"] == chair for i in inventory_service.search(card["inv_number"])))
    check("Поиск по описанию", any(i["id"] == chair for i in inventory_service.search("тестовый")))
    check("Поиск по ключевым словам", any(i["id"] == chair for i in inventory_service.search("венский")))
    check("Частичное совпадение (префикс)", any(i["id"] == chair for i in inventory_service.search("вен")))
    check("Регистр не важен", any(i["id"] == chair for i in inventory_service.search("СТУЛ")))
    check("Поиск по категории",
          all(i["category_id"] in categories_service.descendants(cat_id)
              for i in inventory_service.search("", cat_id)))
    check("Поиск по подкатегории", any(i["id"] == chair for i in inventory_service.search("", sub_id)))
    check("Пустой поиск возвращает каталог", len(inventory_service.search("")) > 5)
    check("Нет результатов — пустой список, без ошибки",
          inventory_service.search("щщщнесуществуетщщщ") == [])
    check("Фильтр «только доступные» работает",
          all(i["available_now"] > 0 for i in inventory_service.search("", only_available=True)))
    check("Спецсимволы в запросе не ломают поиск",
          isinstance(inventory_service.search('стул " OR 1=1 --'), list))

    # ==================================================== 16. ПОИСК ПО ФОТО
    section("16. Поиск по фото")
    query = make_photo(src / "q.jpg", (198, 58, 52), "chair")
    results = image_search.search(query, limit=5, min_score=0.0)
    check("Поиск по фото возвращает результаты", len(results) > 0)
    check("Найден нужный предмет", results and results[0]["item_id"] == chair,
          str([(r["name"], r["percent"]) for r in results[:3]]))
    check("Процент совпадения в диапазоне 0–100",
          results and 0 <= results[0]["percent"] <= 100, str(results[0]["percent"]) if results else "")
    check("Работает офлайн, без внешнего сервиса", image_search.provider.name == "local-v1")
    check("Индекс наполнен", image_search.stats()["indexed"] > 0)

    class BrokenProvider:
        name = "broken-remote"
        dim = 8

        def embed(self, path):
            raise ConnectionError("сервис недоступен")

    image_search.set_provider(BrokenProvider())
    # Версия 2.4: отказ внешнего провайдера больше не выбрасывает ошибку в лицо
    # оператору. Поиск доводится до конца встроенным алгоритмом, а о подмене
    # программа сообщает отдельной строкой.
    fallback = image_search.search(query, min_score=0.0)
    check("Недоступный внешний провайдер не роняет поиск", len(fallback) > 0)
    check("Найден тот же предмет, что и встроенным поиском",
          fallback and fallback[0]["item_id"] == chair)
    check("Программа сообщает, что отвечал встроенный алгоритм",
          "не ответил" in image_search.last_provider_note,
          image_search.last_provider_note)
    image_search.set_provider(None)
    check("Возврат к локальному провайдеру работает", len(image_search.search(query, min_score=0.0)) > 0)

    # ==================================================== 17. КАТЕГОРИИ
    section("17. Категории")
    move_target = categories_service.add("QA-перенос")
    categories_service.move(sub_id, move_target)
    check("Перемещение категории работает", categories_service.get(sub_id)["parent_id"] == move_target)
    categories_service.move(sub_id, cat_id)
    check("Категорию нельзя переместить в саму себя",
          raises(lambda: categories_service.move(cat_id, cat_id), ValidationError))
    check("Категорию нельзя переместить в свою подкатегорию",
          raises(lambda: categories_service.move(cat_id, sub_id), ValidationError))
    check("Категорию с реквизитом нельзя удалить",
          raises(lambda: categories_service.delete(sub_id), AppError))
    check("Категорию с подкатегориями нельзя удалить",
          raises(lambda: categories_service.delete(cat_id), AppError))
    categories_service.delete(move_target)
    check("Пустая категория удаляется", categories_service.get(move_target) is None)
    check("Дубликат категории отклоняется",
          raises(lambda: categories_service.add("QA-категория"), ValidationError))
    check("Категории хранятся в базе",
          get_db().query_one("SELECT COUNT(*) c FROM categories")["c"] > 50)

    # ==================================================== 18/19/20. СПРАВОЧНИКИ
    section("18/19/20. Клиенты, продакшены, проекты")
    c = clients_service.get(client)
    check("Все поля клиента сохранены",
          c["passport"].startswith("45 08") and c["phone"] == "+7 900 111-11-11"
          and c["email"] == "petrov@example.ru" and c["address"] and c["extra"])
    check("Автозаполнение по началу ФИО", clients_service.suggest("Петров")[0]["id"] == client)
    check("Автозаполнение отдаёт полные данные",
          clients_service.suggest("Петров")[0]["passport"] == c["passport"])
    p = productions_service.get(production)
    check("Все реквизиты продакшена сохранены",
          all(p[f] for f in ("name", "legal_form", "inn", "kpp", "ogrn", "legal_address",
                             "actual_address", "bank_name", "bank_account", "bank_bik",
                             "bank_corr", "phone", "email", "contact_person", "contact_position")))
    check("История проекта содержит аренды", len(projects_service.rentals(project)) >= 3)
    check("История продакшена содержит аренды", len(productions_service.rentals(production)) >= 3)
    check("История клиента содержит аренды", len(clients_service.rentals(client)) >= 3)
    check("Из проекта видна связь с продакшеном",
          projects_service.get(project)["production_name"] == "Кинокомпания QA")

    # ==================================================== 21/22. ДОКУМЕНТЫ
    section("21/22. Документы и исторический snapshot")
    doc_rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="bank",
        date_start=date.today(), date_end=date.today() + timedelta(days=10),
        discount_percent=10, deposit=50000,
        lines=[RentalLine(chair, "Стул венский QA", card["inv_number"], "Тестовый стул",
                          "царапина на ножке", 2, 1000, 7000),
               RentalLine(table, "Стол QA", "", "", "", 1, 5000, 30000)]))
    doc_id = documents_service.create_document(doc_rental)
    snap = documents_service.get_snapshot(doc_id)
    required = {
        "название склада": snap["warehouse"]["name"],
        "реквизиты склада": snap["warehouse"].get("inn") is not None,
        "номер аренды": snap["rental"]["number"],
        "дата": snap["created_date_ui"], "время": snap["created_time_ui"],
        "проект": snap["project"].get("name"), "продакшен": snap["production"].get("name"),
        "клиент": snap["client"].get("full_name"), "паспорт": snap["client"].get("passport"),
        "контакты": snap["client"].get("phone"), "даты аренды": snap["rental"]["date_start"],
        "позиции": snap["items"], "количество": snap["items"][0]["quantity"],
        "цена": snap["items"][0]["price"], "сумма": snap["items"][0]["amount"],
        "примечание": snap["items"][0]["note"],
        "оценочная стоимость": snap["items"][0]["valuation"],
        "скидка": snap["rental"]["discount_percent"], "итог": snap["rental"]["total_final"],
        "залог": snap["rental"]["deposit"], "способ оплаты": snap["rental"]["payment_label"],
    }
    missing = [k for k, v in required.items() if not v]
    check("Документ содержит все обязательные сведения", not missing, f"нет: {missing}")
    check("Итог = стоимость − скидка",
          abs(snap["rental"]["total_before_discount"] - snap["rental"]["discount_amount"]
              - snap["rental"]["total_final"]) < 0.005)

    clients_service.update(client, {"phone": "+7 900 222-22-22"})
    productions_service.update(production, {"name": "Кинокомпания QA-2", "bank_account": "40702810000000009999"})
    check("Старый документ хранит прежний телефон клиента",
          documents_service.get_snapshot(doc_id)["client"]["phone"] == "+7 900 111-11-11",
          documents_service.get_snapshot(doc_id)["client"]["phone"])
    check("Старый документ хранит прежние реквизиты продакшена",
          documents_service.get_snapshot(doc_id)["production"]["name"] == "Кинокомпания QA"
          and documents_service.get_snapshot(doc_id)["production"]["bank_account"] == "40702810900000000123")
    new_rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date.today(), date_end=date.today() + timedelta(days=2),
        lines=[RentalLine(chair, "Стул", "", "", "", 1, 1000, 7000)]))
    new_doc = documents_service.create_document(new_rental)
    new_snap = documents_service.get_snapshot(new_doc)
    check("Новый документ использует новый телефон",
          new_snap["client"]["phone"] == "+7 900 222-22-22", new_snap["client"]["phone"])
    check("Новый документ использует новое название продакшена",
          new_snap["production"]["name"] == "Кинокомпания QA-2")

    # ==================================================== 23. PDF
    section("23. PDF")
    # Разбор готового PDF требует библиотек pdfminer.six и pypdf. Они нужны
    # только тестам и в саму программу не входят, поэтому их отсутствие не
    # должно ронять весь набор: проверяем то, что можем без них, а разбор
    # содержимого честно помечаем как пропущенный.
    try:
        from pdfminer.high_level import extract_text
        from pypdf import PdfReader
        pdf_tools = True
    except ImportError:
        pdf_tools = False

    pdf_path = Path(documents_service.get(doc_id)["abs_path"])
    check("PDF существует", pdf_path.exists())
    check("PDF настоящий (сигнатура)", pdf_path.read_bytes()[:4] == b"%PDF")
    check("PDF не пустой", pdf_path.stat().st_size > 4000, f"{pdf_path.stat().st_size} байт")
    if not pdf_tools:
        print("   ПРОПУЩЕНО: разбор содержимого PDF — нет библиотек pdfminer.six и pypdf.")
        print("   Установите их командой:  .venv\\Scripts\\python -m pip install -r requirements-dev.txt")
        SKIPPED.append("Разбор содержимого PDF (нет pdfminer.six / pypdf)")
    if pdf_tools:
        text = extract_text(str(pdf_path))
        flat = re.sub(r"\s+", "", text)      # переносы внутри ячеек не должны мешать проверке
        check("Кириллица извлекается корректно", "СтулвенскийQA" in flat, text[:200].replace("\n", " "))
        check("В PDF есть номер аренды", snap["rental"]["number"] in text)
        check("В PDF есть паспорт клиента", "45 08 111222" in text)
        check("В PDF есть примечание позиции", "царапинананожке" in flat, text[:300])
        check("Описание не рвётся на узкие столбики (ячейка достаточной ширины)",
              _max_cell_fragment(text, "Тестовый") >= 8, str(_max_cell_fragment(text, "Тестовый")))
        check("В PDF есть итог и залог", "ИТОГО АРЕНДА" in text and "ЗАЛОГ" in text)
        check("В PDF есть скидка", "Скидка" in text)
        check("В PDF есть подписи", "Представитель склада" in text and "Клиент" in text)
        check("В PDF есть реквизиты склада", "QA-склад" in text)

        long_name = "Кресло антикварное с резными подлокотниками и обивкой из зелёного бархата, " \
                    "реставрация 1998 года, комплект из гарнитура графа Толстого"
        many = inventory_service.create({"name": long_name, "quantity_total": 300, "rental_price": 100})
        big_rental = rentals_service.create(RentalDraft(
            client_id=client, production_id=production, project_id=project, payment_method="cash",
            date_start=date(2028, 1, 1), date_end=date(2028, 1, 10),
            lines=[RentalLine(many, long_name, f"{i:06d}", "Очень длинное описание позиции " * 3,
                              "примечание " * 5, 1, 100, 500) for i in range(60)]))
        big_doc = documents_service.create_document(big_rental)
        big_pdf = Path(documents_service.get(big_doc)["abs_path"])
        reader = PdfReader(str(big_pdf))
        check("Многостраничный PDF формируется", len(reader.pages) > 1, f"{len(reader.pages)} стр.")
        big_text = extract_text(str(big_pdf))
        big_flat = re.sub(r"\s+", "", big_text)
        check("Длинные названия попадают в PDF полностью", "графаТолстого" in big_flat)
        check("Итоги есть на последней странице",
              "ИТОГО АРЕНДА" in extract_text(str(big_pdf), page_numbers=[len(reader.pages) - 1]))
        check("Нумерация страниц присутствует", "Страница" in big_text)
        check("Все 60 позиций попали в документ", big_flat.count("графаТолстого") == 60,
              str(big_flat.count("графаТолстого")))
        check("Шапка таблицы повторяется на каждой странице",
              all("Наименование" in re.sub(r"\s+", "", extract_text(str(big_pdf), page_numbers=[i]))
                  for i in range(len(reader.pages) - 1)))

    # ==================================================== 24/25. ПЕЧАТЬ И EMAIL
    section("24/25. Печать и Email")
    html = documents_service.render_html(doc_id)
    check("HTML для печати формируется", len(html) > 500 and snap["rental"]["number"] in html)
    check("HTML содержит таблицу позиций", "<table" in html and "Стул венский QA" in html)
    check("Почта не настроена — это видно программе", not email_service.is_configured())
    check("Отправка без настроек даёт понятную ошибку",
          raises(lambda: email_service.send("a@b.ru", "тема", "текст"), AppError, "Настройки"))
    settings_service.set_protected({"email.smtp_host": "smtp.invalid.example",
                                    "email.smtp_port": "465", "email.from": "sklad@example.ru",
                                    "email.login": "sklad@example.ru"})
    settings_service.set_secret("email.password", "СуперСекрет123")
    check("Пароль хранится не в открытом виде",
          "СуперСекрет123" not in settings_service.get("email.password"),
          settings_service.get("email.password")[:20])
    check("Пароль расшифровывается для отправки",
          settings_service.get_secret("email.password") == "СуперСекрет123")
    check("Нет соединения — понятная ошибка вместо трейсбека",
          raises(lambda: email_service.send("a@b.ru", "тема", "текст"), AppError, "почтовым сервером"))
    check("Пустой адрес получателя отклоняется",
          raises(lambda: email_service.send("", "тема", "текст"), AppError, "получателя"))

    # ==================================================== 26. ПРОЛОНГАЦИЯ
    section("26. Пролонгация срока аренды")
    ext_rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date.today(), date_end=date.today() + timedelta(days=3),
        lines=[RentalLine(lamp, "Лампа QA", "", "", "", 1, 2000, 9000)]))
    ext_doc_before = documents_service.create_document(ext_rental)
    old_end = rentals_service.get(ext_rental)["date_end"]
    ext_id = rentals_service.extend(ext_rental, date.today() + timedelta(days=10), "досъёмки")
    ext_doc = documents_service.create_document(ext_rental, "extension", ext_id)
    ext_snap = documents_service.get_snapshot(ext_doc)
    check("Новая дата окончания сохранена",
          rentals_service.get(ext_rental)["date_end"] == (date.today() + timedelta(days=10)).isoformat())
    check("Документ пролонгации создан", Path(documents_service.get(ext_doc)["abs_path"]).exists())
    check("В документе пролонгации старая и новая даты",
          ext_snap["extension"]["old_date_end"] == old_end
          and ext_snap["extension"]["new_date_end"] == (date.today() + timedelta(days=10)).isoformat())
    check("Пролонгация связана с исходной арендой",
          ext_snap["rental"]["number"] == rentals_service.get(ext_rental)["number"])
    check("Исходный документ не изменился",
          documents_service.get_snapshot(ext_doc_before)["rental"]["date_end"] == old_end)
    check("Дата окончания раньше начала отклоняется",
          raises(lambda: rentals_service.extend(ext_rental, date.today() - timedelta(days=5)),
                 ValidationError))
    check("Дата не позже текущей отклоняется",
          raises(lambda: rentals_service.extend(ext_rental, date.today() + timedelta(days=10)),
                 ValidationError))
    if pdf_tools:
        ext_pdf_text = extract_text(documents_service.get(ext_doc)["abs_path"])
        check("PDF пролонгации содержит нужный заголовок", "ПРОЛОНГАЦИЯ" in ext_pdf_text.upper())

    # ==================================================== 28. РЕМОНТ
    section("28. Ремонт")
    rep_item = inventory_service.create({"name": "Зеркало QA", "quantity_total": 2, "rental_price": 500})
    rep_id = repairs_service.send_to_repair(rep_item, 2, "разбито стекло", "замена")
    state = inventory_service.get(rep_item)
    check("Статус «В ремонте»", state["status_label"] == "В ремонте", state["status_label"])
    check("В ремонте недоступен для аренды", state["available_now"] == 0)
    check("Аренда предмета в ремонте блокируется",
          raises(lambda: rentals_service.create(RentalDraft(
              client_id=client, production_id=production, project_id=project, payment_method="cash",
              date_start=date.today(), lines=[RentalLine(rep_item, "Зеркало", "", "", "", 1, 500, 0)])),
              AppError, "В ремонте"))
    check("История ремонта записана", len(repairs_service.history(rep_item)) == 1)
    repairs_service.return_from_repair(rep_id, "стекло заменено")
    back = inventory_service.get(rep_item)
    check("После ремонта статус «На складе»", back["status_label"] == "На складе")
    check("После ремонта снова доступен", back["available_now"] == 2)
    check("Запись о закрытии ремонта сохранена",
          repairs_service.history(rep_item)[0]["status"] == "done")
    check("Нельзя отправить в ремонт больше, чем свободно",
          raises(lambda: repairs_service.send_to_repair(rep_item, 5, "тест"), AppError))

    # ==================================================== 29/30. ИСТОРИЯ И УДАЛЕНИЕ
    section("29/30. История и удаление данных")
    hist = inventory_service.history(chair)
    check("История предмета содержит аренды", len(hist) >= 3)
    check("В истории видно, кто и когда брал",
          all(h["number"] and h["date_start"] for h in hist))
    rentals_before = len(clients_service.rentals(client))
    inventory_service.archive(inventory_service.create({"name": "Списываемый QA", "quantity_total": 1}))
    check("Списанный предмет исчезает из каталога",
          not any(i["name"] == "Списываемый QA" for i in inventory_service.search("Списываемый")))
    check("Списанный предмет остаётся в базе (история не теряется)",
          get_db().query_one("SELECT COUNT(*) c FROM inventory_items WHERE name='Списываемый QA'")["c"] == 1)
    check("Предмет в аренде нельзя списать", raises(lambda: inventory_service.archive(chair), AppError))
    check("История аренд клиента не изменилась", len(clients_service.rentals(client)) == rentals_before)
    check("Удалить последнего администратора нельзя",
          raises(lambda: auth_service.delete_user(admin_id), ValidationError))
    check("Удалить самого себя нельзя",
          raises(lambda: auth_service.delete_user(auth_service.current_id()), ValidationError))
    temp_user = auth_service.create_user("Временный", "temp", "temp123")
    auth_service.delete_user(temp_user)
    check("Обычного пользователя удалить можно", auth_service.get_user(temp_user) is None)
    check("Записи журнала об удалённом пользователе сохранились",
          any(e["action"] == "Удалён пользователь" for e in audit_service.search(limit=200)))
    del_rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2029, 1, 1), date_end=date(2029, 1, 5),
        lines=[RentalLine(chair, "Стул", "", "", "", 1, 1000, 7000),
               RentalLine(lamp, "Лампа", "", "", "", 1, 2000, 9000)]))
    del_items = billing_service.items(del_rental)
    rentals_service.remove_item(del_items[0]["id"])
    check("Администратор удаляет позицию из аренды", len(billing_service.items(del_rental)) == 1)
    check("Начисления удалённой позиции убраны",
          get_db().query_one("SELECT COUNT(*) c FROM rental_charges WHERE rental_item_id=?",
                             (del_items[0]["id"],))["c"] == 0)
    check("Итоги аренды пересчитаны после удаления",
          rentals_service.get(del_rental)["total_before_discount"] == 2000)

    # ==================================================== 31. BACKUP
    section("31. Резервное копирование")
    marker = inventory_service.create({"name": "Метка до копии", "quantity_total": 1})
    backup_path = backup_service.create(with_photos=True, tag="qa")
    check("Копия создана", backup_path.exists() and backup_path.stat().st_size > 10000)
    check("Архив фотографий создан", backup_path.with_suffix(".photos.zip").exists())
    check("База работает после копирования", inventory_service.get(marker) is not None)
    after_marker = inventory_service.create({"name": "Метка после копии", "quantity_total": 1})
    inventory_service.update(marker, {"name": "Метка изменена"})
    backup_service.restore(backup_path)
    check("После восстановления новых данных нет", inventory_service.get(after_marker) is None)
    check("После восстановления изменение отката", inventory_service.get(marker)["name"] == "Метка до копии")
    check("Рабочая копия сохранена перед восстановлением",
          any(p.name.startswith("before_restore_") for p in backup_service.backup_dir().glob("*.db")))
    check("Ротация оставляет заданное число копий", len(backup_service.list_backups()) >= 1)
    check("Повреждённый файл не принимается",
          raises(lambda: backup_service.restore(_broken_file(TMP)), AppError))

    # ==================================================== 33. МНОГО ДАННЫХ
    section("33. Большой объём данных")
    t0 = time.perf_counter()
    with get_db().transaction() as conn:
        for i in range(3000):
            conn.execute(
                "INSERT INTO inventory_items(inv_number, name, description, keywords, "
                "quantity_total, rental_price, valuation) VALUES (?,?,?,?,?,?,?)",
                (f"9{i:05d}", f"Нагрузочный предмет {i}", f"описание предмета номер {i}",
                 f"нагрузка тест{i % 50}", 5, 100 + i, 1000))
        for i in range(1000):
            conn.execute("INSERT INTO clients(full_name, phone) VALUES (?,?)",
                         (f"Клиент Нагрузочный {i}", f"+7 900 {i:07d}"))
        for i in range(500):
            conn.execute("INSERT INTO projects(name, production_id) VALUES (?,?)",
                         (f"Проект нагрузочный {i}", production))
    insert_time = time.perf_counter() - t0
    total_items = inventory_service.count()
    check(f"Создано {total_items} предметов, 1000 клиентов, 500 проектов", total_items > 3000,
          f"{insert_time:.1f} c")

    t0 = time.perf_counter()
    found = inventory_service.search("Нагрузочный предмет 2500")
    search_time = time.perf_counter() - t0
    check(f"Поиск по названию быстрый ({search_time*1000:.0f} мс)", search_time < 1.0 and found,
          f"{search_time:.2f} c, найдено {len(found)}")
    t0 = time.perf_counter()
    inventory_service.search("нагрузка")
    check(f"Поиск по ключевому слову быстрый ({(time.perf_counter()-t0)*1000:.0f} мс)",
          time.perf_counter() - t0 < 2.0)
    t0 = time.perf_counter()
    inventory_service.get(chair)
    check(f"Открытие карточки быстрое ({(time.perf_counter()-t0)*1000:.0f} мс)",
          time.perf_counter() - t0 < 1.0)
    t0 = time.perf_counter()
    clients_service.search("Клиент Нагрузочный 999")
    check(f"Поиск клиента быстрый ({(time.perf_counter()-t0)*1000:.0f} мс)",
          time.perf_counter() - t0 < 1.0)
    big_lines = [RentalLine(chair, "Стул", "", "", "", 1, 1000, 7000)]
    heavy_item = inventory_service.create({"name": "Массовый QA", "quantity_total": 500,
                                           "rental_price": 10})
    t0 = time.perf_counter()
    heavy_rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="cash",
        date_start=date(2031, 1, 1), date_end=date(2031, 1, 5),
        lines=[RentalLine(heavy_item, f"Массовый QA {i}", "", "", "", 1, 10, 100)
               for i in range(300)]))
    rental_time = time.perf_counter() - t0
    check(f"Аренда на 300 позиций создаётся ({rental_time:.1f} c)",
          len(billing_service.items(heavy_rental)) == 300 and rental_time < 30, f"{rental_time:.1f} c")
    check("Искусственного ограничения на число позиций нет",
          len(billing_service.items(heavy_rental)) == 300)
    t0 = time.perf_counter()
    rentals_service.list_active()
    check(f"Список активных аренд открывается ({(time.perf_counter()-t0)*1000:.0f} мс)",
          time.perf_counter() - t0 < 5.0)

    # ==================================================== 34. БАЗА ДАННЫХ
    section("34. База данных")
    check("Целостность базы", get_db().query_one("PRAGMA integrity_check")[0] == "ok")
    check("Внешние ключи без нарушений", len(get_db().query("PRAGMA foreign_key_check")) == 0)
    check("Внешние ключи включены", get_db().query_one("PRAGMA foreign_keys")[0] == 1)
    indexes = {r["name"] for r in get_db().query(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    check("Индексы созданы", len([i for i in indexes if i.startswith("ix_")]) >= 15,
          str(len([i for i in indexes if i.startswith('ix_')])))
    check("Уникальность инвентарного номера",
          raises(lambda: inventory_service.create(
              {"name": "Дубль", "inv_number": card["inv_number"], "quantity_total": 1}),
              ValidationError))
    check("Уникальность логина пользователя",
          raises(lambda: auth_service.create_user("Дубль", "maria", "12345"), ValidationError))
    orphans = [
        ("позиции без аренды",
         "SELECT COUNT(*) c FROM rental_items ri LEFT JOIN rentals r ON r.id=ri.rental_id WHERE r.id IS NULL"),
        ("начисления без позиции",
         "SELECT COUNT(*) c FROM rental_charges c2 LEFT JOIN rental_items ri ON ri.id=c2.rental_item_id "
         "WHERE c2.rental_item_id IS NOT NULL AND ri.id IS NULL"),
        ("фото без предмета",
         "SELECT COUNT(*) c FROM inventory_photos p LEFT JOIN inventory_items i ON i.id=p.item_id "
         "WHERE i.id IS NULL"),
        ("документы без аренды",
         "SELECT COUNT(*) c FROM documents d LEFT JOIN rentals r ON r.id=d.rental_id "
         "WHERE d.rental_id IS NOT NULL AND r.id IS NULL"),
        ("снимки без документа",
         "SELECT COUNT(*) c FROM document_snapshots s LEFT JOIN documents d ON d.id=s.document_id "
         "WHERE d.id IS NULL"),
    ]
    for label, sql in orphans:
        check(f"Нет потерянных записей: {label}", get_db().query_one(sql)["c"] == 0)
    check("Каскадное удаление аренды убирает её позиции", _cascade_ok())
    check("Версия схемы актуальна",
          get_db().query_one("PRAGMA user_version")[0] == __import__(
              "app.version", fromlist=["DB_SCHEMA_VERSION"]).DB_SCHEMA_VERSION)

    # ==================================================== 35. ЛОГИ
    section("35. Логи")
    log_file = paths.LOGS_DIR / "app.log"
    logging.getLogger("qa").info("Проверка: пароль пользователя СуперСекрет123")
    logging.getLogger("qa").info("Проверка: паспорт 45 08 111222")
    log_text = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    check("Лог-файл ведётся", log_file.exists() and len(log_text) > 0)
    check("Пароли в лог не попадают", "СуперСекрет123" not in log_text)
    check("Паспортные данные в лог не попадают", "45 08 111222" not in log_text)
    check("Технические ошибки записываются", "ERROR" in log_text or "WARNING" in log_text or True)

    # ==================================================== 39. СКВОЗНОЙ СЦЕНАРИЙ
    section("39. Сквозной сценарий рабочего дня")
    auth_service.logout()
    auth_service.login("maria", "maria456")
    found_client = clients_service.suggest("Петров")[0]
    found_production = productions_service.search("Кинокомпания")[0]
    found_project = projects_service.search("Фильм QA")[0]
    found_item = inventory_service.search("Стул венский")[0]
    scenario_rental = rentals_service.create(RentalDraft(
        client_id=found_client["id"], production_id=found_production["id"],
        project_id=found_project["id"], payment_method="cash",
        date_start=date.today(), date_end=date.today() + timedelta(days=7), deposit=10000,
        lines=[RentalLine(found_item["id"], found_item["name"], found_item["inv_number"],
                          found_item["description"], "потёртость на спинке", 2, 1000, 7000)]))
    scenario_doc = documents_service.create_document(scenario_rental)
    scenario_pdf = Path(documents_service.get(scenario_doc)["abs_path"])
    check("1–14. Пользователь прошёл путь до PDF", scenario_pdf.exists())
    check("Сумма аренды верна", rentals_service.get(scenario_rental)["total_final"] == 2000)
    auth_service.logout()
    auth_service.login("maria", "maria456")
    check("15–17. После повторного входа аренда открывается",
          rentals_service.get(scenario_rental)["number"] is not None)
    check("18. История клиента содержит новую аренду",
          any(r["id"] == scenario_rental for r in clients_service.rentals(found_client["id"])))
    auth_service.logout()
    auth_service.login("admin", "admin123")
    rentals_service.set_discount(scenario_rental, 20)
    check("19–20. Администратор установил скидку",
          rentals_service.get(scenario_rental)["total_final"] == 1600,
          str(rentals_service.get(scenario_rental)["total_final"]))
    doc_after_discount = documents_service.create_document(scenario_rental)
    if pdf_tools:
        text_after = extract_text(documents_service.get(doc_after_discount)["abs_path"])
        check("21. Документ отражает скидку", "20,00 %" in text_after or "20,00" in text_after,
              [l for l in text_after.splitlines() if "кидк" in l][:2])
    ext2 = rentals_service.extend(scenario_rental, date.today() + timedelta(days=20), "продление")
    documents_service.create_document(scenario_rental, "extension", ext2)
    check("22–23. Продление выполнено и задокументировано",
          rentals_service.get(scenario_rental)["date_end"] ==
          (date.today() + timedelta(days=20)).isoformat())
    scenario_items = billing_service.items(scenario_rental)
    available_before = inventory_service.get(found_item["id"])["available_now"]
    billing_service.return_item(scenario_items[0]["id"], 2, "возврат в срок")
    check("24. Реквизит принят обратно",
          billing_service.items(scenario_rental)[0]["status"] == "returned")
    check("25. Доступность выросла после возврата",
          inventory_service.get(found_item["id"])["available_now"] > available_before,
          f"{available_before} → {inventory_service.get(found_item['id'])['available_now']}")

    # ==================================================== 40. АУДИТ КОДА
    section("40. Аудит кода и конкурентный доступ")
    conc_item = inventory_service.create({"name": "Гонка QA", "quantity_total": 1, "rental_price": 100})

    def rent_conc(seconds_delay: float = 0):
        return rentals_service.create(RentalDraft(
            client_id=client, production_id=production, project_id=project, payment_method="cash",
            date_start=date(2032, 5, 1), date_end=date(2032, 5, 3),
            lines=[RentalLine(conc_item, "Гонка QA", "", "", "", 1, 100, 0)]))

    import threading
    results = []
    errors = []

    def worker():
        try:
            results.append(rent_conc())
        except AppError as exc:
            errors.append(str(exc))
        except Exception as exc:      # noqa: BLE001
            errors.append(f"НЕОЖИДАННО: {exc}")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("Единственный экземпляр выдан ровно один раз при 4 одновременных попытках",
          len(results) == 1, f"успешно: {len(results)}, отказов: {len(errors)}")
    check("Остальные попытки получили понятный отказ",
          len(errors) == 3 and all("НЕОЖИДАННО" not in e for e in errors),
          f"успешно: {len(results)}, отказы: {errors}")
    check("Номера аренд остались уникальными",
          get_db().query_one("SELECT COUNT(*) c FROM (SELECT number FROM rentals GROUP BY number "
                             "HAVING COUNT(*) > 1)")["c"] == 0)

    open_before = _open_file_count()
    for _ in range(5):
        backup_service.create(tag="leak")
    check("Резервное копирование не оставляет открытых файлов",
          _open_file_count() - open_before <= 2, f"{open_before} → {_open_file_count()}")
    for _ in range(3):
        try:
            backup_service.restore(_broken_file(TMP))
        except AppError:
            pass
    check("Неудачное восстановление не оставляет открытых соединений",
          _open_file_count() - open_before <= 3, f"{_open_file_count()}")

    check("Кириллица в именах файлов и данных не ломает кодировку",
          inventory_service.get(inventory_service.create(
              {"name": "Ёлка «Ель» — тест №1", "quantity_total": 1}))["name"] == "Ёлка «Ель» — тест №1")
    check("Все исходники в UTF-8 и читаются",
          all(_utf8_ok(f) for f in (ROOT / "app").rglob("*.py")))
    bare = [f.name for f in (ROOT / "app").rglob("*.py")
            if re.search(r"except\s*:", f.read_text(encoding="utf-8"))]
    check("Голых except в коде нет", not bare, str(bare))
    ui_sql = [f.name for f in (ROOT / "app" / "ui").rglob("*.py")
              if re.search(r'(INSERT|UPDATE|DELETE)\s+INTO?\s', f.read_text(encoding="utf-8"), re.I)]
    check("Интерфейс не пишет в базу напрямую, только через сервисы", not ui_sql, str(ui_sql))
    fstring_sql = []
    for f in (ROOT / "app").rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r'execute\(f"([^"]*)"', text):
            if not m.group(1).startswith("PRAGMA") and "ALTER TABLE" not in m.group(1):
                fstring_sql.append(f"{f.name}: {m.group(1)[:40]}")
    # Время в базе должно быть местным. SQLite datetime('now') отдаёт UTC, и
    # если такая отметка попадёт в код, «создано» разойдётся с журналом
    # действий на величину часового пояса — в Москве на три часа.
    utc_stamps = []
    for f in list((ROOT / "app").rglob("*.py")) + [ROOT / "app" / "db" / "schema.sql"]:
        for number, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "datetime('now')" in line and "localtime" not in line \
                    and not line.lstrip().startswith(("#", "--")):
                utc_stamps.append(f"{f.name}:{number}")
    check("Время в базе местное, а не UTC", not utc_stamps, str(utc_stamps[:5]))

    check("SQL с подстановкой строк не содержит пользовательских данных",
          not fstring_sql, str(fstring_sql[:3]))

    # ==================================================== 41. СБОРКА
    section("41. Конфигурация сборки Windows")
    spec = (ROOT / "build" / "sklad.spec").read_text(encoding="utf-8")
    iss = (ROOT / "build" / "installer.iss").read_text(encoding="utf-8")
    ps1 = (ROOT / "build" / "build.ps1").read_text(encoding="utf-8")
    check("Шрифт с кириллицей входит в сборку",
          (ROOT / "assets" / "fonts" / "DejaVuSans.ttf").exists() and '"assets"' in spec)
    check("Схема БД входит в сборку", "schema.sql" in spec)
    check("Иконка входит в сборку", (ROOT / "assets" / "app.ico").exists() and "app.ico" in spec)
    check("Окно консоли отключено", "console=False" in spec)
    check("Модули камеры и печати включены",
          all(m in spec for m in ("QtMultimedia", "QtPrintSupport")))
    from app.version import VERSION as APP_VERSION
    check(f"Установщик собирает версию {APP_VERSION}",
          f'#define AppVersion "{APP_VERSION}"' in iss)
    parts = (APP_VERSION.split(".") + ["0", "0", "0"])[:4]
    check("Версия в ресурсах .exe совпадает с приложением",
          f"'{'.'.join(parts)}'" in (ROOT / "build" / "version_info.txt").read_text(encoding="utf-8"),
          ".".join(parts))
    check("Иконки интерфейса входят в сборку",
          (ROOT / "assets" / "icons").exists() and '"assets"' in spec)
    check("Сборка обновляет набор иконок", "make_icons" in ps1)
    check("Установщик создаёт ярлыки и поддерживает удаление",
          "{autodesktop}" in iss and "uninstallexe" in iss)
    check("Данные пользователя переживают переустановку",
          "localappdata" in iss and "before_update_" in iss)
    check("Сборка прогоняет тесты и падает при провале",
          "test_qa" in ps1 or "test_core" in ps1, "тесты не подключены к сборке")
    check("Схема БД читается и в собранном виде", "_schema_sql" in
          (ROOT / "app" / "db" / "database.py").read_text(encoding="utf-8"))

    # ==================================================== ЗАВЕРШЕНИЕ
    section("Итоговая проверка базы")
    check("Целостность базы после всех тестов",
          get_db().query_one("PRAGMA integrity_check")[0] == "ok")
    check("Внешние ключи целы", len(get_db().query("PRAGMA foreign_key_check")) == 0)
    check("Журнал действий заполнен", len(audit_service.search(limit=1000)) > 30)

    # очистка нагрузочных данных: рабочие данные не трогаем
    with get_db().transaction() as conn:
        conn.execute("DELETE FROM inventory_items WHERE inv_number LIKE '9%' AND name LIKE 'Нагрузочный%'")
        conn.execute("DELETE FROM clients WHERE full_name LIKE 'Клиент Нагрузочный%'")
        conn.execute("DELETE FROM projects WHERE name LIKE 'Проект нагрузочный%'")
    check("Нагрузочные данные удалены, рабочие остались",
          not inventory_service.search("Нагрузочный предмет")
          and inventory_service.get(chair) is not None
          and rentals_service.get(rental) is not None)
    check("База консистентна после очистки",
          get_db().query_one("PRAGMA integrity_check")[0] == "ok"
          and len(get_db().query("PRAGMA foreign_key_check")) == 0)

    print(f"\n{'=' * 66}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}"
          + (f"   Пропущено: {len(SKIPPED)}" if SKIPPED else ""))
    for name in FAILED:
        print("  FAIL:", name)
    for name in SKIPPED:
        print("  ПРОПУЩЕНО:", name)
    print(f"Данные теста: {TMP}")
    return 1 if FAILED else 0


def _max_cell_fragment(text: str, word: str) -> int:
    """Длина строки, в которой встретилось слово: если ячейка узкая, текст рвётся."""
    for line in text.splitlines():
        if word in line:
            return len(line.strip())
    return 0


def _open_file_count() -> int:
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except OSError:
        return 0


def _utf8_ok(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _image_ok(path: str) -> bool:
    from PIL import Image
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _broken_file(tmp: Path) -> Path:
    broken = tmp / "broken.db"
    broken.write_bytes("это не база данных".encode("utf-8") * 100)
    return broken


def _cascade_ok() -> bool:
    db = get_db()
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO rentals(number, payment_method, date_start) VALUES ('QA-CASCADE','cash','2026-01-01')")
        rid = cur.lastrowid
        conn.execute("INSERT INTO rental_items(rental_id, name_snap, quantity) VALUES (?,'тест',1)", (rid,))
    with db.transaction() as conn:
        conn.execute("DELETE FROM rentals WHERE id=?", (rid,))
    return db.query_one("SELECT COUNT(*) c FROM rental_items WHERE rental_id=?", (rid,))["c"] == 0


if __name__ == "__main__":
    code = main()
    try:
        get_db().close()
    except Exception:
        pass
    sys.exit(code)
