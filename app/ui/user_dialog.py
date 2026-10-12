# -*- coding: utf-8 -*-
"""Карточка пользователя: имя, логин, роль, пароль, состояние."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QLineEdit, QVBoxLayout)

from ..errors import AppError
from ..services.auth_service import ROLE_ADMIN, ROLE_LABELS, ROLE_USER, auth_service
from .errors import show_error
from .widgets.common import scrollable_body


class UserDialog(QDialog):
    def __init__(self, parent=None, user_id: int | None = None):
        super().__init__(parent)
        self.user_id = user_id
        self.setWindowTitle("Пользователь")
        self.resize(460, 300)

        root, footer = scrollable_body(self)
        form = QFormLayout()
        self.full_name = QLineEdit()
        self.login = QLineEdit()
        self.position = QLineEdit()
        self.position.setPlaceholderText("например: Реквизитор")
        self.role = QComboBox()
        for role in (ROLE_USER, ROLE_ADMIN):
            self.role.addItem(ROLE_LABELS[role], role)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        self.is_active = QCheckBox("Учётная запись активна")
        self.is_active.setChecked(True)

        form.addRow("Имя / ФИО*:", self.full_name)
        form.addRow("Логин*:", self.login)
        form.addRow("Должность:", self.position)
        form.addRow("Роль в программе:", self.role)
        form.addRow("Пароль*:", self.password)
        form.addRow("Повторите пароль:", self.confirm)
        form.addRow("", self.is_active)
        root.addLayout(form)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText("Сохранить")
        box.button(QDialogButtonBox.Save).setObjectName("Primary")
        box.button(QDialogButtonBox.Cancel).setText("Отмена")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        root.addStretch()
        footer.addWidget(box)

        if user_id:
            user = auth_service.get_user(user_id) or {}
            self.full_name.setText(user.get("full_name", ""))
            self.position.setText(user.get("position", ""))
            self.login.setText(user.get("login", ""))
            self.login.setReadOnly(True)
            self.role.setCurrentIndex(max(0, self.role.findData(user.get("role", ROLE_USER))))
            self.is_active.setChecked(bool(user.get("is_active", 1)))
            self.password.setPlaceholderText("оставьте пустым, чтобы не менять")

    def _save(self) -> None:
        if self.password.text() and self.password.text() != self.confirm.text():
            show_error(self, AppError("Пароли не совпадают."))
            return
        try:
            if self.user_id:
                auth_service.update_user(self.user_id, self.full_name.text(),
                                         self.role.currentData(), self.is_active.isChecked())
                parts = self.full_name.text().split()
                auth_service.update_profile(
                    self.user_id, " ".join(parts[1:]), parts[0] if parts else "",
                    self.position.text())
                if self.password.text():
                    auth_service.set_password(self.user_id, self.password.text())
            else:
                self.user_id = auth_service.create_user(
                    self.full_name.text(), self.login.text(), self.password.text(),
                    self.role.currentData(), self.position.text())
                if not self.is_active.isChecked():
                    auth_service.update_user(self.user_id, is_active=False)
            self.accept()
        except AppError as exc:
            show_error(self, exc)
