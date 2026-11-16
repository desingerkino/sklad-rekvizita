# -*- coding: utf-8 -*-
"""
Самопроверка собранной программы:  SkladRekvizita --self-test отчёт.json

Нужна, чтобы убедиться, что работает именно СБОРКА (exe / .app), а не только
исходный код: в собранном виде чаще всего теряются файлы шрифта, модель
нейросети, библиотека HEIC или модуль шифрования. Проверка идёт во временной
папке и к настоящим данным пользователя не прикасается.

Что проверяется: шрифт Onest, создание базы, вход, добавление предмета с
фотографиями JPG и HEIC, поиск по фото (и какой способ реально отвечал),
шифрование, файл переноса туда и обратно, сборка главного окна и всех разделов.
Результат пишется в JSON; код возврата 0 — всё в порядке.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
import traceback
from pathlib import Path


def run(report_path: str) -> int:
    os.environ["SKLAD_DATA_DIR"] = tempfile.mkdtemp(prefix="sklad_selftest_")
    os.environ["SKLAD_KEY_DIR"] = tempfile.mkdtemp(prefix="sklad_selftestkey_")
    result: dict = {"ok": False, "checks": {}, "platform": platform.platform(),
                    "python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False))}
    checks = result["checks"]

    def step(name: str, func) -> None:
        try:
            value = func()
            checks[name] = {"ok": value is not False, "info": "" if value in (True, None) else
                            str(value)}
        except Exception:                                          # noqa: BLE001
            checks[name] = {"ok": False, "info": traceback.format_exc()[-900:]}

    try:
        from PySide6 import __version__ as qt_version
        from PySide6.QtWidgets import QApplication

        result["qt"] = qt_version
        app = QApplication.instance() or QApplication(sys.argv[:1])

        from app import paths
        from app.ui import theme
        from app.ui.brand import default_font, load_fonts

        def fonts():
            from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
            if not load_fonts():
                return False
            app.setFont(default_font())
            weights = {}
            for weight in (400, 500, 700):
                font = QFont("Onest")
                font.setPixelSize(14)
                font.setWeight(QFont.Weight(weight))
                info = QFontInfo(font)
                weights[weight] = f"{info.family()}/{int(info.weight())}/{info.styleName()}"
            result["font_weights"] = weights
            result["font_styles"] = sorted(QFontDatabase.styles("Onest"))
            if "Onest" not in QFontDatabase.families():
                return False
            return all(value.startswith("Onest") for value in weights.values()) and str(weights)
        step("Шрифт Onest загружен из поставки", fonts)
        app.setStyleSheet(theme.apply("light"))

        from app.db.database import Database, set_db
        from app.db.seed import seed_if_empty
        from app.services.auth_service import auth_service

        def database():
            paths.ensure_dirs()
            db = Database()
            db.initialize()
            set_db(db)
            seed_if_empty(db)
            auth_service.create_first_admin("Проверка Сборки", "admin", "Пароль!2026", "Админ")
            auth_service.login("admin", "Пароль!2026")
            return f"схема {db.query_one('PRAGMA user_version')[0]}"
        step("База создаётся, вход работает", database)

        from PIL import Image, ImageDraw

        work = Path(tempfile.mkdtemp(prefix="sklad_selftestsrc_"))
        state: dict = {}

        def photos():
            from app.services.inventory_service import inventory_service
            from app.services.photo_service import enable_heic, photo_service
            item = inventory_service.create({"name": "Проверка: телефон дисковый",
                                             "quantity_total": 1})
            state["item"] = item
            image = Image.new("RGB", (640, 480), (236, 236, 230))
            draw = ImageDraw.Draw(image)
            draw.ellipse([180, 100, 460, 380], fill=(30, 30, 30))
            draw.ellipse([260, 180, 380, 300], fill=(236, 236, 230))
            image.save(work / "снимок.jpg")
            image.save(work / "ФОТО.JPEG", "JPEG")
            photo_service.add_photo(item, work / "снимок.jpg", "main")
            photo_service.add_photo(item, work / "ФОТО.JPEG", "other")
            heic = enable_heic()
            if heic:
                image.save(work / "IMG_0001.HEIC", "HEIF")
                photo_service.add_photo(item, work / "IMG_0001.HEIC", "other")
            count = len(photo_service.photos_of(item))
            result["heic"] = bool(heic)
            return count == (3 if heic else 2) and heic and f"фото: {count}"
        step("Фотографии JPG, JPEG и HEIC добавляются", photos)

        def search():
            from app.services import image_search_providers as registry
            from app.services.image_search import image_search
            chosen = registry.get(registry.selected_key())
            ready, reason = chosen.available() if hasattr(chosen, "available") else (True, "")
            result["search_selected"] = getattr(chosen, "key", "?")
            result["search_selected_ready"] = bool(ready)
            result["search_selected_reason"] = reason
            found = image_search.search(work / "снимок.jpg", limit=3, min_score=0.0)
            result["search_provider"] = image_search.last_provider_name
            return bool(found) and found[0]["item_id"] == state["item"] and \
                f"{image_search.last_provider_name}, сходство {found[0]['percent']} %"
        step("Поиск по фото находит предмет", search)

        def neural():
            from app.services import image_search_providers as registry
            clip = registry.get("clip")
            ready, reason = clip.available()
            if not ready:
                return False if getattr(sys, "frozen", False) else f"не в поставке: {reason}"
            vector = clip.embed(work / "снимок.jpg")
            return len(vector) >= 256 and f"вектор {len(vector)}"
        step("Нейросеть поиска встроена и считает", neural)

        def crypto():
            from app.services.crypto_service import crypto_service
            if not crypto_service.available:
                return False
            secret = crypto_service.encrypt("проверка ёЁ")
            return crypto_service.decrypt(secret) == "проверка ёЁ" and secret.startswith("enc1:")
        step("Шифрование личных данных работает", crypto)

        def transfer():
            from app.services.inventory_service import inventory_service
            from app.services.transfer_service import transfer_service
            target = work / "проверка переноса.skladpack"
            transfer_service.export_archive(target, "Перенос-2026")
            inventory_service.create({"name": "Лишний предмет", "quantity_total": 1})
            loaded = transfer_service.import_archive(target, "Перенос-2026")
            return loaded["inventory_items"] == 1 and not loaded["missing_photos"]
        step("Файл переноса собирается и загружается", transfer)

        def window():
            from app.ui.main_window import MENU, MainWindow
            auth_service.login("admin", "Пароль!2026")
            MainWindow._startup_tasks = lambda self: None          # noqa: SLF001
            main_window = MainWindow()
            main_window.resize(1400, 900)
            main_window.show()
            for index in range(len(MENU)):
                main_window.show_page(index)
                app.processEvents()
            main_window.open_item(state["item"])
            app.processEvents()
            main_window._relogin = True                            # noqa: SLF001
            main_window.close()
            return f"разделов: {len(MENU)}"
        step("Главное окно и все разделы открываются", window)

        result["ok"] = all(entry["ok"] for entry in checks.values())
    except Exception:                                              # noqa: BLE001
        result["fatal"] = traceback.format_exc()[-1500:]

    text = json.dumps(result, ensure_ascii=False, indent=1)
    try:
        Path(report_path).write_text(text, encoding="utf-8")
    except OSError:
        pass
    try:
        print(text)
    except Exception:                                              # noqa: BLE001
        pass
    return 0 if result["ok"] else 1
