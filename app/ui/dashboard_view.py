# -*- coding: utf-8 -*-
"""
Главная страница версии 2.3.

Собирает картину дня из существующих данных: сводка по складу, быстрые
действия, аренды, требующие внимания, последние операции и категории.
Своих данных не хранит — всё читается через сервисы.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..services.audit_service import audit_service
from ..services.categories_service import categories_service
from ..services.inventory_service import inventory_service
from ..services.reports_service import period_bounds, reports_service
from ..services.rentals_service import rentals_service
from ..utils import fmt_date, fmt_dt, money_plain
from .reports_view import STATUS_TONES, StatCard
from .theme import Color, Space
from .widgets.common import (Card, StatusBadge, cell, fade_in, icon, make_table, muted,
                             section_title)


class QuickAction(Card):
    """Крупная кнопка быстрого действия."""

    clicked = Signal()

    def __init__(self, title: str, subtitle: str, icon_name: str, parent=None):
        super().__init__(parent, padding=Space.LG)
        self.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout()
        row.setSpacing(Space.MD)
        mark = QLabel()
        mark.setPixmap(icon(icon_name, "active").pixmap(26, 26))
        mark.setFixedSize(48, 48)
        mark.setAlignment(Qt.AlignCenter)
        mark.setObjectName("IconTile")
        row.addWidget(mark)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(title)
        name.setObjectName("CardTitle")
        note = QLabel(subtitle)
        note.setObjectName("Hint")
        note.setWordWrap(True)
        text.addWidget(name)
        text.addWidget(note)
        row.addLayout(text, 1)
        self.body.addLayout(row)
        self.setMinimumWidth(240)
        self.setProperty("interactive", "true")     # рамка при наведении — в theme.py

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

        super().leaveEvent(event)


class DashboardView(QWidget):
    """Главная: что происходит на складе прямо сейчас."""

    open_page = Signal(int)
    open_rental = Signal(int)
    open_item = Signal(int)
    open_category = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()

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

        stats = QHBoxLayout()
        stats.setSpacing(Space.MD)
        self.cards = {
            "items": StatCard("Позиций в каталоге", "info", icon_name="box"),
            "available": StatCard("Свободно экземпляров", "in_stock", icon_name="check"),
            "active": StatCard("Действующих аренд", "rented", icon_name="rental-new"),
            "overdue": StatCard("Просроченных аренд", "prolonged", icon_name="alert"),
            "revenue": StatCard("Начислено в этом месяце", "finished", icon_name="chart"),
        }
        for card in self.cards.values():
            stats.addWidget(card)
        root.addLayout(stats)

        actions = QHBoxLayout()
        actions.setSpacing(Space.MD)
        quick = [
            ("Новая аренда", "Оформить выдачу реквизита", "rental-new", 2),
            ("Каталог и поиск", "Найти реквизит, в том числе по фото", "search", 1),
            ("Принять возврат", "Реквизит вернулся на склад", "return", 4),
            ("Отчёты", "История аренд, календарь и статистика", "chart", 9),
        ]
        for title, subtitle, icon_name, page in quick:
            action = QuickAction(title, subtitle, icon_name)
            action.clicked.connect(lambda p=page: self.open_page.emit(p))
            actions.addWidget(action)
        root.addLayout(actions)

        columns = QHBoxLayout()
        columns.setSpacing(Space.LG)

        attention = Card(padding=Space.MD)
        header = QHBoxLayout()
        header.addWidget(section_title("Требуют внимания"))
        header.addStretch()
        more = QPushButton("Все аренды")
        more.setObjectName("LinkButton")
        more.setCursor(Qt.PointingHandCursor)
        more.clicked.connect(lambda: self.open_page.emit(3))
        header.addWidget(more)
        attention.body.addLayout(header)
        self.attention_table = make_table(
            ["Номер", "Проект", "Клиент", "Окончание", "Осталось", "Сумма", "Статус"], 1)
        self.attention_table.doubleClicked.connect(self._open_attention)
        attention.body.addWidget(self.attention_table)
        self.attention_hint = muted("")
        attention.body.addWidget(self.attention_hint)
        columns.addWidget(attention, 3)

        side = QVBoxLayout()
        side.setSpacing(Space.LG)

        categories_card = Card(padding=Space.MD)
        categories_card.body.addWidget(section_title("Категории"))
        self.categories_layout = QGridLayout()
        self.categories_layout.setSpacing(Space.SM)
        categories_card.body.addLayout(self.categories_layout)
        side.addWidget(categories_card)

        recent = Card(padding=Space.MD)
        recent.body.addWidget(section_title("Последние операции"))
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(Space.SM)
        recent.body.addLayout(self.recent_layout)
        side.addWidget(recent)
        side.addStretch()
        columns.addLayout(side, 2)
        root.addLayout(columns, 1)

    # ---------------------------------------------------------------- данные
    def reload(self) -> None:
        total_units, free_units = inventory_service.available_units()
        active = rentals_service.list_active()
        month = reports_service.search(*period_bounds("month"))
        stats = reports_service.stats(month)
        self.cards["items"].set_value(str(inventory_service.count()))
        self.cards["available"].set_value(f"{free_units} из {total_units}")
        self.cards["active"].set_value(str(len(active)))
        self.cards["overdue"].set_value(str(stats["overdue"]))
        self.cards["revenue"].set_value(money_plain(stats["revenue"]) + " ₽")

        soon = sorted(
            [r for r in active if r.get("days_left") is not None and r["days_left"] <= 3],
            key=lambda r: r["days_left"])
        rows = soon or active[:8]
        self.attention_table.setRowCount(0)
        for rental in rows[:8]:
            index = self.attention_table.rowCount()
            self.attention_table.insertRow(index)
            left = rental.get("days_left")
            status = "Просрочена" if (left is not None and left < 0) else (
                "Скоро окончание" if (left is not None and left <= 2) else "Действует")
            self.attention_table.setItem(index, 0, cell(rental["number"], data=rental["id"]))
            self.attention_table.setItem(index, 1, cell(rental.get("project_name") or "—"))
            self.attention_table.setItem(index, 2, cell(rental.get("client_name") or "—"))
            self.attention_table.setItem(index, 3, cell(
                fmt_date(rental["date_end"]) if rental["date_end"] else "не определено"))
            self.attention_table.setItem(index, 4, cell(
                "—" if left is None else f"{left} дн.", True))
            self.attention_table.setItem(index, 5, cell(
                money_plain(rental["total_final"]), True))
            self.attention_table.setItem(index, 6, cell(status))
        self.attention_table.resizeColumnsToContents()
        self.attention_hint.setText(
            "Аренды, у которых срок заканчивается в ближайшие три дня или уже истёк"
            if soon else "Срочных аренд нет — показаны действующие")

        while self.categories_layout.count():
            item = self.categories_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        tree = sorted(categories_service.tree(), key=lambda n: -n["total_count"])[:8]
        for index, node in enumerate(tree):
            button = QPushButton(f"{node['name']}   {node['total_count']}")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, cid=node["id"]: self.open_category.emit(cid))
            self.categories_layout.addWidget(button, index // 2, index % 2)

        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for entry in audit_service.search(limit=7):
            line = QLabel(f"<b>{entry['action']}</b> · {entry['user_name']}<br>"
                          f"<span style='color:{Color.TEXT_MUTED}'>"
                          f"{fmt_dt(entry['created_at'])} · {entry['details'][:60]}</span>")
            line.setTextFormat(Qt.RichText)
            line.setWordWrap(True)
            self.recent_layout.addWidget(line)
        fade_in(self)

    def _open_attention(self) -> None:
        row = self.attention_table.currentRow()
        if row >= 0:
            self.open_rental.emit(self.attention_table.item(row, 0).data(Qt.UserRole))
