# -*- coding: utf-8 -*-
"""
Главное окно версии 2.1.

Композиция взята из референса: слева светлое меню с иконками, карточкой
пользователя и сводкой по складу; сверху — панель с глобальным поиском,
уведомлениями и главным действием «Новая аренда»; справа — рабочая область.
Логика разделов не менялась, изменилась только подача.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel,
                               QLineEdit,
                               QMainWindow, QMessageBox, QPushButton, QScrollArea,
                               QSizePolicy, QStackedWidget, QStatusBar, QVBoxLayout,
                               QWidget)

from ..errors import AppError
from ..services.audit_service import audit_service
from ..services.auth_service import auth_service
from ..services.backup_service import backup_service
from ..services.billing_service import billing_service
from ..services.inventory_service import inventory_service
from ..services.rentals_service import rentals_service
from ..services.settings_service import settings_service
from ..services.storage_service import storage_service
from ..utils import fmt_dt, now
from ..version import DISPLAY_NAME, RENTAL_PERIOD_DAYS, VERSION, title
from ..services.notifications_service import notifications_service
from .catalog_view import CatalogView
from .dashboard_view import DashboardView
from .directory_views import ClientsView, ProductionsView, ProjectsView
from .history_view import HistoryView
from .item_card import ItemCardView
from .notifications_panel import NotificationsPanel
from .rental_hub import NewRentalHub
from .rentals_view import RentalsView
from .inventory_audit_view import InventoryAuditView
from .reports_view import ReportsView
from .returns_view import ReturnsView
from .settings_view import SettingsView
from .theme import Color, Space
from .widgets.suggest import SuggestList
from .brand import mark_pixmap
from .widgets.common import (Card, ClickableCard, ElidedLabel, PhotoLabel, StatusBadge, fade_in, icon,
                             icon_button)

log = logging.getLogger(__name__)

# Разделы меню: (группа, ключ, подпись, иконка, подзаголовок страницы)
MENU = [
    ("ОСНОВНЫЕ", "dashboard", "Главная", "grid",
     "Состояние склада, срочные аренды и последние операции"),
    ("ОСНОВНЫЕ", "catalog", "Каталог", "search",
     "Поиск реквизита по названию, номеру, описанию и фотографии"),
    ("ОСНОВНЫЕ", "new_rental", "Новая аренда", "rental-new",
     f"Оформление выдачи: цена позиции — аренда на срок до {RENTAL_PERIOD_DAYS} дней"),
    ("ОСНОВНЫЕ", "rentals", "Аренды", "box",
     "Действующие аренды, сроки, пролонгации и документы"),
    ("ОСНОВНЫЕ", "returns", "Возвраты", "return",
     "Что находится у клиентов и что уже принято обратно"),
    ("ОСНОВНЫЕ", "history", "История", "clock",
     "Журнал действий сотрудников и движение реквизита"),
    ("КЛИЕНТЫ", "clients", "Физ. лица", "users",
     "Физические лица, паспортные данные и история аренд"),
    ("КЛИЕНТЫ", "productions", "Юр. лица", "file",
     "Продакшены: реквизиты, проекты и суммы"),
    ("КЛИЕНТЫ", "projects", "Проекты", "folder",
     "Проекты и их история аренд"),
    ("АНАЛИТИКА", "reports", "Отчёты", "chart",
     "История аренд: календарь, поиск, фильтры и статистика"),
    ("АНАЛИТИКА", "audit", "Инвентаризация", "check",
     "Физическая сверка склада со сканером: числится, найдено, расхождения"),
    ("УПРАВЛЕНИЕ", "settings", "Настройки", "settings",
     "Склад, категории, почта, копии, пользователи и журнал"),
]
PAGE_SUBTITLES = {index: entry[4] for index, entry in enumerate(MENU)}
PAGE_TITLES = {index: entry[2] for index, entry in enumerate(MENU)}
PAGE_DASHBOARD, PAGE_CATALOG, PAGE_NEW_RENTAL, PAGE_RENTALS = 0, 1, 2, 3
PAGE_RETURNS, PAGE_HISTORY, PAGE_CLIENTS, PAGE_PRODUCTIONS = 4, 5, 6, 7
PAGE_PROJECTS, PAGE_REPORTS, PAGE_AUDIT = 8, 9, 10
PAGE_SETTINGS = 11


SIDEBAR_WIDTH = 248


class MainWindow(QMainWindow):
    """Логика доступна только после входа: current_user уже установлен."""

    logout_requested = Signal()
    theme_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self._relogin = False
        self.setWindowTitle(title())
        self.resize(1500, 940)
        self.setMinimumSize(1120, 720)
        self._build()
        self._restore_geometry()
        QTimer.singleShot(400, self._startup_tasks)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._update_status)
        self._clock.start(30_000)

        # автоблокировка по бездействию: сессия закрывается, если за компьютер
        # сел другой человек, а сотрудник отошёл
        auth_service.touch()
        self._locked = False
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._check_idle)
        self._idle_timer.start(20_000)
        QApplication.instance().installEventFilter(self)

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._build_topbar())

        # Подсказки поиска — обычный виджет в раскладке, сразу под верхней
        # панелью. Всплывающее окно здесь уходило под соседние виджеты.
        suggest_wrap = QWidget()
        suggest_layout = QVBoxLayout(suggest_wrap)
        suggest_layout.setContentsMargins(Space.XL, 0, Space.XL, 0)
        suggest_layout.setSpacing(0)
        suggest_layout.addWidget(self.search_suggest)
        right_layout.addWidget(suggest_wrap)

        self.stack = QStackedWidget()
        self.dashboard = DashboardView()
        self.catalog = CatalogView()
        self.new_rental = NewRentalHub()
        self.rentals = RentalsView()
        self.returns = ReturnsView()
        self.history = HistoryView()
        self.clients = ClientsView()
        self.productions = ProductionsView()
        self.projects = ProjectsView()
        self.reports = ReportsView()
        self.audit = InventoryAuditView()
        self.settings = SettingsView()
        self.item_card = ItemCardView()
        for widget in (self.dashboard, self.catalog, self.new_rental, self.rentals,
                       self.returns, self.history, self.clients, self.productions,
                       self.projects, self.reports, self.audit,
                       self.settings, self.item_card):
            self.stack.addWidget(widget)
        right_layout.addWidget(self.stack, 1)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

        # связи между разделами
        self.catalog.open_item.connect(self.open_item)
        self.catalog.add_to_rental.connect(self.add_to_rental)
        self.catalog.add_many_to_rental.connect(self.add_many_to_rental)
        self.item_card.add_to_rental.connect(self.add_to_rental)
        self.item_card.open_rental.connect(self.open_rental)
        self.item_card.changed.connect(self._item_changed)
        self.settings.relogin_required.connect(self._force_relogin)
        self.item_card.find_similar.connect(self._find_similar)
        # «Назад к каталогу» возвращает именно в каталог (раньше — на главную)
        self.item_card.back.connect(lambda: self.show_page(PAGE_CATALOG))
        self.rentals.open_item.connect(self.open_item)
        self.new_rental.rental_created.connect(self._after_rental_created)
        self.new_rental.drafts_changed.connect(self._reload_draft_menu)
        for view in (self.projects, self.productions, self.clients):
            view.open_rental.connect(self.open_rental)
        self.dashboard.open_page.connect(self.show_page)
        self.dashboard.open_rental.connect(self.open_rental)
        self.dashboard.open_item.connect(self.open_item)
        self.dashboard.open_category.connect(self._open_category)
        self.returns.open_rental.connect(self.open_rental)
        self.returns.changed.connect(self._data_changed)
        self.history.open_rental.connect(self.open_rental)
        self.history.open_item.connect(self.open_item)
        self.reports.open_rental.connect(self.open_rental)
        self.audit.changed.connect(self._data_changed)

        self._build_statusbar()
        self._build_shortcuts()
        self.buttons.button(0).setChecked(True)
        self._update_profile()
        self._update_status()
        self._reload_draft_menu()
        self.refresh_notifications()

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        outer = QVBoxLayout(sidebar)
        outer.setContentsMargins(0, Space.LG, 0, Space.MD)
        outer.setSpacing(Space.SM)

        logo_row = QHBoxLayout()
        logo_row.setContentsMargins(Space.LG + Space.XS, 0, Space.LG, 0)
        logo_row.setSpacing(Space.MD)
        mark = QLabel()
        mark.setPixmap(mark_pixmap(38) or icon("box", "active").pixmap(30, 30))
        logo_row.addWidget(mark)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        name = QLabel("СКЛАД")
        name.setObjectName("SidebarLogo")
        word = QLabel("РЕКВИЗИТА")
        word.setObjectName("SidebarLogoWord")
        word_font = QFont(word.font())
        word_font.setLetterSpacing(QFont.AbsoluteSpacing, 2.6)    # разрядка, как в логотипе
        word.setFont(word_font)
        version_label = QLabel(f"версия {VERSION}")
        version_label.setObjectName("SidebarLogoSub")
        titles.addWidget(name)
        titles.addWidget(word)
        titles.addWidget(version_label)
        logo_row.addLayout(titles)
        logo_row.addStretch()
        outer.addLayout(logo_row)
        outer.addSpacing(Space.XS)

        menu_scroll = QScrollArea()
        menu_scroll.setWidgetResizable(True)
        menu_scroll.setFrameShape(QScrollArea.NoFrame)
        menu_host = QWidget()
        menu_layout = QVBoxLayout(menu_host)
        menu_layout.setContentsMargins(0, 0, 0, 0)
        menu_layout.setSpacing(2)
        menu_scroll.setWidget(menu_host)

        self.buttons = QButtonGroup(self)
        self.buttons.setExclusive(True)
        current_group = ""
        for index, (group, key, label, icon_name, _subtitle) in enumerate(MENU):
            if group != current_group:
                current_group = group
                caption = QLabel(group)
                caption.setObjectName("NavGroup")
                caption.setContentsMargins(Space.LG + Space.XS, Space.MD, 0, 2)
                menu_layout.addWidget(caption)
            button = QPushButton("   " + label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(icon(icon_name))
            button.setIconSize(QSize(19, 19))
            button.setProperty("page", key)
            button.setProperty("icon_name", icon_name)
            button.clicked.connect(lambda _=False, i=index: self.show_page(i))
            self.buttons.addButton(button, index)
            menu_layout.addWidget(button)
            if key == "new_rental":
                # Незавершённые оформления показываются подпунктами прямо под
                # разделом: видно, сколько выдач сейчас в работе и чьи они.
                self.new_rental_button = button
                self.draft_menu = QVBoxLayout()
                self.draft_menu.setContentsMargins(0, 0, 0, 0)
                self.draft_menu.setSpacing(1)
                menu_layout.addLayout(self.draft_menu)
        menu_layout.addStretch()
        outer.addWidget(menu_scroll, 1)

        outer.addWidget(self._build_profile_card())
        outer.addWidget(self._build_stock_card())

        # Служебные кнопки — одной строкой: меню должно помещаться без прокрутки.
        service = QHBoxLayout()
        service.setContentsMargins(Space.MD, 0, Space.MD, 0)
        service.setSpacing(2)
        self.save_all_btn = self._service_button(
            "save", "Сохранить всё",
            "Принудительно записать все изменения на диск (Ctrl+S). Программа "
            "сохраняет данные сама — кнопка нужна для уверенности перед выключением.",
            self.save_all)
        self.theme_btn = self._service_button(
            "eye", "Тема", "", self.toggle_theme)
        self.keys_btn = self._service_button(
            "details", "Клавиши", "Список горячих клавиш (F1)", self.show_shortcuts)
        self.logout_btn = self._service_button(
            "logout", "Выйти", "Завершить работу сотрудника и вернуться к экрану входа",
            self._logout)
        for button in (self.save_all_btn, self.theme_btn, self.keys_btn, self.logout_btn):
            service.addWidget(button, 1)
        outer.addLayout(service)
        self._sync_theme_button()
        return sidebar

    @staticmethod
    def _service_button(icon_name: str, text: str, tip: str, slot) -> QPushButton:
        button = QPushButton()
        button.setObjectName("IconButton")
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.PointingHandCursor)
        button.setAccessibleName(text)
        button.setToolTip(tip or text)
        button.clicked.connect(slot)
        return button

    def _sync_theme_button(self) -> None:
        from . import theme
        dark = theme.is_dark()
        self.theme_btn.setToolTip(
            "Включить светлую тему" if dark else "Включить тёмную тему — для работы вечером "
            "и в затемнённом помещении")
        self.theme_btn.setAccessibleName("Светлая тема" if dark else "Тёмная тема")

    def toggle_theme(self) -> None:
        """Переключает светлую и тёмную тему. Выбор запоминается на этом компьютере."""
        from . import theme
        self.theme_requested.emit("light" if theme.is_dark() else "dark")

    def show_shortcuts(self) -> None:
        from .shortcuts_dialog import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def _build_profile_card(self) -> QWidget:
        """Профиль сотрудника внизу меню: аватар, имя и должность. Щелчок — профиль."""
        card = ClickableCard(padding=Space.SM)
        card.setObjectName("UserCard")
        card.setCursor(Qt.PointingHandCursor)
        card.clicked.connect(self.open_profile)
        card.setToolTip("Открыть профиль: имя, должность, аватар и пароль")

        row = QHBoxLayout()
        row.setSpacing(Space.SM + 2)
        self.avatar = PhotoLabel(34, radius=17)
        self.avatar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        row.addWidget(self.avatar)

        info_column = QVBoxLayout()
        info_column.setSpacing(0)
        self.user_name = ElidedLabel("—")
        self.user_name.setObjectName("UserName")
        self.user_position = ElidedLabel("")
        self.user_position.setObjectName("UserRole")
        info_column.addWidget(self.user_name)
        info_column.addWidget(self.user_position)
        # строка роли оставлена для совместимости: роль теперь в подписи под именем
        self.role_row = QHBoxLayout()
        row.addLayout(info_column, 1)
        card.body.addLayout(row)
        self._pad(card)
        return card

    def _build_stock_card(self) -> QWidget:
        """Состояние склада одной строкой: сколько свободно и полоса доступности."""
        from PySide6.QtWidgets import QProgressBar

        card = Card(padding=Space.SM)
        card.setObjectName("StatCard")
        line = QHBoxLayout()
        line.setSpacing(Space.XS)
        caption = QLabel("Свободно")
        caption.setObjectName("StatCaption")
        self.stat_available = QLabel("—")
        self.stat_available.setObjectName("StatValueOk")
        of_label = QLabel("из")
        of_label.setObjectName("StatCaption")
        self.stat_total = QLabel("—")
        self.stat_total.setObjectName("UserName")
        self.stat_percent = QLabel("")
        self.stat_percent.setObjectName("StatCaption")
        for widget in (caption, self.stat_available, of_label, self.stat_total):
            line.addWidget(widget, 0, Qt.AlignBaseline)
        line.addStretch()
        line.addWidget(self.stat_percent, 0, Qt.AlignBaseline)
        card.body.addLayout(line)
        self.availability = QProgressBar()
        self.availability.setObjectName("StockBar")
        self.availability.setRange(0, 100)
        self.availability.setTextVisible(False)
        self.availability.setFixedHeight(6)
        card.body.addWidget(self.availability)
        self.stock_hint = QLabel("")
        self.stock_hint.hide()
        card.setToolTip("Единиц реквизита, свободных для аренды сегодня, из общего числа")
        self._pad(card)
        return card

    @staticmethod
    def _pad(widget: QWidget) -> None:
        """Боковые отступы для карточек в меню."""
        widget.setFixedWidth(SIDEBAR_WIDTH - 2 * Space.MD)

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        bar.setFixedHeight(78)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(Space.XL, Space.MD, Space.XL, Space.MD)
        layout.setSpacing(Space.LG)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        self.page_title = QLabel(PAGE_TITLES[0])
        self.page_title.setObjectName("PageTitle")
        self.page_title.setMaximumWidth(420)
        self.page_subtitle = ElidedLabel(PAGE_SUBTITLES[0])
        self.page_subtitle.setObjectName("PageSubtitle")
        self.page_subtitle.setMaximumWidth(520)
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        layout.addLayout(titles, 2)
        layout.addSpacing(Space.MD)

        # глобальный поиск: текст слева, камера справа внутри поля
        self.global_search = QLineEdit()
        self.global_search.setObjectName("SearchBox")
        self.global_search.setPlaceholderText("Поиск реквизита…")
        self.global_search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.camera_action = self.global_search.addAction(
            icon("camera"), QLineEdit.TrailingPosition)
        self.camera_action.setToolTip("Поиск по фотографии")
        self.camera_action.triggered.connect(self._photo_search)
        self.global_search.setMinimumWidth(220)
        self.global_search.setMaximumWidth(460)
        self.global_search.setMinimumHeight(42)
        self.global_search.returnPressed.connect(self._global_search)
        layout.addWidget(self.global_search, 1)

        # Подсказки прямо под полем: видно, что найдётся, ещё до нажатия Enter.
        # Выбор строки открывает карточку, поле при этом очищается — оно
        # нужно для следующего поиска, а не чтобы хранить прошлый запрос.
        self.search_suggest = SuggestList(
            self.global_search, self._suggest_items,
            empty_text="Ничего не найдено — попробуйте другое слово или номер")
        self.search_suggest.chosen.connect(self._suggest_picked)

        self.notify_btn = icon_button("bell", "Уведомления")
        self.notify_btn.clicked.connect(self._toggle_notifications)
        self.notify_badge = QLabel("", self.notify_btn)
        self.notify_badge.setObjectName("NotifyBadge")
        self.notify_badge.setAlignment(Qt.AlignCenter)
        self.notify_badge.setFixedSize(18, 18)
        self.notify_badge.move(22, 2)
        self.notify_badge.hide()
        layout.addWidget(self.notify_btn)

        self.notifications = NotificationsPanel(self)
        self.notifications.open_entity.connect(self._open_entity)
        self.notifications.changed.connect(self.refresh_notifications)

        new_rental_btn = QPushButton("  Новая аренда")
        new_rental_btn.setObjectName("Primary")
        new_rental_btn.setIcon(icon("plus", "white"))
        new_rental_btn.setIconSize(QSize(18, 18))
        new_rental_btn.setCursor(Qt.PointingHandCursor)
        new_rental_btn.setMinimumHeight(42)
        new_rental_btn.setToolTip(
            "Начать ещё одно оформление. Уже открытые останутся в меню слева.")
        new_rental_btn.clicked.connect(self._start_new_rental)
        layout.addWidget(new_rental_btn)
        return bar

    def _photo_search(self) -> None:
        """Камера в строке поиска открывает существующий поиск по фотографии."""
        from .photo_search_dialog import PhotoSearchDialog
        dialog = PhotoSearchDialog(self)
        dialog.open_item.connect(self.open_item)
        dialog.add_to_rental.connect(self.add_to_rental)
        dialog.exec()

    def _toggle_notifications(self) -> None:
        if self.notifications.isVisible():
            self.notifications.hide()
            return
        point = self.notify_btn.mapToGlobal(self.notify_btn.rect().bottomLeft())
        point.setX(point.x() - 360)
        point.setY(point.y() + 8)
        self.notifications.show_at(point)

    def refresh_notifications(self) -> None:
        """Пересобирает уведомления по реальным данным и обновляет счётчик."""
        try:
            notifications_service.refresh()
            unread = notifications_service.unread_count()
        except Exception:
            log.exception("Не удалось обновить уведомления")
            return
        self.notify_badge.setVisible(unread > 0)
        self.notify_badge.setText(str(unread if unread < 100 else "99+"))
        self.notify_btn.setIcon(icon("bell", "danger" if unread else ""))
        self.notify_btn.setToolTip(
            f"Непрочитанных уведомлений: {unread}" if unread else "Новых уведомлений нет")

    def _open_entity(self, entity: str, entity_id: int) -> None:
        if entity == "rental":
            self.open_rental(entity_id)
        elif entity == "item":
            self.open_item(entity_id)

    def _build_statusbar(self) -> None:
        self.setStatusBar(QStatusBar())
        self.saved_label = QLabel("")
        self.saved_label.setObjectName("SavedLabel")
        self.saved_label.setToolTip("Все подтверждённые действия уже записаны в базу")
        self.statusBar().addPermanentWidget(self.saved_label)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.statusBar().addPermanentWidget(self.status_label)

    def _build_shortcuts(self) -> None:
        """
        Горячие клавиши. Записаны через Ctrl: на macOS Qt сам заменяет Ctrl на
        Command (⌘), поэтому одна и та же таблица работает на обеих системах.
        Полный список показывает F1 (см. shortcuts_dialog.py).
        """
        def bind(key, slot) -> None:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.WindowShortcut)
            action.triggered.connect(slot)
            self.addAction(action)

        for key, index in [("Ctrl+1", PAGE_DASHBOARD), ("Ctrl+2", PAGE_CATALOG),
                           ("Ctrl+3", PAGE_NEW_RENTAL), ("Ctrl+4", PAGE_RENTALS),
                           ("Ctrl+5", PAGE_RETURNS), ("Ctrl+N", PAGE_NEW_RENTAL),
                           ("Ctrl+R", PAGE_RENTALS), ("Ctrl+H", PAGE_DASHBOARD),
                           ("Ctrl+P", PAGE_REPORTS)]:
            bind(key, lambda _=False, i=index: self.show_page(i))
        bind("Ctrl+F", self.focus_catalog_search)
        bind("F3", self.global_search.setFocus)
        bind("Ctrl+K", self.global_search.setFocus)
        bind("Ctrl+S", self.save_all)
        bind("Ctrl+I", self._new_item_shortcut)
        bind("Ctrl+Shift+F", self._photo_search_shortcut)
        bind("Ctrl+D", self._favorite_shortcut)
        bind("F1", self.show_shortcuts)
        bind("F5", self._refresh_current)

    def focus_catalog_search(self) -> None:
        self.show_page(PAGE_CATALOG)
        self.catalog.search.setFocus()
        self.catalog.search.selectAll()

    def _new_item_shortcut(self) -> None:
        self.show_page(PAGE_CATALOG)
        self.catalog.new_item()

    def _photo_search_shortcut(self) -> None:
        self.show_page(PAGE_CATALOG)
        self.catalog._photo_search()

    def _favorite_shortcut(self) -> None:
        if self.stack.currentIndex() == PAGE_CATALOG:
            self.catalog.toggle_favorite_selected()
        elif self.stack.currentWidget() is self.item_card:
            self.item_card.toggle_favorite()

    def _refresh_current(self) -> None:
        widget = self.stack.currentWidget()
        try:
            if hasattr(widget, "reload"):
                widget.reload()
        except Exception:                                          # noqa: BLE001
            log.exception("Не удалось обновить раздел")
        self._update_status()

    # ------------------------------------------------------------- навигация
    def show_page(self, index: int) -> None:
        if index == PAGE_NEW_RENTAL and not self.new_rental.open_drafts():
            # раздел открыт, а оформлений нет — сразу начинаем новое: оператор
            # нажал «Новая аренда» именно за этим
            self.new_rental.start_new()
        self.stack.setCurrentIndex(index)
        button = self.buttons.button(index)
        if button:
            button.setChecked(True)
        self._sync_nav_icons()
        page = self.stack.currentWidget()
        for method in ("reload", "reload_items", "reload_references"):
            if hasattr(page, method) and page is not self.item_card:
                try:
                    getattr(page, method)()
                except Exception:
                    log.exception("Не удалось обновить раздел")
        if index in PAGE_TITLES:
            self.page_title.setText(PAGE_TITLES[index])
            self.page_subtitle.setText(PAGE_SUBTITLES.get(index, ""))
        fade_in(page)
        self._update_status()

    def _reload_draft_menu(self) -> None:
        """Перерисовывает подпункты незавершённых аренд."""
        while self.draft_menu.count():
            item = self.draft_menu.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        drafts = self.new_rental.open_drafts()
        count = len(drafts)
        self.new_rental_button.setText(
            "   Новая аренда" + (f"   ({count})" if count else ""))
        self.new_rental_button.setToolTip(
            f"Незавершённых оформлений: {count}" if count
            else "Начать оформление выдачи")

        current = self.new_rental.current_draft_id()
        for draft in drafts:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(Space.XL + Space.MD, 0, Space.MD, 0)
            row_layout.setSpacing(2)

            caption = draft["number"]
            details = []
            if draft["client"]:
                details.append(draft["client"])
            if draft["lines"]:
                details.append(f"{draft['lines']} поз.")
            open_btn = QPushButton(caption + ("   ·   " + ", ".join(details)
                                              if details else ""))
            open_btn.setObjectName("NavSubButton")
            open_btn.setCheckable(True)
            open_btn.setChecked(draft["id"] == current)
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.setToolTip("Продолжить оформление " + caption)
            open_btn.clicked.connect(
                lambda _=False, i=draft["id"]: self.open_draft(i))
            row_layout.addWidget(open_btn, 1)

            close_btn = QPushButton()
            close_btn.setObjectName("NavSubClose")
            close_btn.setIcon(icon("close"))
            close_btn.setIconSize(QSize(14, 14))
            close_btn.setFixedWidth(26)
            close_btn.setCursor(Qt.PointingHandCursor)
            close_btn.setToolTip("Отменить оформление " + caption)
            close_btn.clicked.connect(
                lambda _=False, i=draft["id"]: self.new_rental.cancel_draft(i))
            row_layout.addWidget(close_btn)
            self.draft_menu.addWidget(row)

    def _start_new_rental(self) -> None:
        """
        Кнопка в шапке всегда начинает НОВОЕ оформление: пришёл ещё один
        клиент. Уже открытые никуда не деваются, они остаются в меню.
        """
        if self.new_rental.start_new() is not None:
            self.show_page(PAGE_NEW_RENTAL)
            self._reload_draft_menu()

    def open_draft(self, draft_id: int) -> None:
        """Переключение на конкретное незавершённое оформление."""
        self.new_rental.show_draft(draft_id)
        self.show_page(PAGE_NEW_RENTAL)
        self._reload_draft_menu()

    def _sync_nav_icons(self) -> None:
        """Активный пункт меню получает акцентную иконку."""
        for button in self.buttons.buttons():
            name = button.property("icon_name")
            if name:
                button.setIcon(icon(name, "white" if button.isChecked() else ""))

    @staticmethod
    def _shorten(text: str, limit: int = 48) -> str:
        """Длинное название в заголовке не должно вытеснять поиск и кнопки."""
        text = (text or "").strip()
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def open_item(self, item_id: int) -> None:
        self.item_card.load(item_id)
        self.stack.setCurrentWidget(self.item_card)
        for button in self.buttons.buttons():
            button.setChecked(False)
        self._sync_nav_icons()
        item = inventory_service.get(item_id)
        # Заголовок сокращается по ширине поля, а не по числу букв: раньше длинное
        # название обрезалось посреди буквы.
        title_text = item["name"] if item else "Карточка реквизита"
        self.page_title.setText(self.page_title.fontMetrics().elidedText(
            title_text, Qt.ElideRight, self.page_title.maximumWidth() - 8))
        catalog_button = self.buttons.button(PAGE_CATALOG)
        if catalog_button:
            catalog_button.setChecked(True)      # карточка — часть каталога
            self._sync_nav_icons() if hasattr(self, "_sync_nav_icons") else None
        self.page_title.setToolTip(item["name"] if item else "")
        self.page_subtitle.setText(
            f"Инв. № {item['inv_number']} · {item['category_path'] or 'без категории'}"
            if item else "")
        fade_in(self.item_card)

    def open_rental(self, rental_id: int) -> None:
        self.show_page(PAGE_RENTALS)
        self.rentals.open_rental(rental_id)
        self._update_status()
        self.refresh_notifications()

    def add_to_rental(self, item_id: int) -> None:
        """Добавляет предмет в форму новой аренды и открывает её."""
        self.new_rental.add_item(item_id)
        self.show_page(PAGE_NEW_RENTAL)

    def _item_changed(self) -> None:
        """В карточке поменяли фото или избранное — каталог покажет это при возврате."""
        from .widgets.thumbs import thumbnails
        thumbnails().forget()
        self.catalog.reload_items()

    def _find_similar(self, item_id: int) -> None:
        from .photo_search_dialog import PhotoSearchDialog
        dialog = PhotoSearchDialog(self, similar_to_item=item_id)
        dialog.open_item.connect(self.open_item)
        dialog.add_to_rental.connect(self.add_to_rental)
        dialog.exec()

    def add_many_to_rental(self, item_ids: list) -> None:
        """Добавляет несколько выбранных в каталоге предметов в одну аренду."""
        added = 0
        for item_id in item_ids:
            try:
                self.new_rental.add_item(item_id)
                added += 1
            except Exception:                                      # noqa: BLE001
                log.exception("Не удалось добавить предмет %s в аренду", item_id)
        self.show_page(PAGE_NEW_RENTAL)
        if added:
            from .widgets.toast import toast
            toast(self, f"Добавлено в аренду предметов: {added}")

    def _after_rental_created(self, rental_id: int) -> None:
        self.catalog.reload_items()
        self._data_changed()
        self.show_page(PAGE_RENTALS)
        self.rentals.open_rental(rental_id)

    def _suggest_items(self, text: str) -> list[dict]:
        """Что показать в подсказках: реквизит по названию, номеру, штрихкоду."""
        from ..services.photo_service import photo_service

        rows = []
        for item in inventory_service.search(text, limit=8):
            photo = photo_service.primary_photo(item["id"])
            rows.append({
                "title": item["name"],
                "subtitle": (f"инв. № {item['inv_number']}   ·   "
                             f"доступно {item['available_now']} из "
                             f"{item['quantity_total']}"),
                "icon_path": photo["thumb_path"] if photo else "",
                "item_id": item["id"],
            })
        return rows

    def _suggest_picked(self, row: dict) -> None:
        self.global_search.clear()
        self.search_suggest.close_list()
        self.open_item(row["item_id"])

    def _global_search(self) -> None:
        text = self.global_search.text().strip()
        self.search_suggest.close_list()
        self.global_search.clear()          # поле освобождается под следующий поиск
        if not text:
            return
        self.show_page(PAGE_CATALOG)
        self.catalog.search.setText(text)
        self.catalog.reload_items()
        self.catalog.search.setFocus()

    def _open_audit(self) -> None:
        self.show_page(PAGE_HISTORY)
        return

    def _open_audit_settings(self) -> None:
        self.show_page(PAGE_SETTINGS)
        try:
            tabs = self.settings.tabs
            for index in range(tabs.count()):
                if "Журнал" in tabs.tabText(index):
                    tabs.setCurrentIndex(index)
                    return
        except Exception:
            log.exception("Не удалось открыть журнал действий")

    # ---------------------------------------------------------------- запуск
    def _startup_tasks(self) -> None:
        try:
            path = backup_service.auto_backup_if_needed()
            if path:
                self.statusBar().showMessage(
                    f"Создана автоматическая резервная копия: {path.name}", 8000)
        except Exception:
            log.exception("Автобэкап при запуске не выполнен")

        try:
            created = billing_service.run_prolongations()
        except Exception:
            log.exception("Проверка пролонгаций не выполнена")
            created = []
        if created:
            total = sum(entry["amount"] for entry in created)
            lines = [f"• {entry['name']} × {entry['quantity']} — период №{entry['period_number']} "
                     f"({entry['amount']:,.0f} ₽)".replace(",", " ") for entry in created[:10]]
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle("Автоматическая пролонгация")
            box.setText(f"Начислены новые {RENTAL_PERIOD_DAYS}-дневные периоды "
                        f"на {total:,.0f} ₽".replace(",", " "))
            box.setInformativeText("\n".join(lines) +
                                   (f"\n…всего позиций: {len(created)}" if len(created) > 10 else ""))
            box.exec()
            self.rentals.reload()

        self.refresh_notifications()
        self._update_notifications()
        self._start_photo_indexing()

    # ------------------------------------------------ фоновая индексация фото
    def _start_photo_indexing(self) -> None:
        """
        После обновления программы или смены способа поиска у фотографий нет
        нужных отпечатков. Раньше для этого надо было найти кнопку
        «Переиндексировать» в настройках. Теперь склад доиндексируется сам:
        по одной фотографии в паузах между действиями, с отчётом в строке
        состояния. Закрыли программу — продолжит при следующем запуске.
        """
        from ..services.image_search import image_search
        try:
            self._index_left = image_search.missing_count()
        except Exception:                                          # noqa: BLE001
            log.exception("Не удалось оценить объём индексации фотографий")
            return
        if not self._index_left:
            return
        self._index_total = self._index_left
        self._index_skip: set = set()
        self._index_timer = QTimer(self)
        self._index_timer.setInterval(40)
        self._index_timer.timeout.connect(self._index_step)
        self._index_timer.start()

    def _index_step(self) -> None:
        from ..services.image_search import image_search
        if QApplication.activeModalWidget() is not None:
            return                               # открыт диалог — не мешаем
        try:
            done, self._index_skip = image_search.index_next_missing(1, self._index_skip)
        except Exception:                                          # noqa: BLE001
            log.exception("Индексация фотографий остановлена из-за ошибки")
            self._index_timer.stop()
            return
        self._index_left -= done
        if not done or self._index_left <= 0:
            self._index_timer.stop()
            self.statusBar().showMessage("Поиск по фото: все фотографии проиндексированы", 6000)
            return
        if self._index_left % 5 == 0:
            self.statusBar().showMessage(
                f"Поиск по фото: индексация {self._index_total - self._index_left} "
                f"из {self._index_total}…", 3000)

    def _update_notifications(self) -> None:
        try:
            days = settings_service.get_int("notify.days_before_end", 2)
            expiring = rentals_service.expiring_soon(days)
        except Exception:
            log.exception("Проверка сроков аренды не выполнена")
            return
        if not expiring:
            self.notify_btn.setIcon(icon("bell"))
            self.notify_btn.setToolTip("Аренд с истекающим сроком нет")
            return
        self.notify_btn.setIcon(icon("bell", "danger"))
        self.notify_btn.setToolTip(f"Требуют внимания аренд: {len(expiring)}")
        lines = []
        for rental in expiring[:10]:
            left = rental["days_left"]
            if left is not None and left < 0:
                lines.append(f"• {rental['number']} — срок истёк {abs(left)} дн. назад "
                             f"({rental.get('project_name') or '—'})")
            else:
                lines.append(f"• Аренда {rental['number']} заканчивается через {left} дн. "
                             f"({rental.get('project_name') or '—'})")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Сроки аренды")
        box.setText("Требуют внимания:")
        box.setInformativeText("\n".join(lines))
        open_button = box.addButton("Открыть раздел «В аренде»", QMessageBox.AcceptRole)
        box.addButton("Позже", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == open_button:
            # Раньше здесь открывался раздел №2 — «Новая аренда», и вместо списка
            # аренд оператор получал пустую форму оформления.
            self.show_page(PAGE_RENTALS)

    # -------------------------------------------------------- сохранение
    def save_all(self) -> None:
        """
        «Сохранить всё»: сохраняет черновик открытой формы и сводит журнал базы
        в файл. Подтверждённые данные записаны и без этого.
        """
        try:
            self.new_rental._save_draft()
        except Exception:
            log.exception("Не удалось сохранить черновик формы")
        try:
            report = storage_service.save_all()
        except Exception as exc:
            from .errors import show_error
            show_error(self, exc, "Не удалось выполнить запись на диск.")
            return

        rows = "\n".join(f"• {label}: {count}" for label, count in report["counts"])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Сохранение")
        box.setText("Все данные записаны на диск.")
        box.setInformativeText(
            f"Время: {report['at']:%d.%m.%Y %H:%M:%S}\n"
            f"Файл базы: {report['db_size_mb']} МБ\n"
            f"Несохранённых изменений в журнале: "
            f"{'нет' if not report['pending'] else str(report['wal_size']) + ' байт'}\n\n"
            f"{rows}\n\n{report['db_path']}")
        box.exec()
        self._update_status()
        self.statusBar().showMessage(f"Сохранено в {report['at']:%H:%M:%S}", 6000)

    def eventFilter(self, obj, event):  # noqa: N802 — интерфейс Qt
        """Любое действие мыши или клавиатуры продлевает сессию."""
        if event.type() in (QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            auth_service.touch()
        return super().eventFilter(obj, event)

    def _check_idle(self) -> None:
        minutes = settings_service.get_int("security.lock_minutes", 15)
        if minutes <= 0 or self._locked or not auth_service.current_user:
            return
        if not auth_service.is_idle_expired(minutes):
            return
        self._locked = True
        try:
            from .lock_dialog import LockDialog
            dialog = LockDialog(self, minutes)
            audit_service.log("Сеанс заблокирован по бездействию", entity="user",
                              entity_id=auth_service.current_id())
            accepted = dialog.exec()
            if accepted:
                audit_service.log("Сеанс разблокирован", entity="user",
                                  entity_id=auth_service.current_id())
                auth_service.touch()
            else:
                self._relogin = True
                auth_service.logout()
                self.logout_requested.emit()
                self.close()
        finally:
            self._locked = False

    def _logout(self) -> None:
        from .errors import confirm
        if not confirm(self, "Завершить сеанс и вернуться к экрану входа?", "Выход"):
            return
        self._relogin = True
        auth_service.logout()
        self.logout_requested.emit()
        self.close()

    def _force_relogin(self) -> None:
        """Склад заменён загрузкой из файла: закрыть окно и показать экран входа."""
        self._relogin = True
        try:
            auth_service.logout()
        except Exception:                                          # noqa: BLE001
            log.info("Прежний сеанс уже недействителен")
        self.logout_requested.emit()
        self.close()

    def rebuild_for_theme(self) -> None:
        """Закрывает окно для пересборки в другой теме: сеанс и база остаются открытыми."""
        self._relogin = True
        self.close()

    def open_profile(self) -> None:
        from .profile_dialog import ProfileDialog
        if ProfileDialog(self).exec():
            self._update_profile()

    def _update_profile(self) -> None:
        user = auth_service.refresh_current()
        if not user:
            return
        # подписи сами сокращаются многоточием под ширину меню
        self.user_name.setText(user.display_name)
        position = user.position_label
        role = user.role_label
        self.user_position.setText(
            f"{position} · {role}" if position and position != role else role)
        avatar = auth_service.avatar_file(user.id)
        if avatar:
            self.avatar.set_photo(str(avatar))
        else:
            self.avatar.clear_photo()
            self.avatar.setText(user.initials)
            self.avatar.setStyleSheet(
                f"background: {Color.PRIMARY_SOFT}; color: {Color.PRIMARY_TEXT};"
                f" border-radius: 17px; font-size: 14px; font-weight: 700;")

    def _data_changed(self) -> None:
        """После аренды, возврата или изменения остатков обновляем сводку и отчёты."""
        self._update_status()
        self.refresh_notifications()
        for view in (self.dashboard, self.reports):
            try:
                view.reload()
            except Exception:
                log.exception("Не удалось обновить раздел после изменения данных")

    def _open_category(self, category_id: int) -> None:
        self.show_page(PAGE_CATALOG)
        self.catalog.select_category(category_id)

    def _update_status(self) -> None:
        try:
            active = len(rentals_service.list_active())
            items = inventory_service.count()
            last = storage_service.last_change()
            available = inventory_service.available_units()
        except Exception:
            return
        self.saved_label.setText(
            f"Все изменения сохранены   ·   {fmt_dt(last) if last else '—'}      ")
        user = auth_service.current_user
        who = f"{user.full_name} ({user.role_label})" if user else "—"
        self.status_label.setText(
            f"Пользователь: {who}   ·   Предметов: {items}   ·   Действующих аренд: {active}"
            f"   ·   {now():%d.%m.%Y %H:%M}   ")
        total_units, free_units = available
        percent = round(free_units * 100 / total_units) if total_units else 0
        self.stat_total.setText(f"{total_units:,}".replace(",", " "))
        self.stat_available.setText(f"{free_units:,}".replace(",", " "))
        self.availability.setValue(percent)
        self.stat_percent.setText(f"{percent} %")
        self.stock_hint.setText(f"{free_units} из {total_units} доступно для аренды")

    # ------------------------------------------------------------ завершение
    def _restore_geometry(self) -> None:
        settings = QSettings("SkladRekvizita", "MainWindow")
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:
        timer = getattr(self, "_index_timer", None)
        if timer is not None:
            timer.stop()
        QSettings("SkladRekvizita", "MainWindow").setValue("geometry", self.saveGeometry())
        try:
            self.new_rental._save_draft()
        except Exception:
            log.exception("Не удалось сохранить черновик формы при закрытии")
        if not self._relogin:
            try:
                from ..db.database import get_db
                get_db().close()
            except Exception:
                log.exception("Ошибка при закрытии базы")
            try:
                auth_service.logout()
            except Exception:
                pass
        super().closeEvent(event)
