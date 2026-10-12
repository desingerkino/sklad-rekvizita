# -*- coding: utf-8 -*-
"""
Аварийное восстановление доступа: показ кода и вход по коду.

Код показывается ровно один раз — окно построено так, чтобы его нельзя было
случайно проскочить: кнопка «Готово» разблокируется только после того, как
пользователь подтвердит, что сохранил код.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout)

from .. import paths
from ..errors import AppError
from ..services.recovery_service import recovery_service
from ..version import DISPLAY_NAME
from .errors import info, show_error
from .theme import Color, Font, Space
from .widgets.common import Card, icon, muted


def _open_file(path: Path) -> None:
    """Открывает готовый файл в программе просмотра — оттуда пользователь печатает."""
    try:
        if os.name == "nt":
            os.startfile(str(path))              # noqa: S606 — штатное открытие в Windows
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception:
        pass


class RecoveryCodeDialog(QDialog):
    """
    Показ кода восстановления. Единственное место, где код виден целиком.

    Файл с кодом программа сама никуда не сохраняет: пользователь выбирает,
    куда его положить, и отвечает за это место. В папке данных склада такой
    файл лежать не должен — иначе секрет теряет смысл.
    """

    def __init__(self, code: str, user_name: str = "", parent=None, first_run: bool = False):
        super().__init__(parent)
        self.code = code
        self.user_name = user_name
        self.first_run = first_run
        self.setWindowTitle("Код восстановления")
        self.setModal(True)
        self.setFixedWidth(560)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        card = Card(padding=Space.XL, with_shadow=True)
        outer.addWidget(card)
        root = card.body
        root.setSpacing(Space.MD)

        mark = QLabel()
        mark.setPixmap(icon("alert", "active").pixmap(36, 36))
        mark.setAlignment(Qt.AlignCenter)
        root.addWidget(mark)

        title = QLabel("КОД ВОССТАНОВЛЕНИЯ")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        value = QLabel(self.code)
        value.setAlignment(Qt.AlignCenter)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        value.setStyleSheet(
            f"font-size:{Font.H1 + 4}pt; font-weight:700; letter-spacing:3px; "
            f"color:{Color.TEXT}; background:{Color.SURFACE_ALT}; "
            f"border:1px solid {Color.BORDER}; border-radius:8px; padding:14px;")
        root.addWidget(value)

        warning = QLabel(
            "Сохраните этот код в безопасном месте. Он показывается только один раз.\n\n"
            "Если вы потеряете пароль администратора и код восстановления, "
            "восстановление доступа может быть невозможно.")
        warning.setWordWrap(True)
        warning.setAlignment(Qt.AlignCenter)
        warning.setObjectName("ErrorText")
        root.addWidget(warning)

        root.addWidget(muted(
            "Код нужен только для одного: задать новый пароль администратора, "
            "если старый забыт. Ничего другого сделать по нему нельзя."))

        actions = QHBoxLayout()
        actions.setSpacing(Space.MD)
        self.print_btn = QPushButton("  Распечатать код восстановления")
        self.print_btn.setObjectName("Primary")
        self.print_btn.setIcon(icon("printer", "white"))
        self.print_btn.setIconSize(QSize(18, 18))
        self.print_btn.setMinimumHeight(44)
        self.print_btn.setCursor(Qt.PointingHandCursor)
        self.print_btn.clicked.connect(self._print)
        actions.addWidget(self.print_btn)

        self.save_btn = QPushButton("  Сохранить в файл…")
        self.save_btn.setIcon(icon("save"))
        self.save_btn.setIconSize(QSize(18, 18))
        self.save_btn.setMinimumHeight(44)
        self.save_btn.clicked.connect(self._save)
        actions.addWidget(self.save_btn)
        root.addLayout(actions)

        self.confirmed = QCheckBox("Я записал(а) код и убрал(а) его в надёжное место")
        self.confirmed.toggled.connect(self._toggle_done)
        root.addWidget(self.confirmed)

        footer = QHBoxLayout()
        footer.addStretch()
        self.done_btn = QPushButton("Готово")
        self.done_btn.setObjectName("Primary")
        self.done_btn.setMinimumHeight(42)
        self.done_btn.setMinimumWidth(140)
        self.done_btn.setEnabled(False)
        self.done_btn.clicked.connect(self.accept)
        footer.addWidget(self.done_btn)
        root.addLayout(footer)

    def _toggle_done(self, checked: bool) -> None:
        self.done_btn.setEnabled(bool(checked))

    def _document(self, target: Path) -> Path:
        return Path(recovery_service.code_document_pdf(self.code, target, self.user_name))

    def _print(self) -> None:
        """Готовит лист и открывает его — печать идёт из программы просмотра."""
        try:
            path = self._document(paths.TEMP_DIR / "recovery_code.pdf")
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Не удалось подготовить лист с кодом.")
            return
        _open_file(path)
        self.confirmed.setChecked(True)

    def _save(self) -> None:
        target, _ = QFileDialog.getSaveFileName(
            self, "Сохранить код восстановления", "Код восстановления.pdf", "PDF (*.pdf)")
        if not target:
            return
        try:
            self._document(Path(target))
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Не удалось сохранить файл.")
            return
        info(self, f"Файл сохранён:\n{target}\n\n"
                   "Положите его в сейф или на отдельный носитель, а не в папку программы.")
        self.confirmed.setChecked(True)

    def closeEvent(self, event) -> None:                            # noqa: N802 — метод Qt
        """Закрыть окно, не подтвердив сохранение кода, нельзя."""
        if self.confirmed.isChecked():
            event.accept()
            return
        event.ignore()
        info(self, "Сначала сохраните или распечатайте код, затем отметьте галочку.",
             "Код ещё не сохранён")


class RecoveryLoginDialog(QDialog):
    """
    Вход по коду восстановления: код → новый пароль → новый код.

    Диалог не пускает в программу сам по себе. Он только меняет пароль
    администратора; дальше пользователь входит обычным способом.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{DISPLAY_NAME} — восстановление доступа")
        self.setModal(True)
        self.setFixedWidth(520)
        self.restored_login = ""
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        card = Card(padding=Space.XL, with_shadow=True)
        outer.addWidget(card)
        root = card.body
        root.setSpacing(Space.MD)

        title = QLabel("Восстановление доступа")
        title.setObjectName("PageTitle")
        root.addWidget(title)
        root.addWidget(muted(
            "Введите код восстановления, который вы получили при создании "
            "администратора, и задайте новый пароль. Данные склада не пострадают."))

        form = QFormLayout()
        form.setSpacing(Space.MD)
        self.code = QLineEdit()
        self.code.setPlaceholderText("код вида ХХХХ-ХХХХ-ХХХХ-ХХХХ")
        self.code.setMinimumHeight(42)
        self.code.textEdited.connect(self._format_code)
        form.addRow("Код восстановления:", self.code)

        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("не короче 4 символов")
        self.password.setMinimumHeight(40)
        form.addRow("Новый пароль:", self.password)

        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        self.confirm.setMinimumHeight(40)
        self.confirm.returnPressed.connect(self._restore)
        form.addRow("Повторите пароль:", self.confirm)
        root.addLayout(form)

        self.error = QLabel("")
        self.error.setObjectName("ErrorText")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        root.addWidget(self.error)

        buttons = QHBoxLayout()
        cancel = QPushButton("Отмена")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addStretch()
        self.restore_btn = QPushButton("Восстановить доступ")
        self.restore_btn.setObjectName("Primary")
        self.restore_btn.setMinimumHeight(42)
        self.restore_btn.setMinimumWidth(200)
        self.restore_btn.setCursor(Qt.PointingHandCursor)
        self.restore_btn.clicked.connect(self._restore)
        buttons.addWidget(self.restore_btn)
        root.addLayout(buttons)

        self.code.setFocus()

    def _format_code(self, text: str) -> None:
        """Расставляет дефисы за пользователя: код можно вводить как угодно."""
        formatted = recovery_service.normalize(text)
        if formatted != text:
            position = self.code.cursorPosition() + (len(formatted) - len(text))
            self.code.setText(formatted)
            self.code.setCursorPosition(max(0, min(position, len(formatted))))

    def _restore(self) -> None:
        if self.password.text() != self.confirm.text():
            self._show_error("Пароли не совпадают.")
            return
        self.restore_btn.setEnabled(False)
        try:
            result = recovery_service.reset_password(self.code.text(), self.password.text())
        except AppError as exc:
            self._show_error(str(exc))
            self.restore_btn.setEnabled(True)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Восстановить доступ не удалось.")
            self.restore_btn.setEnabled(True)
            return

        self.restored_login = result.login
        RecoveryCodeDialog(result.new_code, result.full_name, self).exec()
        info(self,
             f"Пароль администратора «{result.login}» изменён.\n"
             "Войдите в программу с новым паролем.",
             "Доступ восстановлен")
        self.accept()

    def _show_error(self, text: str) -> None:
        self.error.setText(text)
        self.error.setVisible(True)
