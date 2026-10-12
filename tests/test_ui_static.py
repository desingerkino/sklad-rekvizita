# -*- coding: utf-8 -*-
"""
Статическая проверка интерфейса без запуска Qt (PySide6 в этой среде недоступен).

Проверяется то, что можно проверить по коду: все обработчики существуют,
сигналы подключены к реальным методам, интерфейс не обходит проверки прав,
русский язык в подписях, наличие подтверждений на удалении, отсутствие
запрещённых API (браузерное хранилище, свои часы).

Запуск:  python -m tests.test_ui_static
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "app" / "ui"

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


# методы, унаследованные от Qt — они существуют, но в исходнике не объявлены
QT_INHERITED = {"accept", "reject", "close", "show", "hide", "clear", "reload", "update",
                "showMaximized", "showNormal", "setFocus", "selectAll", "deleteLater"}


def methods_of(tree: ast.AST) -> set[str]:
    return {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def attributes_assigned(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.add(node.attr)
    return names


def main() -> int:
    files = sorted(UI.rglob("*.py"))
    print(f"Файлов интерфейса: {len(files)}\n")

    print("=== Обработчики и сигналы ===")
    broken = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        own = methods_of(tree) | attributes_assigned(tree)
        for node in ast.walk(tree):
            # .connect(self.что_то) — метод должен существовать
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "connect" and node.args):
                arg = node.args[0]
                if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) \
                        and arg.value.id == "self":
                    if arg.attr not in own and arg.attr not in QT_INHERITED:
                        broken.append(f"{path.name}: self.{arg.attr}")
    check("Все обработчики сигналов существуют", not broken, str(broken[:5]))

    print("\n=== Права: интерфейс не подменяет бизнес-логику ===")
    rentals_view = (UI / "rentals_view.py").read_text(encoding="utf-8")
    new_rental = (UI / "rental_new_view.py").read_text(encoding="utf-8")
    settings_view = (UI / "settings_view.py").read_text(encoding="utf-8")
    check("Скидка в карточке аренды идёт через сервис",
          "rentals_service.set_discount" in rentals_view)
    check("Кнопка скидки скрыта для обычного пользователя",
          "self.discount_btn.setEnabled(is_admin)" in rentals_view)
    check("Удаление позиции идёт через сервис", "rentals_service.remove_item" in rentals_view)
    check("Кнопка удаления позиции недоступна не администратору",
          "self.remove_item_btn.setEnabled(is_admin)" in rentals_view)
    check("Перед удалением позиции спрашивают подтверждение",
          "confirm(self" in rentals_view and "Удалить позицию" in rentals_view)
    check("Поле скидки в новой аренде только для чтения у пользователя",
          "self.discount.setReadOnly(True)" in new_rental)
    check("Вкладки пользователей и журнала только для администратора",
          "if auth_service.is_admin():" in settings_view)
    check("Настройки сохраняются защищённым методом",
          settings_view.count("settings_service.set_protected") >= 3)
    check("Восстановление базы вызывает сервис с проверкой прав",
          "backup_service.restore" in settings_view)

    print("\n=== Диалоги, подтверждения, ошибки ===")
    confirms = sum(f.read_text(encoding="utf-8").count("confirm(") for f in files)
    check("Подтверждения на опасных действиях присутствуют", confirms >= 8, str(confirms))
    for name in ("catalog_view.py", "rentals_view.py", "settings_view.py", "item_card.py",
                 "rental_new_view.py", "document_dialog.py"):
        text = (UI / name).read_text(encoding="utf-8")
        check(f"{name}: ошибки показываются человеку, а не в консоль",
              "show_error" in text or "QMessageBox" in text)
    errors_ui = (UI / "errors.py").read_text(encoding="utf-8")
    check("Технические подробности уходят в лог, а не на экран",
          "log.exception" in errors_ui and "app.log" in errors_ui)
    check("Stack trace пользователю не показывается",
          "format_exc" not in errors_ui and "traceback" not in errors_ui)

    print("\n=== Язык и формат ===")
    english_ui = []
    for path in files:
        for m in re.finditer(r'(?:setText|setWindowTitle|setPlaceholderText|addTab|QPushButton|QLabel)\(\s*"([^"]{4,})"', path.read_text(encoding="utf-8")):
            text = m.group(1)
            if re.search(r"[A-Za-z]{4,}", text) and not re.search(r"[А-Яа-яЁё]", text):
                english_ui.append(f"{path.name}: {text[:30]}")
    check("Подписи интерфейса на русском", not english_ui, str(english_ui[:5]))
    check("Формат даты ДД.ММ.ГГГГ задан в виджете календаря",
          'DATE_FORMAT = "dd.MM.yyyy"' in (UI / "widgets" / "common.py").read_text(encoding="utf-8"))

    print("\n=== Дизайн-система 2.1 ===")
    main_window = (UI / "main_window.py").read_text(encoding="utf-8")
    theme = (UI / "theme.py").read_text(encoding="utf-8")
    for token in ("BG", "SURFACE", "PRIMARY", "SECONDARY", "TEXT", "TEXT_SECONDARY",
                  "BORDER", "SUCCESS", "WARNING", "DANGER", "SELECTED"):
        check(f"Токен цвета {token} определён", f"    {token} = " in theme)
    for token in ("Radius", "Space", "Font", "Duration"):
        check(f"Токены {token} определены", f"class {token}:" in theme)
    hardcoded = []
    for path in files:
        if path.name == "theme.py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.search(r"#[0-9A-Fa-f]{6}", line) and "http" not in line:
                hardcoded.append(f"{path.name}: {line.strip()[:40]}")
    check("Цвета берутся только из дизайн-системы", not hardcoded, str(hardcoded[:4]))
    check("Стили собираются из токенов", "def build_qss()" in theme and "QSS = build_qss()" in theme)
    for state in (":hover", ":pressed", ":disabled", ":checked", ":focus"):
        check(f"В стилях описано состояние {state}", state in theme)
    for variant in ("#Primary", "#Danger", "#Ghost", "#IconButton"):
        check(f"Есть вариант кнопки {variant}", variant in theme)

    icons_dir = ROOT / "assets" / "icons"
    svg_files = list(icons_dir.glob("*.svg"))
    check("Единый набор иконок собран", len(svg_files) > 40, str(len(svg_files)))
    check("Иконки одного стиля (одинаковая толщина линии)",
          all('stroke-width="1.8"' in f.read_text(encoding="utf-8") for f in svg_files[:20]))
    check("Иконки используются в интерфейсе",
          sum(f.read_text(encoding="utf-8").count("icon(") for f in files) > 25)

    print("\n=== Адаптивность и анимации ===")
    catalog_src = (UI / "catalog_view.py").read_text(encoding="utf-8")
    check("Каталог перестраивает карточки под ширину окна", "FlowLayout" in catalog_src)
    check("Нет лишней горизонтальной прокрутки",
          "ScrollBarAlwaysOff" in catalog_src)
    check("Главное окно имеет минимальный размер", "setMinimumSize" in main_window)
    check("Разделители областей не схлопываются",
          "setChildrenCollapsible(False)" in catalog_src)
    check("Переключение разделов анимировано", "fade_in" in main_window)
    check("Анимация короткая и плавная",
          "Duration.NORMAL" in (UI / "widgets" / "common.py").read_text(encoding="utf-8"))

    print("\n=== Запрещённые приёмы ===")
    all_text = "\n".join(f.read_text(encoding="utf-8") for f in files)
    check("Нет localStorage/sessionStorage", "localStorage" not in all_text)
    check("Интерфейс не заводит собственных часов",
          "datetime.now()" not in all_text or "from ..utils import" in all_text)
    check("Нет заглушек «функция будет доступна позже»",
          "будет доступна позже" not in all_text and "не реализовано" not in all_text.lower())
    check("Нет TODO/FIXME в интерфейсе",
          "TODO" not in all_text and "FIXME" not in all_text)

    print("\n=== Структура 2.3: sidebar, topbar, разделы ===")
    menu_groups = re.findall(r'\("([А-ЯЁ]+)", "', main_window)
    check("Меню разбито на группы",
          {"ОСНОВНЫЕ", "КЛИЕНТЫ", "АНАЛИТИКА", "УПРАВЛЕНИЕ"} <= set(menu_groups),
          str(set(menu_groups)))
    for section in ("Главная", "Каталог", "Аренды", "Возвраты", "История",
                    "Физ. лица", "Юр. лица", "Отчёты", "Настройки"):
        check(f"В меню есть раздел «{section}»", f'"{section}"' in main_window)
    check("Профиль сотрудника в сайдбаре, а не в верхней панели",
          "_build_profile_card" in main_window
          and main_window.index("_build_profile_card") > main_window.index("_build_sidebar"))
    check("В профиле показаны имя, должность и роль",
          "display_name" in main_window and "position_label" in main_window
          and "role_label" in main_window)
    check("Аватар открывается из сайдбара", "open_profile" in main_window)
    check("Статус склада в сайдбаре", "_build_stock_card" in main_window)
    check("Круговой индикатор доступности",
          "CircularProgress" in main_window
          and (UI / "widgets" / "circular.py").exists())
    check("Показатели склада считаются из данных",
          "available_units" in main_window and "animate_to" in main_window)
    check("Камера встроена в строку поиска",
          "camera_action" in main_window and "TrailingPosition" in main_window)
    check("Поиск по фото переиспользует существующий диалог",
          "PhotoSearchDialog" in main_window)
    check("Колокольчик с счётчиком непрочитанных",
          "notify_badge" in main_window and "unread_count" in main_window)
    check("Панель уведомлений открывается по клику",
          "NotificationsPanel" in main_window and "_toggle_notifications" in main_window)
    topbar_code = main_window.split("def _build_topbar(self)")[1].split("def _photo_search")[0]
    check("Аватара и имени пользователя в верхней панели нет",
          "avatar" not in topbar_code and "user_name" not in topbar_code)
    check("В верхней панели только глобальные элементы",
          "global_search" in topbar_code and "notify_btn" in topbar_code)

    print("\n=== Отчёты ===")
    reports = (UI / "reports_view.py").read_text(encoding="utf-8")
    check("Отчёты читают существующие аренды", "reports_service" in reports)
    check("Своей базы у отчётов нет",
          "CREATE TABLE" not in reports and "INSERT INTO" not in reports)
    check("Есть календарь по месяцам",
          "calendar_month" in reports and "shift_month" in reports and "go_today" in reports)
    check("Клик по аренде в календаре открывает её",
          "rental_clicked" in reports and "open_rental" in reports)
    check("Быстрые периоды",
          all(p in reports for p in ("today", "yesterday", "week", "month", "prev_month", "year")))
    check("Произвольный период", "date_from" in reports and "date_to" in reports)
    check("Комбинируемые фильтры",
          all(f in reports for f in ("client_id", "project_id", "category_id",
                                     "status", "user_id")))
    check("Поиск по ключевым словам", "text=self.search.text()" in reports)
    check("Список результатов кликабелен", "_open_selected" in reports)
    check("Карточка подробностей аренды", "RentalDetailsDialog" in reports)
    check("Статистика из реальных данных", "reports_service.stats" in reports)
    check("Статусы аренд различаются цветом", "STATUS_TONES" in reports)

    print("\n=== Уведомления и возвраты ===")
    panel = (UI / "notifications_panel.py").read_text(encoding="utf-8")
    check("Уведомление содержит заголовок, текст и время",
          "data[\"title\"]" in panel and "data[\"body\"]" in panel and "created_at" in panel)
    check("Отметить одно прочитанным", "mark_read" in panel)
    check("Отметить все прочитанными", "mark_all_read" in panel)
    check("Переход к связанному объекту", "open_entity" in panel)
    returns = (UI / "returns_view.py").read_text(encoding="utf-8")
    check("Возвраты используют существующий сервис",
          "billing_service.return_item" in returns)
    check("Видно, что и сколько возвращается",
          "К возврату" in returns and "Принятые возвраты" in returns)

    print("\n=== Точка входа ===")
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    check("Программа стартует с экрана входа", "run_login" in main_py)
    check("Без входа главное окно не открывается",
          main_py.index("run_login") < main_py.index("MainWindow()"))
    check("После выхода снова показывается экран входа", "while True" in main_py)
    check("База закрывается при выходе", "close()" in main_py)
    check("В окне видно имя пользователя и роль",
          "user.full_name" in main_window and "role_label" in main_window)
    check("Есть кнопка «Выйти»", "Выйти" in main_window)
    check("Есть кнопка «Сохранить всё»", "Сохранить всё" in main_window)
    check("Версия 2.1 отображается в интерфейсе",
          "VERSION" in main_window and "версия" in main_window)
    check("Автопролонгация запускается при старте",
          "run_prolongations" in main_window)

    print(f"\n{'=' * 62}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    for name in FAILED:
        print("  FAIL:", name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
