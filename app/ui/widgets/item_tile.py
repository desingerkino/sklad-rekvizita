# -*- coding: utf-8 -*-
"""Карточка реквизита в каталоге-плитке: фотография, название, статус, цена."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ...utils import money_plain
from ...version import RENTAL_PERIOD_DAYS
from ..theme import Color, Space
from .common import Card, PhotoLabel, StatusBadge, fade_in

TILE_WIDTH = 236
PHOTO_HEIGHT = 168


def availability_status(item: dict) -> tuple[str, str]:
    """Статус позиции по единой системе 2.3.1: в наличии / в аренде / в ремонте."""
    from ...services.statuses import item_status
    return item_status(item)


class ItemTile(Card):
    """Кликабельная карточка предмета."""

    clicked = Signal(int)
    double_clicked = Signal(int)

    def __init__(self, item: dict, photo_path: str | None = None, parent=None):
        super().__init__(parent, padding=Space.MD)
        self.item_id = item["id"]
        self.setFixedWidth(TILE_WIDTH)
        self.setCursor(Qt.PointingHandCursor)
        self.body.setSpacing(Space.SM)

        self.photo = PhotoLabel(TILE_WIDTH - 2 * Space.MD)
        self.photo.setFixedHeight(PHOTO_HEIGHT)
        if photo_path:
            self.photo.set_photo(photo_path)
        self.body.addWidget(self.photo)

        name = QLabel(item["name"])
        name.setWordWrap(True)
        name.setStyleSheet("font-weight: 600;")
        name.setFixedHeight(52)                # длинное название не растягивает плитку
        name.setAlignment(Qt.AlignTop)
        name.setToolTip(item["name"])
        self.body.addWidget(name)

        category = QLabel(item.get("category_path") or "без категории")
        category.setObjectName("Hint")
        category.setWordWrap(False)
        category.setFixedHeight(18)
        category.setToolTip(item.get("category_path") or "")
        self.body.addWidget(category)

        code, label = availability_status(item)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        badge = StatusBadge(code, label)
        row.addWidget(badge)
        row.addStretch()
        self.body.addLayout(row)

        price = QLabel(f"{money_plain(item['rental_price'])} ₽")
        price.setStyleSheet(f"font-weight: 700; color: {Color.TEXT};")
        period = QLabel(f"за {RENTAL_PERIOD_DAYS} дн.")
        period.setObjectName("Hint")
        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(6)
        price_row.addWidget(price)
        price_row.addWidget(period)
        price_row.addStretch()
        inv = QLabel(f"№ {item['inv_number']}")
        inv.setObjectName("Hint")
        price_row.addWidget(inv)
        self.body.addLayout(price_row)

        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._selected = False
        self._apply_style()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def _apply_style(self) -> None:
        border = Color.PRIMARY if self._selected else Color.BORDER
        background = Color.PRIMARY_SOFT if self._selected else Color.SURFACE
        self.setStyleSheet(
            f"#Card {{ background: {background}; border: 1px solid {border};"
            f" border-radius: 14px; }}")

    def enterEvent(self, event):
        if not self._selected:
            self.setStyleSheet(
                f"#Card {{ background: {Color.SURFACE}; border: 1px solid {Color.BORDER_STRONG};"
                f" border-radius: 14px; }}")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        self.clicked.emit(self.item_id)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self.item_id)
        super().mouseDoubleClickEvent(event)

    def show_animated(self) -> None:
        fade_in(self)
