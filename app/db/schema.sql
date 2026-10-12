-- ВРЕМЯ. SQLite-функция datetime('now') возвращает UTC, а программа показывает
-- и сравнивает местное время (Python datetime.now()). Из-за этого отметки
-- «создано» расходились с журналом действий на величину часового пояса — в
-- Москве на три часа. Поэтому во всех значениях по умолчанию используется
-- datetime('now','localtime'): в базе лежит местное время, как и везде в коде.

-- =====================================================================
-- Склад Реквизита — схема базы данных (SQLite, WAL, внешние ключи)
-- =====================================================================
PRAGMA foreign_keys = ON;

-- Категории и подкатегории (дерево любой глубины, порядок задаёт пользователь)
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id   INTEGER REFERENCES categories(id) ON DELETE RESTRICT,
    name        TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_categories_parent ON categories(parent_id, sort_order);

-- Реквизит
CREATE TABLE IF NOT EXISTS inventory_items (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    inv_number     TEXT    NOT NULL UNIQUE,          -- инвентарный номер, напр. 001234
    name           TEXT    NOT NULL,
    category_id    INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    description    TEXT    NOT NULL DEFAULT '',
    keywords       TEXT    NOT NULL DEFAULT '',
    note           TEXT    NOT NULL DEFAULT '',
    quantity_total INTEGER NOT NULL DEFAULT 1 CHECK (quantity_total >= 0),
    rental_price   REAL    NOT NULL DEFAULT 0 CHECK (rental_price >= 0),
    valuation      REAL    NOT NULL DEFAULT 0 CHECK (valuation >= 0),
    status         TEXT    NOT NULL DEFAULT 'in_stock',  -- in_stock | repair | archived
    is_archived    INTEGER NOT NULL DEFAULT 0,
    barcode        TEXT    NOT NULL DEFAULT '',          -- 2.4: Code 128 = инвентарный номер
    barcode_type   TEXT    NOT NULL DEFAULT 'code128',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_items_name      ON inventory_items(name);
CREATE INDEX IF NOT EXISTS ix_items_category  ON inventory_items(category_id);
CREATE INDEX IF NOT EXISTS ix_items_status    ON inventory_items(status);

-- Полнотекстовый индекс каталога (мгновенный поиск на десятках тысяч позиций)
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    name, description, keywords, note, inv_number,
    content='inventory_items', content_rowid='id', tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON inventory_items BEGIN
    INSERT INTO items_fts(rowid, name, description, keywords, note, inv_number)
    VALUES (new.id, new.name, new.description, new.keywords, new.note, new.inv_number);
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON inventory_items BEGIN
    INSERT INTO items_fts(items_fts, rowid, name, description, keywords, note, inv_number)
    VALUES ('delete', old.id, old.name, old.description, old.keywords, old.note, old.inv_number);
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON inventory_items BEGIN
    INSERT INTO items_fts(items_fts, rowid, name, description, keywords, note, inv_number)
    VALUES ('delete', old.id, old.name, old.description, old.keywords, old.note, old.inv_number);
    INSERT INTO items_fts(rowid, name, description, keywords, note, inv_number)
    VALUES (new.id, new.name, new.description, new.keywords, new.note, new.inv_number);
END;

-- Фотографии: файлы лежат в хранилище, в БД — относительные пути
CREATE TABLE IF NOT EXISTS inventory_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
    rel_path    TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'main',   -- main|front|side|detail|damage|other
    is_primary  INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    sha1        TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_photos_item ON inventory_photos(item_id, sort_order);

-- Векторные признаки фотографий для поиска по фото
-- Отпечатки фотографий для поиска по изображению.
-- Ключ — пара (фотография, способ поиска): у одного снимка одновременно живут
-- вектор встроенного алгоритма и вектор нейросети. Раньше ключом была только
-- фотография, и переиндексация нейросетью затирала встроенные векторы — после
-- этого откат на встроенный поиск возвращал пустоту.
CREATE TABLE IF NOT EXISTS photo_embeddings (
    photo_id   INTEGER NOT NULL REFERENCES inventory_photos(id) ON DELETE CASCADE,
    item_id    INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
    provider   TEXT    NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (photo_id, provider)
);
CREATE INDEX IF NOT EXISTS ix_emb_item ON photo_embeddings(item_id);

-- Конкретные физические экземпляры (задел на поэкземплярный учёт)
CREATE TABLE IF NOT EXISTS inventory_units (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
    unit_number TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'in_stock',
    note        TEXT    NOT NULL DEFAULT '',
    UNIQUE (item_id, unit_number)
);

-- Клиенты — физические лица
CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name   TEXT    NOT NULL,
    passport    TEXT    NOT NULL DEFAULT '',
    phone       TEXT    NOT NULL DEFAULT '',
    address     TEXT    NOT NULL DEFAULT '',
    email       TEXT    NOT NULL DEFAULT '',
    extra       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_clients_name ON clients(full_name);

-- Продакшены (юридические лица / ИП)
CREATE TABLE IF NOT EXISTS productions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    legal_form       TEXT NOT NULL DEFAULT '',   -- ООО / ИП / ...
    inn              TEXT NOT NULL DEFAULT '',
    kpp              TEXT NOT NULL DEFAULT '',
    ogrn             TEXT NOT NULL DEFAULT '',
    legal_address    TEXT NOT NULL DEFAULT '',
    actual_address   TEXT NOT NULL DEFAULT '',
    bank_name        TEXT NOT NULL DEFAULT '',
    bank_account     TEXT NOT NULL DEFAULT '',
    bank_bik         TEXT NOT NULL DEFAULT '',
    bank_corr        TEXT NOT NULL DEFAULT '',
    phone            TEXT NOT NULL DEFAULT '',
    email            TEXT NOT NULL DEFAULT '',
    contact_person   TEXT NOT NULL DEFAULT '',
    contact_position TEXT NOT NULL DEFAULT '',
    extra            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_productions_name ON productions(name);

-- Проекты (фильмы, сериалы, рекламы)
CREATE TABLE IF NOT EXISTS projects (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    production_id INTEGER REFERENCES productions(id) ON DELETE SET NULL,
    contacts      TEXT NOT NULL DEFAULT '',
    date_start    TEXT,
    date_end      TEXT,
    extra         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_projects_name ON projects(name);
CREATE INDEX IF NOT EXISTS ix_projects_prod ON projects(production_id);

-- Аренды
CREATE TABLE IF NOT EXISTS rentals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    number                TEXT    NOT NULL UNIQUE,     -- АР-2026-0001
    client_id             INTEGER REFERENCES clients(id) ON DELETE RESTRICT,
    production_id         INTEGER REFERENCES productions(id) ON DELETE RESTRICT,
    project_id            INTEGER REFERENCES projects(id) ON DELETE RESTRICT,
    payment_method        TEXT    NOT NULL,            -- cash | bank
    invoice_production_id INTEGER REFERENCES productions(id) ON DELETE SET NULL,
    date_start            TEXT    NOT NULL,            -- YYYY-MM-DD
    date_end              TEXT,                        -- NULL = срок пока неизвестен
    discount_percent      REAL    NOT NULL DEFAULT 0 CHECK (discount_percent BETWEEN 0 AND 100),
    deposit               REAL    NOT NULL DEFAULT 0 CHECK (deposit >= 0),
    total_before_discount REAL    NOT NULL DEFAULT 0,
    discount_amount       REAL    NOT NULL DEFAULT 0,
    total_final           REAL    NOT NULL DEFAULT 0,
    total_valuation       REAL    NOT NULL DEFAULT 0,
    status                TEXT    NOT NULL DEFAULT 'active',   -- active | closed | cancelled
    note                  TEXT    NOT NULL DEFAULT '',
    created_at            TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at            TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    closed_at             TEXT
);
CREATE INDEX IF NOT EXISTS ix_rentals_status ON rentals(status, date_end);
CREATE INDEX IF NOT EXISTS ix_rentals_dates  ON rentals(date_start, date_end);
CREATE INDEX IF NOT EXISTS ix_rentals_client ON rentals(client_id);
CREATE INDEX IF NOT EXISTS ix_rentals_proj   ON rentals(project_id);
CREATE INDEX IF NOT EXISTS ix_rentals_prod   ON rentals(production_id);

-- Позиции аренды (снимок названия/описания на момент аренды + индивидуальное примечание)
-- Каждая позиция живёт своей жизнью: своя дата выдачи, свой 14-дневный период,
-- своя пролонгация, свой возврат. Аренда — контейнер для таких позиций.
CREATE TABLE IF NOT EXISTS rental_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rental_id     INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    item_id       INTEGER REFERENCES inventory_items(id) ON DELETE RESTRICT,
    unit_id       INTEGER REFERENCES inventory_units(id) ON DELETE SET NULL,
    name_snap     TEXT    NOT NULL,
    inv_number_snap TEXT  NOT NULL DEFAULT '',
    description_snap TEXT NOT NULL DEFAULT '',
    note          TEXT    NOT NULL DEFAULT '',
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    price         REAL    NOT NULL DEFAULT 0,          -- цена за один 14-дневный период
    valuation     REAL    NOT NULL DEFAULT 0,
    amount        REAL    NOT NULL DEFAULT 0,          -- сумма первоначального периода
    returned_qty  INTEGER NOT NULL DEFAULT 0,
    -- собственный арендный цикл позиции
    date_start    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),  -- дата и время выдачи
    date_returned TEXT,                                        -- фактический возврат
    status        TEXT    NOT NULL DEFAULT 'rented',           -- rented | returned
    period_number INTEGER NOT NULL DEFAULT 1,                  -- текущий период
    period_start  TEXT,                                        -- начало текущего периода
    period_end    TEXT,                                        -- конец текущего периода
    next_prolongation TEXT,                                    -- дата следующей пролонгации
    prolongations INTEGER NOT NULL DEFAULT 0,                  -- сколько раз продлевалось
    initial_amount     REAL NOT NULL DEFAULT 0,                -- начислено за первый период
    prolongation_amount REAL NOT NULL DEFAULT 0,               -- начислено пролонгациями
    total_amount       REAL NOT NULL DEFAULT 0,                -- всего по позиции
    added_at      TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    added_by      INTEGER
);
CREATE INDEX IF NOT EXISTS ix_rental_items_rental ON rental_items(rental_id);
CREATE INDEX IF NOT EXISTS ix_rental_items_item   ON rental_items(item_id);
CREATE INDEX IF NOT EXISTS ix_rental_items_status ON rental_items(status, period_end);

-- История начислений: каждое начисление привязано к конкретной позиции
CREATE TABLE IF NOT EXISTS rental_charges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rental_id      INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    rental_item_id INTEGER REFERENCES rental_items(id) ON DELETE CASCADE,
    charge_type    TEXT    NOT NULL,      -- initial | addition | prolongation
    period_number  INTEGER NOT NULL DEFAULT 1,
    quantity       INTEGER NOT NULL DEFAULT 0,
    price          REAL    NOT NULL DEFAULT 0,
    amount         REAL    NOT NULL DEFAULT 0,
    charge_date    TEXT    NOT NULL,      -- дата, к которой относится начисление
    description    TEXT    NOT NULL DEFAULT '',
    user_id        INTEGER,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (rental_item_id, charge_type, period_number)
);
CREATE INDEX IF NOT EXISTS ix_charges_rental ON rental_charges(rental_id, charge_date);

-- Возвраты позиций (в том числе частичные)
CREATE TABLE IF NOT EXISTS rental_returns (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rental_id      INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    rental_item_id INTEGER NOT NULL REFERENCES rental_items(id) ON DELETE CASCADE,
    quantity       INTEGER NOT NULL CHECK (quantity > 0),
    returned_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    user_id        INTEGER,
    comment        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_returns_item ON rental_returns(rental_item_id);

-- Пользователи и роли. Пароль хранится только в виде криптографического хэша.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    login         TEXT    NOT NULL UNIQUE,
    full_name     TEXT    NOT NULL,
    first_name    TEXT    NOT NULL DEFAULT '',
    last_name     TEXT    NOT NULL DEFAULT '',
    position      TEXT    NOT NULL DEFAULT '',       -- должность: художник по реквизиту и т.п.
    avatar_path   TEXT    NOT NULL DEFAULT '',       -- относительный путь в хранилище аватаров
    role          TEXT    NOT NULL DEFAULT 'user',   -- admin | user (роль ≠ должность)
    password_hash TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    last_login    TEXT
);

-- Уведомления: строятся из реальных данных аренд, состояние «прочитано» хранится в базе
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key  TEXT    NOT NULL UNIQUE,   -- одно событие = одно уведомление
    kind       TEXT    NOT NULL,          -- expiring | overdue | prolonged | repair | system
    title      TEXT    NOT NULL,
    body       TEXT    NOT NULL DEFAULT '',
    entity     TEXT    NOT NULL DEFAULT '',
    entity_id  INTEGER,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    read_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_notifications_read ON notifications(read_at, created_at);

-- Журнал значимых действий
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_name   TEXT    NOT NULL DEFAULT 'Система',
    action      TEXT    NOT NULL,
    entity      TEXT    NOT NULL DEFAULT '',
    entity_id   INTEGER,
    details     TEXT    NOT NULL DEFAULT '',
    amount      REAL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_audit_created ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS ix_audit_entity  ON audit_log(entity, entity_id);


-- Продления
CREATE TABLE IF NOT EXISTS extensions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rental_id   INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    old_date_end TEXT,
    new_date_end TEXT NOT NULL,
    comment     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_ext_rental ON extensions(rental_id);

-- Ремонт
CREATE TABLE IF NOT EXISTS repairs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES inventory_items(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    reason      TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',   -- open | done
    date_start  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    date_end    TEXT,
    comment     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_repairs_item ON repairs(item_id, status);

-- Платежи и залоги (учёт движения денег)
CREATE TABLE IF NOT EXISTS payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    rental_id  INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL DEFAULT 'rent',   -- rent | extension | penalty
    method     TEXT NOT NULL DEFAULT 'cash',
    amount     REAL NOT NULL,
    paid_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    comment    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS deposits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    rental_id       INTEGER NOT NULL REFERENCES rentals(id) ON DELETE CASCADE,
    amount          REAL NOT NULL DEFAULT 0,
    received_at     TEXT,
    returned_at     TEXT,
    returned_amount REAL NOT NULL DEFAULT 0,
    withheld_amount REAL NOT NULL DEFAULT 0,
    withheld_reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_deposits_rental ON deposits(rental_id);

-- Документы и их исторические снимки
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type     TEXT NOT NULL,                -- rental | extension | act
    number       TEXT NOT NULL,
    rental_id    INTEGER REFERENCES rentals(id) ON DELETE CASCADE,
    extension_id INTEGER REFERENCES extensions(id) ON DELETE CASCADE,
    file_path    TEXT,                         -- относительный путь к PDF
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_documents_rental ON documents(rental_id);

CREATE TABLE IF NOT EXISTS document_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    payload     TEXT NOT NULL,                 -- JSON: полный слепок данных на момент создания
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_snapshots_doc ON document_snapshots(document_id);

-- Настройки (склад, email, резервные копии, интерфейс)
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Журнал действий (историю нельзя терять)
CREATE TABLE IF NOT EXISTS history_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity     TEXT NOT NULL,
    entity_id  INTEGER,
    action     TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_history_entity ON history_log(entity, entity_id, created_at);

-- Счётчики номеров документов по годам
CREATE TABLE IF NOT EXISTS counters (
    name  TEXT NOT NULL,
    year  INTEGER NOT NULL,
    value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (name, year)
);

-- Черновики форм: незавершённая «Новая аренда» переживает аварийное закрытие.
-- Пишется автоматически при каждом изменении, очищается после создания аренды.
CREATE TABLE IF NOT EXISTS form_drafts (
    key        TEXT NOT NULL,
    user_id    INTEGER,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (key, user_id)
);

-- ============================ версия 2.4 =====================================

-- Коды аварийного восстановления доступа администратора.
-- Хранится только хэш: сам код нигде в базе не лежит.
CREATE TABLE IF NOT EXISTS recovery_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    code_hash  TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    used_at    TEXT,                                  -- код применён -> недействителен
    revoked_at TEXT                                   -- заменён новым -> недействителен
);
CREATE INDEX IF NOT EXISTS ix_recovery_active
    ON recovery_codes(user_id, used_at, revoked_at);

-- Счётчик неудачных попыток и временная блокировка (перебор кода/пароля).
CREATE TABLE IF NOT EXISTS security_attempts (
    kind         TEXT    NOT NULL,                    -- recovery | login
    subject      TEXT    NOT NULL DEFAULT '',         -- логин или пусто для общего счётчика
    fails        INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT,
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (kind, subject)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_items_barcode
    ON inventory_items(barcode) WHERE barcode <> '';

-- Инвентаризация: периодическая физическая сверка склада.
-- Каждая инвентаризация фиксирует состояние учёта на момент начала (снимок),
-- поэтому более поздние аренды и правки не искажают уже сделанную сверку.
CREATE TABLE IF NOT EXISTS inventory_audits (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    number         TEXT    NOT NULL UNIQUE,          -- «ИНВ-2026-0012»
    title          TEXT    NOT NULL DEFAULT '',
    status         TEXT    NOT NULL DEFAULT 'open',  -- open | done | cancelled
    started_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at    TEXT,
    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    responsible    TEXT    NOT NULL DEFAULT '',
    comment        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_audits_status ON inventory_audits(status, started_at);

CREATE TABLE IF NOT EXISTS inventory_audit_lines (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id     INTEGER NOT NULL REFERENCES inventory_audits(id) ON DELETE CASCADE,
    item_id      INTEGER REFERENCES inventory_items(id) ON DELETE SET NULL,
    inv_number   TEXT    NOT NULL DEFAULT '',        -- снимок: номер на момент сверки
    name_snap    TEXT    NOT NULL DEFAULT '',
    category_snap TEXT   NOT NULL DEFAULT '',
    expected_qty INTEGER NOT NULL DEFAULT 0,         -- числится на складе
    rented_qty   INTEGER NOT NULL DEFAULT 0,         -- у клиентов, на складе быть не должно
    repair_qty   INTEGER NOT NULL DEFAULT 0,         -- в ремонте
    found_qty    INTEGER NOT NULL DEFAULT 0,         -- физически найдено
    unexpected   INTEGER NOT NULL DEFAULT 0,         -- 1 = найдено, но не числилось
    comment      TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_line ON inventory_audit_lines(audit_id, item_id);
CREATE INDEX IF NOT EXISTS ix_audit_lines ON inventory_audit_lines(audit_id);

-- Комплектация по машинам: что и в каких коробках уезжает на съёмку.
-- Это не аренда и не движение остатков — это упаковочный лист для группы.
CREATE TABLE IF NOT EXISTS vehicles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,          -- «Машина №2», «Газель реквизита»
    driver     TEXT    NOT NULL DEFAULT '',
    note       TEXT    NOT NULL DEFAULT '',
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS packing_sets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    number        TEXT    NOT NULL UNIQUE,       -- «КМП-2026-0007»
    vehicle_id    INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    project_id    INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    production_id INTEGER REFERENCES productions(id) ON DELETE SET NULL,
    rental_id     INTEGER REFERENCES rentals(id) ON DELETE SET NULL,
    date_ship     TEXT,                          -- дата выезда
    responsible   TEXT    NOT NULL DEFAULT '',
    comment       TEXT    NOT NULL DEFAULT '',
    status        TEXT    NOT NULL DEFAULT 'open',   -- open | shipped | closed | cancelled
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    shipped_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_packing_sets ON packing_sets(status, date_ship);

CREATE TABLE IF NOT EXISTS packing_boxes (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id   INTEGER NOT NULL REFERENCES packing_sets(id) ON DELETE CASCADE,
    number   INTEGER NOT NULL,                   -- номер коробки внутри комплекта
    label    TEXT    NOT NULL DEFAULT '',        -- «Коробка №1», «Ящик хрупкое»
    note     TEXT    NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_box_number ON packing_boxes(set_id, number);

CREATE TABLE IF NOT EXISTS packing_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id     INTEGER NOT NULL REFERENCES packing_boxes(id) ON DELETE CASCADE,
    item_id    INTEGER REFERENCES inventory_items(id) ON DELETE SET NULL,
    inv_number TEXT    NOT NULL DEFAULT '',      -- снимок на момент упаковки
    name_snap  TEXT    NOT NULL DEFAULT '',
    quantity   INTEGER NOT NULL DEFAULT 1,
    note       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_packing_items ON packing_items(box_id, item_id);

-- Незавершённые аренды: оператор оформляет несколько выдач одновременно.
-- Номер бронируется в момент открытия черновика из того же счётчика, что и у
-- обычных аренд, — двух систем нумерации быть не должно. Номер остаётся за
-- черновиком до выдачи или отмены и повторно не используется.
CREATE TABLE IF NOT EXISTS rental_drafts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    number     TEXT    NOT NULL UNIQUE,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    payload    TEXT    NOT NULL DEFAULT '{}',    -- состояние формы целиком
    status     TEXT    NOT NULL DEFAULT 'open',  -- open | issued | cancelled
    rental_id  INTEGER REFERENCES rentals(id) ON DELETE SET NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_rental_drafts ON rental_drafts(user_id, status, id);
