# -*- coding: utf-8 -*-
"""
Каталог на модели Qt: одна модель данных и два вида — плитка и таблица.

Раньше каждая карточка каталога была отдельным набором виджетов, поэтому
показывалось не больше 120 предметов, а открытие раздела занимало больше
полсекунды. Здесь карточки рисует делегат: виджетов нет, строки подгружаются
страницами по мере прокрутки, и в каталоге можно листать весь склад —
хоть десять тысяч позиций.
"""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import (QAbstractTableModel, QByteArray, QEvent, QMimeData, QModelIndex,
                            QPoint, QRect, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
                           QPolygonF)
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from ...services.statuses import item_status
from ...utils import money_plain
from ...version import RENTAL_PERIOD_DAYS
from ..theme import Color, Radius, Space, px, status_tone
from .thumbs import thumbnails

ItemRole = Qt.UserRole + 1
PAGE = 200
MIME_ITEMS = "application/x-sklad-item-ids"     # перетаскивание предметов в категорию

COLUMNS = ["Инв. №", "Наименование", "Категория", "Всего", "Доступно", "В аренде",
           f"Цена за {RENTAL_PERIOD_DAYS} дн.", "Оценка", "Статус"]

# Плотность плитки: (ширина карточки, высота фотографии)
DENSITIES = {"compact": (168, 120), "normal": (208, 150), "large": (288, 208)}
DENSITY_LABELS = {"compact": "Мелкая плитка", "normal": "Обычная плитка",
                  "large": "Крупная плитка"}


class CatalogModel(QAbstractTableModel):
    """Предметы каталога. Данные приходят страницами от функции-источника."""

    total_changed = Signal(int, int)          # загружено, всего

    def __init__(self, fetch: Callable[[int, int], tuple[list[dict], int]], parent=None):
        super().__init__(parent)
        self._fetch = fetch
        self._items: list[dict] = []
        self._total = 0

    # ------------------------------------------------------------- загрузка
    def reload(self) -> None:
        self.beginResetModel()
        try:
            self._items, self._total = self._fetch(0, PAGE)
        finally:
            self.endResetModel()
        self.total_changed.emit(len(self._items), self._total)

    def canFetchMore(self, parent=QModelIndex()) -> bool:    # noqa: N802 — интерфейс Qt
        return not parent.isValid() and len(self._items) < self._total

    def fetchMore(self, parent=QModelIndex()) -> None:       # noqa: N802
        if parent.isValid():
            return
        items, total = self._fetch(len(self._items), PAGE)
        self._total = total
        if not items:
            self._total = len(self._items)      # данные кончились раньше, чем ожидалось
            self.total_changed.emit(len(self._items), self._total)
            return
        start = len(self._items)
        self.beginInsertRows(QModelIndex(), start, start + len(items) - 1)
        self._items += items
        self.endInsertRows()
        self.total_changed.emit(len(self._items), self._total)

    def load_all(self, limit: int = 20000) -> None:
        """Догружает всё найденное (для «выделить всё»)."""
        while self.canFetchMore() and len(self._items) < limit:
            self.fetchMore()

    # --------------------------------------------------------------- доступ
    def items(self) -> list[dict]:
        return self._items

    def total(self) -> int:
        return self._total

    def item(self, row: int) -> Optional[dict]:
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_of(self, item_id: int) -> int:
        for row, item in enumerate(self._items):
            if item["id"] == item_id:
                return row
        return -1

    def update_item(self, item: dict) -> None:
        """Заменяет данные одной карточки без перезагрузки списка."""
        row = self.row_of(item["id"])
        if row < 0:
            return
        self._items[row] = item
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def photo_ready(self, path: str) -> None:
        """Миниатюра загрузилась — перерисовать карточки с этим файлом."""
        for row, item in enumerate(self._items):
            if item.get("thumb_path") == path:
                index = self.index(row, 0)
                self.dataChanged.emit(index, index, [Qt.DecorationRole])

    # ---------------------------------------------------------- интерфейс Qt
    def rowCount(self, parent=QModelIndex()) -> int:         # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:      # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):   # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole:
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        return None

    def data(self, index, role=Qt.DisplayRole):
        item = self.item(index.row()) if index.isValid() else None
        if item is None:
            return None
        if role == ItemRole:
            return item
        column = index.column()
        if role == Qt.DisplayRole:
            if column == 0:
                return item["inv_number"]
            if column == 1:
                return item["name"]
            if column == 2:
                return item.get("category_path") or "без категории"
            if column == 3:
                return str(item["quantity_total"])
            if column == 4:
                return str(item["available_now"])
            if column == 5:
                return str(item["reserved_now"])
            if column == 6:
                return money_plain(item["rental_price"])
            if column == 7:
                return money_plain(item["valuation"])
            if column == 8:
                return item_status(item)[1]
        if role == Qt.DecorationRole and column == 1 and item.get("is_favorite"):
            from .common import icon
            return icon("star_on")
        if role == Qt.ToolTipRole:
            if column in (1, 2):
                return item["name"] if column == 1 else item.get("category_path") or ""
            return None
        if role == Qt.TextAlignmentRole:
            if column in (3, 4, 5, 6, 7):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ForegroundRole and column == 8:
            return QBrush(QColor(status_tone(item_status(item)[0])[0]))
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled

    # ------------------------------------------------- перетаскивание в дерево
    def mimeTypes(self) -> list[str]:                        # noqa: N802
        return [MIME_ITEMS]

    def mimeData(self, indexes) -> QMimeData:                # noqa: N802
        ids = sorted({self._items[index.row()]["id"] for index in indexes
                      if index.isValid() and index.row() < len(self._items)})
        data = QMimeData()
        data.setData(MIME_ITEMS, QByteArray(",".join(str(i) for i in ids).encode("ascii")))
        return data

    def supportedDragActions(self):                          # noqa: N802
        return Qt.MoveAction


def ids_from_mime(mime) -> list[int]:
    """Номера предметов из перетаскиваемых данных (пустой список — чужие данные)."""
    if mime is None or not mime.hasFormat(MIME_ITEMS):
        return []
    raw = bytes(mime.data(MIME_ITEMS)).decode("ascii", "ignore")
    return [int(part) for part in raw.split(",") if part.isdigit()]


class ItemCardDelegate(QStyledItemDelegate):
    """Рисует карточку предмета: фото, название, категория, статус, цена."""

    favorite_toggled = Signal(int, bool)       # id предмета, новое состояние

    NAME_LINES = 2
    PAD = Space.MD

    def __init__(self, parent=None, density: str = "normal"):
        super().__init__(parent)
        self._density = density if density in DENSITIES else "normal"
        self._width = DENSITIES[self._density][0]
        self._fonts()

    def _fonts(self) -> None:
        from ..brand import ui_font
        from ..theme import Font

        self.f_name = ui_font(Font.BODY + 0.75, Font.W_MEDIUM)    # название — 15 px, Medium
        self.f_small = ui_font(Font.TINY)                         # подписи — 12 px
        self.f_badge = ui_font(Font.TINY, Font.W_MEDIUM)
        self.f_price = ui_font(Font.BODY, Font.W_BOLD)            # цена — акцент

    def set_density(self, density: str) -> None:
        if density in DENSITIES:
            self._density = density
            self._width = DENSITIES[density][0]

    def base_width(self) -> int:
        """Наименьшая ширина карточки при выбранной плотности."""
        return DENSITIES[self._density][0]

    def set_card_width(self, width: int) -> None:
        """Каталог растягивает карточки, чтобы ряд занимал всю ширину окна."""
        self._width = max(self.base_width(), int(width))

    def _photo_height(self) -> int:
        base_width, base_photo = DENSITIES[self._density]
        # фото растёт вместе с карточкой, пропорции рамки сохраняются
        return int(base_photo * (self._width - 2 * self.PAD) / (base_width - 2 * self.PAD))

    def density(self) -> str:
        return self._density

    def card_size(self) -> QSize:
        width, photo = self._width, self._photo_height()
        name_h = QFontMetrics(self.f_name).lineSpacing() * self.NAME_LINES
        small_h = QFontMetrics(self.f_small).lineSpacing()
        badge_h = QFontMetrics(self.f_badge).height() + 10
        price_h = QFontMetrics(self.f_price).lineSpacing()
        height = (self.PAD + photo + Space.SM + name_h + 4 + small_h + Space.SM
                  + badge_h + Space.SM + price_h + self.PAD)
        return QSize(width, height)

    def sizeHint(self, option, index) -> QSize:              # noqa: N802
        return self.card_size()

    # ------------------------------------------------------------- геометрия
    def _photo_rect(self, rect: QRect) -> QRect:
        return QRect(rect.left() + self.PAD, rect.top() + self.PAD,
                     rect.width() - 2 * self.PAD, self._photo_height())

    def _star_rect(self, rect: QRect) -> QRect:
        photo = self._photo_rect(rect)
        return QRect(photo.right() - 30, photo.top() + 6, 26, 26)

    # --------------------------------------------------------------- рисунок
    def paint(self, painter: QPainter, option, index) -> None:
        item = index.data(ItemRole)
        if not item:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        rect = option.rect.adjusted(1, 1, -1, -1)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        border = QColor(Color.PRIMARY if selected else
                        (Color.TEXT_MUTED if hovered else Color.BORDER))
        painter.setPen(QPen(border, 2 if selected else 1))
        painter.setBrush(QColor(Color.PRIMARY_SOFT if selected else Color.SURFACE))
        painter.drawRoundedRect(QRectF(rect), Radius.LG, Radius.LG)

        # ------------------------------------------------------------- фото
        photo = self._photo_rect(rect)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(photo), Radius.MD, Radius.MD)
        painter.setPen(Qt.NoPen)
        painter.fillPath(clip, QColor(Color.PHOTO_BG))
        ratio = painter.device().devicePixelRatioF() if painter.device() else 1.0
        path = item.get("thumb_path") or ""
        pixmap = thumbnails().get(path, int(photo.width() * ratio), int(photo.height() * ratio))
        if pixmap is not None and not pixmap.isNull():
            # снимок вписывается целиком, пропорции не меняются; поля — подложка
            scaled = pixmap.size().scaled(photo.size(), Qt.KeepAspectRatio)
            target = QRect(photo.left() + (photo.width() - scaled.width()) // 2,
                           photo.top() + (photo.height() - scaled.height()) // 2,
                           scaled.width(), scaled.height())
            painter.save()
            painter.setClipPath(clip)
            painter.drawPixmap(target, pixmap)
            painter.restore()
        else:
            painter.setPen(QColor(Color.TEXT_MUTED))
            painter.setFont(self.f_small)
            painter.drawText(photo, Qt.AlignCenter, "загрузка…" if path else "нет фото")

        count = item.get("photo_count", 0)
        if count > 1:
            label = f"{count} фото"
            painter.setFont(self.f_small)
            metrics = QFontMetrics(self.f_small)
            width = metrics.horizontalAdvance(label) + 12
            chip = QRect(photo.left() + 6, photo.bottom() - metrics.height() - 9,
                         width, metrics.height() + 4)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(QRectF(chip), 6, 6)
            painter.setPen(QColor(Color.ON_PHOTO))
            painter.drawText(chip, Qt.AlignCenter, label)

        # ---------------------------------------------------------- избранное
        favorite = bool(item.get("is_favorite"))
        if favorite or hovered or selected:
            self._draw_star(painter, self._star_rect(rect), favorite)

        # ------------------------------------------------------------- текст
        x = rect.left() + self.PAD
        width = rect.width() - 2 * self.PAD
        y = photo.bottom() + 1 + Space.SM

        painter.setFont(self.f_name)
        painter.setPen(QColor(Color.TEXT))
        metrics = QFontMetrics(self.f_name)
        for line in self._wrap(item["name"], metrics, width, self.NAME_LINES):
            painter.drawText(QRect(x, y, width, metrics.lineSpacing()),
                             Qt.AlignLeft | Qt.AlignVCenter, line)
            y += metrics.lineSpacing()
        y = photo.bottom() + 1 + Space.SM + metrics.lineSpacing() * self.NAME_LINES + 4

        painter.setFont(self.f_small)
        painter.setPen(QColor(Color.TEXT_MUTED))
        small = QFontMetrics(self.f_small)
        category = item.get("category_path") or "без категории"
        painter.drawText(QRect(x, y, width, small.lineSpacing()), Qt.AlignLeft | Qt.AlignVCenter,
                         small.elidedText(category, Qt.ElideMiddle, width))
        y += small.lineSpacing() + Space.SM

        code, label = item_status(item)
        tone_text, tone_bg, tone_border = status_tone(code)
        painter.setFont(self.f_badge)
        badge_metrics = QFontMetrics(self.f_badge)
        label = badge_metrics.elidedText(label, Qt.ElideRight, width - 18)
        badge = QRect(x, y, badge_metrics.horizontalAdvance(label) + 18,
                      badge_metrics.height() + 10)
        painter.setPen(QPen(QColor(tone_border), 1))
        painter.setBrush(QColor(tone_bg))
        painter.drawRoundedRect(QRectF(badge), Radius.SM, Radius.SM)
        painter.setPen(QColor(tone_text))
        painter.drawText(badge, Qt.AlignCenter, label)
        y += badge.height() + Space.SM

        painter.setFont(self.f_price)
        painter.setPen(QColor(Color.TEXT))
        price_metrics = QFontMetrics(self.f_price)
        price = f"{money_plain(item['rental_price'])} ₽"
        painter.drawText(QRect(x, y, width, price_metrics.lineSpacing()),
                         Qt.AlignLeft | Qt.AlignVCenter, price)
        painter.setFont(self.f_small)
        painter.setPen(QColor(Color.TEXT_MUTED))
        number = f"№ {item['inv_number']}"
        number_width = small.horizontalAdvance(number)
        painter.drawText(QRect(x, y, width, price_metrics.lineSpacing()),
                         Qt.AlignRight | Qt.AlignVCenter, number)
        period = f"за {RENTAL_PERIOD_DAYS} дн."
        left = x + price_metrics.horizontalAdvance(price) + 6
        if left + small.horizontalAdvance(period) < x + width - number_width - 6:
            painter.drawText(QRect(left, y, width, price_metrics.lineSpacing()),
                             Qt.AlignLeft | Qt.AlignVCenter, period)
        painter.restore()

    @staticmethod
    def _wrap(text: str, metrics: QFontMetrics, width: int, lines: int) -> list[str]:
        """Перенос по словам с многоточием в последней строке."""
        words = (text or "").split()
        result: list[str] = []
        current = ""
        index = 0
        while index < len(words):
            word = words[index]
            candidate = f"{current} {word}".strip()
            if metrics.horizontalAdvance(candidate) <= width:
                current = candidate
                index += 1
                continue
            if len(result) == lines - 1:
                rest = " ".join(([current] if current else []) + words[index:])
                result.append(metrics.elidedText(rest, Qt.ElideRight, width))
                return result
            if not current:              # одно слово длиннее строки — режем его
                result.append(metrics.elidedText(word, Qt.ElideRight, width))
                index += 1
            else:
                result.append(current)
            current = ""
        if current:
            result.append(current)
        return result[:lines]

    @staticmethod
    def _draw_star(painter: QPainter, rect: QRect, filled: bool) -> None:
        import math

        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 110))
        painter.drawEllipse(rect)
        center = QPointF(rect.center().x() + 0.5, rect.center().y() + 1.0)
        outer, inner = rect.width() * 0.34, rect.width() * 0.15
        points = []
        for step in range(10):
            radius = outer if step % 2 == 0 else inner
            angle = -math.pi / 2 + step * math.pi / 5
            points.append(QPointF(center.x() + radius * math.cos(angle),
                                  center.y() + radius * math.sin(angle)))
        painter.setPen(QPen(QColor(Color.STAR if filled else Color.ON_PHOTO), 1.4))
        painter.setBrush(QColor(Color.STAR) if filled else Qt.NoBrush)
        painter.drawPolygon(QPolygonF(points))
        painter.restore()

    # ------------------------------------------------------------- события
    def editorEvent(self, event, model, option, index) -> bool:   # noqa: N802
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            item = index.data(ItemRole)
            position = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if item and self._star_rect(option.rect.adjusted(1, 1, -1, -1)).contains(position):
                self.favorite_toggled.emit(item["id"], not item.get("is_favorite"))
                return True
        return super().editorEvent(event, model, option, index)

    def star_hit(self, option_rect: QRect, position: QPoint) -> bool:
        return self._star_rect(option_rect.adjusted(1, 1, -1, -1)).contains(position)
