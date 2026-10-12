# -*- coding: utf-8 -*-
"""
Список подсказок под полем ввода.

Нужен там, где пользователь набирает текст и хочет увидеть, что найдётся, не
нажимая Enter: поиск реквизита, подбор клиента.

Это обычный виджет, который встраивается в раскладку под полем, а не
всплывающее окно. Всплывающие пробовались дважды и оба раза вели себя плохо:
окно с флагом Qt.Popup перехватывает клавиатуру и на macOS отбирало фокус у
поля, а дочернее окно поверх раскладки на Windows уходило под соседние
виджеты. Встроенный блок занимает место в раскладке — уйти за что-то он не
может по определению.

Пока подсказок нет, блок скрыт и места не занимает. Запросы делаются с
небольшой паузой после последней буквы: на складе в десять тысяч позиций
запрос на каждое нажатие заметно тормозит.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..theme import Color, Space

TYPING_PAUSE_MS = 220        # пауза после последней буквы перед запросом
MIN_CHARS = 2
MAX_ROWS = 6
ROW_HEIGHT = 52


class SuggestList(QFrame):
    """
    Подсказки под полем.

    Источник данных — функция, принимающая текст и возвращающая список
    словарей с ключами: title (обязательно), subtitle, icon_path и любыми
    своими полями, которые вернутся обратно в сигнале chosen.

    Виджет добавляется в раскладку сразу под полем:

        self.suggest = SuggestList(self.search, self._find)
        layout.addWidget(self.suggest)
    """

    chosen = Signal(object)

    def __init__(self, field: QLineEdit, source: Callable[[str], list[dict]],
                 parent=None, empty_text: str = "Ничего не найдено"):
        super().__init__(parent)
        self.field = field
        self.source = source
        self.empty_text = empty_text
        self._rows: list[dict] = []

        self.setObjectName("SuggestList")
        self.setStyleSheet(
            f"#SuggestList {{ background:{Color.SURFACE}; "
            f"border:1px solid {Color.BORDER}; border-radius:10px; }}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM, Space.SM, Space.SM, Space.SM)
        self.list = QListWidget()
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setIconSize(QSize(40, 40))
        self.list.setUniformItemSizes(False)
        # Список не забирает фокус: щелчок по строке не выдёргивает курсор из
        # поля, поэтому можно кликнуть подсказку и продолжить печатать.
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.itemClicked.connect(self._pick)
        layout.addWidget(self.list)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TYPING_PAUSE_MS)
        self._timer.timeout.connect(self._reload)

        field.textEdited.connect(self._schedule)
        field.installEventFilter(self)

    # -------------------------------------------------------------- ввод
    def _schedule(self, text: str) -> None:
        if len(text.strip()) < MIN_CHARS:
            self.close_list()
            return
        self._timer.start()

    def _reload(self) -> None:
        text = self.field.text().strip()
        if len(text) < MIN_CHARS:
            self.close_list()
            return
        try:
            rows = self.source(text) or []
        except Exception:                                          # noqa: BLE001
            self.close_list()
            return

        self._rows = rows[:MAX_ROWS]
        self.list.clear()
        for row in self._rows:
            title = row.get("title", "")
            subtitle = row.get("subtitle", "")
            entry = QListWidgetItem(f"{title}\n{subtitle}" if subtitle else title)
            if row.get("icon_path"):
                entry.setIcon(QIcon(row["icon_path"]))
            self.list.addItem(entry)
        if not self._rows:
            entry = QListWidgetItem(self.empty_text)
            entry.setFlags(Qt.NoItemFlags)
            self.list.addItem(entry)

        self.list.setCurrentRow(0 if self._rows else -1)
        rows_shown = max(1, self.list.count())
        self.setFixedHeight(min(MAX_ROWS, rows_shown) * ROW_HEIGHT + 2 * Space.SM)
        self.show()

    def close_list(self) -> None:
        self._timer.stop()
        self._rows = []
        self.list.clear()
        self.hide()

    # ------------------------------------------------------------- выбор
    def _pick(self, entry: Optional[QListWidgetItem] = None) -> None:
        index = self.list.row(entry) if entry is not None else self.list.currentRow()
        if 0 <= index < len(self._rows):
            row = self._rows[index]
            self.close_list()
            self.chosen.emit(row)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:   # noqa: N802
        """Стрелки и Enter управляют списком, пока он открыт."""
        if watched is not self.field or event.type() != QEvent.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        if key == Qt.Key_Escape and self.isVisible():
            self.close_list()
            return True
        if not self.isVisible() or not self._rows:
            return super().eventFilter(watched, event)
        if key in (Qt.Key_Down, Qt.Key_Up):
            step = 1 if key == Qt.Key_Down else -1
            self.list.setCurrentRow((self.list.currentRow() + step) % len(self._rows))
            return True
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._pick()
            return True
        return super().eventFilter(watched, event)

