# -*- coding: utf-8 -*-
"""
Незавершённые аренды: несколько оформлений одновременно.

Проверяется сценарий из задачи целиком: два клиента, переключение между
оформлениями, сохранение состояния, выдача, отмена, перезапуск программы,
разделение по пользователям и то, что номера не пересекаются с обычными
арендами.

Запуск:  python -m tests.test_v24_drafts
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_drafts_"))
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
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.clients_service import clients_service
    from app.services.inventory_service import inventory_service
    from app.services.productions_service import productions_service
    from app.services.projects_service import projects_service
    from app.services.rental_drafts_service import rental_drafts_service as drafts
    from app.services.rentals_service import RentalDraft, RentalLine, rentals_service
    from app.utils import today

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Иван", "admin", "1234")
    auth_service.pop_recovery_code()
    auth_service.login("admin", "1234")

    cat = categories_service.tree()[0]["id"]
    first_client = clients_service.create({"full_name": "Первый Клиент",
                                           "phone": "+70000000001"})
    second_client = clients_service.create({"full_name": "Второй Клиент",
                                            "phone": "+70000000002"})
    prod = productions_service.create({"name": "Кинокомпания «Кадр»", "legal_form": "ООО"})
    project = projects_service.create({"name": "Фильм «Проба»", "production_id": prod})
    chairs = inventory_service.create({"name": "Стул венский", "category_id": cat,
                                       "quantity_total": 50, "rental_price": 100})
    lamps = inventory_service.create({"name": "Лампа", "category_id": cat,
                                      "quantity_total": 20, "rental_price": 200})

    # ------------------------------------------------------------------
    section("1–2. Открытие двух оформлений")
    first = drafts.open_new()
    check(first["number"].startswith("АР-"),
          f"Черновик получил номер обычного вида ({first['number']})")
    second = drafts.open_new()
    check(second["number"] != first["number"], "Второй черновик получил свой номер")
    check([d["id"] for d in drafts.list_open()] == [first["id"], second["id"]],
          "Оба видны в списке незавершённых")
    check(drafts.count_open() == 2, "Счётчик показывает две открытые аренды")

    # ------------------------------------------------------------------
    section("3. Состояние сохраняется при переключении")
    state_first = {
        "client_id": first_client, "production_id": prod, "project_id": project,
        "payment_method": "cash", "deposit": 5000.0,
        "date_start": str(today()), "date_end": str(today() + timedelta(days=7)),
        "lines": [{"item_id": chairs, "quantity": 15, "price": 100}],
        "note": "первый клиент",
    }
    drafts.save(first["id"], state_first)

    state_second = {
        "client_id": second_client, "production_id": prod, "project_id": project,
        "payment_method": "bank", "deposit": 0.0,
        "date_start": str(today()), "date_end": None,
        "lines": [{"item_id": lamps, "quantity": 3, "price": 200}],
        "note": "второй клиент",
    }
    drafts.save(second["id"], state_second)

    back = drafts.get(first["id"])["payload"]
    check(back["client_id"] == first_client, "Клиент первого оформления на месте")
    check(back["lines"][0]["quantity"] == 15, "Все 15 предметов на месте")
    check(back["deposit"] == 5000.0 and back["payment_method"] == "cash",
          "Депозит и способ оплаты не сбросились")
    check(back["note"] == "первый клиент", "Примечание не перепуталось")
    check(drafts.get(second["id"])["payload"]["client_id"] == second_client,
          "Второе оформление хранит своего клиента")

    # ------------------------------------------------------------------
    section("4. Номера не пересекаются с обычными арендами")
    plain = rentals_service.create(RentalDraft(
        client_id=first_client, production_id=prod, project_id=project,
        payment_method="cash", date_start=today(), date_end=today() + timedelta(days=3),
        lines=[RentalLine(chairs, "Стул венский", "", "", "", 1, 100, 0)]))
    plain_number = rentals_service.get(plain)["number"]
    check(plain_number not in (first["number"], second["number"]),
          f"Обычная аренда получила свой номер ({plain_number})")
    numbers = [r["number"] for r in get_db().query("SELECT number FROM rentals")]
    numbers += [d["number"] for d in get_db().query("SELECT number FROM rental_drafts")]
    check(len(numbers) == len(set(numbers)), f"Все номера уникальны: {sorted(numbers)}")

    # ------------------------------------------------------------------
    section("9. Оформления разных пользователей не смешиваются")
    auth_service.create_user("Сидоров Сидор", "sidorov", "1234", role="user")
    auth_service.logout()
    auth_service.login("sidorov", "1234")
    check(drafts.list_open() == [], "Новый пользователь не видит чужих оформлений")
    other = drafts.open_new()
    check(len(drafts.list_open()) == 1, "И видит только своё")
    expect_error(lambda: drafts.save_strict(first["id"], {}) if hasattr(
        drafts, "save_strict") else drafts._require_own(first["id"]),
        PermissionDeniedError, "Чужое оформление править нельзя")
    drafts.cancel(other["id"])
    auth_service.logout()
    auth_service.login("admin", "1234")
    check(len(drafts.list_open()) == 2, "У администратора по-прежнему два своих")

    # ------------------------------------------------------------------
    section("5. Выдача: черновик уходит, аренда остаётся")
    payload = drafts.get(second["id"])["payload"]
    rental_id = rentals_service.create(RentalDraft(
        client_id=payload["client_id"], production_id=payload["production_id"],
        project_id=payload["project_id"], payment_method=payload["payment_method"],
        date_start=today(), date_end=None,
        lines=[RentalLine(lamps, "Лампа", "", "", "", 3, 200, 0)]),
        number=second["number"])
    drafts.mark_issued(second["id"], rental_id)

    check(rentals_service.get(rental_id)["number"] == second["number"],
          f"Аренда выдана под тем же номером ({second['number']})")
    open_now = [d["id"] for d in drafts.list_open()]
    check(second["id"] not in open_now, "Выданное оформление ушло из списка")
    check(first["id"] in open_now, "Первое оформление осталось")

    from app.services.reports_service import reports_service

    found = reports_service.search(today() - timedelta(days=1), today() + timedelta(days=30))
    check(any(r["id"] == rental_id for r in found), "Аренда видна в отчётах")
    check(any(r["id"] == rental_id for r in clients_service.rentals(second_client)),
          "Аренда попала в карточку клиента")
    check(get_db().query_one("SELECT status, rental_id FROM rental_drafts WHERE id=?",
                             (second["id"],))["rental_id"] == rental_id,
          "Черновик помнит, во что превратился")

    # ------------------------------------------------------------------
    section("6. Отмена оформления")
    third = drafts.open_new()
    third_number = drafts.cancel(third["id"])
    check(third_number == third["number"], "Отмена вернула номер оформления")
    check(third["id"] not in [d["id"] for d in drafts.list_open()],
          "Отменённое ушло из списка")
    check(drafts.number_taken(third_number),
          "Номер остался занятым — переиспользовать его нельзя")
    after = drafts.open_new()
    check(after["number"] != third_number, "Следующее оформление получило новый номер")
    expect_error(lambda: drafts.cancel(third["id"]), ValidationError,
                 "Повторная отмена отклоняется")
    drafts.cancel(after["id"])

    # ------------------------------------------------------------------
    section("8. Программа закрылась и открылась заново")
    saved_number = first["number"]
    db.close()

    restarted = Database()
    restarted.initialize()
    set_db(restarted)
    auth_service.logout()
    auth_service.login("admin", "1234")
    restored = drafts.list_open()
    check(len(restored) == 1, f"После перезапуска осталось одно оформление ({len(restored)})")
    check(restored[0]["number"] == saved_number,
          "Номер незавершённой аренды сохранился")
    payload = restored[0]["payload"]
    check(payload.get("client_id") == first_client and payload["lines"][0]["quantity"] == 15,
          "Состояние формы восстановлено целиком")

    # ------------------------------------------------------------------
    section("12–14. Выдача последнего оформления")
    rental_first = rentals_service.create(RentalDraft(
        client_id=first_client, production_id=prod, project_id=project,
        payment_method="cash", date_start=today(),
        date_end=today() + timedelta(days=7),
        lines=[RentalLine(chairs, "Стул венский", "", "", "", 15, 100, 0)]),
        number=saved_number)
    drafts.mark_issued(restored[0]["id"], rental_first)
    check(drafts.list_open() == [], "Незавершённых оформлений не осталось")
    check(rentals_service.get(rental_first)["number"] == saved_number,
          "И эта аренда сохранила свой номер")

    total = get_db().query_one("SELECT COUNT(*) c FROM rentals")["c"]
    check(total == 3, f"В базе три аренды: две из оформлений и одна обычная ({total})")

    # ------------------------------------------------------------------
    section("Ограничения и ошибки")
    opened = [drafts.open_new() for _ in range(12)]
    expect_error(drafts.open_new, ValidationError,
                 "Больше дюжины одновременных оформлений не открыть")
    for draft in opened:
        drafts.cancel(draft["id"])
    check(drafts.list_open() == [], "Все временные оформления убраны")
    expect_error(lambda: drafts.get(999999), Exception,
                 "Несуществующее оформление не находится")

    check(inventory_service.get(chairs)["available_now"] == 50 - 1 - 15,
          "Черновики остатки не двигают: занято только выданное")

    section("Интерфейс: подпункты и переключение")
    _check_ui()

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки незавершённых аренд пройдены.")
    return 0


def _check_ui() -> None:
    """
    Проверка по исходникам: PySide6 здесь не запускается, но убедиться, что
    оформления действительно живут в одном окне и подключены к меню, можно.
    """
    ui = Path(__file__).resolve().parent.parent / "app" / "ui"
    hub = (ui / "rental_hub.py")
    check(hub.exists(), "Контейнер параллельных оформлений существует")
    if not hub.exists():
        return
    hub_src = hub.read_text(encoding="utf-8")
    window = (ui / "main_window.py").read_text(encoding="utf-8")
    form = (ui / "rental_new_view.py").read_text(encoding="utf-8")

    check("QStackedWidget" in hub_src and "class NewRentalHub" in hub_src,
          "Все оформления — страницы одного окна, отдельных окон не создаётся")
    check("def restore_open" in hub_src and "rental_drafts_service.list_open" in hub_src,
          "После запуска незавершённые оформления возвращаются на место")
    check("def cancel_draft" in hub_src and "confirm(self" in hub_src,
          "Отмена оформления спрашивает подтверждение")
    check("def save_open_drafts" in hub_src,
          "Состояние всех форм сохраняется, а не только текущей")

    check("NewRentalHub()" in window, "Главное окно использует контейнер")
    check("self.draft_menu" in window and "_reload_draft_menu" in window,
          "В меню есть подпункты незавершённых аренд")
    check("NavSubButton" in window, "Подпункты оформлены отдельным стилем")
    check("def open_draft" in window, "По подпункту можно переключиться")
    check("def _start_new_rental" in window,
          "Кнопка в шапке начинает ещё одно оформление")
    check('f"   ({count})"' in window or "({count})" in window,
          "Рядом с разделом виден счётчик открытых оформлений")

    check("self.draft_id" in form and "self.draft_number" in form,
          "Форма привязана к своему оформлению")
    check("number=self.draft_number" in form,
          "Выдача идёт под забронированным номером")
    check("rental_drafts_service.mark_issued" in form,
          "После выдачи оформление закрывается")


if __name__ == "__main__":
    raise SystemExit(main())
