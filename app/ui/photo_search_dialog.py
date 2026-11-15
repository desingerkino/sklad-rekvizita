# -*- coding: utf-8 -*-
"""
Поиск реквизита по фотографии.

Версия 4.0. Слева — снимок для поиска и условия, справа — найденные предметы
от самого похожего к менее похожим, с фотографией, названием, категорией и
шкалой сходства.

Снимок можно выбрать файлом, снять камерой, вставить из буфера (Ctrl+V) или
перетащить в окно. Если на снимке несколько предметов, нужный выделяется
мышью прямо на снимке — поиск пойдёт по выделенной области. Поиск можно
сузить текстом («телефон») и категорией.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImageReader, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..services.categories_service import categories_service
from ..services.image_search import dominant_colors, image_search
from ..services.inventory_service import inventory_service
from ..services.photo_service import photo_service
from .errors import show_error
from .theme import Color, Radius, Space
from .widgets.common import hint, muted, section_title
from .widgets.flow_layout import FlowLayout

from ..services.photo_service import PHOTO_SUFFIXES as IMAGE_SUFFIXES, photo_filter  # noqa: E402
RESULT_LIMIT = 24


class CropLabel(QLabel):
    """Снимок для поиска. Мышью можно выделить область — часть кадра с нужным предметом."""

    region_changed = Signal()

    def __init__(self, side: int = 320, parent=None):
        super().__init__(parent)
        self._side = side
        self.setFixedSize(side, side)
        self.setAlignment(Qt.AlignCenter)
        self.setObjectName("PhotoBox")
        self.setCursor(Qt.CrossCursor)
        self._pixmap: Optional[QPixmap] = None
        self._image_rect = QRect()
        self._start: Optional[QPoint] = None
        self._selection = QRect()
        self.setText("Перетащите сюда фотографию\nили вставьте из буфера (Ctrl+V)")
        self.setWordWrap(True)

    def set_image(self, path: Path) -> bool:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            # формат, который Qt не читает сам (HEIC): готовим копию через Pillow
            try:
                from ..services.image_search import _open_rgb
                handle, name = tempfile.mkstemp(suffix=".png", prefix="sklad_preview_")
                os.close(handle)
                preview = _open_rgb(path)
                preview.thumbnail((900, 900))
                preview.save(name)
                image = QImageReader(name).read()
                os.unlink(name)
            except Exception:                                      # noqa: BLE001
                return False
        if image.isNull():
            return False
        self._pixmap = QPixmap.fromImage(image)
        self._selection = QRect()
        self.setText("")
        self._layout_image()
        self.update()
        return True

    def _layout_image(self) -> None:
        if not self._pixmap:
            return
        size = self._pixmap.size().scaled(QSize(self._side - 4, self._side - 4),
                                          Qt.KeepAspectRatio)
        self._image_rect = QRect((self.width() - size.width()) // 2,
                                 (self.height() - size.height()) // 2,
                                 size.width(), size.height())

    def region(self) -> Optional[tuple]:
        """Выделенная область в долях кадра (лево, верх, право, низ) или None."""
        if self._selection.isNull() or self._image_rect.isEmpty():
            return None
        rect = self._selection.intersected(self._image_rect)
        if rect.width() < 16 or rect.height() < 16:
            return None
        image = self._image_rect
        return ((rect.left() - image.left()) / image.width(),
                (rect.top() - image.top()) / image.height(),
                (rect.right() + 1 - image.left()) / image.width(),
                (rect.bottom() + 1 - image.top()) / image.height())

    def clear_region(self) -> None:
        self._selection = QRect()
        self.update()
        self.region_changed.emit()

    def set_region(self, box: tuple) -> None:
        """Задаёт область программно (доли кадра) — используется в тестах."""
        image = self._image_rect
        self._selection = QRect(
            int(image.left() + box[0] * image.width()), int(image.top() + box[1] * image.height()),
            int((box[2] - box[0]) * image.width()), int((box[3] - box[1]) * image.height()))
        self.update()
        self.region_changed.emit()

    def mousePressEvent(self, event) -> None:                # noqa: N802 — интерфейс Qt
        if self._pixmap and event.button() == Qt.LeftButton:
            self._start = event.position().toPoint()
            self._selection = QRect(self._start, self._start)
            self.update()

    def mouseMoveEvent(self, event) -> None:                 # noqa: N802
        if self._start is not None:
            self._selection = QRect(self._start, event.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, event) -> None:              # noqa: N802
        if self._start is None:
            return
        self._start = None
        if self.region() is None:
            self._selection = QRect()        # щелчок без протяжки снимает выделение
        self.update()
        self.region_changed.emit()

    def paintEvent(self, event) -> None:                     # noqa: N802
        if not self._pixmap:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(Color.PHOTO_BG))
        painter.drawPixmap(self._image_rect, self._pixmap)
        if not self._selection.isNull():
            shade = QColor(0, 0, 0, 120)
            selection = self._selection.intersected(self._image_rect)
            for part in (QRect(0, 0, self.width(), selection.top()),
                         QRect(0, selection.bottom() + 1, self.width(),
                               self.height() - selection.bottom() - 1),
                         QRect(0, selection.top(), selection.left(), selection.height()),
                         QRect(selection.right() + 1, selection.top(),
                               self.width() - selection.right() - 1, selection.height())):
                painter.fillRect(part, shade)
            painter.setPen(QPen(QColor(Color.ON_PHOTO), 2))
            painter.drawRect(selection)
        painter.end()


class ResultCard(QFrame):
    """Один найденный предмет: фото, название, категория, сходство."""

    activated = Signal(int)        # двойной щелчок — открыть карточку
    selected = Signal(int)

    WIDTH = 196

    def __init__(self, result: dict, item: dict, parent=None):
        super().__init__(parent)
        self.item_id = result["item_id"]
        self.item = item
        self.setObjectName("ResultCard")
        self.setFixedWidth(self.WIDTH)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.SM + 2, Space.SM + 2, Space.SM + 2, Space.SM + 2)
        layout.setSpacing(Space.XS + 2)

        photo = QLabel()
        photo.setFixedSize(self.WIDTH - 2 * (Space.SM + 2), 132)
        photo.setAlignment(Qt.AlignCenter)
        photo.setStyleSheet(
            f"background: {Color.PHOTO_BG}; border-radius: {Radius.MD}px; "
            f"color: {Color.TEXT_MUTED};")
        pixmap = QPixmap(result.get("thumb", ""))
        if not pixmap.isNull():
            # снимок целиком, пропорции не меняются
            photo.setPixmap(pixmap.scaled(photo.size(), Qt.KeepAspectRatio,
                                          Qt.SmoothTransformation))
        else:
            photo.setText("нет фото")
        layout.addWidget(photo)

        name = QLabel(item["name"])
        name.setObjectName("ValueStrong")
        name.setWordWrap(True)
        name.setFixedHeight(name.fontMetrics().lineSpacing() * 2 + 2)
        name.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        name.setToolTip(item["name"])
        layout.addWidget(name)

        category = item.get("category_path") or "без категории"
        meta = QLabel(name.fontMetrics().elidedText(
            f"№ {item['inv_number']} · {category}", Qt.ElideRight, self.WIDTH + 30))
        meta.setObjectName("Muted")
        meta.setToolTip(category)
        layout.addWidget(meta)

        percent = result["percent"]
        bar = QProgressBar()
        bar.setObjectName("StockBar")
        bar.setRange(0, 100)
        bar.setValue(percent)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        layout.addWidget(bar)
        level = QLabel(f"{result['level']} · {percent} %")
        level.setObjectName("Muted")
        level.setToolTip(
            "Положение на шкале сходства: 0 — как у случайных снимков, 100 — как у "
            "того же предмета. Это оценка сходства снимков, а не гарантия, что предмет тот же.")
        layout.addWidget(level)

        free = item.get("available_now", 0)
        stock = QLabel(f"Свободно: {free} из {item['quantity_total']}")
        stock.setObjectName("Muted")
        if free <= 0:
            stock.setStyleSheet(f"color: {Color.WARNING};")
        layout.addWidget(stock)
        self.setToolTip("Двойной щелчок — открыть карточку")

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event) -> None:                # noqa: N802 — интерфейс Qt
        self.selected.emit(self.item_id)

    def mouseDoubleClickEvent(self, event) -> None:          # noqa: N802
        self.activated.emit(self.item_id)


class PhotoSearchDialog(QDialog):
    open_item = Signal(int)
    add_to_rental = Signal(int)

    def __init__(self, parent=None, similar_to_item: Optional[int] = None):
        super().__init__(parent)
        self.setWindowTitle("Поиск по фотографии")
        self.resize(1080, 720)
        self.setMinimumSize(820, 560)
        self.setAcceptDrops(True)
        self._path: Optional[Path] = None
        self._exclude: Optional[int] = similar_to_item
        self._cards: list[ResultCard] = []
        self._selected: Optional[int] = None
        self._temp_files: list[str] = []
        self._build()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self._search)
        QShortcut(QKeySequence.Paste, self, activated=self._paste)
        if similar_to_item:
            photo = photo_service.primary_photo(similar_to_item)
            if photo and Path(photo["abs_path"]).exists():
                item = inventory_service.get(similar_to_item)
                self.setWindowTitle("Похожие предметы")
                self.title.setText(f"Похожие на «{item['name']}»" if item else "Похожие предметы")
                self.load_image(Path(photo["abs_path"]))
            else:
                self._message("У этого предмета нет фотографии",
                              "Добавьте фотографию в карточку — и похожие предметы "
                              "можно будет найти.")

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        root.setSpacing(Space.LG)

        # ------------------------------------------------------ запрос
        left = QVBoxLayout()
        left.setSpacing(Space.SM)
        self.title = section_title("Снимок для поиска")
        self.title.setWordWrap(True)
        self.title.setFixedWidth(320)
        left.addWidget(self.title)
        self.preview = CropLabel(320)
        self.preview.region_changed.connect(self._region_changed)
        left.addWidget(self.preview)

        buttons = QHBoxLayout()
        buttons.setSpacing(Space.SM)
        self.file_btn = QPushButton("Выбрать файл…")
        self.file_btn.setObjectName("Primary")
        self.file_btn.clicked.connect(self._from_file)
        buttons.addWidget(self.file_btn)
        self.camera_btn = QPushButton("Камера")
        self.camera_btn.setToolTip("Сделать снимок камерой компьютера")
        self.camera_btn.clicked.connect(self._from_camera)
        buttons.addWidget(self.camera_btn)
        self.paste_btn = QPushButton("Вставить")
        self.paste_btn.setToolTip("Вставить картинку из буфера обмена (Ctrl+V)")
        self.paste_btn.clicked.connect(self._paste)
        buttons.addWidget(self.paste_btn)
        left.addLayout(buttons)

        self.region_btn = QPushButton("Искать по всему снимку")
        self.region_btn.setObjectName("LinkButton")
        self.region_btn.setCursor(Qt.PointingHandCursor)
        self.region_btn.clicked.connect(self.preview.clear_region)
        self.region_btn.setVisible(False)
        left.addWidget(self.region_btn, 0, Qt.AlignLeft)
        self.region_hint = hint(
            "Если на снимке несколько предметов — выделите нужный мышью.")
        self.region_hint.setFixedWidth(320)
        left.addWidget(self.region_hint)

        left.addWidget(muted("Уточнить словами (необязательно)"))
        self.text = QLineEdit()
        self.text.setPlaceholderText("например: телефон, чёрный, 1970")
        self.text.setClearButtonEnabled(True)
        self.text.textChanged.connect(lambda *_: self._timer.start())
        left.addWidget(self.text)

        left.addWidget(muted("Искать в категории"))
        self.category = QComboBox()
        self.category.addItem("Весь каталог", None)
        self._fill_categories(categories_service.tree())
        self.category.currentIndexChanged.connect(lambda *_: self._search())
        left.addWidget(self.category)

        self.colors_label = QLabel("")
        self.colors_label.setObjectName("Muted")
        self.colors_label.setWordWrap(True)
        self.colors_label.setFixedWidth(320)
        left.addWidget(self.colors_label)
        left.addStretch()
        self.engine_label = QLabel("")
        self.engine_label.setObjectName("Hint")
        self.engine_label.setWordWrap(True)
        self.engine_label.setFixedWidth(320)
        left.addWidget(self.engine_label)
        root.addLayout(left)

        # --------------------------------------------------- результаты
        right = QVBoxLayout()
        right.setSpacing(Space.SM)
        self.summary = QLabel("")
        self.summary.setObjectName("ValueStrong")
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)
        self.note = QLabel("")
        self.note.setObjectName("Muted")
        self.note.setWordWrap(True)
        self.note.setVisible(False)
        right.addWidget(self.note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setSingleStep(28)     # плавная прокрутка колесом
        self.host = QWidget()
        self.flow = FlowLayout(self.host, margin=2, spacing=Space.MD)
        self.scroll.setWidget(self.host)
        right.addWidget(self.scroll, 1)

        self.empty = QLabel("")
        self.empty.setObjectName("EmptyNote")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        right.addWidget(self.empty, 1)

        actions = QHBoxLayout()
        actions.setSpacing(Space.SM)
        self.open_btn = QPushButton("Открыть карточку")
        self.open_btn.clicked.connect(self._open)
        self.add_btn = QPushButton("Добавить в аренду")
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self._add)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        actions.addWidget(self.open_btn)
        actions.addWidget(self.add_btn)
        actions.addStretch()
        actions.addWidget(close)
        right.addLayout(actions)
        root.addLayout(right, 1)

        self._message("Выберите фотографию предмета",
                      "Программа сравнит её с фотографиями реквизита на складе и покажет "
                      "похожие предметы. Снимок никуда не отправляется: поиск работает "
                      "на этом компьютере.")
        self._update_buttons()
        self._show_engine()

    def _fill_categories(self, nodes: list, depth: int = 0) -> None:
        for node in nodes:
            self.category.addItem("    " * depth + node["name"], node["id"])
            self._fill_categories(node["children"], depth + 1)

    def _show_engine(self) -> None:
        try:
            stats = image_search.stats()
        except Exception:                                          # noqa: BLE001
            return
        text = f"Способ поиска: {stats['title']}. Проиндексировано фотографий: " \
               f"{stats['indexed']} из {stats['photos']}."
        if stats["indexed"] < stats["photos"]:
            text += " Остальные индексируются в фоне."
        self.engine_label.setText(text)

    # ------------------------------------------------------- источники снимка
    def _from_camera(self) -> None:
        from .camera_dialog import CameraDialog
        dialog = CameraDialog(self)
        if dialog.exec() and dialog.captured_path:
            self.load_image(Path(dialog.captured_path))

    def _from_file(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Фотография для поиска", "", photo_filter())
        if name:
            self.load_image(Path(name))

    def _paste(self) -> None:
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in IMAGE_SUFFIXES and path.exists():
                    self.load_image(path)
                    return
        image = clipboard.image()
        if image.isNull():
            self._message("В буфере обмена нет картинки",
                          "Скопируйте изображение (или файл с фотографией) и нажмите "
                          "«Вставить» ещё раз.")
            return
        handle, name = tempfile.mkstemp(suffix=".png", prefix="sklad_paste_")
        os.close(handle)
        image.save(name, "PNG")
        self._temp_files.append(name)
        self.load_image(Path(name))

    def dragEnterEvent(self, event) -> None:                 # noqa: N802 — интерфейс Qt
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if any(Path(url.toLocalFile()).suffix.lower() in IMAGE_SUFFIXES for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:                      # noqa: N802
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in IMAGE_SUFFIXES:
                event.acceptProposedAction()
                self.load_image(path)
                return

    # ------------------------------------------------------------------ поиск
    def load_image(self, path: Path) -> None:
        """Берёт снимок и сразу ищет по нему."""
        if not self.preview.set_image(path):
            self._path = None
            self._message("Не удалось прочитать изображение",
                          "Поддерживаются JPG, PNG, WEBP, BMP и HEIC (фото с айфона).")
            return
        self._path = Path(path)
        self.region_btn.setVisible(False)
        self._search()

    def _run(self, path: Path) -> None:
        """Прежнее имя метода: оставлено для совместимости."""
        self.load_image(Path(path))

    def _region_changed(self) -> None:
        has_region = self.preview.region() is not None
        self.region_btn.setVisible(has_region)
        self.region_hint.setVisible(not has_region)
        if self._path:
            self._search()

    def _search(self) -> None:
        if not self._path:
            return
        box = self.preview.region()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            options = dict(text=self.text.text(), category_id=self.category.currentData(),
                           box=box, exclude_item_id=self._exclude, min_score=0.0)
            results = image_search.search(self._path, limit=RESULT_LIMIT, min_percent=18,
                                          **options)
            if not results:
                # Уверенных совпадений нет. Пустое окно тут хуже честного ответа:
                # показываем несколько ближайших предметов с их низкой оценкой —
                # оператор сам видит, что это «отдалённо», и решает.
                results = image_search.search(self._path, limit=8, **options)
            colors = dominant_colors(self._path, box)
        except Exception as exc:                                   # noqa: BLE001
            QApplication.restoreOverrideCursor()
            self._clear_results()
            show_error(self, exc, "Поиск по фото выполнить не удалось.")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.colors_label.setText(
            "Основной цвет на снимке: " + ", ".join(colors) + "." if colors else "")
        note = image_search.last_provider_note
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self._show_results(results)

    def _clear_results(self) -> None:
        self.flow.clear()
        self._cards = []
        self._selected = None
        self._update_buttons()

    def _message(self, title: str, text: str) -> None:
        self._clear_results()
        self.summary.setText(title)
        self.empty.setText(text)
        self.empty.setVisible(True)
        self.scroll.setVisible(False)

    def _show_results(self, results: list[dict]) -> None:
        self._clear_results()
        if not results:
            stats = image_search.stats()
            if not stats["photos"]:
                self._message("На складе пока нет фотографий",
                              "Добавьте фотографии в карточки реквизита — и по ним можно "
                              "будет искать.")
            elif self.text.text().strip() or self.category.currentData():
                self._message("Ничего похожего не найдено",
                              "Под выбранные слова и категорию похожих предметов нет. "
                              "Попробуйте убрать уточнение или искать по всему каталогу.")
            else:
                self._message("Ничего похожего не найдено",
                              "На складе нет предметов, похожих на этот снимок. "
                              "Попробуйте снять предмет крупнее или выделить его на снимке.")
            return
        ids = [result["item_id"] for result in results]
        items = {item["id"]: item for item in inventory_service._enrich(
            inventory_service_rows(ids))}
        photos = {}
        for result in results:
            photo = next((p for p in photo_service.photos_of(result["item_id"])
                          if p["id"] == result["photo_id"]), None)
            photos[result["item_id"]] = photo["thumb_path"] if photo else ""
        for result in results:
            item = items.get(result["item_id"])
            if not item:
                continue
            card = ResultCard({**result, "thumb": photos.get(result["item_id"], "")}, item)
            card.selected.connect(self._select)
            card.activated.connect(self._activate)
            self.flow.addWidget(card)
            self._cards.append(card)
        strong = sum(1 for result in results if result["percent"] >= 60)
        self.summary.setText(
            f"Найдено: {len(self._cards)}" + (f" · из них очень похожих и похожих: {strong}"
                                             if strong else " · явных совпадений нет, "
                                                            "показаны ближайшие"))
        self.empty.setVisible(False)
        self.scroll.setVisible(True)
        self.scroll.verticalScrollBar().setValue(0)
        if self._cards:
            self._select(self._cards[0].item_id)

    def results(self) -> list[int]:
        """Найденные предметы в порядке показа (нужно тестам)."""
        return [card.item_id for card in self._cards]

    # --------------------------------------------------------------- действия
    def _select(self, item_id: int) -> None:
        self._selected = item_id
        for card in self._cards:
            card.set_selected(card.item_id == item_id)
        self._update_buttons()

    def _activate(self, item_id: int) -> None:
        self._select(item_id)
        self._open()

    def _current_card(self) -> Optional[ResultCard]:
        return next((card for card in self._cards if card.item_id == self._selected), None)

    def _update_buttons(self) -> None:
        card = self._current_card()
        self.open_btn.setEnabled(card is not None)
        free = bool(card and card.item.get("available_now", 0) > 0)
        self.add_btn.setEnabled(free)
        self.add_btn.setToolTip("" if free or card is None
                                else "У этого предмета нет свободных экземпляров")

    def _open(self) -> None:
        if self._selected:
            self.open_item.emit(self._selected)
            self.accept()

    def _add(self) -> None:
        if self._selected:
            self.add_to_rental.emit(self._selected)
            self.accept()

    def done(self, result: int) -> None:
        for name in self._temp_files:
            try:
                os.unlink(name)
            except OSError:
                pass
        self._temp_files = []
        super().done(result)


def inventory_service_rows(ids: list[int]):
    """Строки предметов по списку id (в том же порядке не гарантируются)."""
    from ..db.database import get_db

    if not ids:
        return []
    return get_db().query(
        f"SELECT * FROM inventory_items WHERE id IN ({','.join('?' * len(ids))})", ids)
