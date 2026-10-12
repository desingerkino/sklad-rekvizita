# -*- coding: utf-8 -*-
"""Переиспользуемые элементы интерфейса 2.1: карточки, статусы, иконки, таблицы."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QDate, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDateEdit, QFrame, QGraphicsDropShadowEffect,
                               QGraphicsOpacityEffect, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ... import paths
from ..theme import Color, Duration, Radius, Space, status_tone

DATE_FORMAT = "dd.MM.yyyy"


# ------------------------------------------------------------------ иконки
def icon(name: str, variant: str = "") -> QIcon:
    """Иконка из единого набора: icon('search'), icon('plus', 'white')."""
    suffix = f"_{variant}" if variant else ""
    path = paths.assets_dir() / "icons" / f"{name}{suffix}.svg"
    return QIcon(str(path)) if path.exists() else QIcon()


def icon_button(name: str, tooltip: str = "", size: int = 20, variant: str = "") -> QPushButton:
    button = QPushButton()
    button.setObjectName("IconButton")
    button.setIcon(icon(name, variant))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tooltip)
    button.setCursor(Qt.PointingHandCursor)
    return button


def shadow(widget: QWidget, blur: int = 24, y: int = 4, alpha: int = 22) -> None:
    """Мягкая тень карточек — как в референсе."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(15, 23, 42, alpha))
    widget.setGraphicsEffect(effect)


def fade_in(widget: QWidget, duration: int = Duration.NORMAL) -> None:
    """Короткое плавное появление — используется при смене разделов."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.finished.connect(lambda: widget.setGraphicsEffect(None))
    animation.start(QPropertyAnimation.DeleteWhenStopped)
    widget._fade_animation = animation      # держим ссылку, иначе анимация оборвётся


# ------------------------------------------------------------------ прокрутка
def scroll_area(content: QWidget, horizontal: bool = False) -> "QScrollArea":
    """
    Оборачивает содержимое в область прокрутки: контент любой высоты
    всегда можно пролистать до последнего элемента.
    """
    from PySide6.QtWidgets import QScrollArea
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(
        Qt.ScrollBarAsNeeded if horizontal else Qt.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setWidget(content)
    return area


def scrollable_body(dialog: QWidget, spacing: int = Space.MD,
                    margins: int = Space.LG) -> tuple[QVBoxLayout, QVBoxLayout]:
    """
    Каркас модального окна: прокручиваемое содержимое и закреплённый низ.

    Возвращает (layout содержимого, layout нижней панели). Кнопки «Сохранить»
    и «Отмена» остаются видимыми, даже если форма длиннее экрана; само окно
    не вырастает за пределы экрана.
    """
    from PySide6.QtWidgets import QApplication

    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    content = QWidget()
    body = QVBoxLayout(content)
    body.setContentsMargins(margins, margins, margins, margins)
    body.setSpacing(spacing)
    outer.addWidget(scroll_area(content), 1)

    footer_host = QFrame()
    footer_host.setObjectName("DialogFooter")
    footer = QVBoxLayout(footer_host)
    footer.setContentsMargins(margins, Space.MD, margins, Space.MD)
    footer.setSpacing(Space.SM)
    outer.addWidget(footer_host)

    screen = QApplication.primaryScreen()
    if screen is not None:
        dialog.setMaximumHeight(int(screen.availableGeometry().height() * 0.92))
    return body, footer


# ------------------------------------------------------------------ блоки
class Card(QFrame):
    """Белая карточка со скруглением и тонкой границей."""

    def __init__(self, parent=None, padding: int = Space.LG, alt: bool = False,
                 with_shadow: bool = False):
        super().__init__(parent)
        self.setObjectName("CardAlt" if alt else "Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding, padding, padding)
        self.body.setSpacing(Space.MD)
        if with_shadow:
            shadow(self)


class StatusBadge(QLabel):
    """«Пилюля» статуса: цветной текст на мягкой подложке."""

    def __init__(self, status: str, label: str, parent=None):
        super().__init__(label, parent)
        self.setObjectName("Badge")
        self.apply(status)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)

    def apply(self, status: str) -> None:
        text_color, background, border = status_tone(status)
        self.setStyleSheet(
            f"background: {background}; color: {text_color}; border: 1px solid {border};")


class Chip(QLabel):
    """Серая метка — ключевые слова, теги."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("Chip")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Maximum)


class PhotoLabel(QLabel):
    """
    Фотография со скруглением, без искажения пропорций.

    Клик отдаётся сигналом clicked. Раньше обработчик присваивали экземпляру
    (thumb.mousePressEvent = ...), и он молча не срабатывал: Qt вызывает метод
    класса, а не подменённый атрибут объекта. Из-за этого миниатюры в карточке
    не открывались.
    """

    clicked = Signal()

    def __init__(self, size: int = 220, parent=None, radius: int = Radius.LG):
        super().__init__(parent)
        self._size = size
        self._radius = radius
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setObjectName("PhotoFrame")
        self.clear_photo()

    def mousePressEvent(self, event) -> None:            # noqa: N802 — метод Qt
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_size(self, size: int) -> None:
        self._size = size
        self.setFixedSize(size, size)

    def clear_photo(self) -> None:
        self.setPixmap(QPixmap())
        self.setText("нет фото")

    def set_photo(self, path: str) -> None:
        source = QPixmap(path)
        if source.isNull():
            self.clear_photo()
            return
        self.setText("")
        scaled = source.scaled(self._size, self._size, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation)
        rounded = QPixmap(scaled.size())
        rounded.fill(Qt.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(0, 0, scaled.width(), scaled.height(), self._radius, self._radius)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, scaled)
        painter.end()
        self.setPixmap(rounded)


class DatePicker(QWidget):
    """Компактный календарь. Может быть пустым (срок неизвестен)."""

    changed = Signal()

    def __init__(self, optional: bool = False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.SM)
        self.edit = QDateEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat(DATE_FORMAT)
        self.edit.setDate(QDate.currentDate())
        self.edit.setMinimumWidth(130)
        self.edit.dateChanged.connect(lambda *_: self.changed.emit())
        layout.addWidget(self.edit)
        self.unknown: Optional[QCheckBox] = None
        if optional:
            self.unknown = QCheckBox("не определена")
            self.unknown.toggled.connect(self._toggle)
            layout.addWidget(self.unknown)
        layout.addStretch()

    def _toggle(self, checked: bool) -> None:
        self.edit.setEnabled(not checked)
        self.changed.emit()

    def value(self) -> Optional[date]:
        if self.unknown is not None and self.unknown.isChecked():
            return None
        return self.edit.date().toPython()

    def set_value(self, value: Optional[date]) -> None:
        if value is None:
            if self.unknown is not None:
                self.unknown.setChecked(True)
            return
        if self.unknown is not None:
            self.unknown.setChecked(False)
        self.edit.setDate(QDate(value.year, value.month, value.day))


# ------------------------------------------------------------------ таблицы
def make_table(headers: list[str], stretch_column: int = 1) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QTableWidget.SelectRows)
    table.setSelectionMode(QTableWidget.SingleSelection)
    table.setEditTriggers(QTableWidget.NoEditTriggers)
    table.setAlternatingRowColors(False)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(46)
    table.horizontalHeader().setStretchLastSection(False)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    table.setFrameShape(QTableWidget.NoFrame)
    if 0 <= stretch_column < len(headers):
        table.horizontalHeader().setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    table.setSortingEnabled(True)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.ElideRight)      # длинное название не ломает ширину
    table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
    table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
    table.horizontalHeader().setMaximumSectionSize(420)
    return table


def cell(text, align_right: bool = False, data=None) -> QTableWidgetItem:
    value = str(text) if text is not None else ""
    item = QTableWidgetItem(value)
    if len(value) > 40:
        item.setToolTip(value)                 # полное значение доступно по наведению
    if align_right:
        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    else:
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    if data is not None:
        item.setData(Qt.UserRole, data)
    return item


# ------------------------------------------------------------------ подписи
def title_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label
