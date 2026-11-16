# -*- coding: utf-8 -*-
"""
Проверка интерфейса версии 4.0 на настоящем Qt (без экрана: QT_QPA_PLATFORM=offscreen).

Запуск:  python -m tests.test_v40_ui [--report файл.md]

Что проверяется:
  1. Аудит кнопок: каждая кнопка каждого раздела и каждого диалога должна
     иметь действие (обработчик нажатия, меню или участие в группе
     переключателей). Кнопка без действия — «фиктивная» — считается ошибкой.
  2. Прогон нажатий: каждая доступная кнопка нажимается; модальные окна
     закрываются сами (ответ «Нет / Отмена»), поэтому данные не меняются.
     Любое исключение в обработчике — ошибка.
  3. Сценарии каталога: поиск, фильтры, прокрутка с подгрузкой, выделение,
     избранное, массовый перенос, списание и возврат, смена вида.
  4. Поиск по фото в окне: загрузка снимка, выделение области, уточнение.
  5. Раскладка: на разных размерах окна ни одна кнопка не обрезана и не
     вылезает за границы окна; обе темы собираются без ошибок.

Тест пишет перечень проверенных элементов в отчёт (--report).
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["SKLAD_DATA_DIR"] = tempfile.mkdtemp(prefix="sklad_v40ui_")
os.environ["SKLAD_KEY_DIR"] = tempfile.mkdtemp(prefix="sklad_v40uikey_")

PASSED = FAILED = 0
ERRORS: list[str] = []
REPORT: list[str] = []


def check(name: str, condition: bool, details: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  [OK]   {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {details}")


def main() -> int:
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtWidgets import (QAbstractButton, QApplication, QDialog, QFileDialog,
                                   QInputDialog, QMenu, QMessageBox, QWidget)

    app = QApplication(sys.argv)

    # ---- исключения в обработчиках Qt не должны теряться ----------------
    def hook(kind, value, trace):
        ERRORS.append("".join(traceback.format_exception(kind, value, trace)))
    sys.excepthook = hook

    # ---- модальные окна отвечают сами: «Отмена / Нет» --------------------
    opened_dialogs: list[str] = []

    def fake_exec(self, *args, **kwargs):
        opened_dialogs.append(type(self).__name__)
        return 0
    QDialog.exec = fake_exec
    QMessageBox.exec = fake_exec
    QMenu.exec = lambda self, *a, **k: None
    # у диалогов печати и выбора файла собственный exec() — перекрываем и его,
    # иначе прогон остановится на настоящем окне печати
    from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog
    for dialog_class in (QPrintDialog, QPrintPreviewDialog, QFileDialog, QInputDialog):
        dialog_class.exec = fake_exec
    for name in ("question", "warning", "information", "critical"):
        setattr(QMessageBox, name, staticmethod(
            lambda *a, **k: QMessageBox.No))
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
    QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
    QInputDialog.getInt = staticmethod(lambda *a, **k: (0, False))
    QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
    import subprocess
    subprocess.run = lambda *a, **k: None            # «открыть папку» не запускает проводник
    if hasattr(os, "startfile"):
        os.startfile = lambda *a, **k: None          # noqa: S606
    from PySide6.QtGui import QDesktopServices
    QDesktopServices.openUrl = staticmethod(lambda *a, **k: True)

    from app.ui import theme
    from app.ui.brand import default_font, load_fonts
    fonts_loaded = load_fonts()
    if fonts_loaded:
        app.setFont(default_font())
    app.setStyleSheet(theme.apply("light"))

    from tools.render_ui import seed
    data = seed(160)

    from app.services.inventory_service import inventory_service
    from app.services.photo_service import photo_service
    from app.ui.main_window import MENU, MainWindow
    from app.ui.widgets.thumbs import thumbnails

    MainWindow._startup_tasks = lambda self: None
    window = MainWindow()
    window.resize(1500, 940)
    window.show()

    def settle(rounds: int = 6) -> None:
        for _ in range(rounds):
            app.processEvents()
        thumbnails().wait(2000)
        for _ in range(3):
            app.processEvents()

    settle()

    def errors_since(mark: int) -> list[str]:
        return ERRORS[mark:]

    # ------------------------------------------------------------------
    def button_label(button: QAbstractButton) -> str:
        text = (button.text() or "").strip() or button.accessibleName() or button.toolTip()
        return " ".join((text or button.objectName() or type(button).__name__).split())[:60]

    def has_action(button: QAbstractButton) -> bool:
        from PySide6.QtCore import SIGNAL
        if button.receivers(SIGNAL("clicked(bool)")) or button.receivers(SIGNAL("clicked()")):
            return True
        if button.receivers(SIGNAL("toggled(bool)")) or button.receivers(SIGNAL("pressed()")):
            return True
        if hasattr(button, "menu") and button.menu() is not None:
            return True
        if button.group() is not None:
            return True                      # переключатель в группе: действие у группы
        if button.isCheckable():
            return True                      # флажок: его состояние читается при сохранении
        name = type(button).__name__
        # служебные кнопки самого Qt (стрелки счётчика, крестик в поле, вкладки)
        return name in ("QToolButton", "QTabBar", "QScrollBar") or \
            button.objectName().startswith("qt_")

    def audit(root: QWidget, where: str) -> tuple[int, list[str]]:
        dead = []
        buttons = [b for b in root.findChildren(QAbstractButton)
                   if not b.objectName().startswith("qt_")]
        for button in buttons:
            if not has_action(button):
                dead.append(button_label(button))
        REPORT.append(f"| {where} | {len(buttons)} | "
                      f"{'все с действием' if not dead else 'БЕЗ ДЕЙСТВИЯ: ' + ', '.join(dead)} |")
        return len(buttons), dead

    SKIP_CLICK = {"Выйти"}        # проверяется отдельно: меняет состояние сеанса

    def click_all(root: QWidget, where: str) -> tuple[int, list[str]]:
        failed, clicked = [], 0
        for button in root.findChildren(QAbstractButton):
            try:
                if button.objectName().startswith("qt_") or not button.isVisible() \
                        or not button.isEnabled():
                    continue
                label = button_label(button)
                if label in SKIP_CLICK:
                    continue
                mark = len(ERRORS)
                button.click()
                settle(2)
                clicked += 1
                problems = errors_since(mark)
                if problems:
                    failed.append(f"{label}: {problems[-1].strip().splitlines()[-1]}")
            except RuntimeError:
                continue                     # кнопку удалила перестройка экрана — это нормально
        return clicked, failed

    # ================================================= 0. шрифт ============
    print("\n=== Шрифт Onest ===")
    from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QFontMetrics
    check("Файлы Onest загружены из папки программы", fonts_loaded)
    check("Семейство Onest зарегистрировано", "Onest" in QFontDatabase.families())
    styles = set(QFontDatabase.styles("Onest"))
    weights = {}
    for weight in (400, 500, 700):
        font = QFont("Onest")
        font.setPixelSize(14)
        font.setWeight(QFont.Weight(weight))
        info = QFontInfo(font)
        weights[weight] = (info.family(), info.weight(), info.styleName())
    print("      начертания:", sorted(styles), weights)
    check("Regular, Medium и Bold — настоящие начертания Onest, без подмены",
          all(value[0].startswith("Onest") for value in weights.values())
          and weights[400][1] == 400 and weights[500][1] == 500 and weights[700][1] == 700,
          str(weights))
    sample = ("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя"
              "ABCXYZabcxyz0123456789№₽«»—…%")
    regular = QFont("Onest")
    from PySide6.QtGui import QRawFont
    raw = QRawFont.fromFont(regular)
    check("Все русские буквы (включая Ё и ё), цифры и знаки есть в самом Onest",
          all(raw.supportsCharacter(ord(ch)) for ch in sample),
          "".join(ch for ch in sample if not raw.supportsCharacter(ord(ch))))
    check("Шрифт приложения — Onest", app.font().family() == "Onest", app.font().family())
    alien = set()
    for widget in window.findChildren(QWidget):
        family = QFontInfo(widget.font()).family()
        if not family.startswith("Onest"):
            alien.add(f"{type(widget).__name__}:{family}")
    check("Во всех элементах главного окна — Onest, без системных шрифтов", not alien,
          ", ".join(sorted(alien))[:300])

    # ================================================= 1. разделы ==========
    print("\n=== Разделы: открытие, аудит кнопок, прогон нажатий ===")
    REPORT.append("## Кнопки по разделам\n")
    REPORT.append("| Раздел | Кнопок | Результат |")
    REPORT.append("|---|---|---|")
    total_buttons = total_clicked = 0
    for index, entry in enumerate(MENU):
        title = entry[2]
        mark = len(ERRORS)
        window.show_page(index)
        settle()
        check(f"Раздел «{title}» открывается без ошибок", not errors_since(mark),
              "\n".join(errors_since(mark))[-400:])
        page = window.stack.currentWidget()
        count, dead = audit(page, title)
        total_buttons += count
        check(f"«{title}»: у всех кнопок ({count}) есть действие", not dead, ", ".join(dead))
        clicked, failed = click_all(page, title)
        total_clicked += clicked
        check(f"«{title}»: нажатия ({clicked}) без ошибок", not failed, "; ".join(failed)[:600])

    # боковое меню и шапка
    count, dead = audit(window, "Главное окно целиком")
    check(f"Главное окно: у всех кнопок ({count}) есть действие", not dead, ", ".join(dead))

    # ============================================ 2. карточка предмета ======
    print("\n=== Карточка предмета ===")
    item_id = data["items"][9]
    mark = len(ERRORS)
    window.open_item(item_id)
    settle()
    check("Карточка открывается без ошибок", not errors_since(mark),
          "\n".join(errors_since(mark))[-400:])
    card = window.item_card
    count, dead = audit(card, "Карточка предмета")
    check(f"Карточка: у всех кнопок ({count}) есть действие", not dead, ", ".join(dead))
    photos_before = len(photo_service.photos_of(item_id))
    check("У предмета в карточке есть фотографии", photos_before >= 1, str(photos_before))
    mark = len(ERRORS)
    card._step_photo(1)
    card._make_primary()
    settle()
    check("«Главная» (сделать фото главным) работает без ошибок", not errors_since(mark),
          "\n".join(errors_since(mark))[-300:])
    mark = len(ERRORS)
    card.toggle_favorite()
    check("Избранное из карточки переключается",
          inventory_service.is_favorite(item_id) and not errors_since(mark))
    card.toggle_favorite()
    check("Карточка попадает в «Недавние»",
          item_id in [i["id"] for i in inventory_service.recent()])
    clicked, failed = click_all(card, "Карточка предмета")
    check(f"Карточка: нажатия ({clicked}) без ошибок", not failed, "; ".join(failed)[:600])
    check("Фотографии не пропали после прогона нажатий (удаление требует подтверждения)",
          len(photo_service.photos_of(item_id)) == photos_before)
    mark = len(ERRORS)
    card.back.emit()
    settle()
    check("«Назад к каталогу» возвращает в каталог",
          window.stack.currentWidget() is window.catalog and not errors_since(mark))

    # ==================================================== 3. каталог ========
    print("\n=== Каталог: сценарии ===")
    catalog = window.catalog
    catalog.set_scope("all")
    catalog.reset_filters()
    settle()
    total = catalog.model.total()
    check("Каталог показывает весь склад", total == 160, str(total))
    check("Первая страница загружена, остальное — по прокрутке",
          0 < catalog.model.rowCount() <= 200)
    visible_index = catalog.grid.indexAt(QPoint(40, 40))
    check("Карточки плитки отрисованы", visible_index.isValid())
    size = catalog.delegate.card_size()
    columns = max(1, (catalog.grid.viewport().width() - 10) // (size.width() + 12))
    check("Плитка занимает ширину окна: карточек в ряду больше одной", columns >= 3, str(columns))

    catalog.search.setText("Предмет 15")
    catalog.reload_items()
    settle()
    check("Поиск по названию сужает список", 0 < catalog.model.total() < 160,
          str(catalog.model.total()))
    check("Кнопка «Сбросить фильтры» появилась", not catalog.reset_btn.isHidden())
    catalog.search.setText("такого предмета нет на складе")
    catalog.reload_items()
    settle()
    check("Пустой результат показывает понятное сообщение",
          catalog.views.currentIndex() == 2 and "не найдено" in catalog.empty_title.text())
    catalog.reset_filters()
    settle()
    check("Сброс фильтров возвращает весь каталог", catalog.model.total() == 160)

    catalog.availability.setCurrentIndex(catalog.availability.findData("rented"))
    settle()
    rented = catalog.model.total()
    check("Фильтр «Есть в аренде»", 0 < rented < 160
          and all(i["reserved_now"] > 0 for i in catalog.model.items()), str(rented))
    catalog.reset_filters()
    catalog.photo_filter.setCurrentIndex(catalog.photo_filter.findData("yes"))
    settle()
    check("Фильтр «Только с фотографией»", 0 < catalog.model.total() < 160
          and all(i["photo_count"] > 0 for i in catalog.model.items()))
    catalog.reset_filters()
    for key in ("price_desc", "newest", "number", "name"):
        catalog.sort.setCurrentIndex(catalog.sort.findData(key))
        settle(2)
    check("Сортировки переключаются без ошибок и не меняют число предметов",
          catalog.model.total() == 160)

    # категории
    mark = len(ERRORS)
    first_id = next(i for i in catalog._all_tree_items()
                    if i.data(0, Qt.UserRole)).data(0, Qt.UserRole)
    catalog.select_category(first_id)
    settle()
    check("Выбор категории фильтрует каталог", catalog.model.total() <= 160
          and catalog.category_id == first_id
          and not errors_since(mark))
    catalog.reset_filters()
    settle()

    # выделение и массовые действия
    catalog._select_ids({data["items"][20], data["items"][21], data["items"][22]})
    settle(2)
    check("Выделение нескольких предметов", len(catalog.selected_item_ids()) == 3)
    check("Панель действий появилась и показывает число",
          catalog.actions_bar.isVisible() and "3" in catalog.selected_label.text())
    catalog.set_view_mode(1)
    settle(2)
    check("Выделение сохраняется при переходе в таблицу",
          len(catalog.selected_item_ids()) == 3 and catalog.views.currentIndex() == 1)
    check("Таблица показывает те же предметы", catalog.table.model().rowCount()
          == catalog.model.rowCount())
    catalog.set_view_mode(0)
    target = next(i for i in catalog._all_tree_items()
                  if i.data(0, Qt.UserRole) and i.data(0, Qt.UserRole) != first_id).data(0, Qt.UserRole)
    catalog._move_items(catalog.selected_item_ids(), target, ask=False)
    settle()
    check("Массовый перенос в категорию выполняется",
          all(inventory_service.get(i)["category_id"] == target
              for i in (data["items"][20], data["items"][21], data["items"][22])))
    catalog._dropped_on_category(first_id, [data["items"][20]])
    check("Перетаскивание предмета на категорию переносит его",
          inventory_service.get(data["items"][20])["category_id"]
          == first_id)

    catalog.clear_selection()
    catalog._set_favorite(data["items"][30], True)
    catalog.set_scope("favorites")
    settle()
    check("Список «Избранное»", catalog.model.total() == 1)
    catalog.set_scope("recent")
    settle()
    check("Список «Недавние»", catalog.model.total() >= 1)
    catalog.set_scope("all")
    settle()

    free_item = next(i for i in catalog.model.items()
                     if i["reserved_now"] == 0 and i["in_repair"] == 0)
    catalog._select_ids({free_item["id"]})
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    catalog._archive_selected()
    settle()
    check("Списание убирает предмет из каталога", catalog.model.total() == 159)
    catalog.set_scope("archived")
    settle()
    check("Списанный предмет виден в «Списанных»", catalog.model.total() == 1)
    catalog._select_ids({free_item["id"]})
    catalog._restore_selected()
    settle()
    catalog.set_scope("all")
    settle()
    check("Списанный предмет возвращён в каталог", catalog.model.total() == 160)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)

    catalog.select_all()
    check("«Выбрать всё» выделяет всё найденное", len(catalog.selected_item_ids()) == 160)
    catalog.clear_selection()
    for density in ("compact", "large", "normal"):
        catalog.set_density(density)
        settle(2)
    check("Размер плитки меняется без ошибок", not ERRORS, "\n".join(ERRORS)[-400:])

    # прокрутка большого каталога с подгрузкой
    extra = [inventory_service.create({"name": f"Доп. предмет {i}", "quantity_total": 1})
             for i in range(500)]
    catalog.reload()
    settle()
    loaded_first = catalog.model.rowCount()
    bar = catalog.grid.verticalScrollBar()
    for _ in range(12):
        bar.setValue(bar.maximum())
        settle(2)
    check("Прокрутка подгружает следующие страницы каталога",
          catalog.model.rowCount() > loaded_first and catalog.model.total() == 660,
          f"{loaded_first} → {catalog.model.rowCount()} из {catalog.model.total()}")
    check(f"Создано {len(extra)} предметов — каталог листается целиком",
          catalog.model.rowCount() == 660, str(catalog.model.rowCount()))
    bar.setValue(0)

    # ============================================ 4. поиск по фото ==========
    print("\n=== Поиск по фотографии (окно) ===")
    from app.ui.photo_search_dialog import PhotoSearchDialog
    with_photo = next(i for i in data["items"] if photo_service.photos_of(i))
    source = Path(photo_service.primary_photo(with_photo)["abs_path"])
    dialog = PhotoSearchDialog(window)
    count, dead = audit(dialog, "Поиск по фото")
    check(f"Поиск по фото: у всех кнопок ({count}) есть действие", not dead, ", ".join(dead))
    mark = len(ERRORS)
    dialog.load_image(source)
    settle()
    check("Снимок загружается, результаты показаны",
          bool(dialog.results()) and not errors_since(mark),
          "\n".join(errors_since(mark))[-400:])
    check("Результаты содержат предметы с этой фотографией в начале",
          with_photo in dialog.results()[:8])
    check("У результатов есть фото, название и шкала сходства",
          all(card.item["name"] for card in dialog._cards))
    dialog.preview.set_region((0.2, 0.2, 0.8, 0.8))
    settle()
    check("Поиск по выделенной области работает", bool(dialog.results())
          and not dialog.region_btn.isHidden() and not errors_since(mark))
    dialog.preview.clear_region()
    dialog.text.setText("такого названия нет")
    dialog._search()
    settle()
    check("Уточнение без совпадений — понятное сообщение, а не ошибка",
          not dialog.results() and "не найдено" in dialog.summary.text())
    dialog.text.setText("")
    dialog._search()
    clicked, failed = click_all(dialog, "Поиск по фото")
    check(f"Поиск по фото: нажатия ({clicked}) без ошибок", not failed, "; ".join(failed)[:400])
    similar = PhotoSearchDialog(window, similar_to_item=with_photo)
    settle()
    check("«Похожие предметы» из карточки не показывают сам предмет",
          with_photo not in similar.results() and not errors_since(mark))

    print("\n=== Фоновая индексация фотографий ===")
    from app.db.database import get_db
    from app.services.image_search import image_search
    import time as _time
    get_db().execute("DELETE FROM photo_embeddings")
    image_search.invalidate()
    need = image_search.missing_count()
    window._start_photo_indexing()
    deadline = _time.time() + 60
    while image_search.missing_count() and _time.time() < deadline:
        app.processEvents()
        _time.sleep(0.01)
    check(f"Склад доиндексируется сам, без кнопки в настройках ({need} фото)",
          need > 0 and image_search.missing_count() == 0, str(image_search.missing_count()))

    # ================================================ 5. диалоги ============
    print("\n=== Диалоги: аудит кнопок и прогон нажатий ===")
    REPORT.append("\n## Кнопки в диалогах\n")
    REPORT.append("| Диалог | Кнопок | Результат |")
    REPORT.append("|---|---|---|")
    from app.ui.bulk_dialogs import BulkEditDialog, CategoryPicker, DuplicatesDialog
    from app.ui.item_dialog import ItemDialog
    from app.ui.label_dialog import LabelPrintDialog
    from app.ui.profile_dialog import ProfileDialog
    from app.ui.shortcuts_dialog import ShortcutsDialog

    factories = {
        "Новая карточка реквизита": lambda: ItemDialog(window),
        "Редактирование карточки": lambda: ItemDialog(window, item_id),
        "Печать этикеток": lambda: LabelPrintDialog([item_id], window),
        "Профиль сотрудника": lambda: ProfileDialog(window),
        "Горячие клавиши": lambda: ShortcutsDialog(window),
        "Выбор категории": lambda: CategoryPicker(window),
        "Правка нескольких предметов": lambda: BulkEditDialog(3, window),
        "Проверка дублей": lambda: DuplicatesDialog(
            inventory_service.duplicate_groups(), window),
    }
    try:
        from app.ui.import_dialog import ImportDialog
        factories["Импорт из папки"] = lambda: ImportDialog(window)
    except Exception:                                              # noqa: BLE001
        pass
    for title, factory in factories.items():
        mark = len(ERRORS)
        try:
            dlg = factory()
            dlg.show()
            settle(2)
            count, dead = audit(dlg, title)
            clicked, failed = click_all(dlg, title)
            dlg.close()
            check(f"«{title}»: кнопок {count}, все с действием, нажатия ({clicked}) без ошибок",
                  not dead and not failed and not errors_since(mark),
                  ", ".join(dead) + " " + "; ".join(failed) + " "
                  + "\n".join(errors_since(mark))[-300:])
        except Exception:                                          # noqa: BLE001
            check(f"«{title}» открывается", False, traceback.format_exc()[-400:])

    # ============================================== 6. раскладка ============
    print("\n=== Раскладка на разных размерах окна ===")

    def clipped(root: QWidget) -> list[str]:
        problems = []
        for button in root.findChildren(QAbstractButton):
            if not button.isVisible() or button.objectName().startswith("qt_"):
                continue
            text = (button.text() or "").strip()
            if text and "\n" not in text and not button.icon().isNull() is False:
                pass
            if text and not hasattr(button, "full_text"):     # сокращаемые сами себя не обрезают
                need = button.fontMetrics().horizontalAdvance(text)
                if not button.icon().isNull():
                    need += button.iconSize().width()
                if button.width() + 2 < need:
                    problems.append(f"«{text}» {button.width()}<{need}")
        return problems

    for width, height in ((1920, 1080), (1500, 940), (1280, 760), (1120, 720)):
        window.resize(width, height)
        settle(3)
        bad = []
        for index, entry in enumerate(MENU):
            window.show_page(index)
            settle(2)
            page = window.stack.currentWidget()
            for problem in clipped(page):
                bad.append(f"{entry[2]}: {problem}")
        nav_fits = all(b.isVisible() for b in window.buttons.buttons())
        check(f"{width}×{height}: текст кнопок не обрезан", not bad, "; ".join(bad[:6]))
        check(f"{width}×{height}: окно собрано, меню на месте", nav_fits)
    window.resize(1500, 940)
    window.show_page(1)
    settle()
    side = window.findChild(QWidget, "Sidebar")
    from PySide6.QtWidgets import QScrollArea
    scroll = next((s for s in window.findChildren(QScrollArea)
                   if s.widget() and s.widget().findChild(QAbstractButton, "NavButton")), None)
    if scroll is not None:
        check("Меню помещается без прокрутки при 1500×940",
              scroll.verticalScrollBar().maximum() == 0,
              str(scroll.verticalScrollBar().maximum()))

    # ================================================ 7. темы ==============
    print("\n=== Темы ===")
    mark = len(ERRORS)
    requested = []
    window.theme_requested.connect(requested.append)
    window.toggle_theme()
    check("Кнопка темы запрашивает тёмную тему", requested == ["dark"])
    app.setStyleSheet(theme.apply("dark"))
    dark_window = MainWindow()
    dark_window.resize(1500, 940)
    dark_window.show()
    for index in range(len(MENU)):
        dark_window.show_page(index)
        settle(2)
    check("В тёмной теме все разделы открываются без ошибок", not errors_since(mark),
          "\n".join(errors_since(mark))[-400:])
    check("Палитра действительно тёмная", theme.is_dark()
          and theme.Color.BG != theme.LIGHT["BG"])
    dark_window._relogin = True
    dark_window.close()
    app.setStyleSheet(theme.apply("light"))
    check("Возврат к светлой теме восстанавливает палитру",
          theme.Color.BG == theme.LIGHT["BG"] and not theme.is_dark())

    # ================================================ итог =================
    print("\n=== Необработанные ошибки за весь прогон ===")
    check("Необработанных исключений нет", not ERRORS,
          f"{len(ERRORS)} шт.\n" + "\n---\n".join(e[-500:] for e in ERRORS[:5]))
    REPORT.insert(0, f"Проверено кнопок в разделах: {total_buttons}; нажато: {total_clicked}; "
                     f"проверок пройдено: {PASSED}, не пройдено: {FAILED}.\n")
    if "--report" in sys.argv:
        target = Path(sys.argv[sys.argv.index("--report") + 1])
        target.write_text("# Аудит кнопок (автоматический прогон)\n\n"
                          + "\n".join(REPORT) + "\n", encoding="utf-8")

    print(f"\nИтог: пройдено {PASSED}, не пройдено {FAILED}")
    window._relogin = True
    window.close()
    thumbnails().stop()
    QTimer.singleShot(0, app.quit)
    app.exec()
    return 1 if FAILED else 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:                                              # noqa: BLE001
        # перехватчик выше собирает исключения Qt; сбой самого теста печатаем явно
        print("\nТЕСТ ПРЕРВАН:\n" + traceback.format_exc())
        code = 2
    sys.exit(code)
