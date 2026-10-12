# -*- coding: utf-8 -*-
"""
Адаптивная раскладка карточек: элементы переносятся на новую строку сами.

Нужна для каталога-плитки — при изменении ширины окна карточки
перестраиваются, горизонтальная прокрутка не появляется.
"""
from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin: int = 0, spacing: int = 16):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(QMargins(margin, margin, margin, margin))

    def addItem(self, item) -> None:          # noqa: N802 — интерфейс Qt
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index):                  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):                  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def clear(self) -> None:
        while self.count():
            item = self.takeAt(0)
            widget = item.widget() if item else None
            if widget:
                widget.setParent(None)
                widget.deleteLater()

    def expandingDirections(self):             # noqa: N802
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:       # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:   # noqa: N802
        return self._layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:    # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, test_only=False)

    def sizeHint(self) -> QSize:               # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:            # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(),
                            margins.top() + margins.bottom())

    def _layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(margins.left(), margins.top(),
                                  -margins.right(), -margins.bottom())
        x, y, line_height = effective.x(), effective.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()
