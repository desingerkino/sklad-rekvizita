# -*- coding: utf-8 -*-
"""
Каталог и поиск — главный экран версии 2.1.

Композиция из референса: слева карточка с деревом категорий, справа рабочая
область с крупной строкой поиска, переключателем «плитка / таблица» и
карточками реквизита, где фотография — главный элемент. Логика поиска,
фильтрации и действий не менялась.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QSplitter,
                               QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget, QCheckBox)

from ..errors import AppError
from ..services.categories_service import categories_service
from ..services.inventory_service import inventory_service
from ..services.photo_service import photo_service
from ..utils import money_plain
from ..version import RENTAL_PERIOD_DAYS
from .errors import confirm, info, show_error
from .theme import Color, Space
from .widgets.common import (Card, cell, hint, icon, make_table, muted, section_title)
from .widgets.flow_layout import FlowLayout
from .widgets.item_tile import ItemTile, availability_status

PAGE_SIZE = 120


class CatalogView(QWidget):
    open_item = Signal(int)
    add_to_rental = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []
        self.category_id = None
        self.selected_id: int | None = None
        self._tiles: dict[int, ItemTile] = {}
        self._build()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self.reload_items)
        self.reload_categories()
        self.reload_items()

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.LG)

        search_row = QHBoxLayout()
        search_row.setSpacing(Space.MD)
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText(
            "Поиск реквизита…  название, номер, описание, примечание, ключевые слова")
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(46)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        search_row.addWidget(self.search, 1)

        self.photo_btn = QPushButton("  Поиск по фото")
        self.photo_btn.setIcon(icon("camera"))
        self.photo_btn.setIconSize(QSize(18, 18))
        self.photo_btn.setMinimumHeight(46)
        self.photo_btn.setCursor(Qt.PointingHandCursor)
        self.photo_btn.clicked.connect(self._photo_search)
        search_row.addWidget(self.photo_btn)

        self.new_btn = QPushButton("  Добавить реквизит")
        self.new_btn.setObjectName("Primary")
        self.new_btn.setIcon(icon("plus", "white"))
        self.new_btn.setIconSize(QSize(18, 18))
        self.new_btn.setMinimumHeight(46)
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.clicked.connect(self.new_item)
        search_row.addWidget(self.new_btn)
        root.addLayout(search_row)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        # ---------------------------------------------------- категории
        categories_card = Card(padding=Space.MD)
        categories_card.setMinimumWidth(230)
        categories_card.setMaximumWidth(360)
        header = QHBoxLayout()
        header.addWidget(section_title("Категории"))
        header.addStretch()
        categories_card.body.addLayout(header)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(True)
        self.tree.itemSelectionChanged.connect(self._category_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._category_menu)
        self.tree.setStyleSheet("QTreeWidget { border: none; }")
        categories_card.body.addWidget(self.tree)
        categories_card.body.addWidget(hint("Правая кнопка мыши — добавить, "
                                            "переименовать или удалить категорию"))
        splitter.addWidget(categories_card)

        # ----------------------------------------------------- результаты
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(Space.MD)

        toolbar = QHBoxLayout()
        self.count_label = muted("")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch()
        self.only_available = QCheckBox("Только доступные")
        self.only_available.toggled.connect(self.reload_items)
        toolbar.addWidget(self.only_available)

        self.view_buttons = QButtonGroup(self)
        self.view_buttons.setExclusive(True)
        for index, (name, tip) in enumerate((("grid", "Плитка с фотографиями"),
                                             ("list", "Таблица"))):
            button = QPushButton()
            button.setObjectName("IconButton")
            button.setIcon(icon(name))
            button.setIconSize(QSize(18, 18))
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self.set_view_mode(i))
            self.view_buttons.addButton(button, index)
            toolbar.addWidget(button)
        self.view_buttons.button(0).setChecked(True)
        results_layout.addLayout(toolbar)

        self.views = QStackedWidget()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tiles_host = QWidget()
        self.tiles_layout = FlowLayout(tiles_host, margin=2, spacing=Space.LG)
        self.scroll.setWidget(tiles_host)
        self.views.addWidget(self.scroll)

        table_card = Card(padding=Space.SM)
        self.table = make_table(
            ["Инв. №", "Наименование", "Категория", "Всего", "Доступно", "В аренде",
             f"Цена за {RENTAL_PERIOD_DAYS} дн.", "Оценка", "Статус"], stretch_column=1)
        # Множественный выбор — для массовой печати этикеток (Ctrl / Shift).
        # Значение перечисления берётся у класса QAbstractItemView: в новых
        # версиях PySide6 обращение к нему через экземпляр таблицы
        # больше не работает.
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.doubleClicked.connect(self._open_selected)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._item_menu)
        table_card.body.addWidget(self.table)
        self.views.addWidget(table_card)
        results_layout.addWidget(self.views, 1)

        self.empty_label = QLabel("Ничего не найдено. Попробуйте изменить запрос "
                                  "или снять фильтр по категории.")
        self.empty_label.setObjectName("Hint")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setVisible(False)
        results_layout.addWidget(self.empty_label)

        actions = QHBoxLayout()
        self.open_btn = QPushButton("  Открыть карточку")
        self.open_btn.setIcon(icon("eye"))
        self.open_btn.setIconSize(QSize(18, 18))
        self.open_btn.clicked.connect(self._open_selected)
        actions.addWidget(self.open_btn)
        self.rent_btn = QPushButton("  Добавить в аренду")
        self.rent_btn.setIcon(icon("plus"))
        self.rent_btn.setIconSize(QSize(18, 18))
        self.rent_btn.clicked.connect(self._add_selected_to_rental)
        actions.addWidget(self.rent_btn)
        self.labels_btn = QPushButton("  Печать этикеток")
        self.labels_btn.setIcon(icon("printer"))
        self.labels_btn.setIconSize(QSize(18, 18))
        self.labels_btn.setToolTip(
            "В режиме таблицы можно выделить несколько строк мышью с Ctrl или Shift")
        self.labels_btn.clicked.connect(self._print_labels)
        actions.addWidget(self.labels_btn)
        self.excel_btn = QPushButton("  Выгрузить в Excel")
        self.excel_btn.setIcon(icon("file"))
        self.excel_btn.setIconSize(QSize(18, 18))
        self.excel_btn.setToolTip(
            "Выгружается то, что сейчас найдено. Ничего не выделено — весь склад.")
        self.excel_btn.clicked.connect(self._export_excel)
        actions.addWidget(self.excel_btn)
        self.import_btn = QPushButton("  Импорт из папки")
        self.import_btn.setIcon(icon("folder"))
        self.import_btn.setIconSize(QSize(18, 18))
        self.import_btn.setToolTip(
            "Загрузить реквизит пачкой из папки с фотографиями: "
            "подпапки станут категориями, имена файлов — названиями")
        self.import_btn.clicked.connect(self._import_from_folder)
        actions.addWidget(self.import_btn)
        actions.addStretch()
        results_layout.addLayout(actions)

        splitter.addWidget(results)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1000])
        root.addWidget(splitter, 1)

    def set_view_mode(self, index: int) -> None:
        self.views.setCurrentIndex(index)
        button = self.view_buttons.button(index)
        if button:
            button.setChecked(True)
        for i, name in enumerate(("grid", "list")):
            btn = self.view_buttons.button(i)
            if btn:
                btn.setIcon(icon(name, "active" if i == index else ""))

    # ------------------------------------------------------------- категории
    def reload_categories(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        all_node = QTreeWidgetItem(["Весь каталог", ""])
        all_node.setData(0, Qt.UserRole, None)
        all_node.setIcon(0, icon("folder"))
        self.tree.addTopLevelItem(all_node)
        for node in categories_service.tree():
            self.tree.addTopLevelItem(self._make_node(node))
        all_node.setSelected(True)
        self.tree.resizeColumnToContents(0)
        self.tree.blockSignals(False)

    def _make_node(self, node: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node["name"], str(node["total_count"] or "")])
        item.setData(0, Qt.UserRole, node["id"])
        item.setForeground(1, QColor(Color.TEXT_MUTED))
        for child in node["children"]:
            item.addChild(self._make_node(child))
        return item

    def _category_changed(self) -> None:
        items = self.tree.selectedItems()
        self.category_id = items[0].data(0, Qt.UserRole) if items else None
        self.reload_items()

    def _category_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QMenu(self)
        add_root = menu.addAction("Добавить категорию")
        cat_id = item.data(0, Qt.UserRole) if item else None
        add_child = menu.addAction("Добавить подкатегорию") if cat_id else None
        rename = menu.addAction("Переименовать") if cat_id else None
        delete = menu.addAction("Удалить") if cat_id else None
        chosen = menu.exec(self.tree.mapToGlobal(pos))
        if chosen is None:
            return
        try:
            if chosen == add_root:
                name, ok = QInputDialog.getText(self, "Новая категория", "Название:")
                if ok and name:
                    categories_service.add(name, None)
            elif add_child and chosen == add_child:
                name, ok = QInputDialog.getText(self, "Новая подкатегория", "Название:")
                if ok and name:
                    categories_service.add(name, cat_id)
            elif rename and chosen == rename:
                name, ok = QInputDialog.getText(self, "Переименовать", "Название:",
                                                text=item.text(0))
                if ok and name:
                    categories_service.rename(cat_id, name)
            elif delete and chosen == delete:
                if confirm(self, f"Удалить категорию «{item.text(0)}»?"):
                    categories_service.delete(cat_id)
            self.reload_categories()
            self.reload_items()
        except AppError as exc:
            show_error(self, exc)

    # --------------------------------------------------------------- каталог
    def reload_items(self) -> None:
        try:
            items = inventory_service.search(
                self.search.text(), self.category_id, self.only_available.isChecked(),
                limit=PAGE_SIZE)
        except Exception as exc:
            show_error(self, exc, "Не удалось загрузить каталог.")
            return

        for item in items:
            item["category_path"] = categories_service.path(item["category_id"])

        self.items = items          # текущий результат поиска: его выгружает Excel
        self._fill_tiles(items)
        self._fill_table(items)
        self.empty_label.setVisible(not items)
        self.views.setVisible(bool(items))
        total = inventory_service.count()
        shown = f"Показано {len(items)}" + (f" из {total}" if len(items) >= PAGE_SIZE else "")
        self.count_label.setText(f"{shown}   ·   всего в каталоге: {total}")
        self._update_actions()

    def _fill_tiles(self, items: list[dict]) -> None:
        self.tiles_layout.clear()
        self._tiles.clear()
        for item in items:
            photo = photo_service.primary_photo(item["id"])
            tile = ItemTile(item, photo["thumb_path"] if photo else None)
            tile.clicked.connect(self._select_item)
            tile.double_clicked.connect(self.open_item.emit)
            self.tiles_layout.addWidget(tile)
            self._tiles[item["id"]] = tile
        if self.selected_id in self._tiles:
            self._tiles[self.selected_id].set_selected(True)

    def _fill_table(self, items: list[dict]) -> None:
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            _, status_label = availability_status(item)
            self.table.setItem(row, 0, cell(item["inv_number"], data=item["id"]))
            self.table.setItem(row, 1, cell(item["name"]))
            self.table.setItem(row, 2, cell(item["category_path"]))
            self.table.setItem(row, 3, cell(item["quantity_total"], True))
            self.table.setItem(row, 4, cell(item["available_now"], True))
            self.table.setItem(row, 5, cell(item["reserved_now"], True))
            self.table.setItem(row, 6, cell(money_plain(item["rental_price"]), True))
            self.table.setItem(row, 7, cell(money_plain(item["valuation"]), True))
            self.table.setItem(row, 8, cell(status_label))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

    # -------------------------------------------------------------- выделение
    def _select_item(self, item_id: int) -> None:
        self.selected_id = item_id
        for tile_id, tile in self._tiles.items():
            tile.set_selected(tile_id == item_id)
        self._update_actions()

    def _table_selection_changed(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.selected_id = self.table.item(row, 0).data(Qt.UserRole)
            self._update_actions()

    def selected_item_id(self) -> int | None:
        if self.views.currentIndex() == 1:
            row = self.table.currentRow()
            return self.table.item(row, 0).data(Qt.UserRole) if row >= 0 else None
        return self.selected_id

    def selected_item_ids(self) -> list[int]:
        """
        Все выделенные предметы. В режиме таблицы — выделенные строки,
        в режиме плитки — один выбранный предмет.
        """
        if self.views.currentIndex() == 1:
            ids = []
            model = self.table.selectionModel()
            for index in (model.selectedRows() if model else []):
                item = self.table.item(index.row(), 0)
                if item is not None:
                    ids.append(item.data(Qt.UserRole))
            if ids:
                return ids
        item_id = self.selected_item_id()
        return [item_id] if item_id else []

    def _print_labels(self) -> None:
        ids = self.selected_item_ids()
        if not ids:
            info(self, "Выберите хотя бы один предмет.", "Ничего не выбрано")
            return
        from .label_dialog import LabelPrintDialog
        LabelPrintDialog(ids, self).exec()

    def _export_excel(self) -> None:
        """
        Выгрузка в Excel. Выгружается то, что видит пользователь: выделенные
        строки, а если ничего не выделено — весь текущий результат поиска.
        """
        from .. import paths
        from ..services.excel_service import excel_service

        ids = self.selected_item_ids()
        if not ids:
            ids = [item["id"] for item in self.items]
        if not ids:
            info(self, "Каталог пуст — выгружать нечего.", "Нет данных")
            return
        target = paths.DOCS_DIR / "Склад.xlsx"
        try:
            excel_service.export_inventory(target, item_ids=ids)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Выгрузить в Excel не удалось.")
            return
        from .settings_view import open_folder
        info(self, f"Выгружено позиций: {len(ids)}\n{target}")
        open_folder(target.parent)

    def _import_from_folder(self) -> None:
        from .import_dialog import ImportDialog

        dialog = ImportDialog(self)
        dialog.exec()
        if dialog.imported:
            self.reload_categories()
            self.reload_items()

    def _update_actions(self) -> None:
        item_id = self.selected_item_id()
        self.open_btn.setEnabled(item_id is not None)
        self.labels_btn.setEnabled(bool(self.selected_item_ids()))
        available = False
        if item_id is not None:
            item = inventory_service.get(item_id)
            available = bool(item and item["available_now"] > 0)
        self.rent_btn.setEnabled(available)
        self.rent_btn.setToolTip("" if available else
                                 "Свободных экземпляров нет или предмет не выбран")

    def _open_selected(self) -> None:
        item_id = self.selected_item_id()
        if item_id:
            self.open_item.emit(item_id)

    def _add_selected_to_rental(self) -> None:
        item_id = self.selected_item_id()
        if item_id:
            self.add_to_rental.emit(item_id)

    def _item_menu(self, pos) -> None:
        if self.selected_item_id() is None:
            return
        menu = QMenu(self)
        act_open = menu.addAction("Открыть карточку")
        act_rent = menu.addAction("Добавить в аренду")
        act_label = menu.addAction("Печать этикеток")
        chosen = menu.exec(self.table.mapToGlobal(pos))
        if chosen == act_label:
            self._print_labels()
            return
        if chosen == act_open:
            self._open_selected()
        elif chosen == act_rent:
            self._add_selected_to_rental()

    # ----------------------------------------------------------------- прочее
    def new_item(self) -> None:
        from .item_dialog import ItemDialog
        dialog = ItemDialog(self)
        if dialog.exec():
            self.reload_categories()
            self.reload_items()
            if dialog.item_id:
                self.open_item.emit(dialog.item_id)

    def _photo_search(self) -> None:
        from .photo_search_dialog import PhotoSearchDialog
        dialog = PhotoSearchDialog(self)
        dialog.open_item.connect(self.open_item.emit)
        dialog.add_to_rental.connect(self.add_to_rental.emit)
        dialog.exec()
