# -*- coding: utf-8 -*-
"""Показ ошибок пользователю: человеческий текст, техника — в лог."""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

from ..errors import AppError
from ..paths import LOGS_DIR

log = logging.getLogger(__name__)


def show_error(parent, exc: Exception, context: str = "") -> None:
    if isinstance(exc, AppError):
        QMessageBox.warning(parent, "Не получилось", str(exc))
        return
    log.exception("Ошибка%s", f" ({context})" if context else "")
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Critical)
    box.setWindowTitle("Ошибка")
    box.setText(context or "Операцию выполнить не удалось.")
    box.setInformativeText(
        f"Технические подробности записаны в журнал:\n{LOGS_DIR / 'app.log'}")
    box.exec()


def show_fatal(exc: Exception) -> None:
    try:
        QMessageBox.critical(
            None, "Ошибка приложения",
            "Произошла непредвиденная ошибка. Данные не потеряны.\n"
            f"Подробности записаны в журнал:\n{LOGS_DIR / 'app.log'}")
    except Exception:
        pass


def confirm(parent, question: str, title: str = "Подтверждение") -> bool:
    return QMessageBox.question(parent, title, question,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


def info(parent, text: str, title: str = "Готово") -> None:
    QMessageBox.information(parent, title, text)
