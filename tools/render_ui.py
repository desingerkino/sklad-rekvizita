# -*- coding: utf-8 -*-
"""
Рендер экранов программы в PNG без показа окон (QT_QPA_PLATFORM=offscreen).

Нужен для проверки внешнего вида в CI и для сравнения «до / после».
Данные временные: отдельная папка и временная база, настоящие данные
пользователя не затрагиваются.

Запуск:
    python -m tools.render_ui --out renders/before --scale 1.0
    python -m tools.render_ui --out renders/before --scale 1.5

Один запуск = один масштаб (QT_SCALE_FACTOR задаётся до создания QApplication).
Каждый экран рисуется в двух размерах окна: 1500x940 и 1120x720.
Падение одного экрана не останавливает остальные: ошибки пишутся в errors.txt.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SIZES = ((1500, 940), (1120, 720))

LONG_ITEM = ("Стул деревянный резной красный с золотой отделкой и декоративной спинкой, "
             "реставрация 1998 года, комплект из парадного гарнитура")
LONG_PROJECT = "Художественный фильм «Очень длинное рабочее название проекта о жизни и судьбе»"
LONG_CLIENT = "Овчинникова-Разумовская Аполлинария Кристиановна"
LONG_PRODUCTION = "ООО «Продакшн-компания с очень длинным названием и уточнением деятельности»"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="папка для PNG")
    parser.add_argument("--scale", type=float, default=1.0, help="масштаб интерфейса")
    parser.add_argument("--items", type=int, default=120, help="сколько позиций создать")
    return parser.parse_args()


def seed(items_count: int) -> dict:
    """Временная база с разнообразными, в том числе «плохими» данными."""
    from app import paths
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.clients_service import clients_service
    from app.services.inventory_service import inventory_service
    from app.services.productions_service import productions_service
    from app.services.projects_service import projects_service
    from app.services.rentals_service import RentalDraft, RentalLine, rentals_service
    from app.services.repairs_service import repairs_service

    paths.ensure_dirs()
    db = Database(paths.DATA_DIR / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Михаил", "admin", "Пароль!2026", "Заведующий")
    auth_service.login("admin", "Пароль!2026")

    category = categories_service.add("Мебель нагрузочная")
    sub = categories_service.add("Стулья нагрузочные", category)
    items = []
    for i in range(items_count):
        items.append(inventory_service.create({
            "name": f"{LONG_ITEM} № {i + 1}" if i % 4 == 0 else f"Предмет {i + 1}",
            "category_id": sub, "description": "Описание позиции. " * 6,
            "keywords": "тест", "quantity_total": 5 + i % 7,
            "rental_price": 500 + i, "valuation": 5000 + i * 10}))
    clients = [clients_service.create({
        "full_name": f"{LONG_CLIENT} {i}" if i % 3 == 0 else f"Клиент {i}",
        "phone": f"+7 900 {i:07d}", "passport": f"45 08 {i:06d}",
        "address": "Москва, улица с очень длинным названием, дом 1, квартира 100"})
        for i in range(12)]
    production = productions_service.create({"name": LONG_PRODUCTION, "legal_form": "ООО",
                                             "inn": "7700000001"})
    projects = [projects_service.create({
        "name": f"{LONG_PROJECT} — сезон {i}" if i % 2 else f"Проект {i}",
        "production_id": production}) for i in range(10)]
    today = date.today()
    rentals = []
    for i in range(24):
        start = today - timedelta(days=i * 2)
        rentals.append(rentals_service.create(RentalDraft(
            client_id=clients[i % len(clients)], production_id=production,
            project_id=projects[i % len(projects)], payment_method="cash",
            date_start=start, date_end=start + timedelta(days=10 + i % 5),
            lines=[RentalLine(items[(i * 3 + k) % len(items)], f"Предмет {k}", "", "Описание",
                              "Примечание", 1 + k, 500 + k, 5000) for k in range(3)])))
    for i in range(0, min(15, len(items)), 3):
        repairs_service.send_to_repair(items[i], 1, "Повреждение: скол и трещина", "Описание")
    return {"items": items, "rentals": rentals, "clients": clients}


def main() -> int:
    args = parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["QT_SCALE_FACTOR"] = str(args.scale)
    tmp = Path(tempfile.mkdtemp(prefix="sklad_render_"))
    os.environ["SKLAD_DATA_DIR"] = str(tmp)
    os.environ["SKLAD_KEY_DIR"] = str(tempfile.mkdtemp(prefix="sklad_renderkey_"))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"x{str(args.scale).replace('.', '_')}"
    errors: list[str] = []

    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    # В offscreen-режиме на сервере нет системных шрифтов с кириллицей (текст
    # превращается в квадраты). Подключаем шрифт из assets и подставляем его в
    # стек шрифтов ТОЛЬКО в этом скрипте; сама программа остаётся как есть.
    # DejaVu шире Segoe UI, поэтому обрезка текста на снимках — с запасом.
    from PySide6.QtGui import QFont, QFontDatabase
    for ttf in (ROOT / "assets" / "fonts").glob("DejaVuSans*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))
    from app.ui import theme
    theme.Font.FAMILY = "'DejaVu Sans'"
    app.setFont(QFont("DejaVu Sans", 10))
    app.setStyleSheet(theme.build_qss())

    data = seed(args.items)

    # Модальные окна в рендере недопустимы: они блокируют цикл событий навсегда.
    from PySide6.QtWidgets import QDialog, QMessageBox
    QDialog.exec = lambda self, *a, **k: 0
    QMessageBox.exec = lambda self, *a, **k: 0
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

    from app.ui.main_window import MENU, MainWindow
    MainWindow._startup_tasks = lambda self: None     # стартовые уведомления и копии не нужны
    window = MainWindow()
    window.show()

    def settle() -> None:
        for _ in range(6):
            app.processEvents()
            time.sleep(0.03)

    def snap(widget, name: str, size: tuple[int, int]) -> None:
        widget.resize(*size)
        settle()
        path = out / f"{name}_{size[0]}x{size[1]}_{tag}.png"
        widget.grab().save(str(path))

    def guard(name: str, func) -> None:
        try:
            func()
        except Exception:
            errors.append(f"{name}\n{traceback.format_exc()}")
            print(f"  ОШИБКА: {name}")

    names = ["dashboard", "catalog", "new_rental", "rentals", "returns", "history",
             "clients", "productions", "projects", "reports", "audit", "packing", "settings"]
    for index, name in enumerate(names):
        if index >= len(MENU):
            break

        def shot(index=index, name=name):
            window.show_page(index)
            for size in SIZES:
                snap(window, f"{index:02d}_{name}", size)
        guard(name, shot)

    def item_card():
        window.open_item(data["items"][0])
        for size in SIZES:
            snap(window, "20_item_card", size)
    guard("item_card", item_card)

    def long_item_card():
        window.open_item(data["items"][4])
        snap(window, "21_item_card_long", SIZES[1])
    guard("item_card_long", long_item_card)

    def notifications():
        window.show_page(0)
        window._toggle_notifications()
        snap(window, "22_notifications", SIZES[0])
        window._toggle_notifications()
    guard("notifications", notifications)

    def login():
        from app.services.auth_service import auth_service
        from app.ui.login_dialog import LoginDialog
        auth_service.logout()
        dialog = LoginDialog()
        dialog.show()
        snap(dialog, "30_login", (dialog.width(), dialog.height()))
        dialog.close()
    guard("login", login)

    if errors:
        (out / f"errors_{tag}.txt").write_text("\n\n".join(errors), encoding="utf-8")
    print(f"Готово: {len(list(out.glob(f'*_{tag}.png')))} кадров, ошибок: {len(errors)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
