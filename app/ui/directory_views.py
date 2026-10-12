# -*- coding: utf-8 -*-
"""Разделы «Физ. лица», «Продакшены», «Проекты»: поиск, карточка, полная история."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ..services.clients_service import clients_service
from ..services.productions_service import productions_service
from ..services.projects_service import projects_service
from ..utils import fmt_date, money, money_plain
from ..ui.theme import Space
from .widgets.common import (Card, cell, icon, make_table, muted,
                             section_title, title_label)


class _BaseDirectoryView(QWidget):
    """Общий каркас: заголовок, поиск, таблица, кнопки."""

    open_rental = Signal(int)

    def __init__(self, title: str, headers: list[str], placeholder: str, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        head = QHBoxLayout()
        head.addWidget(section_title(title))
        head.addStretch()
        self.count_label = muted("")
        head.addWidget(self.count_label)
        root.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText(placeholder)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload)
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setMinimumHeight(42)
        root.addWidget(self.search)

        table_card = Card(padding=Space.SM)
        self.table = make_table(headers, 0)
        self.table.doubleClicked.connect(self.open_selected)
        table_card.body.addWidget(self.table)
        root.addWidget(table_card, 1)

        actions = QHBoxLayout()
        open_btn = QPushButton("  Открыть карточку")
        open_btn.setIcon(icon("eye"))
        open_btn.setMinimumHeight(42)
        open_btn.clicked.connect(self.open_selected)
        actions.addWidget(open_btn)
        edit_btn = QPushButton("  Редактировать")
        edit_btn.setIcon(icon("edit"))
        edit_btn.setMinimumHeight(42)
        edit_btn.clicked.connect(self.edit_selected)
        actions.addWidget(edit_btn)
        actions.addStretch()
        new_btn = QPushButton("  Добавить")
        new_btn.setObjectName("Primary")
        new_btn.setIcon(icon("plus", "white"))
        new_btn.setMinimumHeight(42)
        new_btn.clicked.connect(self.create_new)
        actions.addWidget(new_btn)
        root.addLayout(actions)

    def selected_id(self):
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.UserRole) if row >= 0 else None

    def reload(self) -> None:
        raise NotImplementedError

    def open_selected(self) -> None:
        raise NotImplementedError

    def edit_selected(self) -> None:
        raise NotImplementedError

    def create_new(self) -> None:
        raise NotImplementedError


# --------------------------------------------------------------------- физлица
class ClientsView(_BaseDirectoryView):
    def __init__(self, parent=None):
        super().__init__("Физические лица",
                         ["ФИО", "Телефон", "E-mail", "Адрес", "Аренд", "Действующих"],
                         "Поиск по ФИО, телефону, e-mail или паспорту…", parent)
        self.reload()

    def reload(self) -> None:
        rows = clients_service.search(self.search.text())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for client in rows:
            rentals = clients_service.rentals(client["id"])
            active = [r for r in rentals if r["status"] == "active"]
            index = self.table.rowCount()
            self.table.insertRow(index)
            self.table.setItem(index, 0, cell(client["full_name"], data=client["id"]))
            self.table.setItem(index, 1, cell(client["phone"]))
            self.table.setItem(index, 2, cell(client["email"]))
            self.table.setItem(index, 3, cell(client["address"]))
            self.table.setItem(index, 4, cell(len(rentals), True))
            self.table.setItem(index, 5, cell(len(active), True))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.count_label.setText(f"Всего: {len(rows)}")

    def open_selected(self) -> None:
        client_id = self.selected_id()
        if client_id:
            dialog = ClientCardDialog(self, client_id)
            dialog.open_rental.connect(self.open_rental.emit)
            dialog.exec()
            self.reload()

    def edit_selected(self) -> None:
        from .directory_dialogs import ClientDialog
        client_id = self.selected_id()
        if client_id and ClientDialog(self, client_id).exec():
            self.reload()

    def create_new(self) -> None:
        from .directory_dialogs import ClientDialog
        if ClientDialog(self).exec():
            self.reload()


class ClientCardDialog(QDialog):
    open_rental = Signal(int)

    def __init__(self, parent, client_id: int):
        super().__init__(parent)
        self.client_id = client_id
        client = clients_service.get(client_id) or {}
        self.setWindowTitle(client.get("full_name", "Физическое лицо"))
        self.resize(980, 640)

        root = QVBoxLayout(self)
        header = QLabel(client.get("full_name", ""))
        header.setStyleSheet("font-size:15pt; font-weight:600;")
        root.addWidget(header)

        box = QGroupBox("Данные")
        form = QFormLayout(box)
        for label, key in [("Паспорт:", "passport"), ("Телефон:", "phone"),
                           ("Адрес:", "address"), ("E-mail:", "email"), ("Дополнительно:", "extra")]:
            value = QLabel(client.get(key) or "—")
            value.setWordWrap(True)
            form.addRow(QLabel(label), value)
        root.addWidget(box)

        tabs = QTabWidget()
        self.active_table = self._make_rentals_table()
        self.history_table = self._make_rentals_table()
        current = QWidget(); QVBoxLayout(current).addWidget(self.active_table)
        history = QWidget(); QVBoxLayout(history).addWidget(self.history_table)
        tabs.addTab(current, "Текущие аренды")
        tabs.addTab(history, "История аренд")
        root.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        open_btn = QPushButton("Открыть аренду")
        open_btn.clicked.connect(self._open_rental)
        buttons.addWidget(open_btn)
        buttons.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self._fill()

    def _make_rentals_table(self):
        table = make_table(["Номер", "Проект", "Продакшен", "Начало", "Окончание",
                            "Позиций", "Итого", "Залог", "Статус"], 1)
        table.doubleClicked.connect(self._open_rental)
        return table

    def _fill(self) -> None:
        rentals = clients_service.rentals(self.client_id)
        for table, rows in ((self.active_table, [r for r in rentals if r["status"] == "active"]),
                            (self.history_table, rentals)):
            table.setRowCount(0)
            for rental in rows:
                index = table.rowCount()
                table.insertRow(index)
                table.setItem(index, 0, cell(rental["number"], data=rental["id"]))
                table.setItem(index, 1, cell(rental.get("project") or "—"))
                table.setItem(index, 2, cell(rental.get("production") or "—"))
                table.setItem(index, 3, cell(fmt_date(rental["date_start"])))
                table.setItem(index, 4, cell(fmt_date(rental["date_end"]) if rental["date_end"] else "—"))
                table.setItem(index, 5, cell(rental.get("positions", ""), True))
                table.setItem(index, 6, cell(money_plain(rental["total_final"]), True))
                table.setItem(index, 7, cell(money_plain(rental["deposit"]), True))
                table.setItem(index, 8, cell({"active": "Действует", "closed": "Завершена"}.get(
                    rental["status"], rental["status"])))
            table.resizeColumnsToContents()

    def _open_rental(self) -> None:
        for table in (self.active_table, self.history_table):
            row = table.currentRow()
            if row >= 0 and table.isVisible():
                self.open_rental.emit(table.item(row, 0).data(Qt.UserRole))
                self.accept()
                return


# ------------------------------------------------------------------ продакшены
class ProductionsView(_BaseDirectoryView):
    def __init__(self, parent=None):
        super().__init__("Продакшены",
                         ["Название", "Форма", "ИНН", "Контактное лицо", "Телефон",
                          "Проектов", "Аренд", "Сумма аренд"],
                         "Поиск по названию, ИНН или контактному лицу…", parent)
        self.reload()

    def reload(self) -> None:
        rows = productions_service.search(self.search.text())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for production in rows:
            totals = productions_service.totals(production["id"])
            index = self.table.rowCount()
            self.table.insertRow(index)
            self.table.setItem(index, 0, cell(production["name"], data=production["id"]))
            self.table.setItem(index, 1, cell(production["legal_form"]))
            self.table.setItem(index, 2, cell(production["inn"]))
            self.table.setItem(index, 3, cell(production["contact_person"]))
            self.table.setItem(index, 4, cell(production["phone"]))
            self.table.setItem(index, 5, cell(len(productions_service.projects(production["id"])), True))
            self.table.setItem(index, 6, cell(totals["rentals"], True))
            self.table.setItem(index, 7, cell(money_plain(totals["amount"]), True))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.count_label.setText(f"Всего: {len(rows)}")

    def open_selected(self) -> None:
        production_id = self.selected_id()
        if production_id:
            dialog = ProductionCardDialog(self, production_id)
            dialog.open_rental.connect(self.open_rental.emit)
            dialog.exec()
            self.reload()

    def edit_selected(self) -> None:
        from .directory_dialogs import ProductionDialog
        production_id = self.selected_id()
        if production_id and ProductionDialog(self, production_id).exec():
            self.reload()

    def create_new(self) -> None:
        from .directory_dialogs import ProductionDialog
        if ProductionDialog(self).exec():
            self.reload()


class ProductionCardDialog(QDialog):
    open_rental = Signal(int)

    def __init__(self, parent, production_id: int):
        super().__init__(parent)
        self.production_id = production_id
        production = productions_service.get(production_id) or {}
        self.setWindowTitle(production.get("name", "Продакшен"))
        self.resize(1000, 660)

        root = QVBoxLayout(self)
        header = QLabel(f"{production.get('legal_form','')} {production.get('name','')}".strip())
        header.setStyleSheet("font-size:15pt; font-weight:600;")
        root.addWidget(header)

        info_row = QHBoxLayout()
        legal = QGroupBox("Реквизиты")
        legal_form = QFormLayout(legal)
        for label, key in [("ИНН:", "inn"), ("КПП:", "kpp"), ("ОГРН:", "ogrn"),
                           ("Юр. адрес:", "legal_address"), ("Факт. адрес:", "actual_address")]:
            value = QLabel(production.get(key) or "—")
            value.setWordWrap(True)
            legal_form.addRow(QLabel(label), value)
        info_row.addWidget(legal, 1)

        bank = QGroupBox("Банк и контакты")
        bank_form = QFormLayout(bank)
        for label, key in [("Банк:", "bank_name"), ("Р/с:", "bank_account"), ("БИК:", "bank_bik"),
                           ("К/с:", "bank_corr"), ("Телефон:", "phone"), ("E-mail:", "email"),
                           ("Контакт:", "contact_person"), ("Должность:", "contact_position")]:
            value = QLabel(production.get(key) or "—")
            value.setWordWrap(True)
            bank_form.addRow(QLabel(label), value)
        info_row.addWidget(bank, 1)
        root.addLayout(info_row)

        tabs = QTabWidget()
        self.projects_table = make_table(["Проект", "Начало", "Окончание", "Контакты"], 0)
        projects_page = QWidget(); QVBoxLayout(projects_page).addWidget(self.projects_table)
        tabs.addTab(projects_page, "Проекты")

        self.rentals_table = make_table(
            ["Номер", "Проект", "Клиент", "Начало", "Окончание", "Итого", "Залог", "Статус"], 1)
        self.rentals_table.doubleClicked.connect(self._open_rental)
        rentals_page = QWidget(); QVBoxLayout(rentals_page).addWidget(self.rentals_table)
        tabs.addTab(rentals_page, "Аренды")
        root.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)
        self._fill()

    def _fill(self) -> None:
        for project in productions_service.projects(self.production_id):
            index = self.projects_table.rowCount()
            self.projects_table.insertRow(index)
            self.projects_table.setItem(index, 0, cell(project["name"], data=project["id"]))
            self.projects_table.setItem(index, 1, cell(fmt_date(project["date_start"]) if project["date_start"] else "—"))
            self.projects_table.setItem(index, 2, cell(fmt_date(project["date_end"]) if project["date_end"] else "—"))
            self.projects_table.setItem(index, 3, cell(project["contacts"]))
        self.projects_table.resizeColumnsToContents()

        for rental in productions_service.rentals(self.production_id):
            index = self.rentals_table.rowCount()
            self.rentals_table.insertRow(index)
            self.rentals_table.setItem(index, 0, cell(rental["number"], data=rental["id"]))
            self.rentals_table.setItem(index, 1, cell(rental.get("project") or "—"))
            self.rentals_table.setItem(index, 2, cell(rental.get("client") or "—"))
            self.rentals_table.setItem(index, 3, cell(fmt_date(rental["date_start"])))
            self.rentals_table.setItem(index, 4, cell(fmt_date(rental["date_end"]) if rental["date_end"] else "—"))
            self.rentals_table.setItem(index, 5, cell(money_plain(rental["total_final"]), True))
            self.rentals_table.setItem(index, 6, cell(money_plain(rental["deposit"]), True))
            self.rentals_table.setItem(index, 7, cell({"active": "Действует", "closed": "Завершена"}.get(
                rental["status"], rental["status"])))
        self.rentals_table.resizeColumnsToContents()

    def _open_rental(self) -> None:
        row = self.rentals_table.currentRow()
        if row >= 0:
            self.open_rental.emit(self.rentals_table.item(row, 0).data(Qt.UserRole))
            self.accept()


# --------------------------------------------------------------------- проекты
class ProjectsView(_BaseDirectoryView):
    def __init__(self, parent=None):
        super().__init__("Проекты", ["Название", "Продакшен", "Контакты", "Аренд"],
                         "Поиск по названию проекта или продакшену…", parent)
        self.reload()

    def reload(self) -> None:
        rows = projects_service.search(self.search.text())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for project in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            self.table.setItem(index, 0, cell(project["name"], data=project["id"]))
            self.table.setItem(index, 1, cell(project.get("production_name") or "—"))
            self.table.setItem(index, 2, cell(project["contacts"]))
            self.table.setItem(index, 3, cell(project.get("rentals_count", 0), True))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.count_label.setText(f"Всего: {len(rows)}")

    def open_selected(self) -> None:
        project_id = self.selected_id()
        if project_id:
            dialog = ProjectCardDialog(self, project_id)
            dialog.open_rental.connect(self.open_rental.emit)
            dialog.exec()
            self.reload()

    def edit_selected(self) -> None:
        from .directory_dialogs import ProjectDialog
        project_id = self.selected_id()
        if project_id and ProjectDialog(self, project_id).exec():
            self.reload()

    def create_new(self) -> None:
        from .directory_dialogs import ProjectDialog
        if ProjectDialog(self).exec():
            self.reload()


class ProjectCardDialog(QDialog):
    open_rental = Signal(int)

    def __init__(self, parent, project_id: int):
        super().__init__(parent)
        self.project_id = project_id
        project = projects_service.get(project_id) or {}
        self.setWindowTitle(project.get("name", "Проект"))
        self.resize(1000, 620)

        root = QVBoxLayout(self)
        header = QLabel(project.get("name", ""))
        header.setStyleSheet("font-size:15pt; font-weight:600;")
        root.addWidget(header)

        box = QGroupBox("О проекте")
        form = QFormLayout(box)
        form.addRow(QLabel("Продакшен:"), QLabel(project.get("production_name") or "—"))
        form.addRow(QLabel("Контакты:"), QLabel(project.get("contacts") or "—"))
        extra = QLabel(project.get("extra") or "—")
        extra.setWordWrap(True)
        form.addRow(QLabel("Дополнительно:"), extra)
        root.addWidget(box)

        root.addWidget(QLabel("История аренд проекта:"))
        self.table = make_table(
            ["Номер", "Клиент", "Продакшен", "Начало", "Окончание", "Позиций",
             "Итого", "Скидка", "Залог", "Статус"], 1)
        self.table.doubleClicked.connect(self._open_rental)
        root.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        open_btn = QPushButton("Открыть аренду")
        open_btn.clicked.connect(self._open_rental)
        buttons.addWidget(open_btn)
        buttons.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)
        self._fill()

    def _fill(self) -> None:
        total = 0.0
        for rental in projects_service.rentals(self.project_id):
            index = self.table.rowCount()
            self.table.insertRow(index)
            self.table.setItem(index, 0, cell(rental["number"], data=rental["id"]))
            self.table.setItem(index, 1, cell(rental.get("client") or "—"))
            self.table.setItem(index, 2, cell(rental.get("production") or "—"))
            self.table.setItem(index, 3, cell(fmt_date(rental["date_start"])))
            self.table.setItem(index, 4, cell(fmt_date(rental["date_end"]) if rental["date_end"] else "—"))
            self.table.setItem(index, 5, cell(rental.get("positions", ""), True))
            self.table.setItem(index, 6, cell(money_plain(rental["total_final"]), True))
            self.table.setItem(index, 7, cell(f"{money_plain(rental['discount_percent'])} %", True))
            self.table.setItem(index, 8, cell(money_plain(rental["deposit"]), True))
            self.table.setItem(index, 9, cell({"active": "Действует", "closed": "Завершена"}.get(
                rental["status"], rental["status"])))
            total += rental["total_final"]
        self.table.resizeColumnsToContents()
        self.setWindowTitle(f"{self.windowTitle()} — аренд на {money(total)}")

    def _open_rental(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.open_rental.emit(self.table.item(row, 0).data(Qt.UserRole))
            self.accept()
