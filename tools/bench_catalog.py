# -*- coding: utf-8 -*-
"""
Замер скорости каталога на большом складе (около 4,8 тыс. позиций).

Запуск:  python -m tools.bench_catalog [--items 4800]
Печатает время создания окна, открытия каталога и перерисовки списка.
Используется в CI, чтобы сравнить ветку с main: редизайн не должен тормозить.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=4800)
    args = parser.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["SKLAD_DATA_DIR"] = tempfile.mkdtemp(prefix="sklad_bench_")
    os.environ["SKLAD_KEY_DIR"] = tempfile.mkdtemp(prefix="sklad_benchkey_")

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    from app import paths
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.inventory_service import inventory_service

    paths.ensure_dirs()
    db = Database(paths.DATA_DIR / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Михаил", "admin", "Пароль!2026", "Заведующий")
    auth_service.login("admin", "Пароль!2026")
    sub = categories_service.add("Нагрузка", categories_service.add("Склад"))
    for i in range(args.items):
        inventory_service.create({"name": f"Предмет {i + 1}", "category_id": sub,
                                  "description": "", "keywords": "", "quantity_total": 3,
                                  "rental_price": 500 + i, "valuation": 5000})

    from PySide6.QtWidgets import QDialog, QMessageBox
    QDialog.exec = lambda self, *a, **k: 0
    QMessageBox.exec = lambda self, *a, **k: 0
    from app.ui import theme
    app.setStyleSheet(theme.build_qss())
    from app.ui.main_window import MainWindow
    MainWindow._startup_tasks = lambda self: None

    t0 = time.perf_counter()
    window = MainWindow()
    window.show()
    app.processEvents()
    t_window = time.perf_counter() - t0

    t0 = time.perf_counter()
    window.show_page(1)
    for _ in range(10):
        app.processEvents()
    t_open = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(3):
        window.show_page(0)
        window.show_page(1)
        for _ in range(10):
            app.processEvents()
    t_reopen = (time.perf_counter() - t0) / 3

    print(f"BENCH items={args.items} window={t_window:.2f}s open_catalog={t_open:.2f}s "
          f"reopen_catalog={t_reopen:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
