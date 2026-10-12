# -*- coding: utf-8 -*-
"""
Раздел «Отчёты» версии 2.3.

Единый источник данных — существующие аренды: календарь, список, статистика
и фильтры читают их через reports_service. Своей базы у отчётов нет, поэтому
любая новая аренда, пролонгация или возврат отражаются сразу.
"""
from __future__ import annotations

import calendar as calendar_module
from datetime import date

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter,
                               QVBoxLayout, QWidget)

from ..services.categories_service import categories_service
from ..services.reports_service import STATUS_LABELS, period_bounds, reports_service
from ..utils import fmt_date, fmt_dt, money, money_plain
from .theme import Color, Space, status_tone
from .widgets.common import (Card, DatePicker, StatusBadge, cell, hint, icon, make_table,
                             muted, scrollable_body, section_title)

# статусы аренды в отчётах — по единой системе 2.3.1
STATUS_TONES = {"planned": "open_set", "active": "rented", "overdue": "prolonged",
                "prolonged": "prolonged", "closed": "finished", "cancelled": "finished"}
QUICK = [("today", "Сегодня"), ("yesterday", "Вчера"), ("week", "Эта неделя"),
         ("month", "Этот месяц"), ("prev_month", "Прошлый месяц"), ("year", "Этот год"),
         ("all", "Всё время")]
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
          "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class StatCard(Card):
    """Аналитическая карточка: значение и подпись."""

    def __init__(self, caption: str, tone: str = "info", parent=None):
        super().__init__(parent, padding=Space.MD)
        # Крупное число нейтральное; цвет только у тревожной карточки и только
        # когда в ней есть что-то, кроме нуля (см. #StatTileValue в theme.py).
        self._alert_tone = tone in ("prolonged", "danger", "out")
        self.value = QLabel("—")
        self.value.setObjectName("StatTileValue")
        label = QLabel(caption)
        label.setObjectName("Hint")
        label.setWordWrap(True)
        self.body.setSpacing(2)
        self.body.addWidget(self.value)
        self.body.addWidget(label)
        self.setMinimumWidth(150)

    def set_value(self, text: str) -> None:
        self.value.setText(text)
        alert = self._alert_tone and text.strip() not in ("0", "0 ₽", "—", "")
        self.value.setProperty("alert", "true" if alert else "false")
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)


class DayCell(QFrame):
    """День календаря с полосками аренд."""

    clicked = Signal(object)
    rental_clicked = Signal(int)

    def __init__(self, day: dict, parent=None):
        super().__init__(parent)
        self.day = day
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        layout.setSpacing(3)

        number = QLabel(str(day["date"].day))
        if day["is_today"]:
            number.setStyleSheet(
                f"font-weight: 700; color: {Color.TEXT_ON_PRIMARY};"
                f" background: {Color.PRIMARY}; border-radius: 10px; padding: 1px 7px;")
            number.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        elif not day["in_month"]:
            number.setStyleSheet(f"color: {Color.TEXT_MUTED};")
        else:
            number.setStyleSheet(f"color: {Color.TEXT_SECONDARY}; font-weight: 600;")
        layout.addWidget(number)

        for entry in day["rentals"][:3]:
            color, background, border = status_tone(STATUS_TONES.get(entry["status"], "info"))
            bar = QPushButton(f"{entry['number']} · {entry['client']}")
            bar.setCursor(Qt.PointingHandCursor)
            period = (f"по {fmt_date(entry['date_end'])}" if entry.get("date_end")
                      else "дата возврата не указана")
            bar.setToolTip(f"{entry['number']} · {entry['project']} · {entry['client']}\n"
                           f"{entry['status_label']} · открыта {fmt_date(day['date'])}, "
                           f"{period}")
            bar.setStyleSheet(
                f"QPushButton {{ background: {background}; color: {color}; border: none;"
                f" border-left: 3px solid {color}; border-radius: 5px; padding: 3px 6px;"
                f" text-align: left; font-size: 8pt; }}"
                f"QPushButton:hover {{ background: {color}; color: {Color.TEXT_ON_PRIMARY}; }}")
            bar.clicked.connect(lambda _=False, rid=entry["rental_id"]: self.rental_clicked.emit(rid))
            layout.addWidget(bar)

        if len(day["rentals"]) > 3:
            more = QLabel(f"ещё {len(day['rentals']) - 3}")
            more.setObjectName("Hint")
            layout.addWidget(more)
        layout.addStretch()

        background = Color.SURFACE if day["in_month"] else Color.SURFACE_ALT
        border = Color.PRIMARY if day["is_today"] else Color.BORDER
        self.setStyleSheet(
            f"#Card {{ background: {background}; border: 1px solid {border};"
            f" border-radius: 10px; }}")

    def mousePressEvent(self, event):
        self.clicked.emit(self.day["date"])
        super().mousePressEvent(event)


class ReportsView(QWidget):
    """История аренд: календарь, фильтры, список и статистика."""

    open_rental = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        current = date.today()
        self.year, self.month = current.year, current.month
        self.period = "month"
        self.rentals: list[dict] = []
        self._build()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self.reload)
        self.reload()

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        host = QWidget()
        scroll.setWidget(host)
        outer.addWidget(scroll)

        root = QVBoxLayout(host)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.LG)

        # --------------------------------------------------- статистика
        self.stats_row = QHBoxLayout()
        self.stats_row.setSpacing(Space.MD)
        self.stat_cards = {
            "total": StatCard("Аренд за период", "info"),
            "active": StatCard("Активные", "in_stock"),
            "closed": StatCard("Завершённые", "returned"),
            "overdue": StatCard("Просроченные", "danger"),
            "revenue": StatCard("Начислено за период", "soon"),
        }
        for card in self.stat_cards.values():
            self.stats_row.addWidget(card)
        root.addLayout(self.stats_row)

        # ----------------------------------------------------- фильтры
        filters = Card(padding=Space.LG)
        top = QHBoxLayout()
        top.setSpacing(Space.MD)
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText(
            "Поиск по истории: ФИО клиента, проект, реквизит, номер аренды…")
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(44)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        top.addWidget(self.search, 1)
        reset = QPushButton("Сбросить фильтры")
        reset.setMinimumHeight(44)
        reset.clicked.connect(self.reset_filters)
        top.addWidget(reset)
        filters.body.addLayout(top)

        quick = QHBoxLayout()
        quick.setSpacing(Space.SM)
        quick.addWidget(QLabel("Период:"))
        self.period_buttons: dict[str, QPushButton] = {}
        for code, label in QUICK:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, c=code: self.set_period(c))
            self.period_buttons[code] = button
            quick.addWidget(button)
        self.period_buttons["month"].setChecked(True)
        quick.addSpacing(Space.MD)
        quick.addWidget(QLabel("С:"))
        self.date_from = DatePicker()
        self.date_from.changed.connect(self._custom_period)
        quick.addWidget(self.date_from)
        quick.addWidget(QLabel("По:"))
        self.date_to = DatePicker()
        self.date_to.changed.connect(self._custom_period)
        quick.addWidget(self.date_to)
        quick.addStretch()
        filters.body.addLayout(quick)

        selectors = QHBoxLayout()
        selectors.setSpacing(Space.MD)
        self.filter_boxes: dict[str, QComboBox] = {}
        for key, caption in (("clients", "Клиент"), ("projects", "Проект"),
                             ("productions", "Продакшен"), ("category", "Категория"),
                             ("status", "Статус"), ("users", "Ответственный")):
            box = QComboBox()
            box.setMinimumWidth(170)
            box.currentIndexChanged.connect(lambda *_: self._debounce.start())
            self.filter_boxes[key] = box
            column = QVBoxLayout()
            column.setSpacing(2)
            caption_label = QLabel(caption)
            caption_label.setObjectName("Hint")
            column.addWidget(caption_label)
            column.addWidget(box)
            selectors.addLayout(column)
        selectors.addStretch()
        filters.body.addLayout(selectors)
        root.addWidget(filters)

        # ----------------------------------------------------- календарь
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        calendar_card = Card(padding=Space.LG)
        nav = QHBoxLayout()
        prev_btn = QPushButton()
        prev_btn.setObjectName("IconButton")
        prev_btn.setIcon(icon("arrow-left"))
        prev_btn.setCursor(Qt.PointingHandCursor)
        prev_btn.clicked.connect(lambda: self.shift_month(-1))
        nav.addWidget(prev_btn)
        self.month_label = QLabel()
        self.month_label.setObjectName("SectionTitle")
        self.month_label.setMinimumWidth(220)
        self.month_label.setAlignment(Qt.AlignCenter)
        nav.addWidget(self.month_label)
        next_btn = QPushButton()
        next_btn.setObjectName("IconButton")
        next_btn.setIcon(icon("arrow-left"))
        next_btn.setIconSize(QSize(18, 18))
        next_btn.setCursor(Qt.PointingHandCursor)
        next_btn.setStyleSheet("QPushButton { qproperty-iconSize: 18px; }")
        next_btn.clicked.connect(lambda: self.shift_month(1))
        nav.addWidget(next_btn)
        today_btn = QPushButton("Сегодня")
        today_btn.clicked.connect(self.go_today)
        nav.addWidget(today_btn)
        nav.addStretch()
        legend = QHBoxLayout()
        legend.setSpacing(Space.SM)
        for code, label in STATUS_LABELS.items():
            legend.addWidget(StatusBadge(STATUS_TONES.get(code, "info"), label))
        nav.addLayout(legend)
        calendar_card.body.addLayout(nav)
        calendar_card.body.addWidget(muted(
            "Аренда отмечена в дне открытия. Срок и дата возврата — в списке "
            "справа и в карточке аренды."))

        self.calendar_grid = QGridLayout()
        self.calendar_grid.setSpacing(Space.SM)
        calendar_card.body.addLayout(self.calendar_grid)
        splitter.addWidget(calendar_card)

        # -------------------------------------------------------- список
        list_card = Card(padding=Space.MD)
        list_header = QHBoxLayout()
        list_header.addWidget(section_title("Найденные аренды"))
        list_header.addStretch()
        self.list_hint = muted("")
        list_header.addWidget(self.list_hint)
        list_card.body.addLayout(list_header)
        self.table = make_table(
            ["Дата", "Номер", "Клиент", "Проект", "Реквизит", "Период", "Дней",
             "Сумма", "Статус"], 3)
        self.table.doubleClicked.connect(self._open_selected)
        list_card.body.addWidget(self.table)
        actions = QHBoxLayout()
        open_btn = QPushButton("  Открыть аренду")
        open_btn.setObjectName("Primary")
        open_btn.setIcon(icon("eye", "white"))
        open_btn.setMinimumHeight(42)
        open_btn.clicked.connect(self._open_selected)
        actions.addWidget(open_btn)
        details_btn = QPushButton("  Подробности")
        details_btn.setIcon(icon("file"))
        details_btn.setMinimumHeight(42)
        details_btn.clicked.connect(self._show_details)
        actions.addWidget(details_btn)
        actions.addStretch()
        list_card.body.addLayout(actions)
        splitter.addWidget(list_card)
        splitter.setSizes([560, 380])
        root.addWidget(splitter, 1)

    # -------------------------------------------------------------- периоды
    def set_period(self, code: str) -> None:
        self.period = code
        for key, button in self.period_buttons.items():
            button.setChecked(key == code)
        start, end = period_bounds(code)
        if start:
            self.date_from.set_value(start)
            self.date_to.set_value(end)
            self.year, self.month = start.year, start.month
        self.reload()

    def _custom_period(self) -> None:
        self.period = "custom"
        for button in self.period_buttons.values():
            button.setChecked(False)
        self._debounce.start()

    def shift_month(self, delta: int) -> None:
        month = self.month + delta
        year = self.year
        if month < 1:
            month, year = 12, year - 1
        elif month > 12:
            month, year = 1, year + 1
        self.year, self.month = year, month
        self.reload()

    def go_today(self) -> None:
        current = date.today()
        self.year, self.month = current.year, current.month
        self.set_period("month")

    def reset_filters(self) -> None:
        self.search.clear()
        for box in self.filter_boxes.values():
            box.setCurrentIndex(0)
        self.set_period("month")

    # ---------------------------------------------------------------- данные
    def reload(self) -> None:
        self._fill_filter_options()
        start = self.date_from.value() if self.period != "all" else None
        end = self.date_to.value() if self.period != "all" else None
        try:
            self.rentals = reports_service.search(
                date_from=start, date_to=end, text=self.search.text(),
                client_id=self.filter_boxes["clients"].currentData(),
                project_id=self.filter_boxes["projects"].currentData(),
                production_id=self.filter_boxes["productions"].currentData(),
                category_id=self.filter_boxes["category"].currentData(),
                status=self.filter_boxes["status"].currentData() or "",
                user_id=self.filter_boxes["users"].currentData())
        except Exception:
            from .errors import show_error
            show_error(self, RuntimeError(), "Не удалось построить отчёт.")
            return
        self._fill_stats()
        self._fill_calendar()
        self._fill_table()

    def _fill_filter_options(self) -> None:
        if getattr(self, "_options_loaded", False):
            return
        options = reports_service.filter_options()
        pairs = {
            "clients": [("Все клиенты", None)] + [(c["full_name"], c["id"]) for c in options["clients"]],
            "projects": [("Все проекты", None)] + [(p["name"], p["id"]) for p in options["projects"]],
            "productions": [("Все продакшены", None)] + [(p["name"], p["id"]) for p in options["productions"]],
            "status": [("Любой статус", None)] + [(s["label"], s["code"]) for s in options["statuses"]],
            "users": [("Любой сотрудник", None)] + [(u["full_name"], u["id"]) for u in options["users"]],
        }
        categories = [("Все категории", None)]

        def walk(nodes, depth=0):
            for node in nodes:
                categories.append(("    " * depth + node["name"], node["id"]))
                walk(node["children"], depth + 1)

        walk(categories_service.tree())
        pairs["category"] = categories

        for key, values in pairs.items():
            box = self.filter_boxes[key]
            box.blockSignals(True)
            box.clear()
            for label, value in values:
                box.addItem(label, value)
            box.blockSignals(False)
        self._options_loaded = True

    def refresh_options(self) -> None:
        """Списки фильтров пересобираются, когда появились новые клиенты и проекты."""
        self._options_loaded = False
        self.reload()

    def _fill_stats(self) -> None:
        stats = reports_service.stats(self.rentals)
        self.stat_cards["total"].set_value(str(stats["total"]))
        self.stat_cards["active"].set_value(str(stats["active"]))
        self.stat_cards["closed"].set_value(str(stats["closed"]))
        self.stat_cards["overdue"].set_value(str(stats["overdue"]))
        self.stat_cards["revenue"].set_value(money_plain(stats["revenue"]) + " ₽")

    def _fill_calendar(self) -> None:
        while self.calendar_grid.count():
            item = self.calendar_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        self.month_label.setText(f"{MONTHS[self.month - 1]} {self.year}")
        for column, name in enumerate(WEEKDAYS):
            label = QLabel(name)
            label.setObjectName("Hint")
            label.setAlignment(Qt.AlignCenter)
            self.calendar_grid.addWidget(label, 0, column)

        grid = reports_service.calendar_month(self.year, self.month, self.rentals)
        for row, week in enumerate(grid, start=1):
            for column, day in enumerate(week):
                cell_widget = DayCell(day)
                cell_widget.rental_clicked.connect(self.open_rental.emit)
                cell_widget.clicked.connect(self._select_day)
                self.calendar_grid.addWidget(cell_widget, row, column)
        for column in range(7):
            self.calendar_grid.setColumnStretch(column, 1)

    def _select_day(self, day: date) -> None:
        """
        Клик по дню показывает аренды, открытые в этот день, — ровно то, что
        нарисовано в клетке. Аренда стоит в календаре один раз, в дне начала.
        """
        entries = reports_service.day_rentals(day, self.rentals)
        self._fill_table(entries, note=f"аренды, открытые {fmt_date(day)}")

    def _fill_table(self, entries: list[dict] | None = None, note: str = "") -> None:
        rows = self.rentals if entries is None else entries
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for rental in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            names = self._item_names(rental["id"])
            period = (f"{fmt_date(rental['date_start'])} — "
                      f"{fmt_date(rental['date_end']) if rental['date_end'] else 'без даты'}")
            self.table.setItem(index, 0, cell(fmt_date(rental["date_start"]), data=rental["id"]))
            self.table.setItem(index, 1, cell(rental["number"]))
            self.table.setItem(index, 2, cell(rental.get("client_name") or "—"))
            self.table.setItem(index, 3, cell(rental.get("project_name") or "—"))
            self.table.setItem(index, 4, cell(names))
            self.table.setItem(index, 5, cell(period))
            self.table.setItem(index, 6, cell(rental["days"] if rental["days"] else "—", True))
            self.table.setItem(index, 7, cell(money_plain(rental["total_final"]), True))
            self.table.setItem(index, 8, cell(rental["status_label"]))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.list_hint.setText(
            f"{note + ': ' if note else ''}{len(rows)} записей" if rows
            else "По выбранным условиям аренд не найдено")

    @staticmethod
    def _item_names(rental_id: int) -> str:
        from ..db.database import get_db
        rows = get_db().query(
            "SELECT name_snap, quantity FROM rental_items WHERE rental_id=? LIMIT 4", (rental_id,))
        names = [f"{r['name_snap']} × {r['quantity']}" for r in rows]
        return ", ".join(names) if names else "—"

    # -------------------------------------------------------------- детали
    def _selected_rental(self) -> int | None:
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.UserRole) if row >= 0 else None

    def _open_selected(self) -> None:
        rental_id = self._selected_rental()
        if rental_id:
            self.open_rental.emit(rental_id)

    def _show_details(self) -> None:
        rental_id = self._selected_rental()
        if not rental_id:
            return
        dialog = RentalDetailsDialog(self, rental_id)
        dialog.open_rental.connect(self.open_rental.emit)
        dialog.exec()


class RentalDetailsDialog(QDialog):
    """Подробности аренды из отчёта — только реальные поля приложения."""

    open_rental = Signal(int)

    def __init__(self, parent, rental_id: int):
        super().__init__(parent)
        self.rental_id = rental_id
        data = reports_service.rental_details(rental_id)
        self.setWindowTitle(f"Аренда {data.get('number', '')}")
        self.resize(880, 720)

        root, footer = scrollable_body(self)

        header = QHBoxLayout()
        title = QLabel(f"Аренда {data.get('number', '')}")
        title.setObjectName("PageTitle")
        header.addWidget(title)
        header.addWidget(StatusBadge(STATUS_TONES.get(data.get("status_code", "active"), "info"),
                                     data.get("status_label", "")))
        header.addStretch()
        root.addLayout(header)

        info = Card(padding=Space.LG)
        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.XL)
        grid.setVerticalSpacing(Space.SM)
        rows = [
            ("Клиент:", data.get("client_name") or "—"),
            ("Телефон:", data.get("client_phone") or "—"),
            ("Проект:", data.get("project_name") or "—"),
            ("Продакшен:", data.get("production_name") or "—"),
            ("Период:", f"{fmt_date(data.get('date_start'))} — "
                        f"{fmt_date(data.get('date_end')) if data.get('date_end') else 'без даты'}"),
            ("Создана:", fmt_dt(data.get("created_at"))),
            ("Способ оплаты:", data.get("payment_label") or "—"),
            ("Начислено:", money(data.get("total_before_discount", 0))),
            (f"Скидка {money_plain(data.get('discount_percent', 0))} %:",
             "− " + money(data.get("discount_amount", 0))),
            ("Итого:", money(data.get("total_final", 0))),
            ("Залог:", money(data.get("deposit", 0))),
            ("Ответственные:", ", ".join(data.get("responsible", [])) or "—"),
        ]
        for row, (label, value) in enumerate(rows):
            key = QLabel(label)
            key.setObjectName("Muted")
            grid.addWidget(key, row % 6, 0 if row < 6 else 2)
            value_label = QLabel(str(value))
            value_label.setWordWrap(True)
            grid.addWidget(value_label, row % 6, 1 if row < 6 else 3)
        info.body.addLayout(grid)
        root.addWidget(info)

        root.addWidget(section_title("Состав аренды"))
        items_table = make_table(
            ["Инв. №", "Наименование", "Кол-во", "Возвращено", "Период", "Начислено", "Статус"], 1)
        for item in data.get("items", []):
            index = items_table.rowCount()
            items_table.insertRow(index)
            items_table.setItem(index, 0, cell(item["inv_number_snap"]))
            items_table.setItem(index, 1, cell(item["name_snap"]))
            items_table.setItem(index, 2, cell(item["quantity"], True))
            items_table.setItem(index, 3, cell(item["returned_qty"], True))
            items_table.setItem(index, 4, cell(item["period_label"]))
            items_table.setItem(index, 5, cell(money_plain(item["total_amount"]), True))
            items_table.setItem(index, 6, cell(item["state_label"]))
        items_table.resizeColumnsToContents()
        items_table.setMinimumHeight(220)
        root.addWidget(items_table, 1)

        if data.get("returns"):
            root.addWidget(hint("Возвраты: " + "; ".join(
                f"{fmt_dt(r['returned_at'])} — {r['quantity']} шт." for r in data["returns"][:6])))
        if data.get("extensions"):
            root.addWidget(hint("Продления: " + "; ".join(
                f"{fmt_date(e['old_date_end'])} → {fmt_date(e['new_date_end'])}"
                for e in data["extensions"])))

        buttons = QHBoxLayout()
        open_btn = QPushButton("  Перейти к аренде")
        open_btn.setObjectName("Primary")
        open_btn.setIcon(icon("box", "white"))
        open_btn.setMinimumHeight(42)
        open_btn.clicked.connect(self._go)
        buttons.addWidget(open_btn)
        buttons.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        footer.addLayout(buttons)

    def _go(self) -> None:
        self.open_rental.emit(self.rental_id)
        self.accept()
