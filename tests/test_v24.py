# -*- coding: utf-8 -*-
"""
Тесты версии 2.4, часть 1: аварийное восстановление доступа и штрихкоды.

Запуск:  python -m tests.test_v24
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_v24_"))
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
    except Exception as exc:                                  # noqa: BLE001
        failures.append(f"{title} (получено {type(exc).__name__}: {exc})")
        print(f"   ОШИБКА {title} — неожиданная ошибка {type(exc).__name__}: {exc}")
    else:
        failures.append(f"{title} (ошибка не возникла)")
        print(f"   ОШИБКА {title} — ошибки не было, хотя она обязана быть")


def main() -> int:
    from app.db.database import Database, set_db, get_db
    from app.errors import AuthError, ValidationError
    from app.services import code128
    from app.services.auth_service import auth_service
    from app.services.barcode_service import barcode_service
    from app.services.categories_service import categories_service
    from app.services.inventory_service import inventory_service
    from app.services.recovery_service import recovery_service
    from app.db.seed import seed_if_empty

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    # ------------------------------------------------------------------
    section("Подготовка")
    admin_id = auth_service.create_first_admin("Иванов Иван", "admin", "старый-пароль")
    check(admin_id > 0, "Администратор создан")

    code = auth_service.pop_recovery_code()
    check(bool(code), "Код восстановления выдан автоматически")
    check(len(code) == 19 and code.count("-") == 3, f"Формат кода XXXX-XXXX-XXXX-XXXX ({code})")
    check(auth_service.pop_recovery_code() == "", "Повторно код из памяти не получить")

    stored = get_db().query_one("SELECT code_hash FROM recovery_codes WHERE user_id=?",
                                (admin_id,))["code_hash"]
    plain = code.replace("-", "")
    check(plain not in stored and code not in stored, "В базе лежит хэш, а не сам код")

    logs = get_db().query("SELECT action, details FROM audit_log")
    leaked = any(plain in (r["details"] or "") or code in (r["details"] or "") for r in logs)
    check(not leaked, "Код не попал в журнал аудита")

    auth_service.login("admin", "старый-пароль")

    # ------------------------------------------------------------------
    section("Сценарий 31: потерян пароль администратора")
    auth_service.logout()
    expect_error(lambda: auth_service.login("admin", "старый-пароль-2"), AuthError,
                 "Неверным паролем войти нельзя")

    expect_error(lambda: recovery_service.reset_password("AAAA-BBBB-CCCC-DDDD", "новый-пароль"),
                 AuthError, "Чужой код не подходит")

    result = recovery_service.reset_password(code, "новый-пароль")
    check(result.login == "admin", "Восстановление выполнено для нужного администратора")

    user = auth_service.login("admin", "новый-пароль")
    check(user.is_admin, "Вход новым паролем работает")

    auth_service.logout()
    expect_error(lambda: auth_service.login("admin", "старый-пароль"), AuthError,
                 "Старый пароль больше не работает")

    expect_error(lambda: recovery_service.reset_password(code, "ещё-пароль"), AuthError,
                 "Использованный код второй раз не работает")

    check(bool(result.new_code) and result.new_code != code, "Выдан новый код восстановления")

    # данные склада не пострадали
    auth_service.login("admin", "новый-пароль")
    tree = categories_service.tree()
    check(len(tree) > 30, "Каталог категорий на месте после восстановления")

    # ------------------------------------------------------------------
    section("Одноразовость и права")
    second = recovery_service.reset_password(result.new_code, "пароль-три")
    auth_service.logout()
    auth_service.login("admin", "пароль-три")
    check(True, "Второй цикл восстановления работает")
    expect_error(lambda: recovery_service.reset_password(result.new_code, "x-y-z"), AuthError,
                 "Предыдущий код погашен после второго восстановления")

    active = get_db().query_one(
        "SELECT COUNT(*) c FROM recovery_codes WHERE user_id=? AND used_at IS NULL "
        "AND revoked_at IS NULL", (admin_id,))["c"]
    check(active == 1, f"Действующий код ровно один (найдено {active})")

    auth_service.create_user("Петров Пётр", "petrov", "1234", role="user")
    auth_service.logout()
    auth_service.login("petrov", "1234")
    expect_error(recovery_service.reissue_for_current_admin, Exception,
                 "Обычный пользователь не может выпустить код восстановления")
    expect_error(lambda: recovery_service.issue(auth_service.current_id()), ValidationError,
                 "Код нельзя выдать не-администратору")
    auth_service.logout()
    auth_service.login("admin", "пароль-три")

    # ------------------------------------------------------------------
    section("Защита от перебора")
    recovery_service._reset_failures()
    for _ in range(5):
        try:
            recovery_service.check("ZZZZ-ZZZZ-ZZZZ-ZZZZ")
        except AuthError:
            pass
    left = recovery_service.lock_seconds_left()
    check(left > 0, f"После 5 неверных попыток включилась блокировка ({left} с)")
    expect_error(lambda: recovery_service.check(second.new_code), AuthError,
                 "Во время блокировки даже верный код отклоняется")
    recovery_service._reset_failures()
    check(recovery_service.lock_seconds_left() == 0, "Счётчик попыток сбрасывается")
    check(recovery_service.check(second.new_code)["login"] == "admin",
          "После сброса верный код снова принимается")

    # ------------------------------------------------------------------
    section("Штрихкоды: кодирование Code 128")
    check(code128.encode_values("CODE128") == [104, 35, 47, 36, 37, 17, 18, 24, 26, 106],
          "Контрольная сумма совпадает с эталоном стандарта")
    check(code128.encode_values("000123")[0] == code128.START_C,
          "Числовой код кодируется компактным набором C")
    check(code128.encode_values("ABC-1")[0] == code128.START_B,
          "Буквенный код кодируется набором B")
    check(not code128.is_encodable("СТУЛ"), "Кириллица в штрихкод не кодируется")
    bars = code128.encode_bars("000123")
    check(all(1 <= w <= 4 for _, w in bars), "Ширины элементов в допустимых пределах")
    check(bars[0][0] is True and bars[-1][0] is True, "Код начинается и заканчивается штрихом")

    # ------------------------------------------------------------------
    section("Штрихкоды: предметы")
    cat = categories_service.tree()[0]
    chair_id = inventory_service.create({"name": "Стул венский", "category_id": cat["id"],
                                         "quantity_total": 5, "rental_price": 100})
    chair = inventory_service.get(chair_id)
    check(chair["barcode"] == chair["inv_number"],
          f"Новый предмет сразу получил штрихкод ({chair['barcode']})")

    found = barcode_service.find_item(chair["barcode"])
    check(found and found["id"] == chair_id, "Поиск по отсканированному коду находит предмет")
    found = barcode_service.find_item(f"  {chair['barcode'].lower()}  ")
    check(found and found["id"] == chair_id, "Лишние пробелы и регистр не мешают")
    found = barcode_service.find_item(str(int(chair["inv_number"])))
    check(found and found["id"] == chair_id, "Номер без ведущих нулей тоже находится")
    check(barcode_service.find_item("НЕТ-ТАКОГО") is None, "Неизвестный код не находится")
    check(barcode_service.find_item("") is None, "Пустой ввод не находит ничего")

    lamp_id = inventory_service.create({"name": "Лампа настольная зелёная, стекло",
                                        "category_id": cat["id"], "quantity_total": 2})
    dup = get_db().query_one(
        "SELECT COUNT(*) c FROM inventory_items WHERE barcode=(SELECT barcode "
        "FROM inventory_items WHERE id=?)", (lamp_id,))["c"]
    check(dup == 1, "Штрихкоды не дублируются")

    # ------------------------------------------------------------------
    section("Штрихкоды: печать этикеток")
    out = _TMP / "labels"
    single = barcode_service.labels_pdf([chair_id], out / "one.pdf", fmt="thermal_58x40")
    check(single.exists() and single.stat().st_size > 800, "Этикетка 58×40 мм создана")
    check(_pdf_pages(single) == 1, "Одна этикетка — одна страница")

    many_ids = [chair_id, lamp_id] * 12
    sheet = barcode_service.labels_pdf(many_ids, out / "sheet.pdf", fmt="sheet_a4_3x8")
    check(sheet.exists(), "Лист A4 с 24 этикетками создан")
    check(_pdf_pages(sheet) == 1, "24 этикетки помещаются на один лист A4")

    big = barcode_service.labels_pdf(many_ids + [chair_id], out / "sheet2.pdf",
                                     fmt="sheet_a4_3x8")
    check(_pdf_pages(big) == 2, "25-я этикетка уходит на второй лист")

    copies = barcode_service.labels_pdf([chair_id], out / "copies.pdf",
                                        fmt="thermal_58x30", copies=3)
    check(_pdf_pages(copies) == 3, "Печать в трёх экземплярах даёт три страницы")

    png = barcode_service.render_png(chair["barcode"], out / "code.png")
    check(png.exists() and png.stat().st_size > 100, "PNG для предпросмотра создан")

    doc = recovery_service.code_document_pdf("ABCD-EFGH-JKMN-PQRT", out / "recovery.pdf",
                                             user_name="Иванов Иван")
    check(Path(doc).exists(), "Печатная форма кода восстановления создана")

    # ------------------------------------------------------------------
    section("Интерфейс версии 2.4 (статическая проверка)")
    _check_ui()

    # ------------------------------------------------------------------
    section("Совместимость: миграция старой базы")
    check(_migration_ok(), "База версии 2.3 обновляется до 2.4 без потери данных")

    # ------------------------------------------------------------------
    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки версии 2.4 (часть 1) пройдены.")
    return 0


def _check_ui() -> None:
    """
    Интерфейс проверяется по исходникам: PySide6 в этой среде не запускается,
    но убедиться, что новые функции действительно подключены к экранам,
    а не остались висеть в воздухе, можно и так.
    """
    ui = Path(__file__).resolve().parent.parent / "app" / "ui"
    src = {name: (ui / name).read_text(encoding="utf-8")
           for name in ("login_dialog.py", "item_card.py", "catalog_view.py",
                        "settings_view.py", "recovery_dialog.py", "label_dialog.py")}

    check("class RecoveryCodeDialog" in src["recovery_dialog.py"], "Диалог показа кода восстановления существует")
    check("class RecoveryLoginDialog" in src["recovery_dialog.py"], "Диалог входа по коду существует")
    check("Распечатать код восстановления" in src["recovery_dialog.py"], "Есть кнопка «Распечатать код восстановления»")
    check("def closeEvent" in src["recovery_dialog.py"]
          and "event.ignore()" in src["recovery_dialog.py"], "Закрыть окно, не сохранив код, нельзя")
    check("self.done_btn.setEnabled(False)" in src["recovery_dialog.py"], "Кнопка «Готово» разблокируется только после подтверждения")
    check("BACKUPS_DIR" not in src["recovery_dialog.py"]
          and "DOCS_DIR" not in src["recovery_dialog.py"], "Код не пишется в папку данных сам по себе")

    check("Восстановить доступ" in src["login_dialog.py"]
          and "RecoveryLoginDialog" in src["login_dialog.py"], "На экране входа есть восстановление доступа")
    check("pop_recovery_code" in src["login_dialog.py"]
          and "RecoveryCodeDialog" in src["login_dialog.py"], "После создания администратора код показывается сразу")

    check("Печать этикетки" in src["item_card.py"]
          and "LabelPrintDialog" in src["item_card.py"], "В карточке предмета есть печать этикетки")
    check("Штрихкод:" in src["item_card.py"], "В карточке виден штрихкод")
    check("self.label_btn.setEnabled(has_code)" in src["item_card.py"], "Кнопка этикетки гаснет без штрихкода")

    check("Печать этикеток" in src["catalog_view.py"]
          and "def selected_item_ids" in src["catalog_view.py"], "В каталоге есть массовая печать этикеток")
    check("ExtendedSelection" in src["catalog_view.py"], "В таблице каталога можно выделить несколько строк")

    check("QPrintDialog" in src["label_dialog.py"] and "QPageSize" in src["label_dialog.py"], "Диалог этикеток печатает по-настоящему")
    check("QPageSize.Millimeter" in src["label_dialog.py"], "Размер страницы задаётся в миллиметрах")

    check("_security_tab" in src["settings_view.py"]
          and src["settings_view.py"].index("if auth_service.is_admin():")
          < src["settings_view.py"].index('self._security_tab()'), "Вкладка безопасности только для администратора")
    check("reissue_for_current_admin" in src["settings_view.py"]
          and "confirm(self" in src["settings_view.py"], "Перевыпуск кода спрашивает подтверждение")


def _pdf_pages(path: Path) -> int:
    """Число страниц в PDF без внешних библиотек — по счётчику /Type /Page."""
    data = path.read_bytes()
    return data.count(b"/Type /Page") - data.count(b"/Type /Pages")


def _migration_ok() -> bool:
    """
    Проверяет, что база предыдущей версии обновляется: колонки добавляются,
    существующие предметы получают штрихкоды, данные остаются на месте.
    """
    import sqlite3

    from app.db.database import Database, get_db, set_db
    from app.version import DB_SCHEMA_VERSION

    old_db = get_db()
    legacy = _TMP / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    conn.executescript(
        "CREATE TABLE inventory_items ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, inv_number TEXT NOT NULL UNIQUE,"
        " name TEXT NOT NULL, category_id INTEGER, description TEXT NOT NULL DEFAULT '',"
        " keywords TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',"
        " quantity_total INTEGER NOT NULL DEFAULT 1, rental_price REAL NOT NULL DEFAULT 0,"
        " valuation REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'in_stock',"
        " is_archived INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO inventory_items(inv_number, name) VALUES ('000042','Старый стул');"
        "INSERT INTO inventory_items(inv_number, name) VALUES ('000043','Старая лампа');"
        "PRAGMA user_version=5;")
    conn.commit()
    conn.close()

    migrated = Database(legacy)
    migrated.initialize()
    set_db(migrated)
    try:
        rows = migrated.query("SELECT inv_number, name, barcode FROM inventory_items "
                              "ORDER BY inv_number")
        ok = (len(rows) == 2
              and rows[0]["name"] == "Старый стул"
              and rows[0]["barcode"] == "000042"
              and rows[1]["barcode"] == "000043"
              and migrated.query_one("PRAGMA user_version")[0] == DB_SCHEMA_VERSION)
        migrated.query_one("SELECT COUNT(*) FROM recovery_codes")
        migrated.query_one("SELECT COUNT(*) FROM security_attempts")
    except Exception as exc:                                   # noqa: BLE001
        print(f"   (миграция упала: {exc})")
        ok = False
    finally:
        migrated.close()
        set_db(old_db)
    return bool(ok)


if __name__ == "__main__":
    raise SystemExit(main())
