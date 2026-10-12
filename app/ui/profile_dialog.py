# -*- coding: utf-8 -*-
"""Профиль сотрудника: имя, фамилия, должность, аватар и смена пароля."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout)

from ..errors import AppError
from ..services.auth_service import auth_service
from .errors import confirm, info, show_error
from .theme import Color, Space
from .widgets.common import (Card, PhotoLabel, StatusBadge, hint, icon,
                             scrollable_body, section_title)


class ProfileDialog(QDialog):
    """Своя карточка сотрудника. Роль здесь не меняется — это права, а не должность."""

    def __init__(self, parent=None, user_id: int | None = None):
        super().__init__(parent)
        self.user_id = user_id or auth_service.current_id()
        self.setWindowTitle("Профиль сотрудника")
        self.setFixedWidth(560)
        self._build()
        self._load()

    def _build(self) -> None:
        root, footer = scrollable_body(self)

        header = QHBoxLayout()
        header.setSpacing(Space.LG)
        self.avatar = PhotoLabel(120, radius=60)
        header.addWidget(self.avatar)

        column = QVBoxLayout()
        column.setSpacing(Space.SM)
        self.name_label = QLabel()
        self.name_label.setObjectName("PageTitle")
        column.addWidget(self.name_label)
        self.position_label = QLabel()
        self.position_label.setObjectName("Muted")
        column.addWidget(self.position_label)
        self.role_badge_row = QHBoxLayout()
        self.role_badge_row.addStretch()
        column.addLayout(self.role_badge_row)

        buttons = QHBoxLayout()
        upload = QPushButton("  Загрузить фото")
        upload.setIcon(icon("camera"))
        upload.clicked.connect(self._upload)
        buttons.addWidget(upload)
        self.remove_btn = QPushButton("Удалить")
        self.remove_btn.setObjectName("Danger")
        self.remove_btn.clicked.connect(self._remove)
        buttons.addWidget(self.remove_btn)
        buttons.addStretch()
        column.addLayout(buttons)
        header.addLayout(column, 1)
        root.addLayout(header)

        root.addWidget(section_title("Данные сотрудника"))
        form = QFormLayout()
        form.setSpacing(Space.MD)
        self.first_name = QLineEdit()
        self.last_name = QLineEdit()
        self.position = QLineEdit()
        self.position.setPlaceholderText("например: Художник по реквизиту")
        form.addRow("Имя:", self.first_name)
        form.addRow("Фамилия:", self.last_name)
        form.addRow("Должность:", self.position)
        self.login_label = QLabel()
        form.addRow("Логин:", self.login_label)
        self.role_label = QLabel()
        form.addRow("Роль в программе:", self.role_label)
        root.addLayout(form)
        root.addWidget(hint("Должность — что сотрудник делает на складе. Роль — его права "
                            "в программе; её меняет только администратор в разделе "
                            "«Пользователи»."))

        root.addWidget(section_title("Смена пароля"))
        password_form = QFormLayout()
        password_form.setSpacing(Space.MD)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("оставьте пустым, чтобы не менять")
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        password_form.addRow("Новый пароль:", self.password)
        password_form.addRow("Повторите:", self.confirm)
        root.addLayout(password_form)

        root.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        save = QPushButton("  Сохранить")
        save.setObjectName("Primary")
        save.setIcon(icon("check", "white"))
        save.setMinimumHeight(42)
        save.clicked.connect(self._save)
        actions.addWidget(save)
        footer.addLayout(actions)

    def _load(self) -> None:
        user = auth_service.get_user(self.user_id)
        if not user:
            return
        self.first_name.setText(user["first_name"])
        self.last_name.setText(user["last_name"])
        self.position.setText(user["position"])
        self.login_label.setText(user["login"])
        from ..services.auth_service import ROLE_LABELS
        self.role_label.setText(ROLE_LABELS.get(user["role"], user["role"]))
        self._refresh_header(user)

    def _refresh_header(self, user: dict) -> None:
        name = f"{user['first_name']} {user['last_name']}".strip() or user["full_name"]
        self.name_label.setText(name)
        self.position_label.setText(user["position"] or "должность не указана")
        while self.role_badge_row.count():
            item = self.role_badge_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        from ..services.auth_service import ROLE_LABELS
        tone = "info" if user["role"] == "admin" else "returned"
        self.role_badge_row.addWidget(
            StatusBadge(tone, ROLE_LABELS.get(user["role"], user["role"])))
        self.role_badge_row.addStretch()
        path = auth_service.avatar_file(self.user_id)
        if path:
            self.avatar.set_photo(str(path))
        else:
            self.avatar.clear_photo()
        self.remove_btn.setEnabled(bool(path))

    def _upload(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите фотографию", "", "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.heic *.heif)")
        if not path:
            return
        try:
            auth_service.set_avatar(self.user_id, path)
        except AppError as exc:
            show_error(self, exc)
            return
        self._refresh_header(auth_service.get_user(self.user_id))

    def _remove(self) -> None:
        if not confirm(self, "Удалить фотографию профиля?"):
            return
        try:
            auth_service.delete_avatar(self.user_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self._refresh_header(auth_service.get_user(self.user_id))

    def _save(self) -> None:
        if self.password.text() and self.password.text() != self.confirm.text():
            show_error(self, AppError("Пароли не совпадают."))
            return
        try:
            auth_service.update_profile(
                self.user_id, self.first_name.text(), self.last_name.text(), self.position.text())
            if self.password.text():
                auth_service.set_password(self.user_id, self.password.text())
        except AppError as exc:
            show_error(self, exc)
            return
        info(self, "Профиль сохранён.")
        self.accept()
