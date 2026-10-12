# -*- coding: utf-8 -*-
"""
Security QA версии 2.3.1.

Проверяются сценарии из задания: украдена база, украден backup, получен доступ
к файлам приложения, обычный пользователь пытается выполнить административную
операцию, во ввод подставлены SQL-строки, логи не содержат секретов.

Запуск:  python -m tests.test_security
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sklad_sec_"))
KEYS = Path(tempfile.mkdtemp(prefix="sklad_keys_"))
os.environ["SKLAD_DATA_DIR"] = str(TMP)
os.environ["SKLAD_KEY_DIR"] = str(KEYS)

from app import paths                                            # noqa: E402
from app.db.database import Database, get_db, set_db             # noqa: E402
from app.db.seed import seed_if_empty                            # noqa: E402
from app.errors import AppError, PermissionDeniedError           # noqa: E402
from app.logging_setup import setup as setup_logging             # noqa: E402
from app.services.auth_service import (auth_service, hash_password,  # noqa: E402
                                       needs_rehash, verify_password)
from app.services.backup_service import backup_service           # noqa: E402
from app.services.clients_service import clients_service         # noqa: E402
from app.services.crypto_service import crypto_service           # noqa: E402
from app.services.documents_service import documents_service     # noqa: E402
from app.services.inventory_service import inventory_service     # noqa: E402
from app.services.productions_service import productions_service  # noqa: E402
from app.services.projects_service import projects_service       # noqa: E402
from app.services.rentals_service import (RentalDraft, RentalLine, rentals_service)  # noqa: E402
from app.services.settings_service import settings_service       # noqa: E402

PASSED, FAILED, NOTES = [], [], []

PASSPORT = "45 08 123456, выдан ОВД г. Москвы"
PHONE = "+7 916 111-22-33"
ADDRESS = "Москва, ул. Мира, д. 5, кв. 10"
EMAIL = "ivanov.secret@example.ru"


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


def raw_bytes(path: Path) -> bytes:
    return path.read_bytes()


def main() -> int:  # noqa: C901
    paths.ensure_dirs()
    setup_logging(logging.DEBUG)
    sys.excepthook = sys.__excepthook__
    db = Database(TMP / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    print("\n=== Ключ шифрования (п. 33) ===")
    status = crypto_service.status()
    check("Криптобиблиотека доступна", status["available"])
    check("Ключ создан", status["key_exists"], status["key_path"])
    check("Ключ хранится отдельно от базы данных", status["key_outside_data_dir"],
          status["key_path"])
    key_files = list(paths.DATA_DIR.rglob("*.key"))
    check("Рядом с базой ключей нет", not key_files, str(key_files))
    key_blob = (KEYS / "master.key").read_text(encoding="utf-8", errors="replace")
    check("Ключ на диске не лежит открытым текстом",
          key_blob.startswith(("dpapi:", "b64:")), key_blob[:12])
    if not key_blob.startswith("dpapi:"):
        NOTES.append(
            "На этой системе (не Windows) ключ защищён только правами файла 0600: "
            "DPAPI доступен лишь в Windows, где он и будет применён.")

    print("\n=== Пароли (п. 32) ===")
    admin_id = auth_service.create_first_admin("Иванов Михаил", "admin", "СложныйПароль!7",
                                               "Заведующий складом")
    stored = auth_service.get_user(admin_id)["password_hash"]
    check("Пароль в базе не хранится открытым", "СложныйПароль!7" not in stored)
    check("Использован memory-hard алгоритм",
          stored.startswith(("scrypt$", "$argon2")), stored.split("$")[0])
    check("Хэш содержит соль", len(stored.split("$")) >= 5 or stored.startswith("$argon2"))
    check("Два одинаковых пароля дают разные хэши",
          hash_password("одинаковый") != hash_password("одинаковый"))
    check("Проверка пароля работает", verify_password("СложныйПароль!7", stored))
    check("Неверный пароль отклоняется", not verify_password("другой", stored))
    old_hash = "pbkdf2_sha256$240000$" + os.urandom(16).hex() + "$" + "ab" * 32
    check("Старый формат распознаётся как устаревший", needs_rehash(old_hash))
    check("Новый формат обновления не требует", not needs_rehash(stored))

    auth_service.login("admin", "СложныйПароль!7")

    print("\n=== Сценарий 1: украдена база данных (п. 49) ===")
    client_id = clients_service.create({
        "full_name": "Петров Пётр Петрович", "passport": PASSPORT, "phone": PHONE,
        "address": ADDRESS, "email": EMAIL, "extra": "особые пожелания клиента"})
    production_id = productions_service.create({
        "name": "Кинокомпания «Кадр»", "legal_form": "ООО", "inn": "7712345678",
        "legal_address": "Москва, Мосфильмовская, 1", "bank_account": "40702810900000000999",
        "phone": "+7 495 123-45-67", "email": "buh@kadr.example",
        "contact_person": "Сидорова Анна"})
    project_id = projects_service.create({"name": "Фильм «Тишина»",
                                          "production_id": production_id})
    item_id = inventory_service.create({"name": "Стул венский", "quantity_total": 10,
                                        "rental_price": 1000, "valuation": 7000})
    rental_id = rentals_service.create(RentalDraft(
        client_id=client_id, production_id=production_id, project_id=project_id,
        payment_method="cash", date_start=date.today(),
        date_end=date.today() + timedelta(days=5),
        lines=[RentalLine(item_id, "Стул венский", "", "", "", 2, 1000, 7000)]))
    documents_service.create_document(rental_id)
    get_db().conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    blob = raw_bytes(paths.DB_PATH)
    for label, secret in (("паспорт", PASSPORT), ("телефон", PHONE),
                          ("адрес", ADDRESS), ("e-mail", EMAIL),
                          ("расчётный счёт", "40702810900000000999"),
                          ("контактное лицо", "Сидорова Анна")):
        check(f"В файле базы не читается {label}", secret.encode("utf-8") not in blob)
    check("Зашифрованные значения помечены в базе", b"enc1:" in blob)
    check("Снимок документа зашифрован",
          crypto_service.is_encrypted(get_db().query_one(
              "SELECT payload FROM document_snapshots LIMIT 1")["payload"]))

    external = sqlite3.connect(str(paths.DB_PATH))
    external.row_factory = sqlite3.Row
    row = external.execute("SELECT passport, phone FROM clients LIMIT 1").fetchone()
    external.close()
    check("Сторонняя программа видит только шифртекст",
          row["passport"].startswith("enc1:") and row["phone"].startswith("enc1:"),
          str(dict(row))[:80])

    print("\n=== Данные при этом работают как обычно (п. 51) ===")
    client = clients_service.get(client_id)
    check("Программа читает паспорт", client["passport"] == PASSPORT)
    check("Программа читает телефон и адрес",
          client["phone"] == PHONE and client["address"] == ADDRESS)
    check("Поиск по ФИО работает",
          any(c["id"] == client_id for c in clients_service.search("Петров")))
    check("Поиск по телефону работает",
          any(c["id"] == client_id for c in clients_service.search("916 111")))
    check("Поиск по e-mail работает",
          any(c["id"] == client_id for c in clients_service.search("ivanov.secret")))
    check("Автоподстановка отдаёт расшифрованные данные",
          clients_service.suggest("Петров")[0]["passport"] == PASSPORT)
    check("Продакшен читается целиком",
          productions_service.get(production_id)["bank_account"] == "40702810900000000999")
    check("Поиск продакшена по контактному лицу работает",
          any(p["id"] == production_id for p in productions_service.search("Сидорова")))
    check("Документ формируется из зашифрованного снимка",
          documents_service.get_snapshot(
              documents_service.list_for_rental(rental_id)[0]["id"])["client"]["passport"]
          == PASSPORT)

    print("\n=== Сценарий 2: украден backup (п. 41) ===")
    backup = backup_service.create(tag="sec")
    backup_blob = raw_bytes(backup)
    check("Резервная копия зашифрована", crypto_service.is_encrypted_file(backup))
    check("В копии не читается паспорт", PASSPORT.encode("utf-8") not in backup_blob)
    check("В копии не читается телефон", PHONE.encode("utf-8") not in backup_blob)
    check("Копия не открывается как база данных",
          raises(lambda: sqlite3.connect(f"file:{backup}?mode=ro", uri=True).execute(
              "SELECT COUNT(*) FROM clients").fetchone(), sqlite3.DatabaseError))
    leftovers = list(backup_service.backup_dir().glob("*.tmp"))
    check("Незашифрованных временных копий не осталось", not leftovers, str(leftovers))

    extra_id = inventory_service.create({"name": "Временный", "quantity_total": 1})
    backup_service.restore(backup)
    check("Восстановление из зашифрованной копии работает",
          inventory_service.get(extra_id) is None)
    check("Данные после восстановления читаются",
          clients_service.get(client_id)["passport"] == PASSPORT)
    check("Расшифрованных остатков во временной папке нет",
          not list(paths.TEMP_DIR.glob("restore_*.db")))

    print("\n=== Сценарий 4–5: роли и обход прав (пп. 37, 49) ===")
    worker_id = auth_service.create_user("Сидорова Мария", "maria", "МарияПароль!3",
                                         "user", "Реквизитор")
    auth_service.logout()
    auth_service.login("maria", "МарияПароль!3")
    items = get_db().query("SELECT id FROM rental_items WHERE rental_id=?", (rental_id,))
    check("Пользователь не может назначить скидку",
          raises(lambda: rentals_service.set_discount(rental_id, 20), PermissionDeniedError))
    check("Пользователь не может удалить позицию из аренды",
          raises(lambda: rentals_service.remove_item(items[0]["id"]), PermissionDeniedError))
    check("Пользователь не может создать администратора",
          raises(lambda: auth_service.create_user("Хакер", "hack", "12345", "admin"),
                 PermissionDeniedError))
    check("Пользователь не может повысить себе роль",
          raises(lambda: auth_service.update_user(worker_id, role="admin"),
                 PermissionDeniedError))
    check("Пользователь не может изменить настройки склада",
          raises(lambda: settings_service.set_protected({"warehouse.name": "Взлом"}),
                 PermissionDeniedError))
    check("Пользователь не может восстановить базу",
          raises(lambda: backup_service.restore(backup), PermissionDeniedError))
    check("Пользователь не может менять чужой профиль",
          raises(lambda: auth_service.update_profile(admin_id, position="Взлом"),
                 PermissionDeniedError))
    check("Роль в объекте сессии не подделывается напрямую",
          not auth_service.current_user.is_admin
          and raises(lambda: setattr(auth_service.current_user, "role", "admin"), Exception))
    check("После попыток права не изменились", not auth_service.is_admin())

    print("\n=== Автоблокировка сессии (п. 38) ===")
    auth_service.touch()
    check("Свежая сессия не заблокирована", not auth_service.is_idle_expired(15))
    auth_service._last_activity -= 16 * 60      # имитация бездействия
    check("После бездействия сессия требует пароль", auth_service.is_idle_expired(15))
    check("Неверный пароль блокировку не снимает",
          not auth_service.verify_current_password("не тот"))
    check("Верный пароль снимает блокировку",
          auth_service.verify_current_password("МарияПароль!3"))
    check("После снятия таймер сброшен", not auth_service.is_idle_expired(15))
    auth_service.logout()
    check("После выхода сессия очищена",
          auth_service.current_user is None and auth_service.idle_seconds() == 0)
    check("Без входа изменение данных отклоняется",
          raises(lambda: clients_service.create({"full_name": "Никто"}),
                 PermissionDeniedError))
    auth_service.login("admin", "СложныйПароль!7")

    print("\n=== Сценарий 6: SQL-инъекции и подстановки (п. 44) ===")
    payloads = ["'; DROP TABLE clients; --", "' OR '1'='1", "\" OR 1=1 --",
                "'); DELETE FROM rentals; --", "%' UNION SELECT * FROM users --",
                "../../etc/passwd", "Robert'); DROP TABLE inventory_items;--"]
    for payload in payloads:
        try:
            clients_service.search(payload)
            inventory_service.search(payload)
            productions_service.search(payload)
            from app.services.reports_service import reports_service
            reports_service.search(text=payload)
        except Exception as exc:      # noqa: BLE001
            check(f"Опасная строка не роняет поиск: {payload[:24]}", False, str(exc))
            break
    else:
        check("Опасные строки в поиске обрабатываются безопасно", True)
    check("Таблицы на месте после инъекций",
          get_db().query_one("SELECT COUNT(*) c FROM clients")["c"] >= 1
          and get_db().query_one("SELECT COUNT(*) c FROM rentals")["c"] >= 1)
    injected = clients_service.create({"full_name": "'; DROP TABLE clients; --",
                                       "phone": "' OR 1=1 --"})
    check("Опасная строка сохраняется как обычный текст",
          clients_service.get(injected)["full_name"] == "'; DROP TABLE clients; --")
    check("Таблица clients не пострадала",
          get_db().query_one("SELECT COUNT(*) c FROM clients")["c"] >= 2)

    sources = list((ROOT / "app").rglob("*.py"))
    unsafe = []
    patterns = [r'execute\(\s*"[^"]*"\s*%',        # форматирование через %
                r'execute\(\s*"[^"]*"\s*\+',        # склейка строк
                r'execute\(\s*\w+\s*\+\s*\w+']     # склейка переменных
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                unsafe.append(f"{path.name}: {match.group(0)[:40]}")
    check("SQL не собирается конкатенацией пользовательских строк", not unsafe, str(unsafe[:3]))

    print("\n=== Секреты в коде и логи (пп. 40, 46) ===")
    hardcoded = []
    for path in sources:
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r'(?i)(password|secret|api_key|token|encryption_key)\s*=\s*["\'][^"\']{6,}',
                         line) and "placeholder" not in line.lower():
                hardcoded.append(f"{path.name}: {line.strip()[:50]}")
    check("В исходном коде нет захардкоженных секретов", not hardcoded, str(hardcoded[:3]))

    settings_service.set_secret("email.password", "ОченьСекретныйПароль")
    check("Пароль почты хранится защищённым",
          "ОченьСекретныйПароль" not in settings_service.get("email.password"))
    check("Пароль почты расшифровывается",
          settings_service.get_secret("email.password") == "ОченьСекретныйПароль")

    logging.getLogger("qa").info("Проверка: пароль пользователя ОченьСекретныйПароль")
    logging.getLogger("qa").info("Проверка: паспорт %s", PASSPORT)
    logging.getLogger("qa").info("Клиент: Петров Пётр Петрович, телефон %s", PHONE)
    log_text = (paths.LOGS_DIR / "app.log").read_text(encoding="utf-8")
    check("Пароли в лог не попадают", "ОченьСекретныйПароль" not in log_text)
    check("Паспортные данные в лог не попадают", PASSPORT not in log_text)
    check("Ключ шифрования в лог не попадает",
          not re.search(r"master\.key.*=", log_text) and "enc1:" not in log_text)

    print("\n=== Сеть и файлы (пп. 34, 45) ===")
    network = []
    for path in sources:
        text = path.read_text(encoding="utf-8")
        for pattern in ("socket.bind", "socket.listen", "HTTPServer", "app.run(",
                        "Flask(", "uvicorn", "serve_forever"):
            if pattern in text:
                network.append(f"{path.name}: {pattern}")
    check("Приложение не открывает входящих сетевых портов", not network, str(network))
    outgoing = [p.name for p in sources
                if "smtplib" in p.read_text(encoding="utf-8")]
    check("Единственное сетевое соединение — исходящая почта",
          outgoing == ["email_service.py"], str(outgoing))
    email_src = (ROOT / "app" / "services" / "email_service.py").read_text(encoding="utf-8")
    check("Почта отправляется по защищённому каналу",
          "SMTP_SSL" in email_src and "starttls" in email_src)
    check("Сертификаты проверяются", "create_default_context" in email_src)
    check("Пароль почты не передаётся в URL и не логируется",
          "get_secret" in email_src and "log.info(\"Письмо отправлено" in email_src)

    if os.name != "nt":
        mode = oct(os.stat(KEYS / "master.key").st_mode)[-3:]
        check("Права на файл ключа ограничены", mode == "600", mode)

    print("\n=== Ошибки не раскрывают внутренности (п. 47) ===")
    try:
        rentals_service.create(RentalDraft(client_id=None, production_id=None,
                                           project_id=None, payment_method="cash",
                                           date_start=date.today(), lines=[]))
        message = ""
    except AppError as exc:
        message = str(exc)
    check("Пользователю показывается человеческое сообщение",
          message and "SELECT" not in message and "Traceback" not in message
          and str(paths.DATA_DIR) not in message, message)
    errors_ui = (ROOT / "app" / "ui" / "errors.py").read_text(encoding="utf-8")
    check("Stack trace на экран не выводится",
          "format_exc" not in errors_ui and "log.exception" in errors_ui)

    print("\n=== Аудит действий (п. 48) ===")
    from app.services.audit_service import audit_service
    entries = audit_service.search(limit=500)
    actions = {e["action"] for e in entries}
    backup_service.create(tag="audit")     # копия после восстановления — запись в текущей базе
    entries = audit_service.search(limit=500)
    actions = {e["action"] for e in entries}
    for action in ("Вход в программу", "Создана аренда", "Отказано в доступе",
                   "Создан пользователь", "Создана резервная копия"):
        check(f"В журнале есть событие «{action}»", action in actions)
    details = " ".join(e["details"] or "" for e in entries)
    check("В журнале нет паспортов и телефонов",
          PASSPORT not in details and PHONE not in details)
    check("В журнале нет паролей",
          "СложныйПароль" not in details and "МарияПароль" not in details)

    print(f"\n{'=' * 62}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    for name in FAILED:
        print("  FAIL:", name)
    for note in NOTES:
        print("  ЗАМЕЧАНИЕ:", note)
    print(f"Данные теста: {TMP}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    code = main()
    try:
        get_db().close()
    except Exception:
        pass
    sys.exit(code)
