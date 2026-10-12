# -*- coding: utf-8 -*-
"""Диалоги карточек: физическое лицо, продакшен, проект."""
from __future__ import annotations

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit,
                               QPlainTextEdit, QVBoxLayout)

from ..errors import AppError
from ..services.clients_service import clients_service
from ..services.productions_service import productions_service
from ..services.projects_service import projects_service
from .errors import show_error
from .widgets.common import scrollable_body


class _BaseDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 560):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(width, 480)
        # содержимое прокручивается, кнопки остаются на виду
        self.root, self.footer = scrollable_body(self)
        self.form = QFormLayout()
        self.form.setSpacing(12)
        self.root.addLayout(self.form)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText("Сохранить")
        box.button(QDialogButtonBox.Save).setObjectName("Primary")
        box.button(QDialogButtonBox.Cancel).setText("Отмена")
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        self.footer.addWidget(box)
        self.root.addStretch()

    def save(self) -> None:
        raise NotImplementedError


class ClientDialog(_BaseDialog):
    def __init__(self, parent=None, client_id: int | None = None):
        super().__init__(parent, "Физическое лицо")
        self.client_id = client_id
        self.fields = {}
        for key, label in [("full_name", "ФИО*:"), ("passport", "Паспорт (серия, номер, кем выдан):"),
                           ("phone", "Телефон:"), ("address", "Адрес:"), ("email", "E-mail:")]:
            edit = QLineEdit()
            self.fields[key] = edit
            self.form.addRow(label, edit)
        self.extra = QPlainTextEdit()
        self.extra.setFixedHeight(80)
        self.form.addRow("Дополнительно:", self.extra)
        if client_id:
            data = clients_service.get(client_id) or {}
            for key, edit in self.fields.items():
                edit.setText(data.get(key, ""))
            self.extra.setPlainText(data.get("extra", ""))

    def save(self) -> None:
        data = {key: edit.text().strip() for key, edit in self.fields.items()}
        data["extra"] = self.extra.toPlainText().strip()
        try:
            if self.client_id:
                clients_service.update(self.client_id, data)
            else:
                self.client_id = clients_service.create(data)
            self.accept()
        except AppError as exc:
            show_error(self, exc)


class ProductionDialog(_BaseDialog):
    FIELDS = [("name", "Название*:"), ("legal_form", "ИП / ООО:"), ("inn", "ИНН:"), ("kpp", "КПП:"),
              ("ogrn", "ОГРН / ОГРНИП:"), ("legal_address", "Юридический адрес:"),
              ("actual_address", "Фактический адрес:"), ("bank_name", "Банк:"),
              ("bank_account", "Расчётный счёт:"), ("bank_bik", "БИК:"),
              ("bank_corr", "Корреспондентский счёт:"), ("phone", "Телефон:"), ("email", "E-mail:"),
              ("contact_person", "Контактное лицо:"), ("contact_position", "Должность:")]

    def __init__(self, parent=None, production_id: int | None = None):
        super().__init__(parent, "Продакшен", 620)
        self.production_id = production_id
        self.fields = {}
        for key, label in self.FIELDS:
            edit = QLineEdit()
            self.fields[key] = edit
            self.form.addRow(label, edit)
        self.extra = QPlainTextEdit()
        self.extra.setFixedHeight(70)
        self.form.addRow("Дополнительно:", self.extra)
        if production_id:
            data = productions_service.get(production_id) or {}
            for key, edit in self.fields.items():
                edit.setText(data.get(key, ""))
            self.extra.setPlainText(data.get("extra", ""))

    def save(self) -> None:
        data = {key: edit.text().strip() for key, edit in self.fields.items()}
        data["extra"] = self.extra.toPlainText().strip()
        try:
            if self.production_id:
                productions_service.update(self.production_id, data)
            else:
                self.production_id = productions_service.create(data)
            self.accept()
        except AppError as exc:
            show_error(self, exc)


class ProjectDialog(_BaseDialog):
    def __init__(self, parent=None, project_id: int | None = None, production_id: int | None = None):
        super().__init__(parent, "Проект")
        self.project_id = project_id
        self.name = QLineEdit()
        self.production = QComboBox()
        self.production.addItem("— не указан —", None)
        for production in productions_service.search(""):
            self.production.addItem(production["name"], production["id"])
        self.contacts = QLineEdit()
        self.extra = QPlainTextEdit()
        self.extra.setFixedHeight(90)
        self.form.addRow("Название проекта*:", self.name)
        self.form.addRow("Продакшен:", self.production)
        self.form.addRow("Контакты:", self.contacts)
        self.form.addRow("Дополнительно:", self.extra)

        if project_id:
            data = projects_service.get(project_id) or {}
            self.name.setText(data.get("name", ""))
            self.contacts.setText(data.get("contacts", ""))
            self.extra.setPlainText(data.get("extra", ""))
            production_id = data.get("production_id")
        if production_id:
            index = self.production.findData(production_id)
            self.production.setCurrentIndex(max(0, index))

    def save(self) -> None:
        data = {"name": self.name.text().strip(), "production_id": self.production.currentData(),
                "contacts": self.contacts.text().strip(), "extra": self.extra.toPlainText().strip()}
        try:
            if self.project_id:
                projects_service.update(self.project_id, data)
            else:
                self.project_id = projects_service.create(data)
            self.accept()
        except AppError as exc:
            show_error(self, exc)
