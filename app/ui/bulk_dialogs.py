# -*- coding: utf-8 -*-
"""Диалоги массовых действий каталога: выбор категории, правка нескольких предметов, дубли."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout)

from ..services.categories_service import categories_service
from .theme import Space
from .widgets.common import hint, muted, scrollable_body


class CategoryPicker(QDialog):
    """Выбор категории в дереве. result_id — выбранная категория, None — «без категории»."""

    def __init__(self, parent=None, title: str = "Выберите категорию",
                 note: str = "", exclude_id: Optional[int] = None,
                 allow_none: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 560)
        self.result_id: Optional[int] = None
        self._exclude = exclude_id

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        root.setSpacing(Space.MD)
        if note:
            root.addWidget(muted(note))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Быстрый поиск по названию категории…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        root.addWidget(self.filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(lambda *_: self._accept())
        root.addWidget(self.tree, 1)
        if allow_none:
            none_node = QTreeWidgetItem(["Без категории"])
            none_node.setData(0, Qt.UserRole, 0)
            self.tree.addTopLevelItem(none_node)
        for node in categories_service.tree():
            made = self._node(node)
            if made is not None:
                self.tree.addTopLevelItem(made)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok_btn = QPushButton("Выбрать")
        self.ok_btn.setObjectName("Primary")
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._accept)
        buttons.addWidget(self.ok_btn)
        root.addLayout(buttons)

    def _node(self, node: dict) -> Optional[QTreeWidgetItem]:
        if node["id"] == self._exclude:
            return None                      # категорию нельзя вложить в саму себя
        item = QTreeWidgetItem([node["name"]])
        item.setData(0, Qt.UserRole, node["id"])
        for child in node["children"]:
            made = self._node(child)
            if made is not None:
                item.addChild(made)
        return item

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            own = needle in item.text(0).lower()
            child_match = False
            for index in range(item.childCount()):
                child_match = visit(item.child(index)) or child_match
            item.setHidden(bool(needle) and not (own or child_match))
            if needle and child_match:
                item.setExpanded(True)
            return own or child_match

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def _accept(self) -> None:
        current = self.tree.currentItem()
        if current is None:
            return
        value = current.data(0, Qt.UserRole)
        self.result_id = value or None
        self.accept()


class BulkEditDialog(QDialog):
    """Правка нескольких предметов сразу: цена, оценка, ключевые слова."""

    def __init__(self, count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Изменить выбранные предметы")
        self.setMinimumWidth(460)
        self.values: dict = {}
        body, footer = scrollable_body(self)

        body.addWidget(muted(
            f"Выбрано предметов: {count}. Меняются только отмеченные поля, "
            "остальное остаётся как есть."))
        form = QFormLayout()
        form.setVerticalSpacing(Space.MD)

        self.price_on = QCheckBox("Цена аренды, ₽")
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 10_000_000)
        self.price.setDecimals(2)
        self.price.setEnabled(False)
        self.price_on.toggled.connect(self.price.setEnabled)
        form.addRow(self.price_on, self.price)

        self.valuation_on = QCheckBox("Оценочная стоимость, ₽")
        self.valuation = QDoubleSpinBox()
        self.valuation.setRange(0, 100_000_000)
        self.valuation.setDecimals(2)
        self.valuation.setEnabled(False)
        self.valuation_on.toggled.connect(self.valuation.setEnabled)
        form.addRow(self.valuation_on, self.valuation)

        self.keywords_on = QCheckBox("Добавить ключевые слова")
        self.keywords = QLineEdit()
        self.keywords.setPlaceholderText("например: ретро советский 1970-е")
        self.keywords.setEnabled(False)
        self.keywords_on.toggled.connect(self.keywords.setEnabled)
        form.addRow(self.keywords_on, self.keywords)
        body.addLayout(form)
        body.addWidget(hint("Ключевые слова добавляются к уже имеющимся, без повторов."))

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        save = QPushButton("Применить")
        save.setObjectName("Primary")
        save.setDefault(True)
        save.clicked.connect(self._accept)
        buttons.addWidget(save)
        footer.addLayout(buttons)

    def _accept(self) -> None:
        values = {}
        if self.price_on.isChecked():
            values["rental_price"] = self.price.value()
        if self.valuation_on.isChecked():
            values["valuation"] = self.valuation.value()
        if self.keywords_on.isChecked() and self.keywords.text().strip():
            values["add_keywords"] = self.keywords.text().strip()
        if not values:
            self.reject()
            return
        self.values = values
        self.accept()


class DuplicatesDialog(QDialog):
    """Список возможных дублей: одинаковые названия и почти одинаковые фотографии."""

    open_item = Signal(int)

    def __init__(self, groups: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Проверка дублей")
        self.resize(720, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        root.setSpacing(Space.MD)
        if not groups:
            empty = QLabel("Дублей не найдено: одинаковых названий и почти "
                           "одинаковых фотографий в каталоге нет.")
            empty.setObjectName("EmptyNote")
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            root.addWidget(empty, 1)
        else:
            root.addWidget(muted(
                "Программа ничего не удаляет и не объединяет сама: это список для "
                "проверки. Двойной щелчок по строке открывает карточку."))
            self.tree = QTreeWidget()
            self.tree.setHeaderLabels(["Предмет", "Инв. №", "Кол-во"])
            self.tree.setColumnWidth(0, 440)
            self.tree.itemDoubleClicked.connect(self._open)
            for group in groups:
                title = group["reason"]
                if group.get("percent") is not None:
                    title += f" · сходство {group['percent']} %"
                node = QTreeWidgetItem([f"{title} — {len(group['items'])} шт."])
                node.setFirstColumnSpanned(True)
                for item in group["items"]:
                    child = QTreeWidgetItem([item["name"], item["inv_number"],
                                             str(item.get("quantity_total", ""))])
                    child.setData(0, Qt.UserRole, item["id"])
                    node.addChild(child)
                self.tree.addTopLevelItem(node)
                node.setExpanded(True)
            root.addWidget(self.tree, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        root.addLayout(buttons)

    def _open(self, item: QTreeWidgetItem) -> None:
        item_id = item.data(0, Qt.UserRole)
        if item_id:
            self.open_item.emit(int(item_id))
            self.accept()
