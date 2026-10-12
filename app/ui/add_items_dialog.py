# -*- coding: utf-8 -*-
"""Выбор реквизита для добора в действующую аренду (поиск, фото-поиск, количество)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout)

from ..services.inventory_service import inventory_service
from ..services.rentals_service import RentalLine
from ..utils import money, money_plain, today
from ..version import RENTAL_PERIOD_DAYS
from .widgets.common import cell, make_table, muted, scrollable_body


class AddItemsDialog(QDialog):
    """Возвращает список RentalLine в поле `lines` после подтверждения."""

    def __init__(self, parent=None, date_start=None, date_end=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить реквизит в аренду")
        self.resize(980, 640)
        self.lines: list[RentalLine] = []
        self.date_start = date_start or today()
        self.date_end = date_end
        self._build()
        self._search()

    def _build(self) -> None:
        root, footer = scrollable_body(self)
        root.addWidget(muted(
            f"Новые позиции получат дату выдачи «сегодня» и собственный "
            f"{RENTAL_PERIOD_DAYS}-дневный период. Сроки ранее выданных позиций не изменятся."))

        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию, номеру, описанию, ключевым словам…")
        self.search.textChanged.connect(self._search)
        search_row.addWidget(self.search, 1)
        photo_btn = QPushButton("\U0001F4F7 По фото")
        photo_btn.clicked.connect(self._photo_search)
        search_row.addWidget(photo_btn)
        root.addLayout(search_row)

        self.catalog = make_table(
            ["Инв. №", "Наименование", "Доступно", f"Цена за {RENTAL_PERIOD_DAYS} дн."], 1)
        self.catalog.doubleClicked.connect(self._add_selected)
        self.catalog.setMinimumHeight(260)
        root.addWidget(self.catalog, 1)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Количество:"))
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 10000)
        add_row.addWidget(self.quantity)
        add_btn = QPushButton("Добавить в список ↓")
        add_btn.clicked.connect(self._add_selected)
        add_row.addWidget(add_btn)
        add_row.addStretch()
        root.addLayout(add_row)

        self.selected = make_table(["Инв. №", "Наименование", "Кол-во", "Цена", "Сумма"], 1)
        self.selected.setMinimumHeight(140)
        self.selected.setMaximumHeight(220)
        root.addWidget(self.selected)

        bottom = QHBoxLayout()
        remove_btn = QPushButton("Убрать из списка")
        remove_btn.clicked.connect(self._remove_selected)
        bottom.addWidget(remove_btn)
        self.total_label = QLabel(money(0))
        self.total_label.setObjectName("BigNumber")
        bottom.addStretch()
        bottom.addWidget(QLabel("Сумма добора:"))
        bottom.addWidget(self.total_label)
        root.addLayout(bottom)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok_btn = QPushButton("Выдать клиенту")
        self.ok_btn.setObjectName("Primary")
        self.ok_btn.clicked.connect(self._accept)
        buttons.addWidget(self.ok_btn)
        footer.addLayout(buttons)

    # ---------------------------------------------------------------- поиск
    def _search(self) -> None:
        items = inventory_service.search(self.search.text(), limit=200)
        self.catalog.setSortingEnabled(False)
        self.catalog.setRowCount(0)
        for item in items:
            available = inventory_service.available_for_period(
                item["id"], self.date_start, self.date_end)
            row = self.catalog.rowCount()
            self.catalog.insertRow(row)
            self.catalog.setItem(row, 0, cell(item["inv_number"], data=item["id"]))
            self.catalog.setItem(row, 1, cell(item["name"]))
            self.catalog.setItem(row, 2, cell(available, True))
            self.catalog.setItem(row, 3, cell(money_plain(item["rental_price"]), True))
        self.catalog.setSortingEnabled(True)
        self.catalog.resizeColumnsToContents()

    def _photo_search(self) -> None:
        from .photo_search_dialog import PhotoSearchDialog
        dialog = PhotoSearchDialog(self)
        dialog.add_to_rental.connect(self._add_item_id)
        dialog.open_item.connect(self._add_item_id)
        dialog.exec()

    # -------------------------------------------------------------- выбор
    def _add_selected(self) -> None:
        row = self.catalog.currentRow()
        if row < 0:
            return
        self._add_item_id(self.catalog.item(row, 0).data(Qt.UserRole), self.quantity.value())

    def _add_item_id(self, item_id: int, quantity: int = 1) -> None:
        item = inventory_service.get(item_id)
        if not item:
            return
        for line in self.lines:
            if line.item_id == item_id:
                line.quantity += quantity
                break
        else:
            self.lines.append(RentalLine(
                item_id=item_id, name=item["name"], inv_number=item["inv_number"],
                description=item["description"], quantity=quantity,
                price=item["rental_price"], valuation=item["valuation"]))
        self._refresh_selected()

    def _remove_selected(self) -> None:
        row = self.selected.currentRow()
        if 0 <= row < len(self.lines):
            self.lines.pop(row)
            self._refresh_selected()

    def _refresh_selected(self) -> None:
        self.selected.setSortingEnabled(False)
        self.selected.setRowCount(0)
        total = 0.0
        for line in self.lines:
            row = self.selected.rowCount()
            self.selected.insertRow(row)
            self.selected.setItem(row, 0, cell(line.inv_number, data=line.item_id))
            self.selected.setItem(row, 1, cell(line.name))
            self.selected.setItem(row, 2, cell(line.quantity, True))
            self.selected.setItem(row, 3, cell(money_plain(line.price), True))
            self.selected.setItem(row, 4, cell(money_plain(line.amount), True))
            total += line.amount
        self.selected.setSortingEnabled(True)
        self.selected.resizeColumnsToContents()
        self.total_label.setText(money(total))

    def _accept(self) -> None:
        if not self.lines:
            QMessageBox.information(self, "Добор реквизита", "Не выбрано ни одной позиции.")
            return
        self.accept()
