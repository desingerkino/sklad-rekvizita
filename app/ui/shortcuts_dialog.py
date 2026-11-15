# -*- coding: utf-8 -*-
"""Справка по горячим клавишам (F1). На macOS вместо Ctrl показывается ⌘."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .theme import Space
from .widgets.common import section_title

# (клавиши, действие). Записаны как на Windows; для macOS подставляется ⌘.
SHORTCUTS = [
    ("Навигация", [
        ("Ctrl+1 … Ctrl+5", "Главная, Каталог, Новая аренда, Аренды, Возвраты"),
        ("Ctrl+F", "Каталог и поле поиска"),
        ("Ctrl+K или F3", "Быстрый поиск реквизита в шапке"),
        ("Ctrl+N", "Новая аренда"),
        ("Ctrl+R", "Список аренд"),
        ("Ctrl+P", "Отчёты"),
        ("F5", "Обновить раздел"),
        ("F1", "Это окно"),
    ]),
    ("Каталог", [
        ("Enter", "Открыть карточку выбранного предмета"),
        ("Ctrl+щелчок, Shift+щелчок", "Выбрать несколько предметов"),
        ("Ctrl+A", "Выбрать всё найденное"),
        ("Ctrl+D", "Добавить в избранное / убрать"),
        ("Ctrl+I", "Новая карточка реквизита"),
        ("Ctrl+Shift+F", "Поиск по фотографии"),
        ("Delete", "Списать выбранное (с подтверждением)"),
        ("Esc", "Снять выделение"),
    ]),
    ("Общее", [
        ("Ctrl+S", "Сохранить всё на диск"),
        ("Ctrl+V", "В поиске по фото — вставить картинку из буфера"),
    ]),
]


def platform_keys(keys: str) -> str:
    """Подписи клавиш для текущей системы."""
    if sys.platform != "darwin":
        return keys
    return (keys.replace("Ctrl+", "⌘").replace("Shift+", "⇧").replace("Delete", "Fn+⌫")
            .replace("Enter", "Return"))


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Горячие клавиши")
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)
        for title, rows in SHORTCUTS:
            root.addWidget(section_title(title))
            grid = QGridLayout()
            grid.setHorizontalSpacing(Space.LG)
            grid.setVerticalSpacing(Space.XS + 2)
            grid.setColumnStretch(1, 1)
            for row, (keys, action) in enumerate(rows):
                key_label = QLabel(platform_keys(keys))
                key_label.setObjectName("Pill")
                key_label.setAlignment(Qt.AlignCenter)
                grid.addWidget(key_label, row, 0, Qt.AlignLeft)
                grid.addWidget(QLabel(action), row, 1)
            root.addLayout(grid)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Закрыть")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)
