# -*- coding: utf-8 -*-
"""Новая аренда: клиент с автозаполнением, продакшен, проект, позиции, расчёт, скидка, залог."""
from __future__ import annotations

from typing import Optional

from datetime import date

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QInputDialog)

from ..errors import AppError
from ..services.auth_service import auth_service
from ..services.clients_service import clients_service
from ..services.documents_service import documents_service
from ..services.draft_service import DRAFT_NEW_RENTAL, draft_service
from ..services.rental_drafts_service import rental_drafts_service
from ..services.barcode_service import barcode_service
from ..services.inventory_service import inventory_service
from ..services.productions_service import productions_service
from ..services.projects_service import projects_service
from ..services.rentals_service import RentalDraft, RentalLine, calculate, rentals_service
from ..utils import fmt_dt, money, money_plain
from ..version import RENTAL_PERIOD_DAYS
from .errors import show_error
from .widgets.common import (Card, DatePicker, PhotoLabel, cell, icon, muted,
                             section_title, title_label)
from .widgets.scan_input import ScanInput
from .widgets.suggest import SuggestList

# Единые размеры полей формы: на macOS стандартные виджеты заметно ниже,
# и без явной высоты форма выглядит сплюснутой.
FIELD_HEIGHT = 38
FORM_BOX_WIDTH = 420

COLUMNS = ["Инв. №", "Наименование", "Описание", "Примечание к аренде", "Кол-во",
           f"Цена за {RENTAL_PERIOD_DAYS} дн.", "Оценочная стоимость", "Сумма", ""]


class NewRentalView(QWidget):
    rental_created = Signal(int)
    draft_finished = Signal(int)      # оформление закрыто (выдано или отменено)

    def __init__(self, parent=None, draft_id: Optional[int] = None,
                 number: str = ""):
        """
        draft_id — незавершённое оформление, к которому привязана форма.
        Без него форма работает по-старому: один общий черновик автосохранения.
        """
        super().__init__(parent)
        self.draft_id = draft_id
        self.draft_number = number
        self.lines: list[RentalLine] = []
        self.client_id = None
        self._restoring = False
        self._build()
        self.reload_references()
        # автосохранение черновика: форма пишется в базу через секунду после
        # последнего изменения, отдельной кнопки «Сохранить» не требуется
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(1000)
        self._autosave.timeout.connect(self._save_draft)
        for widget in (self.client_edit, self.item_search):
            widget.textChanged.connect(lambda *_: self._touch())
        for widget in (self.production, self.project, self.payment, self.invoice_to):
            widget.currentIndexChanged.connect(lambda *_: self._touch())
        for widget in (self.discount, self.deposit):
            widget.valueChanged.connect(lambda *_: self._touch())
        for picker in (self.date_start, self.date_end):
            picker.changed.connect(self._touch)
        self._restore_draft()

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        from .theme import Space
        from .widgets.common import scroll_area
        # весь экран прокручивается: форма + таблица позиций + расчёт
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        host = QWidget()
        outer.addWidget(scroll_area(host))
        root = QVBoxLayout(host)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)
        root.addWidget(section_title("Оформление аренды"))
        root.addWidget(muted(
            f"Цена позиции — стоимость аренды на срок до {RENTAL_PERIOD_DAYS} календарных дней. "
            "Досрочный возврат стоимость периода не уменьшает; не возвращённые к концу периода "
            "позиции автоматически пролонгируются."))

        top = QHBoxLayout()

        party = QGroupBox("Кто берёт")
        party.setMinimumWidth(FORM_BOX_WIDTH)
        party_form = QFormLayout(party)
        party_form.setSpacing(Space.MD)
        party_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        party_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("Начните вводить фамилию…")
        self.client_edit.setMinimumHeight(FIELD_HEIGHT)
        self.client_edit.setMinimumWidth(260)
        self.client_edit.textEdited.connect(self._client_text_changed)

        # Подсказки по похожим именам: с телефоном и документом, чтобы двух
        # однофамильцев было видно, кто есть кто. Раньше здесь был QCompleter,
        # он показывал только строку ФИО и в узком поле почти не читался.
        self.client_suggest = SuggestList(
            self.client_edit, self._suggest_clients,
            empty_text="Такого физ. лица ещё нет — оно будет создано")
        self.client_suggest.chosen.connect(lambda row: self._apply_client(row["client"]))

        client_row = QHBoxLayout()
        client_row.setSpacing(Space.SM)
        client_row.addWidget(self.client_edit, 1)
        pick_client_btn = QPushButton("Выбрать…")
        pick_client_btn.setMinimumHeight(FIELD_HEIGHT)
        pick_client_btn.setToolTip(
            "Открыть список физических лиц — если фамилию не помните")
        pick_client_btn.clicked.connect(self._pick_client)
        client_row.addWidget(pick_client_btn)
        new_client_btn = QPushButton("Новый…")
        new_client_btn.setMinimumHeight(FIELD_HEIGHT)
        new_client_btn.clicked.connect(self._new_client)
        client_row.addWidget(new_client_btn)
        client_widget = QWidget()
        client_widget.setLayout(client_row)

        self.client_info = QLabel("—")
        self.client_info.setObjectName("Muted")
        self.client_info.setWordWrap(True)
        self.client_info.setMinimumHeight(40)
        party_form.addRow("Физ. лицо*:", client_widget)
        party_form.addRow("", self.client_suggest)      # подсказки прямо под полем
        party_form.addRow("Данные:", self.client_info)
        top.addWidget(party, 1)

        work = QGroupBox("Продакшен и проект")
        work.setMinimumWidth(FORM_BOX_WIDTH)
        work_form = QFormLayout(work)
        work_form.setSpacing(Space.MD)
        work_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        work_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.production = QComboBox()
        self.production.setMinimumHeight(FIELD_HEIGHT)
        self.production.setMinimumWidth(220)
        self.production.currentIndexChanged.connect(self._production_changed)
        prod_row = QHBoxLayout()
        prod_row.addWidget(self.production, 1)
        new_prod = QPushButton("Новый…")
        new_prod.setMinimumHeight(FIELD_HEIGHT)
        new_prod.clicked.connect(self._new_production)
        prod_row.addWidget(new_prod)
        prod_widget = QWidget()
        prod_widget.setLayout(prod_row)

        self.project = QComboBox()
        self.project.setMinimumHeight(FIELD_HEIGHT)
        self.project.setMinimumWidth(220)
        proj_row = QHBoxLayout()
        proj_row.addWidget(self.project, 1)
        new_proj = QPushButton("Новый…")
        new_proj.setMinimumHeight(FIELD_HEIGHT)
        new_proj.clicked.connect(self._new_project)
        proj_row.addWidget(new_proj)
        proj_widget = QWidget()
        proj_widget.setLayout(proj_row)

        self.payment = QComboBox()
        self.payment.setMinimumHeight(FIELD_HEIGHT)
        self.payment.setMinimumWidth(220)
        self.payment.addItem("Наличные", "cash")
        self.payment.addItem("Безналичные", "bank")
        self.payment.currentIndexChanged.connect(self._payment_changed)
        self.invoice_to = QComboBox()
        self.invoice_to.setMinimumHeight(FIELD_HEIGHT)
        self.invoice_to.setMinimumWidth(220)
        self.invoice_to.setEnabled(False)

        work_form.addRow("Продакшен*:", prod_widget)
        work_form.addRow("Проект*:", proj_widget)
        work_form.addRow("Способ оплаты*:", self.payment)
        work_form.addRow("Счёт выставить на:", self.invoice_to)
        top.addWidget(work, 1)

        period = QGroupBox("Сроки")
        period.setMinimumWidth(320)
        period_form = QFormLayout(period)
        period_form.setSpacing(Space.MD)
        period_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        period_form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.date_start = DatePicker()
        self.date_end = DatePicker(optional=True)
        self.date_end.set_value(date.today())
        period_form.addRow("Аренда с*:", self.date_start)
        period_form.addRow("Аренда по:", self.date_end)
        top.addWidget(period)
        root.addLayout(top)

        add_row = QHBoxLayout()
        self.item_search = QLineEdit()
        self.item_search.setPlaceholderText("Найти реквизит по названию, номеру, описанию или ключевым словам…")
        self.item_search.returnPressed.connect(self._search_items)
        self.item_search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.item_search.setMinimumHeight(42)
        add_row.addWidget(self.item_search, 1)
        find_btn = QPushButton("Найти")
        find_btn.setMinimumHeight(42)
        find_btn.clicked.connect(self._search_items)
        add_row.addWidget(find_btn)
        photo_btn = QPushButton("  По фото")
        photo_btn.setIcon(icon("camera"))
        photo_btn.setMinimumHeight(42)
        photo_btn.clicked.connect(self._photo_search)
        add_row.addWidget(photo_btn)
        root.addLayout(add_row)

        # Подсказки под полем: пока набираешь — видно, что найдётся.
        # Выбор строки НЕ добавляет позицию сразу: сначала предпросмотр, где
        # видно фотографию, остаток и количество. Оператор должен понимать,
        # что именно он кладёт в аренду.
        self.item_suggest = SuggestList(
            self.item_search, self._suggest_items,
            empty_text="Ничего не найдено — попробуйте другое слово или инв. номер")
        self.item_suggest.chosen.connect(lambda row: self._preview_item(row["item_id"]))
        root.addWidget(self.item_suggest)

        self.preview = Card(padding=Space.MD)
        preview_row = QHBoxLayout()
        preview_row.setSpacing(Space.MD)
        self.preview_photo = PhotoLabel(96, radius=10)
        preview_row.addWidget(self.preview_photo)
        preview_text = QVBoxLayout()
        preview_text.setSpacing(2)
        self.preview_name = QLabel("")
        self.preview_name.setStyleSheet("font-size:13pt; font-weight:600;")
        self.preview_name.setWordWrap(True)
        self.preview_info = QLabel("")
        self.preview_info.setObjectName("Muted")
        self.preview_info.setWordWrap(True)
        preview_text.addWidget(self.preview_name)
        preview_text.addWidget(self.preview_info)
        preview_row.addLayout(preview_text, 1)

        preview_actions = QVBoxLayout()
        quantity_row = QHBoxLayout()
        quantity_row.addWidget(QLabel("Кол-во:"))
        self.preview_qty = QSpinBox()
        self.preview_qty.setRange(1, 100000)
        self.preview_qty.setMinimumHeight(36)
        self.preview_qty.setMinimumWidth(90)
        quantity_row.addWidget(self.preview_qty)
        preview_actions.addLayout(quantity_row)
        add_btn = QPushButton("  Добавить в аренду")
        add_btn.setObjectName("Primary")
        add_btn.setIcon(icon("plus", "white"))
        add_btn.setMinimumHeight(40)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._add_previewed)
        preview_actions.addWidget(add_btn)
        hide_btn = QPushButton("Скрыть")
        hide_btn.setObjectName("Ghost")
        hide_btn.clicked.connect(lambda: self.preview.setVisible(False))
        preview_actions.addWidget(hide_btn)
        preview_row.addLayout(preview_actions)
        self.preview.body.addLayout(preview_row)
        self.preview.setVisible(False)
        self._preview_id: int | None = None
        root.addWidget(self.preview)

        # Режим сканирования. Складской сканер работает как клавиатура: печатает
        # код и жмёт Enter. Поле само возвращает себе фокус, чтобы оператор мог
        # сканировать позицию за позицией, не трогая мышь.
        self.scan = ScanInput(
            "Сканируйте штрихкод — реквизит сразу попадёт в выдачу")
        self.scan.scanned.connect(self._scanned)
        root.addWidget(self.scan)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.setMinimumHeight(260)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        bottom.addStretch()

        totals_box = QGroupBox("Расчёт")
        totals_form = QFormLayout(totals_box)
        self.discount = QDoubleSpinBox()
        self.discount.setRange(0, 100)
        self.discount.setSuffix(" %")
        self.discount.valueChanged.connect(self._recalc)
        if not auth_service.is_admin():
            # скидка — административная операция; поле доступно только для просмотра,
            # а сама попытка изменения отклоняется в бизнес-логике
            self.discount.setReadOnly(True)
            self.discount.setButtonSymbols(QDoubleSpinBox.NoButtons)
            self.discount.setToolTip("Скидку может установить только администратор.")
        self.deposit = QDoubleSpinBox()
        self.deposit.setRange(0, 100_000_000)
        self.deposit.setSuffix(" \u20bd")
        self.deposit.valueChanged.connect(self._recalc)
        self.lbl_before = QLabel("0,00 ₽")
        self.lbl_discount = QLabel("0,00 ₽")
        self.lbl_total = QLabel("0,00 ₽")
        self.lbl_total.setObjectName("BigNumber")
        self.lbl_valuation = QLabel("0,00 ₽")
        totals_form.addRow(f"Стоимость ({RENTAL_PERIOD_DAYS} дн.):", self.lbl_before)
        totals_form.addRow("Скидка:", self.discount)
        totals_form.addRow("Сумма скидки:", self.lbl_discount)
        totals_form.addRow("ИТОГО АРЕНДА:", self.lbl_total)
        totals_form.addRow("Залог:", self.deposit)
        totals_form.addRow("Оценочная стоимость:", self.lbl_valuation)
        bottom.addWidget(totals_box)
        root.addLayout(bottom)

        actions = QHBoxLayout()
 
        actions.addWidget(muted("Форма сохраняется автоматически — "
                                "при закрытии программы набранное не пропадёт."))
        clear_btn = QPushButton("Очистить форму")
        clear_btn.clicked.connect(self.reset)
        actions.addWidget(clear_btn)
        actions.addStretch()
        self.create_btn = QPushButton("  Создать аренду и документ")
        self.create_btn.setObjectName("Primary")
        self.create_btn.setIcon(icon("check", "white"))
        self.create_btn.setMinimumHeight(46)
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.clicked.connect(self._create)
        actions.addWidget(self.create_btn)
        root.addLayout(actions)

    # -------------------------------------------------- черновик формы
    def _touch(self) -> None:
        if not self._restoring:
            self._autosave.start()

    def _draft_payload(self) -> dict:
        return {
            "client_id": self.client_id,
            "client_text": self.client_edit.text(),
            "production_id": self.production.currentData(),
            "project_id": self.project.currentData(),
            "payment_method": self.payment.currentData(),
            "invoice_production_id": self.invoice_to.currentData(),
            "date_start": self.date_start.value().isoformat() if self.date_start.value() else None,
            "date_end": self.date_end.value().isoformat() if self.date_end.value() else None,
            "discount_percent": self.discount.value(),
            "deposit": self.deposit.value(),
            "lines": [
                {"item_id": line.item_id, "name": line.name, "inv_number": line.inv_number,
                 "description": line.description, "note": line.note, "quantity": line.quantity,
                 "price": line.price, "valuation": line.valuation}
                for line in self.lines
            ],
        }

    def _save_draft(self) -> None:
        payload = self._draft_payload()
        if self.draft_id:
            # у оформления свой номер и своя строка в базе: пустую форму тоже
            # сохраняем, иначе после перезапуска потеряется сам факт открытия
            rental_drafts_service.save(self.draft_id, payload)
            return
        if not payload["lines"] and not payload["client_text"]:
            draft_service.clear(DRAFT_NEW_RENTAL)
            return
        draft_service.save(DRAFT_NEW_RENTAL, payload)

    def _restore_draft(self) -> None:
        """Восстанавливает форму, если прошлый сеанс завершился, не создав аренду."""
        if self.draft_id:
            return          # состояние оформления загружается напрямую, без вопросов
        draft = draft_service.load(DRAFT_NEW_RENTAL)
        if not draft or not (draft.get("lines") or draft.get("client_text")):
            return
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Незавершённая аренда",
            f"Найдена незаконченная форма новой аренды от {fmt_dt(draft.get('_updated_at'))}\n"
            f"(позиций: {len(draft.get('lines', []))}).\n\nВосстановить её?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer != QMessageBox.Yes:
            draft_service.clear(DRAFT_NEW_RENTAL)
            return
        self.apply_draft(draft)

    def apply_draft(self, draft: dict) -> None:
        self._restoring = True
        try:
            self.client_id = draft.get("client_id")
            self.client_edit.setText(draft.get("client_text") or "")
            if self.client_id:
                client = clients_service.get(self.client_id)
                if client:
                    self._apply_client(client)
            index = self.production.findData(draft.get("production_id"))
            if index >= 0:
                self.production.setCurrentIndex(index)
                self._production_changed()
            index = self.project.findData(draft.get("project_id"))
            if index >= 0:
                self.project.setCurrentIndex(index)
            index = self.payment.findData(draft.get("payment_method"))
            if index >= 0:
                self.payment.setCurrentIndex(index)
            index = self.invoice_to.findData(draft.get("invoice_production_id"))
            if index >= 0:
                self.invoice_to.setCurrentIndex(index)
            if draft.get("date_start"):
                self.date_start.set_value(date.fromisoformat(draft["date_start"]))
            self.date_end.set_value(
                date.fromisoformat(draft["date_end"]) if draft.get("date_end") else None)
            self.discount.setValue(float(draft.get("discount_percent") or 0))
            self.deposit.setValue(float(draft.get("deposit") or 0))
            self.lines = [RentalLine(**{k: v for k, v in line.items()}) for line in draft.get("lines", [])]
            self._refresh_table()
        finally:
            self._restoring = False

    # ------------------------------------------------------------ справочники
    def reload_references(self) -> None:
        current_prod = self.production.currentData()
        self.production.blockSignals(True)
        self.production.clear()
        self.invoice_to.clear()
        self.production.addItem("— выберите —", None)
        self.invoice_to.addItem("— выберите —", None)
        for prod in productions_service.search(""):
            label = f"{prod['legal_form']} {prod['name']}".strip()
            self.production.addItem(label, prod["id"])
            self.invoice_to.addItem(label, prod["id"])
        index = self.production.findData(current_prod)
        self.production.setCurrentIndex(max(0, index))
        self.production.blockSignals(False)
        self._production_changed()

    def _production_changed(self) -> None:
        production_id = self.production.currentData()
        self.project.clear()
        self.project.addItem("— выберите —", None)
        for project in projects_service.search("", production_id):
            self.project.addItem(project["name"], project["id"])
        if production_id:
            index = self.invoice_to.findData(production_id)
            if index >= 0:
                self.invoice_to.setCurrentIndex(index)

    def _payment_changed(self) -> None:
        self.invoice_to.setEnabled(self.payment.currentData() == "bank")

    # --------------------------------------------------------------- клиент
    def _client_text_changed(self, text: str) -> None:
        """Пока имя набирается, выбранное физ. лицо считается сброшенным."""
        self.client_id = None
        self.client_info.setText("новое физ. лицо — будет создано после ввода данных")
        exact = next((c for c in clients_service.suggest(text, 10)
                      if c["full_name"].strip().lower() == text.strip().lower()), None)
        if exact:
            self._apply_client(exact)

    def _pick_client(self) -> None:
        """Выбор из полного списка — альтернатива подсказкам под полем."""
        from .client_picker import ClientPickerDialog

        dialog = ClientPickerDialog(self, self.client_edit.text().strip())
        if dialog.exec() and dialog.client:
            self._apply_client(dialog.client)

    def _suggest_clients(self, text: str) -> list[dict]:
        rows = []
        for client in clients_service.suggest(text, 8):
            details = [d for d in (client.get("phone"), client.get("passport"),
                                   client.get("email")) if d]
            rows.append({
                "title": client["full_name"],
                "subtitle": "   ·   ".join(details) if details else "без контактов",
                "client": client,
            })
        return rows

    def _apply_client(self, client: dict) -> None:
        """Автоподстановка: паспорт, телефон, адрес, email больше вводить не нужно."""
        self.client_suggest.close_list()
        self.client_id = client["id"]
        self.client_edit.setText(client["full_name"])
        self.client_info.setText(
            f"Паспорт: {client['passport'] or '—'} · Тел.: {client['phone'] or '—'} · "
            f"{client['email'] or '—'}\n{client['address'] or ''}")

    def _new_client(self) -> None:
        from .directory_dialogs import ClientDialog
        dialog = ClientDialog(self)
        if dialog.exec() and dialog.client_id:
            self._apply_client(clients_service.get(dialog.client_id))

    def _new_production(self) -> None:
        from .directory_dialogs import ProductionDialog
        dialog = ProductionDialog(self)
        if dialog.exec() and dialog.production_id:
            self.reload_references()
            self.production.setCurrentIndex(self.production.findData(dialog.production_id))

    def _new_project(self) -> None:
        from .directory_dialogs import ProjectDialog
        dialog = ProjectDialog(self, production_id=self.production.currentData())
        if dialog.exec() and dialog.project_id:
            self._production_changed()
            self.project.setCurrentIndex(self.project.findData(dialog.project_id))

    # -------------------------------------------------------------- позиции
    def _suggest_items(self, text: str) -> list[dict]:
        from ..services.photo_service import photo_service

        rows = []
        for item in inventory_service.search(text, limit=8):
            photo = photo_service.primary_photo(item["id"])
            rows.append({
                "title": item["name"],
                "subtitle": (f"инв. № {item['inv_number']}   ·   доступно "
                             f"{item['available_now']} из {item['quantity_total']}"),
                "icon_path": photo["thumb_path"] if photo else "",
                "item_id": item["id"],
            })
        return rows

    def _preview_item(self, item_id: int) -> None:
        # список подсказок больше не нужен — его место занимает предпросмотр
        """
        Показывает выбранный реквизит перед добавлением: фотография, остаток,
        цена, количество. Ничего в аренду ещё не попадает.
        """
        from ..services.photo_service import photo_service

        item = inventory_service.get(item_id)
        if not item:
            return
        self._preview_id = item_id
        self.item_suggest.close_list()
        photo = photo_service.primary_photo(item_id)
        if photo:
            self.preview_photo.set_photo(photo["thumb_path"])
        else:
            self.preview_photo.clear_photo()
        self.preview_name.setText(item["name"])
        already = next((l.quantity for l in self.lines if l.item_id == item_id), 0)
        parts = [f"инв. № {item['inv_number']}",
                 f"доступно {item['available_now']} из {item['quantity_total']}",
                 f"{money(item['rental_price'])} за {RENTAL_PERIOD_DAYS} дн."]
        if item.get("category_path"):
            parts.append(item["category_path"])
        if already:
            parts.append(f"уже в списке: {already} шт.")
        self.preview_info.setText("   ·   ".join(parts))
        self.preview_qty.setMaximum(max(1, item["available_now"] or 1))
        self.preview_qty.setValue(1)
        self.preview.setVisible(True)

    def _add_previewed(self) -> None:
        if not self._preview_id:
            return
        for _ in range(self.preview_qty.value()):
            self.add_item(self._preview_id)
        self.item_search.clear()
        self.preview.setVisible(False)
        self._preview_id = None
        self.item_search.setFocus()

    def _search_items(self) -> None:
        """Enter в поле поиска: один результат — сразу в предпросмотр, иначе выбор."""
        text = self.item_search.text().strip()
        if not text:
            return
        items = inventory_service.search(text, limit=50)
        if not items:
            QMessageBox.information(self, "Поиск", "Ничего не найдено.")
            return
        if len(items) == 1:
            self._preview_item(items[0]["id"])
            return
        options = [f"{i['inv_number']} · {i['name']} · доступно {i['available_now']}"
                   for i in items]
        choice, ok = QInputDialog.getItem(self, "Выберите реквизит", "Найдено:",
                                          options, 0, False)
        if ok:
            self._preview_item(items[options.index(choice)]["id"])

    def _photo_search(self) -> None:
        from .photo_search_dialog import PhotoSearchDialog
        dialog = PhotoSearchDialog(self)
        dialog.add_to_rental.connect(self.add_item)
        dialog.open_item.connect(self.add_item)
        dialog.exec()

    def _scanned(self, code: str) -> None:
        """
        Отсканированный штрихкод добавляет позицию в текущую выдачу.

        Три исхода, и каждый оператор видит сразу: добавлено, уже в списке
        (количество увеличено), не найдено. Ошибки доступности проверит
        бизнес-логика при сохранении аренды — здесь список только собирается.
        """
        item = barcode_service.find_item(code)
        if not item:
            self.scan.show_result(
                f"Штрихкод «{barcode_service.normalize(code)}» не найден. "
                "Проверьте этикетку или найдите реквизит поиском.", "error")
            return
        if item.get("is_archived"):
            self.scan.show_result(f"«{item['name']}» в архиве и не выдаётся.", "error")
            return

        full = inventory_service.get(item["id"])
        already = next((l for l in self.lines if l.item_id == item["id"]), None)
        self.add_item(item["id"])
        line = next((l for l in self.lines if l.item_id == item["id"]), None)
        quantity = line.quantity if line else 1
        available = full["available_now"] if full else 0

        if quantity > available:
            self.scan.show_result(
                f"«{item['name']}»: в списке {quantity} шт., а свободно {available}. "
                "Уменьшите количество, иначе аренду не сохранить.", "warn")
        elif already:
            self.scan.show_result(
                f"«{item['name']}» уже в списке — количество увеличено до {quantity}.", "ok")
        else:
            self.scan.show_result(
                f"«{item['name']}» добавлен. Свободно: {available} шт.", "ok")

    def add_item(self, item_id: int) -> None:
        item = inventory_service.get(item_id)
        if not item:
            return
        for line in self.lines:
            if line.item_id == item_id:
                line.quantity += 1
                self._refresh_table()
                return
        self.lines.append(RentalLine(
            item_id=item_id, name=item["name"], inv_number=item["inv_number"],
            description=item["description"], quantity=1,
            price=item["rental_price"], valuation=item["valuation"]))
        self._refresh_table()

    def _refresh_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for index, line in enumerate(self.lines):
            self.table.insertRow(index)
            self.table.setItem(index, 0, cell(line.inv_number, data=line.item_id))
            self.table.setItem(index, 1, cell(line.name))
            desc = QTableWidgetItem(line.description)
            desc.setFlags(desc.flags() | Qt.ItemIsEditable)
            self.table.setItem(index, 2, desc)
            note = QTableWidgetItem(line.note)
            note.setFlags(note.flags() | Qt.ItemIsEditable)
            note.setToolTip("Например: «Царапина на правой ножке» — попадёт в документ аренды")
            self.table.setItem(index, 3, note)

            qty = QSpinBox()
            qty.setRange(1, 100000)
            qty.setValue(line.quantity)
            qty.valueChanged.connect(lambda value, i=index: self._set_quantity(i, value))
            self.table.setCellWidget(index, 4, qty)

            price = QDoubleSpinBox()
            price.setRange(0, 10_000_000)
            price.setValue(line.price)
            price.valueChanged.connect(lambda value, i=index: self._set_price(i, value))
            self.table.setCellWidget(index, 5, price)

            self.table.setItem(index, 6, cell(money_plain(line.valuation_amount), True))
            self.table.setItem(index, 7, cell(money_plain(line.amount), True))

            remove = QPushButton()
            remove.setIcon(icon("trash", "danger"))
            remove.setObjectName("IconButton")
            remove.setToolTip("Удалить позицию из аренды")
            remove.clicked.connect(lambda _=False, i=index: self._remove(i))
            self.table.setCellWidget(index, 8, remove)
        self.table.blockSignals(False)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._recalc()
        self._touch()

    def _cell_changed(self, item: QTableWidgetItem) -> None:
        row, col = item.row(), item.column()
        if row >= len(self.lines):
            return
        if col == 2:
            self.lines[row].description = item.text()
        elif col == 3:
            self.lines[row].note = item.text()
        self._touch()

    def _set_quantity(self, index: int, value: int) -> None:
        self.lines[index].quantity = value
        self.table.setItem(index, 7, cell(money_plain(self.lines[index].amount), True))
        self.table.setItem(index, 6, cell(money_plain(self.lines[index].valuation_amount), True))
        self._recalc()

    def _set_price(self, index: int, value: float) -> None:
        self.lines[index].price = value
        self.table.setItem(index, 7, cell(money_plain(self.lines[index].amount), True))
        self._recalc()

    def _remove(self, index: int) -> None:
        if 0 <= index < len(self.lines):
            self.lines.pop(index)
            self._refresh_table()

    def _recalc(self) -> None:
        totals = calculate(self.lines, self.discount.value(), self.deposit.value())
        self.lbl_before.setText(money(totals["total_before_discount"]))
        self.lbl_discount.setText("− " + money(totals["discount_amount"]))
        self.lbl_total.setText(money(totals["total_final"]))
        self.lbl_valuation.setText(money(totals["total_valuation"]))

    # ------------------------------------------------------------- создание
    def _draft(self) -> RentalDraft:
        return RentalDraft(
            client_id=self.client_id,
            production_id=self.production.currentData(),
            project_id=self.project.currentData(),
            payment_method=self.payment.currentData(),
            invoice_production_id=self.invoice_to.currentData(),
            date_start=self.date_start.value(),
            date_end=self.date_end.value(),
            discount_percent=self.discount.value(),
            deposit=self.deposit.value(),
            lines=self.lines,
        )

    def _create(self) -> None:
        if not self.client_id and self.client_edit.text().strip():
            show_error(self, AppError(
                "Клиент не выбран из базы. Нажмите «Новый…», чтобы завести карточку физического лица."))
            return
        try:
            rental_id = rentals_service.create(
                self._draft(), number=self.draft_number or None)
            documents_service.create_document(rental_id)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:
            show_error(self, exc, "Не удалось создать аренду.")
            return
        rental = rentals_service.get(rental_id)
        QMessageBox.information(self, "Аренда создана",
                                f"Аренда {rental['number']} сохранена, документ сформирован.")
        if self.draft_id:
            # оформление завершено: оно уходит из списка незавершённых, но сама
            # аренда остаётся в базе, истории, отчётах и календаре
            rental_drafts_service.mark_issued(self.draft_id, rental_id)
            self.rental_created.emit(rental_id)
            self.draft_finished.emit(self.draft_id)
            return
        draft_service.clear(DRAFT_NEW_RENTAL)
        self.reset()
        self.rental_created.emit(rental_id)

    def reset(self) -> None:
        if not self.draft_id:
            draft_service.clear(DRAFT_NEW_RENTAL)
        self.lines = []
        self.client_id = None
        self.client_edit.clear()
        self.client_info.setText("—")
        self.discount.setValue(0)
        self.deposit.setValue(0)
        self.date_start.set_value(date.today())
        self.date_end.set_value(date.today())
        self.project.setCurrentIndex(0)
        self._refresh_table()
