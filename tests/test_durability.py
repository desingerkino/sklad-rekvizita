# -*- coding: utf-8 -*-
"""
Проверка автосохранения и устойчивости к аварийному завершению.

Тест работает НЕ в памяти: каждый «сеанс работы» — отдельный процесс Python,
который пишет данные и затем убивается без всякой уборки — как при снятии
задачи через диспетчер задач или пропадании питания. На Linux это SIGKILL,
на Windows — TerminateProcess: сигнала SIGKILL там просто нет. Следующий процесс
открывает базу заново и проверяет, что данные на месте, база цела, а начатая,
но не подтверждённая операция не оставила половинчатой записи.

Запуск:  python -m tests.test_durability
"""
from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="sklad_durab_"))

PASSED, FAILED = [], []

# Код возврата процесса, убитого без возможности прибраться за собой.
# На POSIX это отрицательный номер сигнала, на Windows — код, который мы
# передаём в TerminateProcess.
HARD_KILL_CODE = 137
KILLED_RETURNCODE = -signal.SIGKILL if hasattr(signal, "SIGKILL") else HARD_KILL_CODE


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def run_session(code: str, expect_kill: bool = False) -> subprocess.CompletedProcess:
    """Запускает отдельный процесс-«сеанс работы в программе»."""
    script = textwrap.dedent(f"""
        import os, sys, signal
        def hard_kill():
            # Мгновенная смерть процесса: без закрытия базы, без atexit и без
            # finally — как снятие задачи в диспетчере или пропадание питания.
            # На Windows сигнала SIGKILL нет, там os._exit обрывает процесс
            # так же резко: интерпретатор не прибирается, SQLite не сводит
            # журнал, файл data.db-wal остаётся незакрытым.
            sys.stdout.flush()      # свой вывод отдаём, базу — нет
            if hasattr(signal, "SIGKILL"):
                os.kill(os.getpid(), signal.SIGKILL)
            os._exit({HARD_KILL_CODE})
        sys.path.insert(0, r"{ROOT}")
        os.environ["SKLAD_DATA_DIR"] = r"{TMP}"
        from app import paths
        paths.ensure_dirs()
        from app.db.database import Database, set_db, get_db
        from app.db.seed import seed_if_empty
        db = Database(paths.DB_PATH)
        db.initialize()
        set_db(db)
        seed_if_empty(db)
        from app.services.auth_service import auth_service
        from app.services.audit_service import audit_service
        from app.services.billing_service import billing_service
        from app.services.categories_service import categories_service
        from app.services.clients_service import clients_service
        from app.services.documents_service import documents_service
        from app.services.draft_service import DRAFT_NEW_RENTAL, draft_service
        from app.services.photo_service import photo_service
        from datetime import timedelta
        from app.services.inventory_service import inventory_service
        from app.services.productions_service import productions_service
        from app.services.projects_service import projects_service
        from app.services.rentals_service import RentalDraft, RentalLine, rentals_service
        from app.services.settings_service import settings_service
        if auth_service.has_users():
            auth_service.login("admin", "admin123")
        else:
            auth_service.create_first_admin("Администратор", "admin", "admin123")
            auth_service.login("admin", "admin123")
{textwrap.indent(textwrap.dedent(code), " " * 8)}
    """)
    result = subprocess.run([sys.executable, "-u", "-c", script],
                            capture_output=True, text=True)
    if not expect_kill and result.returncode != 0:
        print(result.stdout)
        print(result.stderr)
    return result


def db_query(sql: str, params=()):
    conn = sqlite3.connect(str(TMP / "data.db"))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def main() -> int:
    print(f"Папка данных теста: {TMP}\n")

    # ------------------------------------------------------------ Сеанс 1
    print("=== Сеанс 1: ввод данных без единого нажатия «Сохранить», затем аварийное завершение ===")
    session1 = """
        item_id = inventory_service.create({"name": "Стул для проверки", "quantity_total": 8,
                                            "rental_price": 1000, "valuation": 5000})
        client_id = clients_service.create({"full_name": "Проверкин Иван", "phone": "+7 900 111-22-33"})
        prod_id = productions_service.create({"name": "Продакшен Проверка", "legal_form": "ООО"})
        project_id = projects_service.create({"name": "Проект Проверка", "production_id": prod_id})
        settings_service.set("warehouse.name", "Склад до аварии")
        from datetime import date
        rental_id = rentals_service.create(RentalDraft(
            client_id=client_id, production_id=prod_id, project_id=project_id,
            payment_method="cash", date_start=date.today(),
            lines=[RentalLine(item_id, "Стул для проверки", "", "", "потёртость", 3, 1000, 5000)]))
        draft_service.save(DRAFT_NEW_RENTAL, {"client_text": "Недописанный клиент",
                                              "lines": [{"item_id": item_id, "name": "Стул",
                                                         "quantity": 2, "price": 1000}]})
        print("RENTAL_ID", rental_id, "ITEM_ID", item_id)
        # аварийное завершение: процесс убит, close() и чекпойнт WAL не выполнялись
        hard_kill()
    """
    result = run_session(session1, expect_kill=True)
    killed = result.returncode == KILLED_RETURNCODE
    check("Сеанс 1 завершён аварийно (без корректного закрытия базы)", killed,
          f"код возврата {result.returncode}")
    ids = dict(zip(result.stdout.split()[::2], result.stdout.split()[1::2]))
    rental_id, item_id = int(ids["RENTAL_ID"]), int(ids["ITEM_ID"])

    wal = Path(str(TMP / "data.db") + "-wal")
    check("Журнал WAL остался незакрытым после аварии", wal.exists(),
          "файла -wal нет, проверка теряет смысл")

    # ------------------------------------------------------------ Сеанс 2
    print("\n=== Сеанс 2: запуск после аварии — данные должны быть на месте ===")
    session2 = """
        item = inventory_service.get(%d)
        rental = rentals_service.get(%d)
        draft = draft_service.load(DRAFT_NEW_RENTAL)
        print("ITEM", item["name"] if item else None)
        print("QTY", item["quantity_total"] if item else 0)
        print("RENTAL", rental["number"] if rental else None)
        print("TOTAL", rental["total_final"] if rental else 0)
        print("NOTE", rental["items"][0]["note"] if rental else None)
        print("CLIENT", rental["client_name"] if rental else None)
        print("SETTING", settings_service.get("warehouse.name"))
        print("CHARGES", len(billing_service.charges(%d)))
        print("DRAFT", (draft or {}).get("client_text"))
        print("AUDIT", len(audit_service.search(limit=100)))
        print("INTEGRITY", get_db().query_one("PRAGMA integrity_check")[0])
        print("AVAILABLE", item["available_now"] if item else -1)
    """ % (item_id, rental_id, rental_id)
    out = run_session(session2)
    values = dict(line.split(" ", 1) for line in out.stdout.strip().splitlines() if " " in line)

    check("Реквизит сохранился", values.get("ITEM") == "Стул для проверки", values.get("ITEM"))
    check("Количество сохранилось", values.get("QTY") == "8", values.get("QTY"))
    check("Аренда сохранилась", values.get("RENTAL", "").startswith("АР-"), values.get("RENTAL"))
    check("Сумма аренды сохранилась", values.get("TOTAL") == "3000.0", values.get("TOTAL"))
    check("Примечание позиции сохранилось", values.get("NOTE") == "потёртость", values.get("NOTE"))
    check("Клиент сохранился", values.get("CLIENT") == "Проверкин Иван", values.get("CLIENT"))
    check("Настройки склада сохранились", values.get("SETTING") == "Склад до аварии",
          values.get("SETTING"))
    check("Начисления сохранились", values.get("CHARGES") == "1", values.get("CHARGES"))
    check("Журнал действий сохранился", int(values.get("AUDIT", 0)) >= 3, values.get("AUDIT"))
    check("Незаконченная форма восстановлена из черновика",
          values.get("DRAFT") == "Недописанный клиент", values.get("DRAFT"))
    check("Остатки пересчитаны с учётом аренды", values.get("AVAILABLE") == "5", values.get("AVAILABLE"))
    check("База не повреждена после аварии", values.get("INTEGRITY") == "ok", values.get("INTEGRITY"))

    # ------------------------------------------------------------ Сеанс 3
    print("\n=== Сеанс 3: авария посреди незавершённой транзакции ===")
    session3 = """
        before = inventory_service.count()
        print("BEFORE", before)
        db = get_db()
        conn = db.conn
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO inventory_items(inv_number, name, quantity_total) "
                     "VALUES ('999999','Недописанный предмет',1)")
        conn.execute("INSERT INTO clients(full_name) VALUES ('Недописанный клиент')")
        # питание пропало ровно здесь: COMMIT не выполнен
        hard_kill()
    """
    result3 = run_session(session3, expect_kill=True)
    before = int(result3.stdout.split()[1])
    check("Сеанс 3 убит внутри транзакции", result3.returncode == KILLED_RETURNCODE)

    session4 = """
        print("COUNT", inventory_service.count())
        print("ORPHAN", len(inventory_service.search("Недописанный")))
        print("CLIENTS", len(clients_service.search("Недописанный")))
        print("INTEGRITY", get_db().query_one("PRAGMA integrity_check")[0])
        print("FK", len(get_db().query("PRAGMA foreign_key_check")))
        print("RENTAL", (rentals_service.get(%d) or {}).get("number"))
    """ % rental_id
    out4 = run_session(session4)
    values4 = dict(line.split(" ", 1) for line in out4.stdout.strip().splitlines() if " " in line)
    check("Незавершённая транзакция откачена целиком", values4.get("COUNT") == str(before),
          f"{values4.get('COUNT')} вместо {before}")
    check("Половинчатых записей не осталось (реквизит)", values4.get("ORPHAN") == "0",
          values4.get("ORPHAN"))
    check("Половинчатых записей не осталось (клиент)", values4.get("CLIENTS") == "0",
          values4.get("CLIENTS"))
    check("Ранее сохранённые данные не пострадали",
          (values4.get("RENTAL") or "").startswith("АР-"), values4.get("RENTAL"))
    check("База цела после аварии в транзакции", values4.get("INTEGRITY") == "ok",
          values4.get("INTEGRITY"))
    check("Нарушений внешних ключей нет", values4.get("FK") == "0", values4.get("FK"))

    # ------------------------------------------------------------ Сеанс 5
    print("\n=== Сеанс 5: изменения версии 2.0 пишутся сразу, затем авария ===")
    session5 = """
        from datetime import date
        items = billing_service.items(%d)
        billing_service.return_item(items[0]["id"], 1, "принят один стул")
        rentals_service.set_discount(%d, 25)
        added = rentals_service.add_items(%d, [RentalLine(%d, "Стул для проверки", "", "", "", 2, 1000, 5000)])
        print("ADDED", added[0])
        hard_kill()
    """ % (rental_id, rental_id, rental_id, item_id)
    result5 = run_session(session5, expect_kill=True)
    check("Сеанс 5 убит сразу после операций", result5.returncode == KILLED_RETURNCODE)
    added_id = int(result5.stdout.split()[1])

    session6 = """
        rental = rentals_service.get(%d)
        items = billing_service.items(%d)
        added = [i for i in items if i["id"] == %d]
        print("RETURNED", items[0]["returned_qty"])
        print("RETURNS", len(items[0]["returns"]))
        print("DISCOUNT", rental["discount_percent"])
        print("DISCOUNT_AMOUNT", rental["discount_amount"])
        print("ADDED_OK", len(added))
        print("ADDED_PERIOD", added[0]["period_end"] if added else None)
        print("CHARGES", len(billing_service.charges(%d)))
        print("INTEGRITY", get_db().query_one("PRAGMA integrity_check")[0])
    """ % (rental_id, rental_id, added_id, rental_id)
    out6 = run_session(session6)
    values6 = dict(line.split(" ", 1) for line in out6.stdout.strip().splitlines() if " " in line)
    check("Возврат позиции сохранился", values6.get("RETURNED") == "1", values6.get("RETURNED"))
    check("Запись о возврате сохранилась", values6.get("RETURNS") == "1", values6.get("RETURNS"))
    check("Скидка сохранилась", values6.get("DISCOUNT") == "25.0", values6.get("DISCOUNT"))
    check("Пересчёт по скидке сохранился",
          values6.get("DISCOUNT_AMOUNT") == "1250.0", values6.get("DISCOUNT_AMOUNT"))
    check("Добор реквизита сохранился", values6.get("ADDED_OK") == "1", values6.get("ADDED_OK"))
    check("У добранной позиции сохранился её период", bool(values6.get("ADDED_PERIOD")),
          values6.get("ADDED_PERIOD"))
    check("Начисления добора сохранились", values6.get("CHARGES") == "2", values6.get("CHARGES"))
    check("База цела после третьей аварии", values6.get("INTEGRITY") == "ok", values6.get("INTEGRITY"))

    # --------------------------------------------------- кнопка «Сохранить всё»
    print("\n=== Кнопка «Сохранить всё» ===")
    session_save = """
        from app.services.storage_service import storage_service
        inventory_service.create({"name": "Предмет перед кнопкой", "quantity_total": 4})
        import os as _os
        wal_before = _os.path.getsize(str(paths.DB_PATH) + "-wal")
        report = storage_service.save_all()          # это и делает кнопка
        wal_after = _os.path.getsize(str(paths.DB_PATH) + "-wal")
        print("WALBEFORE", wal_before)
        print("WALAFTER", wal_after)
        print("PENDING", report["pending"])
        print("DBSIZE", report["db_size_mb"])
        print("COUNTS", len(report["counts"]))
        print("ITEMS", dict(report["counts"])["Реквизит"])
        print("LASTCHANGE", bool(storage_service.last_change()))
        hard_kill()       # авария сразу после нажатия
    """
    result_save = run_session(session_save, expect_kill=True)
    values_s = dict(line.split(" ", 1) for line in result_save.stdout.strip().splitlines() if " " in line)
    check("До нажатия журнал WAL был не пуст", int(values_s.get("WALBEFORE", 0)) > 0,
          values_s.get("WALBEFORE"))
    check("Кнопка свела журнал в файл базы (WAL стал пустым)",
          values_s.get("WALAFTER") == "0", values_s.get("WALAFTER"))
    check("Отчёт сообщает, что несохранённого нет", values_s.get("PENDING") == "False",
          values_s.get("PENDING"))
    check("Отчёт показывает размер базы", float(values_s.get("DBSIZE", 0)) > 0, values_s.get("DBSIZE"))
    check("Отчёт содержит все разделы данных", values_s.get("COUNTS") == "10", values_s.get("COUNTS"))
    check("Счётчик реквизита в отчёте верный", int(values_s.get("ITEMS", 0)) >= 2, values_s.get("ITEMS"))
    check("Время последнего изменения определяется", values_s.get("LASTCHANGE") == "True")

    session_after_save = """
        print("SAVED", len(inventory_service.search("Предмет перед кнопкой")))
        print("INTEGRITY", get_db().query_one("PRAGMA integrity_check")[0])
    """
    out_after = run_session(session_after_save)
    values_a = dict(line.split(" ", 1) for line in out_after.stdout.strip().splitlines() if " " in line)
    check("После «Сохранить всё» и аварии данные на месте", values_a.get("SAVED") == "1",
          values_a.get("SAVED"))
    check("База цела", values_a.get("INTEGRITY") == "ok", values_a.get("INTEGRITY"))

    # ------------------------------- перезапуск и сохранность всех сущностей
    print("\n=== Перезапуск программы: сохраняются все виды данных ===")
    session_all = """
        from datetime import date
        cat = categories_service.add("Категория перезапуска")
        sub = categories_service.add("Подкатегория перезапуска", cat)
        item = inventory_service.create({"name": "Реквизит перезапуска", "category_id": sub,
                                         "description": "описание", "keywords": "ключевые слова",
                                         "quantity_total": 6, "rental_price": 700, "valuation": 4000,
                                         "note": "примечание"})
        from PIL import Image
        p = paths.TEMP_DIR / "restart.jpg"
        Image.new("RGB", (300, 300), (10, 120, 200)).save(p, "JPEG")
        photo_id = photo_service.add_photo(item, p, "main")
        client = clients_service.create({"full_name": "Перезапускин Пётр", "passport": "11 22 333444",
                                         "phone": "+7 911", "address": "Адрес", "email": "p@e.ru",
                                         "extra": "доп"})
        prod = productions_service.create({"name": "Продакшен перезапуска", "legal_form": "ООО",
                                           "inn": "7700000001", "kpp": "770001001", "ogrn": "1150000000",
                                           "legal_address": "Юр", "actual_address": "Факт",
                                           "bank_name": "Банк", "bank_account": "40702810000000000001",
                                           "bank_bik": "044525000", "bank_corr": "30101810000000000001",
                                           "phone": "+7 495", "email": "p@prod.ru",
                                           "contact_person": "Контакт", "contact_position": "Должность"})
        proj = projects_service.create({"name": "Проект перезапуска", "production_id": prod})
        rent = rentals_service.create(RentalDraft(
            client_id=client, production_id=prod, project_id=proj, payment_method="bank",
            date_start=date.today(), date_end=date.today() + timedelta(days=6),
            deposit=15000, lines=[RentalLine(item, "Реквизит перезапуска", "", "", "потёртость",
                                             2, 700, 4000)]))
        doc = documents_service.create_document(rent)
        settings_service.set("warehouse.phone", "+7 000 000-00-00")
        print("IDS", item, client, prod, proj, rent, doc, photo_id, sub)
    """
    out_all = run_session(session_all)
    ids = out_all.stdout.strip().split()[1:]
    check("Сеанс с полным набором данных отработал", len(ids) == 8, out_all.stdout.strip()[:200])
    item_i, client_i, prod_i, proj_i, rent_i, doc_i, photo_i, sub_i = [int(x) for x in ids]

    session_check = """
        from app.services.documents_service import documents_service
        from app.services.photo_service import photo_service
        from app.services.categories_service import categories_service
        from pathlib import Path
        item = inventory_service.get(%d)
        rental = rentals_service.get(%d)
        prod = productions_service.get(%d)
        client = clients_service.get(%d)
        photos = photo_service.photos_of(%d)
        print("ITEM", item["name"], "|", item["description"], "|", item["keywords"], "|",
              item["quantity_total"], item["rental_price"], item["valuation"], "|", item["note"])
        print("CATEGORY", item["category_path"])
        print("PHOTO", len(photos), Path(photos[0]["abs_path"]).exists() if photos else False)
        print("CLIENTFIELDS", all([client["passport"], client["phone"], client["address"],
                                   client["email"], client["extra"]]))
        print("PRODFIELDS", all([prod[f] for f in ("name","legal_form","inn","kpp","ogrn",
              "legal_address","actual_address","bank_name","bank_account","bank_bik","bank_corr",
              "phone","email","contact_person","contact_position")]))
        print("PROJECT", projects_service.get(%d)["name"])
        print("RENTAL", rental["number"], rental["total_final"], rental["deposit"])
        print("RENTALNOTE", rental["items"][0]["note"])
        print("DOCPDF", Path(documents_service.get(%d)["abs_path"]).exists())
        print("SNAPSHOT", documents_service.get_snapshot(%d)["client"]["phone"])
        print("SETTING", settings_service.get("warehouse.phone"))
        print("SEARCH", len(inventory_service.search("Реквизит перезапуска")))
        print("CATTREE", categories_service.get(%d)["name"])
    """ % (item_i, rent_i, prod_i, client_i, item_i, proj_i, doc_i, doc_i, sub_i)
    out_check = run_session(session_check)
    v = dict(line.split(" ", 1) for line in out_check.stdout.strip().splitlines() if " " in line)
    check("Карточка реквизита полностью сохранилась",
          v.get("ITEM") == "Реквизит перезапуска | описание | ключевые слова | 6 700.0 4000.0 | примечание",
          v.get("ITEM"))
    check("Категория и подкатегория сохранились",
          v.get("CATEGORY") == "Категория перезапуска / Подкатегория перезапуска", v.get("CATEGORY"))
    check("Фотография и файл на месте", v.get("PHOTO") == "1 True", v.get("PHOTO"))
    check("Все поля клиента сохранились", v.get("CLIENTFIELDS") == "True")
    check("Все реквизиты продакшена сохранились", v.get("PRODFIELDS") == "True")
    check("Проект сохранился", v.get("PROJECT") == "Проект перезапуска", v.get("PROJECT"))
    check("Аренда, сумма и залог сохранились",
          v.get("RENTAL", "").endswith("1400.0 15000.0"), v.get("RENTAL"))
    check("Примечание позиции сохранилось", v.get("RENTALNOTE") == "потёртость", v.get("RENTALNOTE"))
    check("PDF документа на месте после перезапуска", v.get("DOCPDF") == "True")
    check("Исторический снимок документа сохранился", v.get("SNAPSHOT") == "+7 911", v.get("SNAPSHOT"))
    check("Настройки склада сохранились", v.get("SETTING") == "+7 000 000-00-00", v.get("SETTING"))
    check("Поиск находит данные после перезапуска", v.get("SEARCH") == "1", v.get("SEARCH"))
    check("Дерево категорий читается", v.get("CATTREE") == "Подкатегория перезапуска")

    # ------------------------------------------------------- корректный выход
    print("\n=== Сеанс 7: корректное закрытие программы ===")
    session7 = """
        inventory_service.create({"name": "Предмет перед закрытием", "quantity_total": 1})
        get_db().close()      # то, что делает окно программы при закрытии
        print("CLOSED ok")
    """
    run_session(session7)
    wal_size = wal.stat().st_size if wal.exists() else 0
    check("После корректного закрытия журнал WAL сведён в базу", wal_size == 0,
          f"{wal_size} байт")
    rows = db_query("SELECT COUNT(*) c FROM inventory_items WHERE name='Предмет перед закрытием'")
    check("Данные последнего сеанса на месте", rows[0]["c"] == 1)
    check("Файл базы читается сторонним процессом",
          db_query("PRAGMA integrity_check")[0][0] == "ok")

    # ------------------------------- обновление интерфейса не трогает данные
    print("\n=== Обновление до 2.1: данные на месте ===")
    session_version = """
        from app.version import VERSION, DISPLAY_NAME, DB_SCHEMA_VERSION
        print("VERSION", VERSION)
        print("NAME", DISPLAY_NAME)
        print("SCHEMA", get_db().query_one("PRAGMA user_version")[0], DB_SCHEMA_VERSION)
        print("ITEMS", inventory_service.count())
        print("RENTALS", len(rentals_service.list_all()))
        print("USERS", len(auth_service.all_users()))
    """
    out_v = run_session(session_version)
    v = dict(line.split(" ", 1) for line in out_v.stdout.strip().splitlines() if " " in line)
    from app.version import VERSION as APP_VERSION
    check(f"Версия приложения — {APP_VERSION}", v.get("VERSION") == APP_VERSION, v.get("VERSION"))
    check("Название версии обновлено",
          v.get("NAME") == f"СКЛАД РЕКВИЗИТА {APP_VERSION}", v.get("NAME"))
    check("Схема базы не менялась при обновлении интерфейса",
          v.get("SCHEMA", "").split()[0] == v.get("SCHEMA", "").split()[-1], v.get("SCHEMA"))
    check("Реквизит на месте после обновления", int(v.get("ITEMS", 0)) > 0, v.get("ITEMS"))
    check("Аренды на месте после обновления", int(v.get("RENTALS", 0)) > 0, v.get("RENTALS"))
    check("Пользователи на месте после обновления", int(v.get("USERS", 0)) > 0, v.get("USERS"))

    # ------------------------------------------------------- режимы SQLite
    print("\n=== Режимы записи базы ===")
    conn = sqlite3.connect(str(TMP / "data.db"))
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    check("Журналирование WAL включено", journal.lower() == "wal", journal)
    source = (ROOT / "app" / "db" / "database.py").read_text(encoding="utf-8")
    check("Синхронная запись FULL (данные доходят до диска)", "synchronous=FULL" in source)
    check("Автофиксация включена (isolation_level=None, без ручного commit)",
          "isolation_level=None" in source)

    print(f"\n{'=' * 62}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    for name in FAILED:
        print("  FAIL:", name)
    print(f"Данные теста: {TMP}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
