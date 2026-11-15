# -*- coding: utf-8 -*-
"""
Нагрузочный замер: каталог, поиск и поиск по фото на 1 000 / 5 000 / 10 000 предметов.

Запуск:  python -m tools.bench_scale --items 10000 [--photos 2000] [--ui]

Скрипт работает во временной папке и настоящие данные не трогает. Он написан
так, чтобы запускаться и на версии 2.4, и на 4.0: использует только то, что
есть в обеих (сервисы каталога, фотографий и поиска), поэтому цифры «до» и
«после» получены одним и тем же кодом.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORDS = ["стул", "стол", "лампа", "телефон", "чемодан", "зеркало", "ваза", "часы",
         "кресло", "диван", "шкаф", "торшер", "самовар", "граммофон", "чайник",
         "радиоприёмник", "печатная машинка", "портфель", "сундук", "подсвечник"]
ADJECTIVES = ["старинный", "красный", "деревянный", "латунный", "советский", "резной",
              "чёрный", "зелёный", "дорожный", "настольный", "венский", "кожаный"]


def timed(func, repeat: int = 5) -> float:
    """Медиана времени выполнения, мс."""
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        func()
        samples.append((time.perf_counter() - started) * 1000)
    return round(statistics.median(samples), 1)


def seed(items: int, photos: int, rentals: int) -> dict:
    from PIL import Image, ImageDraw

    from app import paths
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.services.auth_service import auth_service
    from app.services.clients_service import clients_service
    from app.services.photo_service import photo_service
    from app.services.rentals_service import RentalDraft, RentalLine, rentals_service

    paths.ensure_dirs()
    db = Database(paths.DATA_DIR / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Михаил", "admin", "Пароль!2026", "Заведующий")
    auth_service.login("admin", "Пароль!2026")

    rng = random.Random(4)
    category_ids = [row["id"] for row in db.query("SELECT id FROM categories")]
    with db.transaction() as conn:
        for index in range(items):
            name = f"{rng.choice(WORDS).capitalize()} {rng.choice(ADJECTIVES)} {index + 1}"
            number = f"{index + 1:06d}"
            conn.execute(
                "INSERT INTO inventory_items(inv_number, name, category_id, description, "
                "keywords, note, quantity_total, rental_price, valuation, status, barcode, "
                "barcode_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (number, name, rng.choice(category_ids),
                 f"Описание предмета: {rng.choice(ADJECTIVES)}, {rng.choice(ADJECTIVES)}.",
                 rng.choice(WORDS), "", 1 + index % 5, 300 + index % 900,
                 3000 + index % 7000, "in_stock", number, "code128"))
    item_ids = [row["id"] for row in db.query("SELECT id FROM inventory_items ORDER BY id")]

    folder = paths.TEMP_DIR / "bench_src"
    folder.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    for index in range(photos):
        image = Image.new("RGB", (640, 480), (rng.randrange(256), rng.randrange(256),
                                              rng.randrange(256)))
        draw = ImageDraw.Draw(image)
        for _ in range(6):
            x, y = rng.randrange(600), rng.randrange(440)
            draw.rectangle([x, y, x + rng.randrange(20, 200), y + rng.randrange(20, 200)],
                           fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
        source = folder / f"{index}.jpg"
        image.save(source, "JPEG", quality=85)
        photo_service.add_photo(item_ids[index % len(item_ids)], source, "main")
        source.unlink()
    photo_seconds = time.perf_counter() - started

    client = clients_service.create({"full_name": "Клиент Нагрузочный"})
    today = date.today()
    for index in range(rentals):
        start = today - timedelta(days=index % 20)
        lines = [RentalLine(item_ids[(index * 7 + k * 13) % len(item_ids)], f"Предмет {k}",
                            "", "", "", 1, 500, 5000) for k in range(4)]
        try:
            rentals_service.create(RentalDraft(
                client_id=client, production_id=None, project_id=None,
                payment_method="cash", date_start=start,
                date_end=start + timedelta(days=30), lines=lines))
        except Exception:                                          # noqa: BLE001
            pass       # позиция занята — для замера это не важно
    return {"item_ids": item_ids, "photo_seconds": round(photo_seconds, 1)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=10000)
    parser.add_argument("--photos", type=int, default=1000)
    parser.add_argument("--rentals", type=int, default=150)
    parser.add_argument("--ui", action="store_true", help="замерить и окно каталога")
    parser.add_argument("--json", default="", help="куда записать результат")
    args = parser.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["SKLAD_DATA_DIR"] = tempfile.mkdtemp(prefix="sklad_scale_")
    os.environ["SKLAD_KEY_DIR"] = tempfile.mkdtemp(prefix="sklad_scalekey_")

    app = None
    if args.ui:
        from PySide6.QtWidgets import QApplication
        app = QApplication(sys.argv)

    started = time.perf_counter()
    data = seed(args.items, args.photos, args.rentals)
    seed_seconds = round(time.perf_counter() - started, 1)

    from app import paths
    from app.services.categories_service import categories_service
    from app.services.image_search import image_search
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import photo_service

    result: dict = {"items": args.items, "photos": args.photos, "seed_s": seed_seconds,
                    "add_photo_ms": round(data["photo_seconds"] * 1000 / max(1, args.photos), 1)}
    result["search_empty_120_ms"] = timed(lambda: inventory_service.search("", limit=120))
    result["search_empty_300_ms"] = timed(lambda: inventory_service.search("", limit=300))
    result["search_text_ms"] = timed(lambda: inventory_service.search("лампа красн", limit=120))
    result["search_available_ms"] = timed(
        lambda: inventory_service.search("", None, True, limit=120))
    result["search_barcode_ms"] = timed(lambda: inventory_service.search("004321", limit=120))
    result["category_tree_ms"] = timed(categories_service.tree)
    result["count_ms"] = timed(inventory_service.count)
    result["available_units_ms"] = timed(inventory_service.available_units)

    def page_with_photos():
        for item in inventory_service.search("", limit=120):
            categories_service.path(item["category_id"])
            photo_service.primary_photo(item["id"])
    result["catalog_page_data_ms"] = timed(page_with_photos)

    if args.photos:
        probe = next(paths.PHOTOS_DIR.rglob("*.jpg"))
        result["photo_search_ms"] = timed(lambda: image_search.search(probe, limit=20), 3)

    if app is not None:
        from PySide6.QtWidgets import QDialog, QMessageBox
        QDialog.exec = lambda self, *a, **k: 0
        QMessageBox.exec = lambda self, *a, **k: 0
        from app.ui import theme
        app.setStyleSheet(theme.build_qss())
        from app.ui.main_window import MainWindow
        MainWindow._startup_tasks = lambda self: None

        started = time.perf_counter()
        window = MainWindow()
        window.show()
        app.processEvents()
        result["window_ms"] = round((time.perf_counter() - started) * 1000)

        def open_catalog():
            window.show_page(0)
            window.show_page(1)
            for _ in range(10):
                app.processEvents()
        result["open_catalog_ms"] = timed(open_catalog, 3)

        def type_search():
            window.catalog.search.setText("лампа")
            window.catalog.reload_items()
            for _ in range(10):
                app.processEvents()
            window.catalog.search.setText("")
            window.catalog.reload_items()
            for _ in range(10):
                app.processEvents()
        result["catalog_search_roundtrip_ms"] = timed(type_search, 3)

        def open_card():
            window.open_item(data["item_ids"][5])
            for _ in range(5):
                app.processEvents()
        result["open_item_card_ms"] = timed(open_card, 3)

    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result["peak_rss_mb"] = round(peak / (1024 if sys.platform != "darwin" else 1024 ** 2))
    except ImportError:
        pass

    print("SCALE " + json.dumps(result, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
