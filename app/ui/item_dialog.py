# -*- coding: utf-8 -*-
"""Добавление и редактирование карточки реквизита, работа с фотографиями."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox,
                               QVBoxLayout, QWidget)

from ..errors import AppError
from ..services.categories_service import categories_service
from ..services.inventory_service import inventory_service
from ..services.photo_service import photo_service
from ..version import RENTAL_PERIOD_DAYS
from .errors import confirm, show_error
from .widgets.common import scrollable_body

PHOTO_KINDS = [("main", "Основная"), ("front", "Вид спереди"), ("side", "Вид сбоку"),
               ("detail", "Детали"), ("damage", "Повреждения"), ("other", "Прочее")]


class ItemDialog(QDialog):
    def __init__(self, parent=None, item_id: int | None = None):
        super().__init__(parent)
        self.item_id = item_id
        self.setWindowTitle("Карточка реквизита" if item_id else "Новый реквизит")
        self.resize(760, 640)
        self._build()
        if item_id:
            self._load()
        else:
            self.inv_number.setText(inventory_service.next_inv_number())

    def _build(self) -> None:
        # длинная форма прокручивается, кнопки сохранения закреплены внизу
        root, footer = scrollable_body(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.inv_number = QLineEdit()
        self.name = QLineEdit()
        self.category = QComboBox()
        self._fill_categories()
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(70)
        self.keywords = QLineEdit()
        self.keywords.setPlaceholderText("через пробел или запятую: стул венский красный дерево")
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(60)
        self.quantity = QSpinBox()
        self.quantity.setRange(0, 100000)
        self.quantity.setValue(1)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 10_000_000)
        self.price.setSuffix(f" \u20bd за {RENTAL_PERIOD_DAYS} дн.")
        self.valuation = QDoubleSpinBox()
        self.valuation.setRange(0, 100_000_000)
        self.valuation.setSuffix(" \u20bd")
        self.status = QComboBox()
        self.status.addItem("На складе", "in_stock")
        self.status.addItem("В ремонте", "repair")

        form.addRow("Инвентарный №:", self.inv_number)
        form.addRow("Название*:", self.name)
        form.addRow("Категория:", self.category)
        form.addRow("Описание:", self.description)
        form.addRow("Ключевые слова:", self.keywords)
        form.addRow("Количество:", self.quantity)
        form.addRow(f"Цена за {RENTAL_PERIOD_DAYS} дн.:", self.price)
        form.addRow("Оценочная стоимость:", self.valuation)
        form.addRow("Примечание:", self.note)
        form.addRow("Статус:", self.status)
        root.addLayout(form)

        photos_row = QHBoxLayout()
        self.photos = QListWidget()
        self.photos.setViewMode(QListWidget.IconMode)
        self.photos.setIconSize(QSize(120, 120))
        self.photos.setFixedHeight(160)
        self.photos.setSpacing(6)
        photos_row.addWidget(self.photos, 1)

        buttons = QVBoxLayout()
        self.add_photo_btn = QPushButton("Добавить фото…")
        self.add_photo_btn.clicked.connect(self._add_photos)
        self.camera_btn = QPushButton("Снять камерой…")
        self.camera_btn.clicked.connect(self._capture)
        self.primary_btn = QPushButton("Сделать основной")
        self.primary_btn.clicked.connect(self._set_primary)
        self.del_photo_btn = QPushButton("Удалить фото")
        self.del_photo_btn.clicked.connect(self._delete_photo)
        for btn in (self.add_photo_btn, self.camera_btn, self.primary_btn, self.del_photo_btn):
            buttons.addWidget(btn)
        buttons.addStretch()
        photos_row.addLayout(buttons)
        root.addLayout(photos_row)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Save).setText("Сохранить")
        box.button(QDialogButtonBox.Save).setObjectName("Primary")
        box.button(QDialogButtonBox.Cancel).setText("Отмена")
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        footer.addWidget(box)
        root.addStretch()

        if not self.item_id:
            for btn in (self.primary_btn, self.del_photo_btn):
                btn.setEnabled(False)

    def _fill_categories(self) -> None:
        self.category.clear()
        self.category.addItem("— без категории —", None)

        def walk(nodes, depth=0):
            for node in nodes:
                self.category.addItem("    " * depth + node["name"], node["id"])
                walk(node["children"], depth + 1)

        walk(categories_service.tree())

    def _load(self) -> None:
        item = inventory_service.get(self.item_id)
        if not item:
            return
        self.inv_number.setText(item["inv_number"])
        self.inv_number.setReadOnly(True)
        self.name.setText(item["name"])
        index = self.category.findData(item["category_id"])
        self.category.setCurrentIndex(max(0, index))
        self.description.setPlainText(item["description"])
        self.keywords.setText(item["keywords"])
        self.note.setPlainText(item["note"])
        self.quantity.setValue(item["quantity_total"])
        self.price.setValue(item["rental_price"])
        self.valuation.setValue(item["valuation"])
        self.status.setCurrentIndex(max(0, self.status.findData(item["status"])))
        self._reload_photos()

    def _reload_photos(self) -> None:
        self.photos.clear()
        if not self.item_id:
            return
        for photo in photo_service.photos_of(self.item_id):
            label = dict(PHOTO_KINDS).get(photo["kind"], photo["kind"])
            if photo["is_primary"]:
                label = "Основная · " + label
            entry = QListWidgetItem(QIcon(photo["thumb_path"]), label)
            entry.setData(Qt.UserRole, photo["id"])
            self.photos.addItem(entry)

    # ---------------------------------------------------------------- фото
    def _ensure_saved(self) -> bool:
        """Фото привязываются к предмету, поэтому карточку нужно сначала сохранить."""
        if self.item_id:
            return True
        try:
            self.item_id = inventory_service.create(self._collect())
            self.primary_btn.setEnabled(True)
            self.del_photo_btn.setEnabled(True)
            return True
        except AppError as exc:
            show_error(self, exc)
            return False

    def _add_photos(self) -> None:
        if not self._ensure_saved():
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Выберите фотографии", "", "Изображения (*.jpg *.jpeg *.png *.bmp *.webp *.heic *.heif)")
        for path in files:
            try:
                photo_service.add_photo(self.item_id, Path(path))
            except Exception as exc:
                show_error(self, exc, "Не удалось добавить фотографию.")
        self._reload_photos()

    def _capture(self) -> None:
        if not self._ensure_saved():
            return
        from .camera_dialog import CameraDialog
        dialog = CameraDialog(self)
        if dialog.exec() and dialog.captured_path:
            try:
                photo_service.add_photo(self.item_id, dialog.captured_path)
            except Exception as exc:
                show_error(self, exc, "Не удалось сохранить снимок.")
            self._reload_photos()

    def _set_primary(self) -> None:
        current = self.photos.currentItem()
        if current:
            photo_service.set_primary(current.data(Qt.UserRole))
            self._reload_photos()

    def _delete_photo(self) -> None:
        current = self.photos.currentItem()
        if current and confirm(self, "Удалить эту фотографию?"):
            photo_service.delete_photo(current.data(Qt.UserRole))
            self._reload_photos()

    # ------------------------------------------------------------- сохранение
    def _collect(self) -> dict:
        return {
            "inv_number": self.inv_number.text().strip(),
            "name": self.name.text().strip(),
            "category_id": self.category.currentData(),
            "description": self.description.toPlainText().strip(),
            "keywords": self.keywords.text().strip(),
            "note": self.note.toPlainText().strip(),
            "quantity_total": self.quantity.value(),
            "rental_price": self.price.value(),
            "valuation": self.valuation.value(),
            "status": self.status.currentData(),
        }

    def _save(self) -> None:
        try:
            if self.item_id:
                inventory_service.update(self.item_id, self._collect())
            else:
                self.item_id = inventory_service.create(self._collect())
            self.accept()
        except AppError as exc:
            show_error(self, exc)
        except Exception as exc:
            show_error(self, exc, "Не удалось сохранить карточку реквизита.")
