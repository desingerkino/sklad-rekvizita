# -*- coding: utf-8 -*-
"""Блокировка программы после бездействия: продолжить работу можно только по паролю."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from ..services.auth_service import auth_service
from ..version import DISPLAY_NAME
from .theme import Color, Space
from .widgets.common import Card, icon


class LockDialog(QDialog):
    """
    Модальный экран блокировки. Закрыть его можно только правильным паролем
    или выходом из учётной записи — данные открытой сессии остаются недоступны.
    """

    def __init__(self, parent=None, minutes: int = 15):
        super().__init__(parent)
        self.setWindowTitle(f"{DISPLAY_NAME} — сеанс заблокирован")
        self.setModal(True)
        self.setFixedWidth(460)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.logout_requested = False
        self._build(minutes)

    def _build(self, minutes: int) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        card = Card(padding=Space.XL, with_shadow=True)
        outer.addWidget(card)
        root = card.body

        mark = QLabel()
        mark.setPixmap(icon("logout", "active").pixmap(34, 34))
        mark.setAlignment(Qt.AlignCenter)
        root.addWidget(mark)

        title = QLabel("Сеанс заблокирован")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        user = auth_service.current_user
        subtitle = QLabel(
            f"Программа была без действий более {minutes} мин.\n"
            f"Чтобы продолжить работу, введите пароль "
            f"{user.display_name if user else ''}.")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        subtitle.setObjectName("Muted")
        root.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(Space.MD)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setMinimumHeight(40)
        self.password.returnPressed.connect(self._unlock)
        form.addRow("Пароль:", self.password)
        root.addLayout(form)

        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {Color.DANGER};")
        self.error.setVisible(False)
        root.addWidget(self.error)

        buttons = QHBoxLayout()
        logout = QPushButton("Сменить пользователя")
        logout.setObjectName("Ghost")
        logout.clicked.connect(self._logout)
        buttons.addWidget(logout)
        buttons.addStretch()
        unlock = QPushButton("  Продолжить")
        unlock.setObjectName("Primary")
        unlock.setIcon(icon("check", "white"))
        unlock.setIconSize(QSize(18, 18))
        unlock.setMinimumHeight(42)
        unlock.clicked.connect(self._unlock)
        buttons.addWidget(unlock)
        root.addLayout(buttons)
        self.password.setFocus()

    def _unlock(self) -> None:
        if auth_service.verify_current_password(self.password.text()):
            self.accept()
            return
        self.password.clear()
        self.error.setText("Неверный пароль.")
        self.error.setVisible(True)

    def _logout(self) -> None:
        self.logout_requested = True
        self.reject()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            return                      # блокировку нельзя закрыть клавишей Esc
        super().keyPressEvent(event)
