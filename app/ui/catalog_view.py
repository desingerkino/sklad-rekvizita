# -*- coding: utf-8 -*-
"""
Каталог и поиск — главный рабочий экран.

Версия 4.0. Слева дерево категорий, справа — предметы плиткой или таблицей.
Обе раскладки показывают одну модель данных (widgets/catalog_grid.py):
карточки рисуются делегатом и подгружаются по мере прокрутки, поэтому каталог
листается целиком на любом размере склада. Плитка сама перестраивается под
ширину окна (QListView с ResizeMode.Adjust заменил прежний FlowLayout).

Что умеет экран:
  • поиск по названию, номеру, описанию, примечанию и ключевым словам;
  • быстрые списки: весь каталог, избранное, недавно открытые, списанные;
  • фильтры по доступности, наличию фото и цене, сортировка;
  • выделение нескольких предметов (Ctrl / Shift / Ctrl+A) и действия над
    ними: в аренду, перенос в категорию, правка, этикетки, Excel, списание;
  • перенос предметов перетаскиванием на категорию;
  • работа с категориями правой кнопкой: добавить, переименовать, перенести,
    удалить.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDoubleSpinBox,
                               QSizePolicy,
                               QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListView, QMenu, QPushButton, QSplitter,
                               QStackedWidget, QTableView, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QWidgetAction)

from ..errors import AppError
from ..services.categories_service import categories_service
from ..services.inventory_service import inventory_service
from .errors import confirm, info, show_error
from .theme import Color, Space
from .widgets.catalog_grid import (DENSITIES, DENSITY_LABELS, CatalogModel, ItemCardDelegate,
                                   ItemRole, ids_from_mime)
from .widgets.common import Card, hint, icon, muted, section_title
from .widgets.thumbs import thumbnails
from .widgets.toast import toast

SCOPES = (("all", "Весь каталог"), ("favorites", "Избранное"),
          ("recent", "Недавние"), ("archived", "Списанные"))


class CategoryTree(QTreeWidget):
    """Дерево категорий, принимающее перетаскиваемые предметы."""

    items_dropped = Signal(object, list)      # id категории (None — «без категории»), id предметов

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _target(self, event):
        position = event.position().toPoint() if hasattr(event, "position") else event.pos()
        return self.itemAt(position)

    def dragEnterEvent(self, event) -> None:                 # noqa: N802 — интерфейс Qt
        if ids_from_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:                  # noqa: N802
        item = self._target(event)
        if item is not None and ids_from_mime(event.mimeData()) \
                and item.data(0, Qt.UserRole + 1) != "all":
            self.setCurrentItem(item)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:                      # noqa: N802
        item = self._target(event)
        ids = ids_from_mime(event.mimeData())
        if item is None or not ids or item.data(0, Qt.UserRole + 1) == "all":
            event.ignore()
            return
        event.acceptProposedAction()
        self.items_dropped.emit(item.data(0, Qt.UserRole), ids)


class CatalogView(QWidget):
    open_item = Signal(int)
    add_to_rental = Signal(int)
    add_many_to_rental = Signal(list)
    photo_search_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.category_id = None
        self.scope = "all"
        self._prefs = QSettings("SkladRekvizita", "Interface")
        self.model = CatalogModel(self._fetch, self)
        self.model.total_changed.connect(self._update_count)
        thumbnails().loaded.connect(self.model.photo_ready)
        self._build()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self.reload_items)
        self.reload_categories()
        self.reload_items()

    # ---------------------------------------------------------------- данные
    @property
    def items(self) -> list[dict]:
        """Загруженные сейчас предметы (то, что уже есть в списке)."""
        return self.model.items()

    def _filters(self) -> dict:
        price_min = self.price_min.value() or None
        price_max = self.price_max.value() or None
        photo = self.photo_filter.currentData()
        return {
            "text": self.search.text(),
            "category_id": self.category_id if self.scope in ("all", "favorites") else None,
            "availability": self.availability.currentData() or "",
            "sort": self.sort.currentData() or "name",
            "favorites_only": self.scope == "favorites",
            "recent_only": self.scope == "recent",
            "archived": self.scope == "archived",
            "has_photo": {"yes": True, "no": False}.get(photo),
            "price_min": price_min,
            "price_max": price_max,
        }

    def _fetch(self, offset: int, limit: int) -> tuple[list[dict], int]:
        return inventory_service.search_page(limit=limit, offset=offset, **self._filters())

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        # ------------------------------------------------------ строка поиска
        search_row = QHBoxLayout()
        search_row.setSpacing(Space.MD)
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText(
            "Поиск: название, номер, описание, примечание, ключевые слова")
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(42)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        self.search.returnPressed.connect(self.reload_items)
        search_row.addWidget(self.search, 1)

        self.photo_btn = self._button("Поиск по фото", "camera", self._photo_search)
        self.photo_btn.setMinimumHeight(42)
        search_row.addWidget(self.photo_btn)

        self.more_btn = self._button("Ещё", "list", None)
        self.more_btn.setMinimumHeight(42)
        self.more_btn.setToolTip("Импорт из папки, выгрузка в Excel, проверка дублей")
        more = QMenu(self.more_btn)
        self.import_action = more.addAction(icon("folder"), "Импорт из папки…")
        self.import_action.triggered.connect(self._import_from_folder)
        self.excel_action = more.addAction(icon("file"), "Выгрузить в Excel (найденное)")
        self.excel_action.triggered.connect(self._export_excel)
        self.duplicates_action = more.addAction(icon("details"), "Проверить дубли…")
        self.duplicates_action.triggered.connect(self._check_duplicates)
        more.addSeparator()
        # Подменю создаётся с явным родителем и хранится в поле: так им владеет
        # меню «Ещё», и объект не исчезает, пока жив каталог.
        self.more_menu = more
        self.density_menu = QMenu("Размер плитки", more)
        more.addMenu(self.density_menu)
        for key, label in DENSITY_LABELS.items():
            action = self.density_menu.addAction(label)
            action.setCheckable(True)
            action.setData(key)
            action.triggered.connect(lambda _=False, k=key: self.set_density(k))
        self.more_btn.setMenu(more)
        search_row.addWidget(self.more_btn)

        self.new_btn = self._button("Добавить реквизит", "plus", self.new_item, "white")
        self.new_btn.setObjectName("Primary")
        self.new_btn.setMinimumHeight(42)
        self.new_btn.setToolTip("Новая карточка реквизита")
        search_row.addWidget(self.new_btn)
        root.addLayout(search_row)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.splitter = splitter

        # ---------------------------------------------------- категории
        categories_card = Card(padding=Space.MD)
        categories_card.setMinimumWidth(250)
        categories_card.setMaximumWidth(380)
        header = QHBoxLayout()
        header.addWidget(section_title("Категории"))
        header.addStretch()
        add_category = QPushButton()
        add_category.setObjectName("IconButton")
        add_category.setIcon(icon("plus"))
        add_category.setIconSize(QSize(16, 16))
        add_category.setToolTip("Добавить категорию")
        add_category.setCursor(Qt.PointingHandCursor)
        add_category.clicked.connect(lambda: self._add_category(None))
        header.addWidget(add_category)
        categories_card.body.addLayout(header)
        self.tree = CategoryTree()
        self.tree.setObjectName("CategoryTree")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setRootIsDecorated(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._category_changed)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._category_menu)
        self.tree.items_dropped.connect(self._dropped_on_category)
        categories_card.body.addWidget(self.tree, 1)
        categories_card.body.addWidget(hint(
            "Правая кнопка — действия с категорией. Предметы можно перетащить "
            "на категорию мышью."))
        splitter.addWidget(categories_card)

        # ----------------------------------------------------- результаты
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(Space.MD, 0, 0, 0)   # отступ от дерева категорий
        results_layout.setSpacing(Space.SM)

        # быстрые списки
        scope_row = QHBoxLayout()
        scope_row.setSpacing(Space.XS)
        self.scope_buttons = QButtonGroup(self)
        self.scope_buttons.setExclusive(True)
        for index, (key, label) in enumerate(SCOPES):
            button = QPushButton(label)
            button.setObjectName("SegmentButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty("scope", key)
            button.clicked.connect(lambda _=False, k=key: self.set_scope(k))
            self.scope_buttons.addButton(button, index)
            scope_row.addWidget(button)
        self.scope_buttons.button(0).setChecked(True)
        scope_row.addStretch()
        self.reset_btn = QPushButton("Сбросить фильтры")
        self.reset_btn.setObjectName("LinkButton")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.clicked.connect(self.reset_filters)
        self.reset_btn.setVisible(False)
        scope_row.addWidget(self.reset_btn)
        self.filter_btn = self._button("Фильтры", "settings", None)
        self.filter_btn.setToolTip("Фильтр по наличию фотографии и по цене")
        self._build_filter_menu()
        scope_row.addWidget(self.filter_btn)
        results_layout.addLayout(scope_row)

        # вторая строка: счётчик слева, доступность / сортировка / вид справа.
        # Две строки вместо одной: на узком окне кнопки не сжимаются и не
        # обрезаются.
        toolbar = QHBoxLayout()
        toolbar.setSpacing(Space.SM)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        toolbar.addWidget(self.count_label)
        toolbar.addStretch(1)

        self.availability = QComboBox()
        self.availability.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.availability.setMinimumContentsLength(14)
        for key, label in inventory_service.AVAILABILITY.items():
            self.availability.addItem(label, key)
        self.availability.setToolTip("Фильтр по доступности на сегодня")
        self.availability.currentIndexChanged.connect(self.reload_items)
        toolbar.addWidget(self.availability)

        self.sort = QComboBox()
        self.sort.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sort.setMinimumContentsLength(21)
        for key, (label, _sql) in inventory_service.SORTS.items():
            self.sort.addItem(label, key)
        self.sort.setToolTip("Порядок предметов в каталоге")
        saved_sort = self._prefs.value("catalog/sort", "name")
        self.sort.setCurrentIndex(max(0, self.sort.findData(saved_sort)))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        toolbar.addWidget(self.sort)

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
        results_layout.addLayout(toolbar)

        # ------------------------------------------------------------ виды
        self.views = QStackedWidget()

        self.delegate = ItemCardDelegate(self, self._prefs.value("catalog/density", "normal"))
        self.delegate.favorite_toggled.connect(self._set_favorite)
        self.grid = QListView()
        self.grid.setObjectName("CatalogGrid")
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(self.delegate)
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)      # перестройка под ширину
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setFlow(QListView.Flow.LeftToRight)
        self.grid.setWrapping(True)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(0)
        self.grid.setLayoutMode(QListView.LayoutMode.Batched)
        self.grid.setBatchSize(120)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid.setMouseTracking(True)
        self.grid.setFrameShape(QFrame.NoFrame)
        self.grid.setDragEnabled(True)
        self.grid.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.grid.verticalScrollBar().setSingleStep(36)
        self.grid.doubleClicked.connect(self._open_index)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(
            lambda pos: self._item_menu(self.grid, pos))
        self.grid.installEventFilter(self)
        self.grid.viewport().installEventFilter(self)
        self.views.addWidget(self.grid)

        table_card = Card(padding=Space.SM)
        self.table = QTableView()
        self.table.setObjectName("CatalogTable")
        self.table.setModel(self.model)
        self.table.setSelectionModel(self.grid.selectionModel())   # одно выделение на оба вида
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Множественный выбор — для массовых действий (Ctrl / Shift).
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header_view = self.table.horizontalHeader()
        header_view.setHighlightSections(False)
        header_view.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header_view.setMaximumSectionSize(420)
        header_view.setSectionResizeMode(QHeaderView.Interactive)
        header_view.setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in ((0, 96), (2, 220), (3, 70), (4, 90), (5, 90), (6, 120),
                              (7, 110), (8, 200)):
            self.table.setColumnWidth(column, width)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.table.doubleClicked.connect(self._open_index)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(
            lambda pos: self._item_menu(self.table, pos))
        self.table.installEventFilter(self)
        table_card.body.addWidget(self.table)
        self.views.addWidget(table_card)

        # пустое состояние
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(Space.MD)
        self.empty_title = QLabel("")
        self.empty_title.setObjectName("EmptyTitle")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_text = QLabel("")
        self.empty_text.setObjectName("Muted")
        self.empty_text.setAlignment(Qt.AlignCenter)
        self.empty_text.setWordWrap(True)
        self.empty_text.setMaximumWidth(520)
        empty_buttons = QHBoxLayout()
        empty_buttons.setAlignment(Qt.AlignCenter)
        self.empty_reset = QPushButton("Сбросить фильтры")
        self.empty_reset.clicked.connect(self.reset_filters)
        self.empty_add = QPushButton("Добавить реквизит")
        self.empty_add.setObjectName("Primary")
        self.empty_add.clicked.connect(self.new_item)
        self.empty_import = QPushButton("Импорт из папки с фотографиями")
        self.empty_import.clicked.connect(self._import_from_folder)
        for button in (self.empty_reset, self.empty_add, self.empty_import):
            empty_buttons.addWidget(button)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_text, 0, Qt.AlignHCenter)
        empty_layout.addLayout(empty_buttons)
        self.views.addWidget(self.empty)
        results_layout.addWidget(self.views, 1)

        self.grid.selectionModel().selectionChanged.connect(self._update_actions)

        # ------------------------------------------- панель действий с выбранным
        self.actions_bar = QFrame()
        self.actions_bar.setObjectName("SelectionBar")
        actions = QHBoxLayout(self.actions_bar)
        actions.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        actions.setSpacing(Space.SM)
        self.selected_label = QLabel("")
        self.selected_label.setObjectName("ValueStrong")
        actions.addWidget(self.selected_label)
        self.open_btn = self._button("Открыть карточку", "eye", self._open_selected)
        self.rent_btn = self._button("Добавить в аренду", "plus", self._add_selected_to_rental)
        self.move_btn = self._button("Переместить…", "folder", self._move_selected)
        self.move_btn.setToolTip("Перенести выбранные предметы в другую категорию")
        self.edit_btn = self._button("Изменить…", "edit", self._edit_selected)
        self.edit_btn.setToolTip("Цена, оценка и ключевые слова сразу у всех выбранных")
        self.labels_btn = self._button("Печать этикеток", "printer", self._print_labels)
        self.labels_btn.setToolTip("Этикетки со штрихкодом для выбранных предметов")
        self.excel_btn = self._button("Выгрузить в Excel", "file", self._export_excel)
        self.excel_btn.setToolTip("Выгружаются выбранные предметы")
        self.archive_btn = self._button("Списать", "trash", self._archive_selected)
        self.archive_btn.setObjectName("Danger")
        self.restore_btn = self._button("Вернуть в каталог", "return-stock",
                                        self._restore_selected)
        for button in (self.open_btn, self.rent_btn, self.move_btn, self.edit_btn,
                       self.labels_btn, self.excel_btn, self.archive_btn, self.restore_btn):
            actions.addWidget(button)
        actions.addStretch()
        self.clear_btn = QPushButton("Снять выделение")
        self.clear_btn.setObjectName("LinkButton")
        self.clear_btn.setCursor(Qt.PointingHandCursor)
        self.clear_btn.clicked.connect(self.clear_selection)
        actions.addWidget(self.clear_btn)
        results_layout.addWidget(self.actions_bar)

        self.tip_label = hint(
            "Двойной щелчок — открыть карточку.  Ctrl или Shift + щелчок — выбрать "
            "несколько.  Звёздочка на фото — добавить в избранное.")
        results_layout.addWidget(self.tip_label)

        splitter.addWidget(results)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([int(self._prefs.value("catalog/tree_width", 300)), 1100])
        splitter.splitterMoved.connect(
            lambda *_: self._prefs.setValue("catalog/tree_width", splitter.sizes()[0]))
        root.addWidget(splitter, 1)

        select_all = QAction(self)
        select_all.setShortcut(QKeySequence.SelectAll)
        select_all.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        select_all.triggered.connect(self.select_all)
        self.addAction(select_all)

        self.set_density(self.delegate.density(), save=False)
        self.set_view_mode(int(self._prefs.value("catalog/view", 0)), save=False)
        self._update_actions()

    @staticmethod
    def _button(text: str, icon_name: str, slot, variant: str = "") -> QPushButton:
        button = QPushButton("  " + text)
        button.setIcon(icon(icon_name, variant))
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.PointingHandCursor)
        if slot is not None:
            button.clicked.connect(slot)
        return button

    def _build_filter_menu(self) -> None:
        menu = QMenu(self.filter_btn)
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.setSpacing(Space.SM)
        layout.addWidget(muted("Фотография"))
        self.photo_filter = QComboBox()
        for key, label in (("", "Не важно"), ("yes", "Только с фотографией"),
                           ("no", "Только без фотографии")):
            self.photo_filter.addItem(label, key)
        self.photo_filter.currentIndexChanged.connect(self.reload_items)
        layout.addWidget(self.photo_filter)
        layout.addWidget(muted("Цена аренды, ₽"))
        price_row = QHBoxLayout()
        self.price_min = QDoubleSpinBox()
        self.price_max = QDoubleSpinBox()
        for box, prefix in ((self.price_min, "от "), (self.price_max, "до ")):
            box.setRange(0, 10_000_000)
            box.setDecimals(0)
            box.setSingleStep(100)
            box.setPrefix(prefix)
            box.setSpecialValueText(prefix + "—")
            box.setMinimumWidth(120)
            box.valueChanged.connect(lambda *_: self._debounce.start())
            price_row.addWidget(box)
        layout.addLayout(price_row)
        reset = QPushButton("Сбросить фильтры")
        reset.clicked.connect(self.reset_filters)
        layout.addWidget(reset)
        action = QWidgetAction(menu)
        action.setDefaultWidget(panel)
        menu.addAction(action)
        self.filter_btn.setMenu(menu)

    # ------------------------------------------------------------- вид списка
    def set_view_mode(self, index: int, save: bool = True) -> None:
        index = 1 if index == 1 else 0
        self._view_mode = index
        button = self.view_buttons.button(index)
        if button:
            button.setChecked(True)
        for i, name in enumerate(("grid", "list")):
            btn = self.view_buttons.button(i)
            if btn:
                btn.setIcon(icon(name, "active" if i == index else ""))
        if save:
            self._prefs.setValue("catalog/view", index)
        self._show_results()

    def view_mode(self) -> int:
        return self._view_mode

    def set_density(self, density: str, save: bool = True) -> None:
        if density not in DENSITIES:
            density = "normal"
        self.delegate.set_density(density)
        self._fit_grid()
        for action in self.density_menu.actions():
            action.setChecked(action.data() == density)
        if save:
            self._prefs.setValue("catalog/density", density)

    def _fit_grid(self) -> None:
        """
        Подгоняет ширину карточек так, чтобы ряд занимал всю ширину области:
        сколько карточек помещается при выбранном размере — столько и ставится,
        а остаток ширины делится между ними поровну. Пустой полосы справа нет.
        """
        gap = Space.MD
        available = self.grid.viewport().width() - 10      # запас: иначе ряд переносится
        if available <= 0:
            return
        base = self.delegate.base_width()
        columns = max(1, available // (base + gap))
        cell = available // columns
        self.delegate.set_card_width(cell - gap)
        size = self.delegate.card_size()
        grid_size = QSize(cell, size.height() + gap)
        if self.grid.gridSize() != grid_size:
            self.grid.setGridSize(grid_size)
            self.grid.doItemsLayout()

    def _sort_changed(self) -> None:
        self._prefs.setValue("catalog/sort", self.sort.currentData())
        self.reload_items()

    def set_scope(self, scope: str) -> None:
        self.scope = scope if scope in dict(SCOPES) else "all"
        for button in self.scope_buttons.buttons():
            button.setChecked(button.property("scope") == self.scope)
        self.tree.setEnabled(self.scope in ("all", "favorites"))
        self.sort.setEnabled(self.scope != "recent")
        self.reload_items()

    def filters_active(self) -> bool:
        return bool(self.search.text().strip() or self.category_id
                    or self.availability.currentData() or self.photo_filter.currentData()
                    or self.price_min.value() or self.price_max.value())

    def reset_filters(self) -> None:
        for widget in (self.search, self.availability, self.photo_filter,
                       self.price_min, self.price_max, self.tree):
            widget.blockSignals(True)
        self.search.clear()
        self.availability.setCurrentIndex(0)
        self.photo_filter.setCurrentIndex(0)
        self.price_min.setValue(0)
        self.price_max.setValue(0)
        self.tree.clearSelection()
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        for widget in (self.search, self.availability, self.photo_filter,
                       self.price_min, self.price_max, self.tree):
            widget.blockSignals(False)
        self.category_id = None
        self.reload_items()

    # ------------------------------------------------------------- категории
    def reload_categories(self) -> None:
        expanded = set()
        for item in self._all_tree_items():
            if item.isExpanded():
                expanded.add(item.data(0, Qt.UserRole))
        self.tree.blockSignals(True)
        self.tree.clear()
        all_node = QTreeWidgetItem(["Весь каталог", str(inventory_service.count())])
        all_node.setData(0, Qt.UserRole, None)
        all_node.setData(0, Qt.UserRole + 1, "all")
        all_node.setIcon(0, icon("folder"))
        all_node.setForeground(1, QColor(Color.TEXT_MUTED))
        self.tree.addTopLevelItem(all_node)
        for node in categories_service.tree():
            self.tree.addTopLevelItem(self._make_node(node))
        selected = None
        for item in self._all_tree_items():
            if item.data(0, Qt.UserRole) in expanded:
                item.setExpanded(True)
            if self.category_id and item.data(0, Qt.UserRole) == self.category_id:
                selected = item
        if selected is None:
            self.category_id = None
            selected = all_node
        self.tree.setCurrentItem(selected)
        self.tree.blockSignals(False)

    def _all_tree_items(self) -> list[QTreeWidgetItem]:
        result, stack = [], [self.tree.topLevelItem(i)
                             for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            result.append(item)
            stack += [item.child(i) for i in range(item.childCount())]
        return result

    def _make_node(self, node: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node["name"], str(node["total_count"] or "")])
        item.setData(0, Qt.UserRole, node["id"])
        item.setToolTip(0, node["name"])
        item.setForeground(1, QColor(Color.TEXT_MUTED))
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        for child in node["children"]:
            item.addChild(self._make_node(child))
        return item

    def select_category(self, category_id) -> None:
        """Открывает категорию в дереве (на любой глубине вложенности)."""
        if self.scope not in ("all", "favorites"):
            self.set_scope("all")
        for item in self._all_tree_items():
            if item.data(0, Qt.UserRole) == category_id:
                parent = item.parent()
                while parent is not None:
                    parent.setExpanded(True)
                    parent = parent.parent()
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return

    def _category_changed(self) -> None:
        items = self.tree.selectedItems()
        self.category_id = items[0].data(0, Qt.UserRole) if items else None
        self.reload_items()

    def _add_category(self, parent_id) -> None:
        title = "Новая подкатегория" if parent_id else "Новая категория"
        name, ok = QInputDialog.getText(self, title, "Название:")
        if not (ok and name.strip()):
            return
        try:
            new_id = categories_service.add(name, parent_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload_categories()
        self.select_category(new_id)
        toast(self, f"Категория «{name.strip()}» добавлена")

    def _category_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        cat_id = item.data(0, Qt.UserRole) if item else None
        menu = QMenu(self)
        add_root = menu.addAction("Добавить категорию")
        add_child = rename = move = delete = None
        if cat_id:
            add_child = menu.addAction("Добавить подкатегорию")
            menu.addSeparator()
            rename = menu.addAction("Переименовать…")
            move = menu.addAction("Перенести в другую категорию…")
            menu.addSeparator()
            delete = menu.addAction("Удалить")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        try:
            if chosen == add_root:
                self._add_category(None)
                return
            if chosen == add_child:
                self._add_category(cat_id)
                return
            if chosen == rename:
                name, ok = QInputDialog.getText(self, "Переименовать категорию", "Название:",
                                                text=item.text(0))
                if not (ok and name.strip()):
                    return
                categories_service.rename(cat_id, name)
                toast(self, "Категория переименована")
            elif chosen == move:
                from .bulk_dialogs import CategoryPicker
                picker = CategoryPicker(
                    self, "Куда перенести категорию",
                    f"Категория «{item.text(0)}» со всеми подкатегориями и предметами "
                    "переедет внутрь выбранной. «Без категории» — сделать её верхней.",
                    exclude_id=cat_id)
                if not picker.exec():
                    return
                categories_service.move(cat_id, picker.result_id)
                toast(self, "Категория перенесена")
            elif chosen == delete:
                if not confirm(self, f"Удалить категорию «{item.text(0)}»?\n\n"
                                     "Удалить можно только пустую категорию: без "
                                     "предметов и без подкатегорий."):
                    return
                categories_service.delete(cat_id)
                if self.category_id == cat_id:
                    self.category_id = None
                toast(self, "Категория удалена")
            self.reload_categories()
            self.reload_items()
        except AppError as exc:
            show_error(self, exc)

    def _dropped_on_category(self, category_id, ids: list) -> None:
        self._move_items(ids, category_id, ask=True)

    # --------------------------------------------------------------- каталог
    def reload_items(self) -> None:
        keep = set(self.selected_item_ids())
        try:
            self.model.reload()
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Не удалось загрузить каталог.")
            return
        self._show_results()
        if keep:
            self._select_ids(keep)
        self._update_actions()
        self.reset_btn.setVisible(self.filters_active())

    def reload(self) -> None:
        """Раздел открыт заново: обновить и дерево (счётчики), и список."""
        self.reload_categories()
        self.reload_items()

    def _show_results(self) -> None:
        if self.model.rowCount():
            self.views.setCurrentIndex(self._view_mode)
            return
        if self.scope == "favorites":
            title, text = "В избранном пока пусто", (
                "Нажмите звёздочку на фотографии предмета — он появится здесь. "
                "Избранное у каждого сотрудника своё.")
        elif self.scope == "recent":
            title, text = "Недавних карточек нет", (
                "Здесь появляются предметы, карточки которых вы открывали.")
        elif self.scope == "archived":
            title, text = "Списанных предметов нет", (
                "Списанный реквизит не удаляется: он хранится здесь вместе с историей "
                "аренд, и его можно вернуть в каталог.")
        elif self.filters_active():
            title, text = "Ничего не найдено", (
                "Попробуйте изменить запрос, выбрать «Весь каталог» или сбросить фильтры.")
        else:
            title, text = "Каталог пуст", (
                "Добавьте первый предмет или загрузите реквизит пачкой из папки с "
                "фотографиями: подпапки станут категориями, имена файлов — названиями.")
        self.empty_title.setText(title)
        self.empty_text.setText(text)
        nothing_at_all = self.scope == "all" and not self.filters_active()
        self.empty_reset.setVisible(self.filters_active())
        self.empty_add.setVisible(nothing_at_all)
        self.empty_import.setVisible(nothing_at_all)
        self.views.setCurrentIndex(2)

    def _update_count(self, loaded: int, total: int) -> None:
        if not total:
            self.count_label.setText("")
            return
        self.count_label.setText(f"Найдено: {total}")
        self.count_label.setToolTip(
            f"Загружено {loaded} из {total}; остальные подгружаются при прокрутке."
            if loaded < total else "")

    # -------------------------------------------------------------- выделение
    def _current_view(self) -> QAbstractItemView:
        return self.table if self._view_mode == 1 else self.grid

    def selected_item_ids(self) -> list[int]:
        """Все выделенные предметы в порядке списка (в плитке и в таблице одинаково)."""
        model = self.grid.selectionModel()
        if model is None:
            return []
        rows = sorted({index.row() for index in model.selectedIndexes()})
        return [self.model.item(row)["id"] for row in rows if self.model.item(row)]

    def selected_items(self) -> list[dict]:
        ids = set(self.selected_item_ids())
        return [item for item in self.model.items() if item["id"] in ids]

    def selected_item_id(self) -> int | None:
        ids = self.selected_item_ids()
        return ids[0] if ids else None

    def _select_ids(self, ids) -> None:
        from PySide6.QtCore import QItemSelection, QItemSelectionModel

        selection = QItemSelection()
        last = self.model.columnCount() - 1
        for row, item in enumerate(self.model.items()):
            if item["id"] in ids:
                selection.select(self.model.index(row, 0), self.model.index(row, last))
        self.grid.selectionModel().select(selection, QItemSelectionModel.ClearAndSelect)

    def select_all(self) -> None:
        """Выделяет всё найденное, а не только уже подгруженную часть."""
        self.model.load_all()
        self._select_ids({item["id"] for item in self.model.items()})

    def clear_selection(self) -> None:
        self.grid.selectionModel().clearSelection()

    def _update_actions(self, *_args) -> None:
        items = self.selected_items()
        count = len(items)
        archived = self.scope == "archived"
        self.actions_bar.setVisible(count > 0)
        self.tip_label.setVisible(count == 0 and self.model.rowCount() > 0)
        if not count:
            return
        self.selected_label.setText(
            f"Выбрано: {count}" if count > 1 else self._short(items[0]["name"]))
        self.selected_label.setToolTip(items[0]["name"] if count == 1 else "")
        self.open_btn.setVisible(count == 1)
        available = [item for item in items if item["available_now"] > 0]
        self.rent_btn.setVisible(not archived)
        self.rent_btn.setEnabled(bool(available))
        self.rent_btn.setText("  Добавить в аренду" + (f" ({len(available)})"
                                                      if count > 1 else ""))
        self.rent_btn.setToolTip(
            "" if available else "У выбранных предметов нет свободных экземпляров")
        for button in (self.move_btn, self.edit_btn, self.labels_btn, self.archive_btn):
            button.setVisible(not archived)
        self.restore_btn.setVisible(archived)

    @staticmethod
    def _short(text: str, limit: int = 46) -> str:
        return text if len(text) <= limit else text[:limit - 1] + "…"

    # --------------------------------------------------------------- действия
    def _open_index(self, index) -> None:
        item = index.data(ItemRole)
        if item:
            self.open_item.emit(item["id"])

    def _open_selected(self) -> None:
        item_id = self.selected_item_id()
        if item_id:
            self.open_item.emit(item_id)

    def _add_selected_to_rental(self) -> None:
        ids = [item["id"] for item in self.selected_items() if item["available_now"] > 0]
        if not ids:
            return
        if len(ids) == 1:
            self.add_to_rental.emit(ids[0])
        else:
            self.add_many_to_rental.emit(ids)

    def _set_favorite(self, item_id: int, favorite: bool) -> None:
        try:
            inventory_service.set_favorite(item_id, favorite)
        except AppError as exc:
            show_error(self, exc)
            return
        if self.scope == "favorites" and not favorite:
            self.reload_items()
            return
        row = self.model.row_of(item_id)
        item = self.model.item(row)
        if item:
            self.model.update_item({**item, "is_favorite": favorite})

    def toggle_favorite_selected(self) -> None:
        items = self.selected_items()
        if not items:
            return
        target = not all(item.get("is_favorite") for item in items)
        for item in items:
            self._set_favorite(item["id"], target)
        toast(self, "Добавлено в избранное" if target else "Убрано из избранного")

    def _move_selected(self) -> None:
        ids = self.selected_item_ids()
        if not ids:
            return
        from .bulk_dialogs import CategoryPicker
        picker = CategoryPicker(self, "Переместить в категорию",
                                f"Выбрано предметов: {len(ids)}.")
        if picker.exec():
            self._move_items(ids, picker.result_id, ask=False)

    def _move_items(self, ids: list, category_id, ask: bool) -> None:
        target = categories_service.path(category_id) or "без категории"
        if ask and len(ids) > 1 and not confirm(
                self, f"Перенести предметов: {len(ids)} — в «{target}»?"):
            return
        try:
            moved = inventory_service.bulk_move(ids, category_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload_categories()
        self.reload_items()
        toast(self, f"Перенесено предметов: {moved} → {target}")

    def _edit_selected(self) -> None:
        ids = self.selected_item_ids()
        if not ids:
            return
        if len(ids) == 1:
            from .item_dialog import ItemDialog
            if ItemDialog(self, ids[0]).exec():
                self.reload_categories()
                self.reload_items()
                toast(self, "Карточка сохранена")
            return
        from .bulk_dialogs import BulkEditDialog
        dialog = BulkEditDialog(len(ids), self)
        if not dialog.exec():
            return
        try:
            changed = inventory_service.bulk_update(ids, **dialog.values)
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload_items()
        toast(self, f"Изменено предметов: {changed}")

    def _archive_selected(self) -> None:
        items = self.selected_items()
        if not items:
            return
        names = "\n".join(f"• {item['inv_number']}  {self._short(item['name'], 60)}"
                          for item in items[:8])
        more = f"\n…и ещё {len(items) - 8}" if len(items) > 8 else ""
        if not confirm(
                self, f"Списать предметов: {len(items)}?\n\n{names}{more}\n\n"
                      "Списанный реквизит исчезает из каталога, но не удаляется: история "
                      "аренд сохраняется, а предмет можно вернуть из списка «Списанные».",
                "Списание реквизита"):
            return
        done, skipped = inventory_service.bulk_archive([item["id"] for item in items])
        self.reload_categories()
        self.reload_items()
        if skipped:
            info(self, f"Списано: {done}.\nНе списано: {len(skipped)}.\n\n"
                       + "\n".join(skipped[:10]), "Списание реквизита")
        else:
            toast(self, f"Списано предметов: {done}")

    def _restore_selected(self) -> None:
        ids = self.selected_item_ids()
        if not ids:
            return
        try:
            for item_id in ids:
                inventory_service.restore(item_id)
        except AppError as exc:
            show_error(self, exc)
        self.reload_categories()
        self.reload_items()
        toast(self, f"Возвращено в каталог: {len(ids)}")

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
        предметы, а если ничего не выделено — всё найденное по текущему запросу
        (целиком, а не только подгруженная часть).
        """
        from .. import paths
        from ..services.excel_service import excel_service

        ids = self.selected_item_ids()
        if not ids:
            ids = inventory_service.matching_ids(**self._filters())
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
        toast(self, f"Выгружено позиций: {len(ids)} → {target.name}")
        open_folder(target.parent)

    def _import_from_folder(self) -> None:
        from .import_dialog import ImportDialog

        dialog = ImportDialog(self)
        dialog.exec()
        if dialog.imported:
            self.reload_categories()
            self.reload_items()

    def _check_duplicates(self) -> None:
        from ..services.image_search import image_search
        from .bulk_dialogs import DuplicatesDialog

        try:
            groups = inventory_service.duplicate_groups() + image_search.duplicate_pairs()
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Проверить дубли не удалось.")
            return
        dialog = DuplicatesDialog(groups, self)
        dialog.open_item.connect(self.open_item.emit)
        dialog.exec()

    def _item_menu(self, view, pos) -> None:
        index = view.indexAt(pos)
        if not index.isValid():
            return
        if index.row() not in {i.row() for i in view.selectionModel().selectedIndexes()}:
            self._select_ids({self.model.item(index.row())["id"]})
        items = self.selected_items()
        if not items:
            return
        menu = QMenu(self)
        archived = self.scope == "archived"
        act_open = menu.addAction(icon("eye"), "Открыть карточку") if len(items) == 1 else None
        act_rent = act_fav = act_move = act_edit = act_label = act_archive = None
        act_restore = None
        if archived:
            act_restore = menu.addAction(icon("return-stock"), "Вернуть в каталог")
        else:
            act_rent = menu.addAction(icon("plus"), "Добавить в аренду")
            act_rent.setEnabled(any(item["available_now"] > 0 for item in items))
            all_fav = all(item.get("is_favorite") for item in items)
            act_fav = menu.addAction("Убрать из избранного" if all_fav
                                     else "Добавить в избранное")
            menu.addSeparator()
            act_move = menu.addAction(icon("folder"), "Переместить в категорию…")
            act_edit = menu.addAction(icon("edit"), "Изменить…")
            act_label = menu.addAction(icon("printer"), "Печать этикеток")
        act_excel = menu.addAction(icon("file"), "Выгрузить в Excel")
        if not archived:
            menu.addSeparator()
            act_archive = menu.addAction(icon("trash"), "Списать…")
        chosen = menu.exec(view.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        handlers = {act_open: self._open_selected, act_rent: self._add_selected_to_rental,
                    act_fav: self.toggle_favorite_selected, act_move: self._move_selected,
                    act_edit: self._edit_selected, act_label: self._print_labels,
                    act_excel: self._export_excel, act_archive: self._archive_selected,
                    act_restore: self._restore_selected}
        handler = handlers.get(chosen)
        if handler:
            handler()

    def eventFilter(self, watched, event) -> bool:           # noqa: N802 — интерфейс Qt
        if event.type() == QEvent.Resize and watched is self.grid.viewport():
            self._fit_grid()
        if event.type() == QEvent.KeyPress and watched in (self.grid, self.table):
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._open_selected()
                return True
            if key in (Qt.Key_Delete, Qt.Key_Backspace) and self.scope != "archived" \
                    and self.selected_item_ids() and key == Qt.Key_Delete:
                self._archive_selected()
                return True
            if key == Qt.Key_Escape and self.selected_item_ids():
                self.clear_selection()
                return True
        return super().eventFilter(watched, event)

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
