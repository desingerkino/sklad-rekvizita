# -*- coding: utf-8 -*-
"""
Проверка миграции 1.x → 2.0 на настоящей базе версии 1.0:
создаётся база по старой схеме, наполняется данными, затем открывается
приложением версии 2.0. Ничего не должно потеряться.

Запуск:  python -m tests.test_migration
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="sklad_migr_"))
os.environ["SKLAD_DATA_DIR"] = str(TMP)

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


OLD_SCHEMA = """
PRAGMA user_version=1;
CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE inventory_items (id INTEGER PRIMARY KEY AUTOINCREMENT, inv_number TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, category_id INTEGER, description TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    quantity_total INTEGER NOT NULL DEFAULT 1, rental_price REAL NOT NULL DEFAULT 0,
    valuation REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'in_stock',
    is_archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE clients (id INTEGER PRIMARY KEY AUTOINCREMENT, full_name TEXT NOT NULL,
    passport TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE productions (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
    legal_form TEXT NOT NULL DEFAULT '', inn TEXT NOT NULL DEFAULT '', kpp TEXT NOT NULL DEFAULT '',
    ogrn TEXT NOT NULL DEFAULT '', legal_address TEXT NOT NULL DEFAULT '',
    actual_address TEXT NOT NULL DEFAULT '', bank_name TEXT NOT NULL DEFAULT '',
    bank_account TEXT NOT NULL DEFAULT '', bank_bik TEXT NOT NULL DEFAULT '',
    bank_corr TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '',
    contact_person TEXT NOT NULL DEFAULT '', contact_position TEXT NOT NULL DEFAULT '',
    extra TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE projects (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, production_id INTEGER,
    contacts TEXT NOT NULL DEFAULT '', date_start TEXT, date_end TEXT, extra TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE rentals (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE,
    client_id INTEGER, production_id INTEGER, project_id INTEGER, payment_method TEXT NOT NULL,
    invoice_production_id INTEGER, date_start TEXT NOT NULL, date_end TEXT,
    discount_percent REAL NOT NULL DEFAULT 0, deposit REAL NOT NULL DEFAULT 0,
    total_before_discount REAL NOT NULL DEFAULT 0, discount_amount REAL NOT NULL DEFAULT 0,
    total_final REAL NOT NULL DEFAULT 0, total_valuation REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active', note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT);
CREATE TABLE rental_items (id INTEGER PRIMARY KEY AUTOINCREMENT, rental_id INTEGER NOT NULL,
    item_id INTEGER, unit_id INTEGER, name_snap TEXT NOT NULL, inv_number_snap TEXT NOT NULL DEFAULT '',
    description_snap TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL, price REAL NOT NULL DEFAULT 0, valuation REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0, returned_qty INTEGER NOT NULL DEFAULT 0);
CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, doc_type TEXT NOT NULL, number TEXT NOT NULL,
    rental_id INTEGER, extension_id INTEGER, file_path TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE document_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE history_log (id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, entity_id INTEGER,
    action TEXT NOT NULL, details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE counters (name TEXT NOT NULL, year INTEGER NOT NULL, value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (name, year));
CREATE TABLE inventory_photos (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL,
    rel_path TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'main', is_primary INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0, sha1 TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
"""


def build_v1_database(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(OLD_SCHEMA)
    conn.execute("INSERT INTO categories(name, sort_order) VALUES ('Мебель', 0)")
    conn.execute("INSERT INTO inventory_items(inv_number, name, category_id, quantity_total, "
                 "rental_price, valuation) VALUES ('000001','Стул старый',1,10,500,7000)")
    conn.execute("INSERT INTO inventory_photos(item_id, rel_path, kind, is_primary) "
                 "VALUES (1,'2026/000001/old.jpg','main',1)")
    conn.execute("INSERT INTO clients(full_name, phone, passport) "
                 "VALUES ('Старый Клиент','+7 000','45 08 000000')")
    conn.execute("INSERT INTO productions(name, legal_form, inn) VALUES ('Старый продакшен','ООО','7700')")
    conn.execute("INSERT INTO projects(name, production_id) VALUES ('Старый проект',1)")
    conn.execute("INSERT INTO rentals(number, client_id, production_id, project_id, payment_method, "
                 "date_start, date_end, discount_percent, deposit, total_before_discount, "
                 "discount_amount, total_final, total_valuation, status) "
                 "VALUES ('АР-2025-0001',1,1,1,'cash','2025-11-01','2025-11-20',0,5000,"
                 "3000,0,3000,42000,'active')")
    conn.execute("INSERT INTO rental_items(rental_id, item_id, name_snap, inv_number_snap, quantity, "
                 "price, valuation, amount, note) "
                 "VALUES (1,1,'Стул старый','000001',6,500,7000,3000,'Царапина')")
    conn.execute("INSERT INTO documents(doc_type, number, rental_id) VALUES ('rental','АР-2025-0001',1)")
    conn.execute("INSERT INTO document_snapshots(document_id, payload) "
                 "VALUES (1,'{\"client\":{\"phone\":\"+7 000\"}}')")
    conn.execute("INSERT INTO settings(key, value) VALUES ('warehouse.name','Старый склад')")
    conn.execute("INSERT INTO counters(name, year, value) VALUES ('rental',2025,1)")
    conn.commit()
    conn.close()


def main() -> int:
    from app import paths
    paths.ensure_dirs()
    db_path = TMP / "data.db"
    build_v1_database(db_path)
    print("\n=== База версии 1.0 создана и наполнена ===")

    from app.db.database import Database, set_db, get_db
    db = Database(db_path)
    db.initialize()          # здесь выполняется миграция
    set_db(db)

    from app.services.billing_service import billing_service
    from app.services.clients_service import clients_service
    from app.services.inventory_service import inventory_service
    from app.services.rentals_service import rentals_service
    from app.services.settings_service import settings_service

    from app.version import DB_SCHEMA_VERSION
    check(f"Схема обновлена до версии {DB_SCHEMA_VERSION}",
          get_db().query_one("PRAGMA user_version")[0] == DB_SCHEMA_VERSION)
    check("35. Реквизит сохранён", inventory_service.get(1)["name"] == "Стул старый")
    check("35. Фотографии сохранены",
          get_db().query_one("SELECT COUNT(*) c FROM inventory_photos")["c"] == 1)
    check("35. Категории сохранены",
          get_db().query_one("SELECT COUNT(*) c FROM categories")["c"] >= 1)
    check("35. Клиент сохранён", clients_service.get(1)["full_name"] == "Старый Клиент")
    check("35. Настройки сохранены", settings_service.get("warehouse.name") == "Старый склад")
    check("35. Документы и снимки сохранены",
          get_db().query_one("SELECT COUNT(*) c FROM document_snapshots")["c"] == 1)
    check("35. Счётчики номеров сохранены",
          get_db().query_one("SELECT value FROM counters WHERE year=2025")["value"] == 1)

    rental = rentals_service.get(1)
    check("35. Аренда сохранена", rental["number"] == "АР-2025-0001")
    check("35. Позиция аренды сохранена вместе с примечанием",
          rental["items"][0]["note"] == "Царапина" and rental["items"][0]["quantity"] == 6)

    item = billing_service.items(1)[0]
    check("36. Позиции присвоена дата выдачи из старой аренды",
          item["date_start"][:10] == "2025-11-01", item["date_start"])
    check("36. Рассчитан первый 14-дневный период 01.11–14.11",
          item["period_start"] == "2025-11-01" and item["period_end"] == "2025-11-14",
          f"{item['period_start']}–{item['period_end']}")
    check("36. Проставлена дата следующей пролонгации",
          item["next_prolongation"] == "2025-11-15", item["next_prolongation"])
    charges = billing_service.charges(1)
    check("36. Создано начальное начисление из старой суммы",
          len(charges) == 1 and charges[0]["amount"] == 3000 and charges[0]["charge_type"] == "initial",
          str(charges))
    check("36. Новые таблицы созданы",
          {r["name"] for r in get_db().query(
              "SELECT name FROM sqlite_master WHERE type='table'")} >=
          {"users", "audit_log", "rental_charges", "rental_returns", "form_drafts"})

    from app import paths as p2
    backups = list(p2.BACKUPS_DIR.glob("before_migration_*.db"))
    check("28. Перед миграцией сделана резервная копия", len(backups) == 1,
          str([b.name for b in backups]))

    # повторный запуск не должен ничего ломать
    db.close()
    db2 = Database(db_path)
    db2.initialize()
    set_db(db2)
    check("Повторный запуск не дублирует начисления", len(billing_service.charges(1)) == 1)
    check("Целостность базы после миграции",
          get_db().query_one("PRAGMA integrity_check")[0] == "ok")

    print(f"\n{'=' * 62}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    for name in FAILED:
        print("  FAIL:", name)
    print(f"Данные теста: {TMP}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    code = main()
    sys.exit(code)
