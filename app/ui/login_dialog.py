# -*- coding: utf-8 -*-
"""Экран входа и мастер первоначальной настройки (создание первого администратора)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QVBoxLayout, QWidget)

from PySide6.QtCore import QSize
from PySide6.QtGui import QPixmap

from ..errors import AppError
from ..services.auth_service import ROLE_LABELS, auth_service
from ..version import DISPLAY_NAME, VERSION
from .errors import show_error
from .theme import Color, Space
from .brand import mark_pixmap
from .widgets.common import Card, icon


class LoginDialog(QDialog):
    """Первый экран программы. Без правильного пароля дальше не пускает."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(DISPLAY_NAME)
        self.setModal(True)
        self.setFixedWidth(460)
        self.user = None
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        card = Card(padding=Space.XL, with_shadow=True)
        outer.addWidget(card)
        root = card.body
        root.setSpacing(Space.MD)

        mark = QLabel()
        mark.setPixmap(mark_pixmap(56) or icon("box", "active").pixmap(40, 40))
        mark.setAlignment(Qt.AlignCenter)
        root.addWidget(mark)

        title = QLabel(DISPLAY_NAME)
        title.setAlignment(Qt.AlignCenter)
        title.setObjectName("PageTitle")
        root.addWidget(title)

        subtitle = QLabel("Выберите пользователя и введите пароль")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setObjectName("Muted")
        root.addWidget(subtitle)
        root.addSpacing(Space.SM)

        form = QFormLayout()
        form.setSpacing(Space.MD)
        self.users = QComboBox()
        for user in auth_service.login_users():
            self.users.addItem(
                f"{user['full_name']}  ·  {ROLE_LABELS.get(user['role'], user['role'])}",
                user["login"])
        form.addRow("Пользователь:", self.users)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Пароль")
        self.password.returnPressed.connect(self._login)
        self.password.setMinimumHeight(40)
        self.users.setMinimumHeight(40)
        form.addRow("Пароль:", self.password)
        root.addLayout(form)

        self.error = QLabel("")
        self.error.setObjectName("ErrorText")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        root.addWidget(self.error)

        recover = QPushButton("Забыли пароль? Восстановить доступ")
        recover.setObjectName("Ghost")
        recover.setCursor(Qt.PointingHandCursor)
        recover.clicked.connect(self._recover)
        root.addWidget(recover, alignment=Qt.AlignCenter)

        buttons = QHBoxLayout()
        quit_btn = QPushButton("Выход")
        quit_btn.setObjectName("Ghost")
        quit_btn.clicked.connect(self.reject)
        buttons.addWidget(quit_btn)
        buttons.addStretch()
        self.login_btn = QPushButton("  Войти")
        self.login_btn.setObjectName("Primary")
        self.login_btn.setIcon(icon("logout", "white"))
        self.login_btn.setIconSize(QSize(18, 18))
        self.login_btn.setMinimumWidth(150)
        self.login_btn.setMinimumHeight(42)
        self.login_btn.setCursor(Qt.PointingHandCursor)
        self.login_btn.clicked.connect(self._login)
        buttons.addWidget(self.login_btn)
        root.addLayout(buttons)

        footer = QLabel(f"версия {VERSION}")
        footer.setAlignment(Qt.AlignCenter)
        footer.setObjectName("Hint")
        root.addWidget(footer)

        self.password.setFocus()

    def _login(self) -> None:
        login = self.users.currentData()
        if not login:
            self._show_error("В программе нет активных пользователей.")
            return
        try:
            self.user = auth_service.login(login, self.password.text())
        except AppError as exc:
            self._show_error(str(exc))
            self.password.selectAll()
            self.password.setFocus()
            return
        self.accept()

    def _recover(self) -> None:
        """
        Вход по коду восстановления. Сам диалог в программу не пускает —
        он только задаёт новый пароль, дальше обычный вход.
        """
        from .recovery_dialog import RecoveryLoginDialog
        dialog = RecoveryLoginDialog(self)
        if not dialog.exec() or not dialog.restored_login:
            return
        index = self.users.findData(dialog.restored_login)
        if index >= 0:
            self.users.setCurrentIndex(index)
        self.error.setVisible(False)
        self.password.clear()
        self.password.setFocus()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(True)


class FirstRunDialog(QDialog):
    """Мастер первого запуска: создаёт первого администратора. Пароль сохраняется хэшем."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{DISPLAY_NAME} — первый запуск")
        self.setModal(True)
        self.setFixedWidth(500)
        self.created = False
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        card = Card(padding=Space.XL, with_shadow=True)
        outer.addWidget(card)
        root = card.body
        root.setSpacing(Space.MD)

        title = QLabel("Создание администратора")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        hint = QLabel("В программе ещё нет пользователей. Создайте учётную запись "
                      "администратора — она понадобится для входа и настройки склада.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        root.addWidget(hint)

        form = QFormLayout()
        self.full_name = QLineEdit()
        self.full_name.setPlaceholderText("Например: Иван Петров")
        self.login = QLineEdit()
        self.login.setPlaceholderText("латиницей, например admin")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        self.confirm.returnPressed.connect(self._create)
        form.addRow("Имя / ФИО:", self.full_name)
        form.addRow("Логин:", self.login)
        form.addRow("Пароль:", self.password)
        form.addRow("Повторите пароль:", self.confirm)
        root.addLayout(form)

        self.error = QLabel("")
        self.error.setObjectName("ErrorText")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        root.addWidget(self.error)

        buttons = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        create = QPushButton("Создать и войти")
        create.setObjectName("Primary")
        create.setMinimumHeight(42)
        create.setCursor(Qt.PointingHandCursor)
        create.clicked.connect(self._create)
        buttons.addWidget(create)
        root.addLayout(buttons)

    def _create(self) -> None:
        if self.password.text() != self.confirm.text():
            self._show_error("Пароли не совпадают.")
            return
        try:
            auth_service.create_first_admin(
                self.full_name.text(), self.login.text(), self.password.text())
            auth_service.login(self.login.text().strip(), self.password.text())
        except AppError as exc:
            self._show_error(str(exc))
            return

        # Код восстановления показывается сразу и только здесь: если его не
        # сохранить сейчас, потерянный пароль станет потерянным складом.
        code = auth_service.pop_recovery_code()
        if code:
            from .recovery_dialog import RecoveryCodeDialog
            RecoveryCodeDialog(code, self.full_name.text().strip(), self,
                               first_run=True).exec()
        else:
            QMessageBox.warning(
                self, "Код восстановления не выдан",
                "Администратор создан, но код аварийного восстановления выдать не "
                "удалось. Выпустите его вручную: Настройки → Безопасность.")
        self.created = True
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(True)


def run_login(parent=None) -> bool:
    """Возвращает True, если пользователь успешно вошёл в программу."""
    if not auth_service.has_users():
        wizard = FirstRunDialog(parent)
        return bool(wizard.exec() and wizard.created)
    dialog = LoginDialog(parent)
    return bool(dialog.exec() and dialog.user)
