# -*- coding: utf-8 -*-
"""Поиск по фотографии: снимок с камеры или файл — и список похожих предметов."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)
from PySide6.QtGui import QIcon

from ..services.image_search import image_search
from ..services.inventory_service import inventory_service
from ..services.photo_service import photo_service
from .errors import show_error
from .widgets.common import PhotoLabel, muted


class PhotoSearchDialog(QDialog):
    open_item = Signal(int)
    add_to_rental = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Поиск реквизита по фотографии")
        self.resize(880, 620)
        self.query_path: Path | None = None
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        top = QHBoxLayout()

        left = QVBoxLayout()
        self.preview = PhotoLabel(260)
        left.addWidget(self.preview)
        camera_btn = QPushButton("\U0001F4F7  Снять камерой")
        camera_btn.setObjectName("Primary")
        camera_btn.clicked.connect(self._from_camera)
        left.addWidget(camera_btn)
        file_btn = QPushButton("Выбрать файл…")
        file_btn.clicked.connect(self._from_file)
        left.addWidget(file_btn)
        stats = image_search.stats()
        left.addWidget(muted(f"В индексе: {stats['indexed']} фото"))
        left.addStretch()
        top.addLayout(left)

        right = QVBoxLayout()
        self.title = QLabel("Сделайте снимок или выберите фотографию — программа найдёт похожий реквизит.")
        self.title.setWordWrap(True)
        right.addWidget(self.title)
        self.results = QListWidget()
        self.results.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.results.setMinimumHeight(300)
        self.results.setIconSize(QSize(72, 72))
        self.results.itemDoubleClicked.connect(self._open)
        right.addWidget(self.results, 1)
        top.addLayout(right, 1)
        root.addLayout(top, 1)

        buttons = QHBoxLayout()
        open_btn = QPushButton("Открыть карточку")
        open_btn.clicked.connect(self._open)
        buttons.addWidget(open_btn)
        rent_btn = QPushButton("Добавить в аренду")
        rent_btn.clicked.connect(self._add)
        buttons.addWidget(rent_btn)
        buttons.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    # --------------------------------------------------------------- ввод
    def _from_camera(self) -> None:
        from .camera_dialog import CameraDialog
        dialog = CameraDialog(self)
        if dialog.exec() and dialog.captured_path:
            self._run(dialog.captured_path)

    def _from_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Фотография для поиска", "", "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.heic *.heif)")
        if path:
            self._run(Path(path))

    def _run(self, path: Path) -> None:
        self.query_path = path
        self.preview.set_photo(str(path))
        self.results.clear()
        try:
            matches = image_search.search(path, limit=10, min_score=0.45)
        except Exception as exc:
            show_error(self, exc, "Поиск по фотографии не выполнен.")
            return
        # Если выбранный способ поиска не сработал и ответил встроенный
        # алгоритм, оператор должен об этом знать: результат корректный, но
        # найден иначе, чем он рассчитывал.
        note = image_search.last_provider_note
        if not matches:
            self.title.setText(
                "Похожий реквизит не найден. Попробуйте снять предмет крупнее и "
                "при лучшем свете." + (f"\n{note}" if note else ""))
            return
        best = matches[0]
        self.title.setText(f"Возможно, это: {best['name']} (инв. № {best['inv_number']}) — "
                           f"совпадение {best['percent']} %"
                           + (f"\n{note}" if note else ""))
        for match in matches:
            item = inventory_service.get(match["item_id"])
            photo = photo_service.primary_photo(match["item_id"])
            text = (f"{match['percent']} %   {match['name']}   ·   инв. № {match['inv_number']}   ·   "
                    f"доступно {item['available_now']} из {item['quantity_total']}")
            entry = QListWidgetItem(QIcon(photo["thumb_path"]) if photo else QIcon(), text)
            entry.setData(Qt.UserRole, match["item_id"])
            self.results.addItem(entry)
        self.results.setCurrentRow(0)

    # -------------------------------------------------------------- действия
    def _current(self):
        item = self.results.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _open(self) -> None:
        item_id = self._current()
        if item_id:
            self.open_item.emit(item_id)
            self.accept()

    def _add(self) -> None:
        item_id = self._current()
        if item_id:
            self.add_to_rental.emit(item_id)
            self.accept()
