# -*- coding: utf-8 -*-
"""
Поле ввода для сканера штрихкодов.

Складские сканеры (USB и Bluetooth) работают как обычная клавиатура: быстро
«печатают» код и жмут Enter. Специальный драйвер не нужен — нужно только,
чтобы курсор всегда стоял в правильном поле.

Поле само возвращает себе фокус после каждого сканирования: оператор держит
предмет в одной руке и сканер в другой, мышкой щёлкать ему нечем.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from ..theme import Color, Space
from .common import icon

TONE_STYLES = {
    "ok": Color.SUCCESS,
    "warn": Color.WARNING,
    "error": Color.DANGER,
    "info": Color.TEXT_SECONDARY,
}


class ScanInput(QWidget):
    """
    Строка сканирования с полем ввода и строкой результата.

    Сигнал scanned отдаёт очищенный код. Ошибок сам не показывает —
    что делать с кодом, решает экран, который этот виджет использует.
    """

    scanned = Signal(str)

    def __init__(self, placeholder: str = "Отсканируйте штрихкод или введите инвентарный номер",
                 parent=None):
        super().__init__(parent)
        self._build(placeholder)
        self._clear_timer = QTimer(self)
        self._clear_timer.setSingleShot(True)
        self._clear_timer.setInterval(6000)
        self._clear_timer.timeout.connect(self._fade_result)

    def _build(self, placeholder: str) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(Space.MD)

        self.field = QLineEdit()
        self.field.setObjectName("SearchBox")
        self.field.setPlaceholderText(placeholder)
        self.field.addAction(icon("tag"), QLineEdit.LeadingPosition)
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumHeight(46)
        self.field.returnPressed.connect(self._emit)
        root.addWidget(self.field, 1)

        self.button = QPushButton("  Найти")
        self.button.setIcon(icon("search"))
        self.button.setMinimumHeight(46)
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.clicked.connect(self._emit)
        root.addWidget(self.button)

        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setMinimumWidth(280)
        root.addWidget(self.result, 1)

    # ---------------------------------------------------------------- ввод
    def _emit(self) -> None:
        code = self.field.text().strip()
        if not code:
            return
        self.field.clear()
        self.scanned.emit(code)
        self.take_focus()

    def take_focus(self) -> None:
        """Возвращает курсор в поле: сканер печатает туда, где фокус."""
        self.field.setFocus()
        self.field.selectAll()

    # -------------------------------------------------------------- ответ
    def show_result(self, text: str, tone: str = "info") -> None:
        self.result.setText(text)
        self.result.setStyleSheet(f"color:{TONE_STYLES.get(tone, Color.TEXT_SECONDARY)};")
        self._clear_timer.start()

    def _fade_result(self) -> None:
        self.result.setText("")
