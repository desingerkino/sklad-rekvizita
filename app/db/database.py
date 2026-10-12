# -*- coding: utf-8 -*-
"""
Слой доступа к БД.

SQLite в режиме WAL: устойчив к аварийному завершению программы (журнал
докатывается при следующем запуске), быстрый на чтении, не требует установки
сервера. Схема вынесена в schema.sql, версия — в user_version (для миграций).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Optional

from .. import paths
from ..version import DB_SCHEMA_VERSION

log = logging.getLogger(__name__)


def _busy_error(exc: sqlite3.Error) -> Exception:
    """
    Превращает «database is locked» и подобное в понятный человеку отказ.

    Остальные ошибки SQLite — это ошибки в программе, их подменять нельзя:
    они должны дойти до журнала как есть.
    """
    from ..errors import AppError

    text = str(exc).lower()
    if "locked" in text or "busy" in text:
        return AppError("База данных сейчас занята другой операцией. "
                        "Подождите несколько секунд и повторите действие.")
    return exc

_local = threading.local()


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        paths.ensure_dirs()
        self.path = Path(db_path) if db_path else paths.DB_PATH
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ соединение
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.path), timeout=15, isolation_level=None, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            # Встроенные lower() и LIKE в SQLite приводят регистр только у
            # латиницы: «Анна» по запросу «анна» не находилась. Своя функция
            # использует правила Python и знает про кириллицу.
            self._conn.create_function(
                "rulower", 1, lambda value: value.lower() if isinstance(value, str) else value,
                deterministic=True)
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA synchronous=FULL")   # приоритет — сохранность данных
            cur.execute("PRAGMA busy_timeout=15000")
        return self._conn

    def initialize(self) -> None:
        """
        Создаёт схему при первом запуске и применяет миграции при обновлении.

        Порядок для старой базы важен: сначала добавляются колонки в существующие
        таблицы, затем выполняется schema.sql (появляются новые таблицы и индексы),
        и только потом переносятся данные.
        """
        schema = self._schema_sql()
        with self._lock:
            current = self.conn.execute("PRAGMA user_version").fetchone()[0]
            is_new = self.conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchone()[0] == 0
            upgrading = (not is_new) and current < DB_SCHEMA_VERSION

            if upgrading:
                self._backup_before_migration(current)
                if current < 2:
                    self._v2_alter_tables()
                if current < 4:
                    self._v4_alter_tables()
                if current < 6:
                    self._v6_alter_tables()
                if current < 7:
                    self._v7_rebuild_embeddings()

            self.conn.executescript(schema)

            if upgrading:
                if current < 2:
                    self._v2_backfill()
                if current < 4:
                    self._v4_backfill()
                if current < 5:
                    self._v5_encrypt_personal_data()
                if current < 6:
                    self._v6_backfill()
                log.info("Миграция схемы БД завершена: %s -> %s", current, DB_SCHEMA_VERSION)
            self.conn.execute(f"PRAGMA user_version={DB_SCHEMA_VERSION}")
        self.conn.execute("PRAGMA optimize")
        try:
            from ..services.crypto_service import crypto_service
            crypto_service.ensure_key()      # ключ готовится при первом запуске
        except Exception:
            log.exception("Не удалось подготовить ключ шифрования")

    @staticmethod
    def _schema_sql() -> str:
        """
        Читает schema.sql. В собранном .exe файл лежит рядом с модулем, но на
        всякий случай проверяется и корень приложения — чтобы сборка не падала
        из-за расположения ресурсов.
        """
        candidates = [Path(__file__).parent / "schema.sql"]
        try:
            from .. import paths
            candidates.append(paths.app_root() / "app" / "db" / "schema.sql")
        except Exception:
            pass
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise FileNotFoundError(
            "Не найден файл схемы базы данных schema.sql. Переустановите программу.")

    def _backup_before_migration(self, from_version: int) -> None:
        import shutil
        from datetime import datetime

        from .. import paths
        try:
            paths.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
            target = (paths.BACKUPS_DIR /
                      f"before_migration_v{from_version}_{datetime.now():%Y-%m-%d_%H-%M-%S}.db")
            self.conn.execute("PRAGMA wal_checkpoint(FULL)")
            shutil.copy2(self.path, target)
            log.info("Резервная копия перед миграцией: %s", target)
        except Exception:
            log.exception("Не удалось сделать копию перед миграцией")

    def _columns(self, table: str) -> set[str]:
        return {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def _v7_rebuild_embeddings(self) -> None:
        """
        2.4: у отпечатка фотографии появился составной ключ (фото, способ поиска).

        Раньше ключом была только фотография, поэтому векторы разных способов
        вытесняли друг друга. Изменить первичный ключ в SQLite нельзя, таблицу
        приходится пересобирать. Данные не теряются: старые векторы
        переносятся как есть, и встроенный поиск продолжает работать сразу,
        без переиндексации.
        """
        existing = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='photo_embeddings'"
        ).fetchone()
        if not existing:
            return                       # таблицу создаст schema.sql
        if "PRIMARY KEY (photo_id, provider)" in (existing["sql"] or ""):
            return                       # уже пересобрана
        log.info("Миграция 2.4: пересобираю таблицу отпечатков фотографий")
        self.conn.executescript(
            "CREATE TABLE photo_embeddings_new ("
            "  photo_id INTEGER NOT NULL, item_id INTEGER NOT NULL, provider TEXT NOT NULL,"
            "  dim INTEGER NOT NULL, vector BLOB NOT NULL,"
            "  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),"
            "  PRIMARY KEY (photo_id, provider));"
            "INSERT OR IGNORE INTO photo_embeddings_new"
            "  (photo_id, item_id, provider, dim, vector, created_at)"
            "  SELECT photo_id, item_id, provider, dim, vector, created_at"
            "  FROM photo_embeddings;"
            "DROP TABLE photo_embeddings;"
            "ALTER TABLE photo_embeddings_new RENAME TO photo_embeddings;")

    def _v6_alter_tables(self) -> None:
        """
        2.3 → 2.4, шаг 1: колонки штрихкода у предметов.

        Только добавление колонок со значением по умолчанию — существующие
        данные, инвентарные номера и аренды не затрагиваются. Новые таблицы
        (recovery_codes, security_attempts) создаёт schema.sql следом.
        """
        existing = self._columns("inventory_items")
        for column, definition in (("barcode", "TEXT NOT NULL DEFAULT ''"),
                                   ("barcode_type", "TEXT NOT NULL DEFAULT 'code128'")):
            if column not in existing:
                self.conn.execute(
                    f"ALTER TABLE inventory_items ADD COLUMN {column} {definition}")
                log.info("Миграция 2.4: добавлена колонка inventory_items.%s", column)

    def _v6_backfill(self) -> None:
        """
        2.3 → 2.4, шаг 2: штрихкод для каждого существующего предмета.

        Штрихкод — это инвентарный номер, поэтому старые предметы получают его
        автоматически и ничего перенумеровывать не нужно. Если инвентарный
        номер задан кириллицей, штрихкод не создаётся: предмет продолжает
        работать как раньше, а программа покажет это в настройках.
        """
        # работаем напрямую с этим соединением: во время initialize() база ещё
        # не установлена как текущая, и сервисы обратились бы не туда
        try:
            from ..services import code128
        except Exception:
            log.exception("Миграция 2.4: модуль штрихкодов недоступен")
            return
        # Индекс полнотекстового поиска перестраивается ДО правки предметов.
        # Триггер items_au при каждом UPDATE дописывает изменения в items_fts;
        # если индекс рассинхронизирован (например, база правилась внешними
        # средствами), SQLite отвечает «database disk image is malformed» и
        # редактирование карточек перестаёт работать. Перестроение стоит доли
        # секунды и заодно лечит такую базу.
        try:
            self.conn.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
        except sqlite3.Error:
            log.warning("Миграция 2.4: не удалось перестроить индекс поиска")

        done = skipped = 0
        for row in self.conn.execute(
                "SELECT id, inv_number FROM inventory_items WHERE barcode=''").fetchall():
            value = (row["inv_number"] or "").strip().upper()
            if not value or not code128.is_encodable(value):
                skipped += 1
                continue
            try:
                self.conn.execute(
                    "UPDATE inventory_items SET barcode=?, barcode_type='code128' WHERE id=?",
                    (value, row["id"]))
                done += 1
            except sqlite3.Error as exc:
                log.warning("Миграция 2.4: штрихкод для %s не создан: %s", value, exc)
                skipped += 1        # дубликат номера в старой базе — предмет без кода
        log.info("Миграция 2.4: штрихкоды проставлены у %s предметов, пропущено %s",
                 done, skipped)

    def _v2_alter_tables(self) -> None:
        """1.x → 2.0, шаг 1: новые колонки у позиций аренды (данные не трогаются)."""
        existing = self._columns("rental_items")
        additions = [
            ("date_start", "TEXT"),
            ("date_returned", "TEXT"),
            ("status", "TEXT NOT NULL DEFAULT 'rented'"),
            ("period_number", "INTEGER NOT NULL DEFAULT 1"),
            ("period_start", "TEXT"),
            ("period_end", "TEXT"),
            ("next_prolongation", "TEXT"),
            ("prolongations", "INTEGER NOT NULL DEFAULT 0"),
            ("initial_amount", "REAL NOT NULL DEFAULT 0"),
            ("prolongation_amount", "REAL NOT NULL DEFAULT 0"),
            ("total_amount", "REAL NOT NULL DEFAULT 0"),
            ("added_at", "TEXT"),
            ("added_by", "INTEGER"),
        ]
        for name, decl in additions:
            if name not in existing:
                self.conn.execute(f"ALTER TABLE rental_items ADD COLUMN {name} {decl}")

    def _v4_alter_tables(self) -> None:
        """2.1 → 2.3: у пользователя появляются имя, фамилия, должность и аватар."""
        if "users" not in {row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}:
            return                      # новая база: таблица создастся из schema.sql
        existing = self._columns("users")
        for name, decl in (("first_name", "TEXT NOT NULL DEFAULT ''"),
                           ("last_name", "TEXT NOT NULL DEFAULT ''"),
                           ("position", "TEXT NOT NULL DEFAULT ''"),
                           ("avatar_path", "TEXT NOT NULL DEFAULT ''")):
            if name not in existing:
                self.conn.execute(f"ALTER TABLE users ADD COLUMN {name} {decl}")

    def _v4_backfill(self) -> None:
        """
        Разбирает прежнее поле full_name на фамилию и имя: «Иванов Иван Иванович»
        → фамилия «Иванов», имя «Иван Иванович». Данные не теряются: full_name остаётся.
        """
        rows = self.conn.execute(
            "SELECT id, full_name FROM users WHERE first_name='' AND last_name=''").fetchall()
        for row in rows:
            parts = (row["full_name"] or "").split()
            last = parts[0] if parts else ""
            first = " ".join(parts[1:]) if len(parts) > 1 else ""
            self.conn.execute("UPDATE users SET last_name=?, first_name=? WHERE id=?",
                              (last, first, row["id"]))
        log.info("Профили пользователей перенесены: %s", len(rows))

    def _v5_encrypt_personal_data(self) -> None:
        """
        2.3 → 2.3.1: персональные данные, которые раньше лежали открытым текстом,
        зашифровываются на месте. Значения не теряются, ключ создаётся при первом
        запуске и хранится отдельно от базы.
        """
        from ..services.crypto_service import crypto_service
        if not crypto_service.available:
            log.error("Шифрование недоступно — миграция персональных данных пропущена")
            return
        plans = [
            ("clients", ("passport", "phone", "address", "email", "extra")),
            ("productions", ("legal_address", "actual_address", "bank_account", "bank_corr",
                             "phone", "email", "contact_person", "extra")),
        ]
        total = 0
        for table, fields in plans:
            columns = self._columns(table)
            fields = [f for f in fields if f in columns]
            if not fields:
                continue
            for row in self.conn.execute(
                    f"SELECT id, {','.join(fields)} FROM {table}").fetchall():
                values = []
                changed = False
                for field in fields:
                    value = row[field]
                    if value and not crypto_service.is_encrypted(value):
                        values.append(crypto_service.encrypt(value))
                        changed = True
                    else:
                        values.append(value)
                if changed:
                    self.conn.execute(
                        f"UPDATE {table} SET {','.join(f + '=?' for f in fields)} WHERE id=?",
                        values + [row["id"]])
                    total += 1
        for row in self.conn.execute("SELECT id, payload FROM document_snapshots").fetchall():
            if row["payload"] and not crypto_service.is_encrypted(row["payload"]):
                self.conn.execute("UPDATE document_snapshots SET payload=? WHERE id=?",
                                  (crypto_service.encrypt(row["payload"]), row["id"]))
                total += 1
        log.info("Зашифровано записей с персональными данными: %s", total)

    def _v2_backfill(self) -> None:
        """
        1.x → 2.0, шаг 2: перенос существующих аренд на модель 14-дневных периодов.
        Дата выдачи позиции = дата начала старой аренды, от неё считается первый
        период, а прежняя сумма позиции становится начислением типа initial.
        """
        from ..version import RENTAL_PERIOD_DAYS

        # переносим старые позиции на новую модель
        rows = self.conn.execute(
            "SELECT ri.id, ri.amount, ri.quantity, ri.price, ri.returned_qty, ri.rental_id, "
            "r.date_start, r.status FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id"
        ).fetchall()
        for row in rows:
            start = (row["date_start"] or "")[:10] or "1970-01-01"
            start_dt = f"{start} 00:00:00"
            period_end = self.conn.execute(
                "SELECT date(?, ?)", (start, f"+{RENTAL_PERIOD_DAYS - 1} days")).fetchone()[0]
            next_prolongation = self.conn.execute(
                "SELECT date(?, '+1 day')", (period_end,)).fetchone()[0]
            returned = row["returned_qty"] >= row["quantity"] or row["status"] != "active"
            self.conn.execute(
                "UPDATE rental_items SET date_start=?, period_number=1, period_start=?, "
                "period_end=?, next_prolongation=?, prolongations=0, initial_amount=?, "
                "prolongation_amount=0, total_amount=?, status=?, added_at=?, "
                "date_returned=CASE WHEN ? THEN ? ELSE NULL END WHERE id=?",
                (start_dt, start, period_end, next_prolongation, row["amount"], row["amount"],
                 "returned" if returned else "rented", start_dt, 1 if returned else 0,
                 start_dt, row["id"]))
            self.conn.execute(
                "INSERT OR IGNORE INTO rental_charges(rental_id, rental_item_id, charge_type, "
                "period_number, quantity, price, amount, charge_date, description, created_at) "
                "VALUES (?,?, 'initial', 1, ?, ?, ?, ?, 'Первоначальная аренда (перенос из версии 1.x)', ?)",
                (row["rental_id"], row["id"], row["quantity"], row["price"], row["amount"],
                 start, start_dt))
        # переиндексируем полнотекстовый поиск: в базе 1.x таблицы items_fts не было
        try:
            self.conn.execute("INSERT INTO items_fts(items_fts) VALUES('rebuild')")
        except sqlite3.Error:
            log.warning("Не удалось перестроить индекс поиска, он соберётся при правке карточек")
        log.info("Перенесено позиций аренды: %s", len(rows))

    def flush(self) -> dict:
        """
        Принудительно сводит журнал WAL в файл базы.

        Данные и без этого записаны на диск (каждая операция фиксируется сразу),
        но после чекпойнта всё лежит непосредственно в data.db, а файл -wal пуст.
        Именно это делает кнопка «Сохранить всё».
        """
        with self._lock:
            conn = self.conn
            # сначала служебные операции (они сами пишут в журнал),
            # только потом чекпойнт — иначе журнал сразу наполнится заново
            conn.execute("PRAGMA optimize")
            result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        wal = Path(str(self.path) + "-wal")
        return {
            "checkpoint_blocked": bool(result[0]) if result else False,
            "pages_written": result[2] if result and len(result) > 2 else 0,
            "db_size": self.path.stat().st_size if self.path.exists() else 0,
            "wal_size": wal.stat().st_size if wal.exists() else 0,
        }

    def close(self) -> None:
        """
        Закрывает базу и сводит журнал в файл.

        После close() Windows должна освободить data.db-wal и data.db-shm,
        поэтому здесь же освобождаются ссылки на закрытое соединение: иначе
        файлы журнала могут остаться занятыми до ближайшей сборки мусора.
        """
        import gc

        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None
        gc.collect()

    # ------------------------------------------------------------------ транзакции
    @contextmanager
    def transaction(self):
        """Атомарная операция: либо всё записалось, либо ничего."""
        with self._lock:
            conn = self.conn
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise _busy_error(exc) from exc
            try:
                yield conn
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    # откатывать нечего — не подменяем исходную ошибку этой
                    pass
                raise
            else:
                try:
                    conn.execute("COMMIT")
                except sqlite3.OperationalError as exc:
                    raise _busy_error(exc) from exc

    # ------------------------------------------------------------------ хелперы
    # Соединение одно на всю программу и делится между потоком интерфейса,
    # фоновыми таймерами (уведомления, автосохранение) и служебными задачами.
    # Один и тот же sqlite3.Connection нельзя использовать из нескольких потоков
    # одновременно — курсоры мешают друг другу, и ошибка получается невнятной
    # («Recursive use of cursors», «database is locked»). Поэтому под общий замок
    # берутся и чтения тоже, а не только запись.
    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            return int(cur.lastrowid)

    def log_history(self, entity: str, entity_id: Optional[int], action: str, details: str = "") -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) VALUES (?,?,?,?)",
                (entity, entity_id, action, details),
            )


_db: Optional[Database] = None


def get_db() -> Database:
    """Единая точка доступа к базе для всего приложения."""
    global _db
    if _db is None:
        _db = Database()
        _db.initialize()
    return _db


def set_db(db: Database) -> None:
    """Используется тестами и восстановлением из резервной копии."""
    global _db
    _db = db
