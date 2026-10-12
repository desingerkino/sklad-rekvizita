# -*- coding: utf-8 -*-
"""
Выбор физического лица из списка.

Подсказки под полем удобны, когда фамилию помнишь. Когда не помнишь —
нужен обычный список с поиском: открыл, посмотрел, выбрал. Здесь же видно,
у кого сейчас есть незакрытые аренды: выдавать новое человеку, который ещё
не вернул старое, стоит осознанно.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout)

from ..services.clients_service import clients_service
from .theme import Space
from .widgets.common import cell, icon, make_table, muted, section_title

SEARCH_PAUSE_MS = 200


class ClientPickerDialog(QDialog):
    """Список физических лиц с поиском. Возвращает выбранного через .client."""

    def __init__(self, parent=None, preset: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Выбор физического лица")
        self.setModal(True)
        self.resize(760, 560)
        self.client: Optional[dict] = None
        self._rows: list[dict] = []
        self._build(preset)
        self._reload()

    def _build(self, preset: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        root.setSpacing(Space.MD)

        root.addWidget(section_title("Физические лица"))
        root.addWidget(muted(
            "Найдите человека по фамилии, телефону или документу. "
            "Если его ещё нет в базе — закройте окно и введите ФИО прямо в поле: "
            "карточка создастся при оформлении аренды."))

        self.search = QLineEdit(preset)
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("Фамилия, телефон или номер документа…")
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(42)
        self.search.textEdited.connect(self._schedule)
        root.addWidget(self.search)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEARCH_PAUSE_MS)
        self._timer.timeout.connect(self._reload)

        self.table = make_table(["ФИО", "Телефон", "Паспорт", "Аренд сейчас"], 0)
        self.table.doubleClicked.connect(self._accept_selected)
        self.table.itemSelectionChanged.connect(self._update_button)
        root.addWidget(self.table, 1)

        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        root.addWidget(self.summary)

        buttons = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        self.pick_btn = QPushButton("Выбрать")
        self.pick_btn.setObjectName("Primary")
        self.pick_btn.setMinimumHeight(42)
        self.pick_btn.setMinimumWidth(160)
        self.pick_btn.setEnabled(False)
        self.pick_btn.clicked.connect(self._accept_selected)
        buttons.addWidget(self.pick_btn)
        root.addLayout(buttons)

        self.search.setFocus()

    def _schedule(self) -> None:
        self._timer.start()

    def _reload(self) -> None:
        text = self.search.text().strip()
        try:
            self._rows = clients_service.search(text, limit=300)
        except Exception:                                          # noqa: BLE001
            self._rows = []
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for client in self._rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            active = len(clients_service.rentals(client["id"], only_active=True))
            self.table.setItem(row, 0, cell(client["full_name"], data=client["id"]))
            self.table.setItem(row, 1, cell(client.get("phone") or "—"))
            self.table.setItem(row, 2, cell(client.get("passport") or "—"))
            self.table.setItem(row, 3, cell(active if active else "", True))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.summary.setText(
            f"Найдено: {len(self._rows)}" if text
            else f"Всего физических лиц: {len(self._rows)}")
        self._update_button()

    def _update_button(self) -> None:
        self.pick_btn.setEnabled(self.table.currentRow() >= 0)

    def _accept_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        client_id = self.table.item(row, 0).data(Qt.UserRole)
        self.client = next((c for c in self._rows if c["id"] == client_id), None)
        if self.client:
            self.accept()
