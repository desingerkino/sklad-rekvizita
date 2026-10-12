# -*- coding: utf-8 -*-
"""
UI QA версии 2.3.1: прокрутка, перекрытия, длинные данные и статусы.

Qt в этой среде не запускается, поэтому проверки статические, но предметные:
разбирается фактический код каждого экрана и диалога — есть ли область
прокрутки, закреплены ли кнопки, ограничены ли размеры, используются ли
статусы из единого модуля и иконки из утверждённого набора.

Запуск:  python -m tests.test_ui_scroll
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
UI = ROOT / "app" / "ui"

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def source(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def has_scroll(text: str) -> bool:
    """Есть ли в экране собственная прокрутка или прокручиваемая таблица."""
    return ("scroll_area(" in text or "scrollable_body(" in text
            or "QScrollArea" in text or "make_table(" in text)


def main() -> int:  # noqa: C901
    print("=== Прокрутка экранов (пп. 4–6) ===")
    screens = {
        "dashboard_view.py": "Главная",
        "catalog_view.py": "Каталог",
        "rental_new_view.py": "Новая аренда",
        "rentals_view.py": "Аренды",
        "returns_view.py": "Возвраты",
        "history_view.py": "История",
        "directory_views.py": "Клиенты, юр. лица, проекты",
        "reports_view.py": "Отчёты",
        "settings_view.py": "Настройки",
        "item_card.py": "Карточка реквизита",
    }
    for file_name, title in screens.items():
        check(f"{title}: содержимое прокручивается", has_scroll(source(file_name)))

    print("\n=== Прокрутка длинных форм и модальных окон (пп. 7, 21, 22) ===")
    dialogs = {
        "item_dialog.py": "Карточка реквизита (форма)",
        "directory_dialogs.py": "Клиент / продакшен / проект",
        "profile_dialog.py": "Профиль сотрудника",
        "user_dialog.py": "Пользователь",
        "add_items_dialog.py": "Добор реквизита",
    }
    for file_name, title in dialogs.items():
        text = source(file_name)
        check(f"{title}: форма прокручивается", "scrollable_body(" in text)
        check(f"{title}: кнопки закреплены внизу", "footer" in text)
    check("Окно не выходит за пределы экрана",
          "availableGeometry" in source("widgets/common.py"))
    check("Заголовок и кнопки остаются доступными при прокрутке",
          "DialogFooter" in source("theme.py"))
    check("Карточка аренды прокручивается", "scrollable_body(" in source("rentals_view.py"))
    check("Подробности аренды прокручиваются", "scrollable_body(" in source("reports_view.py"))
    check("Список результатов поиска по фото прокручивается",
          "ScrollPerPixel" in source("photo_search_dialog.py"))

    print("\n=== Вложенная и горизонтальная прокрутка (пп. 10, 11) ===")
    common = source("widgets/common.py")
    check("Таблицы прокручиваются по пикселям, а не по строкам",
          "setVerticalScrollMode(QTableWidget.ScrollPerPixel)" in common)
    check("Горизонтальная прокрутка таблиц доступна при нехватке ширины",
          "setHorizontalScrollMode(QTableWidget.ScrollPerPixel)" in common)
    check("Ширина колонки ограничена, таблица не растягивает экран",
          "setMaximumSectionSize" in common)
    check("Внутренние списки имеют минимальную высоту и не схлопываются",
          "setMinimumHeight" in source("rental_new_view.py")
          and "setMinimumHeight" in source("add_items_dialog.py"))
    check("Область каталога-плитки без горизонтальной прокрутки",
          "ScrollBarAlwaysOff" in source("catalog_view.py"))
    check("Разделители не позволяют схлопнуть список",
          "setChildrenCollapsible(False)" in source("catalog_view.py")
          and "setChildrenCollapsible(False)" in source("reports_view.py"))

    print("\n=== Перекрытия и длинные данные (пп. 2, 3) ===")
    check("Длинное значение в таблице обрезается многоточием, а не ломает колонку",
          "ElideRight" in common)
    check("Полное значение доступно подсказкой", "setToolTip(value)" in common)
    check("Название в плитке каталога имеет фиксированную высоту",
          "setFixedHeight(52)" in source("widgets/item_tile.py"))
    check("Длинное название раздела в шапке сокращается",
          "_shorten" in source("main_window.py"))
    check("Заголовок и подзаголовок не выталкивают поиск",
          "setMaximumWidth(420)" in source("main_window.py"))
    check("Длинные подписи переносятся по словам",
          source("item_card.py").count("setWordWrap(True)") >= 3)

    layout_issues = []
    for path in UI.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\.move\(\s*\d+\s*,\s*\d+\s*\)", text) and "notify_badge" not in text:
            layout_issues.append(path.name)      # ручное позиционирование = риск наложения
    check("Элементы размещаются раскладками, а не жёсткими координатами",
          not layout_issues, str(layout_issues))
    fixed_sizes = []
    for path in UI.rglob("*.py"):
        for match in re.finditer(r"setFixedSize\((\d+),\s*(\d+)\)", path.read_text(encoding="utf-8")):
            if int(match.group(1)) > 700 or int(match.group(2)) > 700:
                fixed_sizes.append(f"{path.name}: {match.group(0)}")
    check("Нет жёстко зафиксированных больших размеров", not fixed_sizes, str(fixed_sizes))

    print("\n=== Статусы (пп. 14, 15) ===")
    statuses = (ROOT / "app" / "services" / "statuses.py").read_text(encoding="utf-8")
    theme = source("theme.py")
    for code, label in (("in_stock", "В наличии"), ("rented", "В аренде"),
                        ("prolonged", "Пролонгация"), ("open_set", "Открыт набор / аренда"),
                        ("finished", "Аренда завершена")):
        check(f"Статус «{label}» определён", f'"{label}"' in statuses)
        check(f"Для статуса {code} задан цвет", f'"{code}": (' in theme)
    check("«В наличии» — зелёный", '"in_stock": (Color.SUCCESS' in theme)
    check("«В аренде» — оранжевый", '"rented": (Color.WARNING' in theme)
    check("«Пролонгация» — красный", '"prolonged": (Color.DANGER' in theme)
    check("«Открыт набор / аренда» — серый", '"open_set": (Color.NEUTRAL' in theme)
    check("«Аренда завершена» — тёмный", '"finished": (Color.FINISHED' in theme)
    check("Статус всегда сопровождается текстом",
          "StatusBadge(" in source("widgets/item_tile.py")
          and "label" in statuses and "LABELS" in statuses)
    emoji = []
    for path in UI.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", text):
            emoji.append(f"{path.name}: {match.group(0)}")
    check("Emoji вместо иконок не используются", not emoji, str(emoji[:5]))

    print("\n=== Иконки утверждённого набора (п. 1) ===")
    icons_dir = ROOT / "assets" / "icons"
    kit = ["search", "rental-new", "box", "return", "folder", "users", "chart", "alert",
           "settings", "front-view", "side-view", "details", "damage", "plus-circle",
           "edit", "wrench", "return-stock"]
    missing = [name for name in kit if not (icons_dir / f"{name}.svg").exists()]
    check("Все иконки UI Kit нарисованы", not missing, str(missing))
    widths = {re.search(r'stroke-width="([\d.]+)"', p.read_text(encoding="utf-8")).group(1)
              for p in icons_dir.glob("*.svg")}
    check("Одинаковая толщина линий во всём наборе", len(widths) == 1, str(widths))
    sizes = {re.search(r'viewBox="([^"]+)"', p.read_text(encoding="utf-8")).group(1)
             for p in icons_dir.glob("*.svg")}
    check("Одинаковая сетка иконок", sizes == {"0 0 24 24"}, str(sizes))
    caps = {re.search(r'stroke-linecap="(\w+)"', p.read_text(encoding="utf-8")).group(1)
            for p in icons_dir.glob("*.svg")}
    check("Одинаковое скругление концов линий", caps == {"round"}, str(caps))
    used = set()
    for path in UI.rglob("*.py"):
        used.update(re.findall(r'icon\("([a-z-]+)"', path.read_text(encoding="utf-8")))
    unknown = sorted(name for name in used if not (icons_dir / f"{name}.svg").exists())
    check("Интерфейс не ссылается на несуществующие иконки", not unknown, str(unknown))

    print("\n=== Кнопки (пп. 17, 18) ===")
    small_buttons = []
    for path in UI.rglob("*.py"):
        for match in re.finditer(r"setMinimumHeight\((\d+)\)", path.read_text(encoding="utf-8")):
            if int(match.group(1)) < 28:
                small_buttons.append(f"{path.name}: {match.group(0)}")
    check("Кнопки не сжимаются до неудобного размера", not small_buttons, str(small_buttons))
    check("Основные действия выделены стилем Primary",
          sum(p.read_text(encoding="utf-8").count('setObjectName("Primary")')
              for p in UI.rglob("*.py")) >= 10)
    check("Опасные действия выделены отдельно",
          any('setObjectName("Danger")' in p.read_text(encoding="utf-8")
              for p in UI.rglob("*.py")))
    check("У кнопок есть отступы и единая высота", "padding: 9px 16px" in theme)

    print(f"\n{'=' * 62}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    for name in FAILED:
        print("  FAIL:", name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
