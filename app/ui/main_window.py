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
from .packing_view import PackingView
from .reports_view import ReportsView
from .returns_view import ReturnsView
from .settings_view import SettingsView
from .theme import Color, Space
from .widgets.circular import CircularProgress
from .widgets.suggest import SuggestList
from .brand import mark_pixmap
from .widgets.common import Card, PhotoLabel, StatusBadge, fade_in, icon, icon_button

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
    ("АНАЛИТИКА", "packing", "Машины", "box",
     "Комплектация по машинам и коробкам, упаковочные листы для группы"),
    ("УПРАВЛЕНИЕ", "settings", "Настройки", "settings",
     "Склад, категории, почта, копии, пользователи и журнал"),
]
PAGE_SUBTITLES = {index: entry[4] for index, entry in enumerate(MENU)}
PAGE_TITLES = {index: entry[2] for index, entry in enumerate(MENU)}
PAGE_DASHBOARD, PAGE_CATALOG, PAGE_NEW_RENTAL, PAGE_RENTALS = 0, 1, 2, 3
PAGE_RETURNS, PAGE_HISTORY, PAGE_CLIENTS, PAGE_PRODUCTIONS = 4, 5, 6, 7
PAGE_PROJECTS, PAGE_REPORTS, PAGE_AUDIT = 8, 9, 10
PAGE_PACKING, PAGE_SETTINGS = 11, 12


SIDEBAR_WIDTH = 248


class MainWindow(QMainWindow):
    """Логика доступна только после входа: current_user уже установлен."""

    logout_requested = Signal()

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
        self.packing = PackingView()
        self.settings = SettingsView()
        self.item_card = ItemCardView()
        for widget in (self.dashboard, self.catalog, self.new_rental, self.rentals,
                       self.returns, self.history, self.clients, self.productions,
                       self.projects, self.reports, self.audit, self.packing,
                       self.settings, self.item_card):
            self.stack.addWidget(widget)
        right_layout.addWidget(self.stack, 1)
        root.addWidget(right, 1)
        self.setCentralWidget(central)

        # связи между разделами
        self.catalog.open_item.connect(self.open_item)
        self.catalog.add_to_rental.connect(self.add_to_rental)
        self.item_card.add_to_rental.connect(self.add_to_rental)
        self.item_card.open_rental.connect(self.open_rental)
        self.item_card.back.connect(lambda: self.show_page(0))
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
        self.packing.changed.connect(self._data_changed)

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

        self.save_all_btn = QPushButton("   Сохранить всё")
        self.save_all_btn.setObjectName("NavButtonSmall")
        self.save_all_btn.setIcon(icon("save"))
        self.save_all_btn.setIconSize(QSize(16, 16))
        self.save_all_btn.setCursor(Qt.PointingHandCursor)
        self.save_all_btn.setToolTip(
            "Данные записываются в базу сразу после каждого действия.\n"
            "Эта кнопка дополнительно сводит журнал базы в файл (Ctrl+S).")
        self.save_all_btn.clicked.connect(self.save_all)
        outer.addWidget(self.save_all_btn)

        self.logout_btn = QPushButton("   Выйти")
        self.logout_btn.setObjectName("NavButtonSmall")
        self.logout_btn.setIcon(icon("logout"))
        self.logout_btn.setIconSize(QSize(16, 16))
        self.logout_btn.setCursor(Qt.PointingHandCursor)
        self.logout_btn.clicked.connect(self._logout)
        outer.addWidget(self.logout_btn)
        return sidebar

    def _build_profile_card(self) -> QWidget:
        """Профиль сотрудника внизу меню: аватар, имя, должность и роль."""
        card = Card(padding=Space.SM + Space.XS)
        card.setObjectName("UserCard")
        card.setCursor(Qt.PointingHandCursor)
        card.mousePressEvent = lambda _e: self.open_profile()
        card.setToolTip("Открыть профиль: имя, должность, аватар и пароль")

        row = QHBoxLayout()
        row.setSpacing(Space.MD)
        self.avatar = PhotoLabel(38, radius=19)
        self.avatar.setToolTip("Фотография профиля")
        row.addWidget(self.avatar)

        info_column = QVBoxLayout()
        info_column.setSpacing(1)
        self.user_name = QLabel("—")
        self.user_name.setObjectName("UserName")
        self.user_position = QLabel("")
        self.user_position.setObjectName("UserRole")
        self.user_position.setWordWrap(True)
        info_column.addWidget(self.user_name)
        info_column.addWidget(self.user_position)
        self.role_row = QHBoxLayout()
        self.role_row.setContentsMargins(0, 2, 0, 0)
        self.role_row.addStretch()
        info_column.addLayout(self.role_row)
        row.addLayout(info_column, 1)
        card.body.addLayout(row)
        self._pad(card)
        return card

    def _build_stock_card(self) -> QWidget:
        """Состояние склада: всего, доступно и круговой индикатор доступности."""
        card = Card(padding=Space.SM + Space.XS)
        card.setObjectName("StatCard")
        caption = QLabel("СОСТОЯНИЕ СКЛАДА")
        caption.setObjectName("NavGroup")
        card.body.addWidget(caption)

        numbers = QHBoxLayout()
        numbers.setSpacing(Space.MD)
        left = QVBoxLayout()
        left.setSpacing(0)
        total_caption = QLabel("Всего на складе")
        total_caption.setObjectName("StatCaption")
        self.stat_total = QLabel("—")
        self.stat_total.setObjectName("StatValue")
        available_caption = QLabel("Доступно")
        available_caption.setObjectName("StatCaption")
        self.stat_available = QLabel("—")
        self.stat_available.setObjectName("StatValueOk")
        left.addWidget(total_caption)
        left.addWidget(self.stat_total)
        left.addSpacing(Space.SM)
        left.addWidget(available_caption)
        left.addWidget(self.stat_available)
        numbers.addLayout(left, 1)

        self.availability = CircularProgress(size=76, thickness=8)
        numbers.addWidget(self.availability, 0, Qt.AlignVCenter)
        card.body.addLayout(numbers)

        self.stock_hint = QLabel("")
        self.stock_hint.setObjectName("StatHint")
        self.stock_hint.setWordWrap(True)
        # сводка «808 из 957» повторяет числа слева; текст остаётся в подсказке
        self.stock_hint.hide()
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
        self.page_subtitle = QLabel(PAGE_SUBTITLES[0])
        self.page_subtitle.setObjectName("PageSubtitle")
        self.page_subtitle.setMaximumWidth(520)
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        layout.addLayout(titles)
        layout.addStretch()

        # глобальный поиск: текст слева, камера справа внутри поля
        self.global_search = QLineEdit()
        self.global_search.setObjectName("SearchBox")
        self.global_search.setPlaceholderText("Поиск реквизита…")
        self.global_search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.camera_action = self.global_search.addAction(
            icon("camera"), QLineEdit.TrailingPosition)
        self.camera_action.setToolTip("Поиск по фотографии")
        self.camera_action.triggered.connect(self._photo_search)
        self.global_search.setMinimumWidth(320)
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
        for key, index in [("Ctrl+F", PAGE_CATALOG), ("Ctrl+N", PAGE_NEW_RENTAL),
                           ("Ctrl+R", PAGE_RENTALS), ("Ctrl+H", PAGE_DASHBOARD),
                           ("Ctrl+P", PAGE_REPORTS)]:
            action = QAction(self)
            action.setShortcut(QKeySequence(key))
            action.triggered.connect(lambda _=False, i=index: self.show_page(i))
            self.addAction(action)
        search = QAction(self)
        search.setShortcut(QKeySequence("F3"))
        search.triggered.connect(self.global_search.setFocus)
        self.addAction(search)
        save = QAction(self)
        save.setShortcut(QKeySequence("Ctrl+S"))
        save.triggered.connect(self.save_all)
        self.addAction(save)

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
        self.page_title.setText(self._shorten(item["name"]) if item else "Карточка реквизита")
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
            self.show_page(2)

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

    def open_profile(self) -> None:
        from .profile_dialog import ProfileDialog
        if ProfileDialog(self).exec():
            self._update_profile()

    def _update_profile(self) -> None:
        user = auth_service.refresh_current()
        if not user:
            return
        self.user_name.setText(user.display_name)
        self.user_position.setText(user.position_label)
        while self.role_row.count():
            item = self.role_row.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.role_row.addWidget(
            StatusBadge("info" if user.is_admin else "returned", user.role_label))
        self.role_row.addStretch()
        avatar = auth_service.avatar_file(user.id)
        if avatar:
            self.avatar.set_photo(str(avatar))
        else:
            self.avatar.clear_photo()
            self.avatar.setText(user.initials)
            self.avatar.setStyleSheet(
                f"background: {Color.PRIMARY_SOFT}; color: {Color.PRIMARY};"
                f" border-radius: 23px; font-size: 13pt; font-weight: 700;")

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
        for index in range(self.catalog.tree.topLevelItemCount()):
            item = self.catalog.tree.topLevelItem(index)
            if item.data(0, Qt.UserRole) == category_id:
                self.catalog.tree.setCurrentItem(item)
                return

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
        self.availability.animate_to(percent)
        self.stock_hint.setText(f"{free_units} из {total_units} доступно для аренды")

    # ------------------------------------------------------------ завершение
    def _restore_geometry(self) -> None:
        settings = QSettings("SkladRekvizita", "MainWindow")
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event) -> None:
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
