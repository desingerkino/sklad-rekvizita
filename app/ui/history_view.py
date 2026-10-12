# -*- coding: utf-8 -*-
"""
Раздел «История» версии 2.3: журнал действий и движение реквизита.

Данные берутся из журнала действий (audit_log) и истории операций —
новых таблиц не заводится.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QTabWidget,
                               QVBoxLayout, QWidget)

from ..db.database import get_db
from ..services.audit_service import audit_service
from ..utils import fmt_dt, money_plain
from .theme import Space
from .widgets.common import Card, cell, icon, make_table, muted, section_title

ENTITY_LABELS = {"": "Все объекты", "rental": "Аренды", "rental_item": "Позиции аренд",
                 "item": "Реквизит", "user": "Пользователи", "settings": "Настройки",
                 "backup": "Резервные копии"}


class HistoryView(QWidget):
    open_rental = Signal(int)
    open_item = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(200)
        self._debounce.timeout.connect(self.reload)
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        head = QHBoxLayout()
        head.addWidget(section_title("История операций"))
        head.addStretch()
        self.summary = muted("")
        head.addWidget(self.summary)
        root.addLayout(head)

        filters = QHBoxLayout()
        filters.setSpacing(Space.MD)
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("Поиск по сотруднику, действию или подробностям…")
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(44)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        filters.addWidget(self.search, 1)
        self.entity_box = QComboBox()
        for code, label in ENTITY_LABELS.items():
            self.entity_box.addItem(label, code)
        self.entity_box.setMinimumWidth(200)
        self.entity_box.currentIndexChanged.connect(lambda *_: self._debounce.start())
        filters.addWidget(self.entity_box)
        root.addLayout(filters)

        tabs = QTabWidget()
        journal = QWidget()
        journal_layout = QVBoxLayout(journal)
        journal_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        self.table = make_table(
            ["Дата и время", "Сотрудник", "Действие", "Объект", "Подробности", "Сумма"], 4)
        self.table.doubleClicked.connect(self._open_selected)
        journal_layout.addWidget(self.table)
        journal_layout.addWidget(muted(
            "Двойной щелчок открывает связанную аренду или карточку реквизита. "
            "Записи журнала не редактируются и не удаляются."))
        tabs.addTab(journal, "Журнал действий")

        movement = QWidget()
        movement_layout = QVBoxLayout(movement)
        movement_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        self.movement_table = make_table(
            ["Дата", "Реквизит", "Операция", "Количество", "Аренда", "Клиент"], 1)
        self.movement_table.doubleClicked.connect(self._open_movement)
        movement_layout.addWidget(self.movement_table)
        tabs.addTab(movement, "Движение реквизита")
        root.addWidget(tabs, 1)

    def reload(self) -> None:
        entries = audit_service.search(self.search.text(), self.entity_box.currentData() or "")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for entry in entries:
            index = self.table.rowCount()
            self.table.insertRow(index)
            item = cell(fmt_dt(entry["created_at"]))
            item.setData(Qt.UserRole, (entry["entity"], entry["entity_id"]))
            self.table.setItem(index, 0, item)
            self.table.setItem(index, 1, cell(entry["user_name"]))
            self.table.setItem(index, 2, cell(entry["action"]))
            self.table.setItem(index, 3, cell(ENTITY_LABELS.get(entry["entity"], entry["entity"])))
            self.table.setItem(index, 4, cell(entry["details"]))
            self.table.setItem(index, 5, cell(
                money_plain(entry["amount"]) if entry["amount"] else "", True))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.summary.setText(f"Записей: {len(entries)}")

        rows = get_db().query(
            "SELECT ri.date_start date, ri.name_snap, ri.quantity, r.number, r.id rental_id, "
            "c.full_name client, 'Выдача' op FROM rental_items ri "
            "JOIN rentals r ON r.id = ri.rental_id LEFT JOIN clients c ON c.id = r.client_id "
            "UNION ALL "
            "SELECT rr.returned_at, ri.name_snap, rr.quantity, r.number, r.id, c.full_name, "
            "'Возврат' FROM rental_returns rr JOIN rental_items ri ON ri.id = rr.rental_item_id "
            "JOIN rentals r ON r.id = rr.rental_id LEFT JOIN clients c ON c.id = r.client_id "
            "ORDER BY date DESC LIMIT 300")
        self.movement_table.setRowCount(0)
        for row in rows:
            index = self.movement_table.rowCount()
            self.movement_table.insertRow(index)
            self.movement_table.setItem(index, 0, cell(fmt_dt(row["date"]), data=row["rental_id"]))
            self.movement_table.setItem(index, 1, cell(row["name_snap"]))
            self.movement_table.setItem(index, 2, cell(row["op"]))
            self.movement_table.setItem(index, 3, cell(row["quantity"], True))
            self.movement_table.setItem(index, 4, cell(row["number"]))
            self.movement_table.setItem(index, 5, cell(row["client"] or "—"))
        self.movement_table.resizeColumnsToContents()

    def _open_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        entity, entity_id = self.table.item(row, 0).data(Qt.UserRole)
        if not entity_id:
            return
        if entity == "rental":
            self.open_rental.emit(entity_id)
        elif entity == "item":
            self.open_item.emit(entity_id)

    def _open_movement(self) -> None:
        row = self.movement_table.currentRow()
        if row >= 0:
            self.open_rental.emit(self.movement_table.item(row, 0).data(Qt.UserRole))
