# -*- coding: utf-8 -*-
"""Раздел «В аренде»: список активных аренд, уведомления о завершении, карточка аренды."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QPushButton, QTabWidget,
                               QVBoxLayout, QWidget)

from ..errors import AppError
from ..services.audit_service import audit_service
from ..services.auth_service import auth_service
from ..services.billing_service import billing_service
from ..services.documents_service import documents_service
from ..services.rentals_service import rentals_service
from ..services.settings_service import settings_service
from ..utils import fmt_date, fmt_dt, money, money_plain, parse_date
from ..version import RENTAL_PERIOD_DAYS
from .errors import confirm, info, show_error
from .theme import Color, Space
from .theme import DANGER, WARN
from .widgets.common import (Card, DatePicker, StatusBadge, cell, hint, icon,
                             make_table, muted, scrollable_body, section_title,
                             title_label)


class RentalsView(QWidget):
    open_item = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_closed = False
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        head = QHBoxLayout()
        head.addWidget(section_title("Действующие аренды"))
        head.addStretch()
        self.summary = muted("")
        head.addWidget(self.summary)
        root.addLayout(head)

        filters = QHBoxLayout()
        filters.setSpacing(Space.MD)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по номеру аренды, клиенту, проекту или продакшену…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload)
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setMinimumHeight(42)
        filters.addWidget(self.search, 1)
        self.all_box = QCheckBox("Показывать завершённые")
        self.all_box.toggled.connect(self.reload)
        filters.addWidget(self.all_box)
        root.addLayout(filters)

        self.notice = QLabel("")
        self.notice.setObjectName("Notice")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        root.addWidget(self.notice)

        table_card = Card(padding=Space.SM)
        self.table = make_table(
            ["Номер", "Проект", "Клиент", "Продакшен", "Начало", "Окончание", "Осталось",
             "Позиций", "Итого аренда", "Залог", "Статус"], 1)
        self.table.doubleClicked.connect(self.open_selected)
        table_card.body.addWidget(self.table)
        root.addWidget(table_card, 1)

        actions = QHBoxLayout()
        actions.setSpacing(Space.MD)
        open_btn = QPushButton("  Открыть аренду")
        open_btn.setObjectName("Primary")
        open_btn.setIcon(icon("eye", "white"))
        open_btn.setMinimumHeight(44)
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(self.open_selected)
        actions.addWidget(open_btn)
        extend_btn = QPushButton("  Продлить")
        extend_btn.setIcon(icon("calendar"))
        extend_btn.setMinimumHeight(44)
        extend_btn.clicked.connect(self._extend_selected)
        actions.addWidget(extend_btn)
        excel_btn = QPushButton("  Выгрузить в Excel")
        excel_btn.setIcon(icon("file"))
        excel_btn.setMinimumHeight(44)
        excel_btn.setToolTip("Аренды и их позиции двумя листами — для расчётов и сводок")
        excel_btn.clicked.connect(self._export_excel)
        actions.addWidget(excel_btn)
        actions.addStretch()
        root.addLayout(actions)

    def _export_excel(self) -> None:
        """Выгружает то, что сейчас в списке: с учётом фильтров экрана."""
        from .. import paths
        from ..services.excel_service import excel_service

        ids = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                ids.append(item.data(Qt.UserRole))
        if not ids:
            info(self, "В списке нет аренд — выгружать нечего.", "Нет данных")
            return
        target = paths.DOCS_DIR / "Аренды.xlsx"
        try:
            excel_service.export_rentals(target, rental_ids=ids)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Выгрузить в Excel не удалось.")
            return
        from .settings_view import open_folder
        info(self, f"Выгружено аренд: {len(ids)}\n{target}")
        open_folder(target.parent)

    # ---------------------------------------------------------------- данные
    def reload(self) -> None:
        # при каждом открытии раздела проверяем, не наступил ли новый 14-дневный период
        try:
            billing_service.run_prolongations()
        except Exception:
            pass
        text = self.search.text()
        rows = (rentals_service.list_all(text) if self.all_box.isChecked()
                else rentals_service.list_active(text))
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        total = 0.0
        for row in rows:
            index = self.table.rowCount()
            self.table.insertRow(index)
            left = row.get("days_left")
            self.table.setItem(index, 0, cell(row["number"], data=row["id"]))
            self.table.setItem(index, 1, cell(row.get("project_name") or "—"))
            self.table.setItem(index, 2, cell(row.get("client_name") or "—"))
            self.table.setItem(index, 3, cell(row.get("production_name") or "—"))
            self.table.setItem(index, 4, cell(fmt_date(row["date_start"])))
            self.table.setItem(index, 5, cell(fmt_date(row["date_end"]) if row["date_end"] else "не определено"))
            self.table.setItem(index, 6, cell("—" if left is None else f"{left} дн.", True))
            self.table.setItem(index, 7, cell(row.get("positions", ""), True))
            self.table.setItem(index, 8, cell(money_plain(row["total_final"]), True))
            self.table.setItem(index, 9, cell(money_plain(row["deposit"]), True))
            self.table.setItem(index, 10, cell({"active": "Действует", "closed": "Завершена",
                                                "cancelled": "Отменена"}.get(row["status"], row["status"])))
            if row["status"] == "active" and left is not None and left <= 2:
                # цвет только у ячейки «осталось дней», где и есть тревога:
                # окрашенная целиком строка читается хуже и шумит
                color = QColor(DANGER if left < 0 else WARN)
                alarm = self.table.item(index, 6)
                if alarm:
                    alarm.setForeground(color)
                    font = alarm.font()
                    font.setBold(True)
                    alarm.setFont(font)
            if row["status"] == "active":
                total += row["total_final"]
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.summary.setText(f"Аренд: {len(rows)}   ·   сумма действующих: {money(total)}")
        self._update_notice()

    def _update_notice(self) -> None:
        days = settings_service.get_int("notify.days_before_end", 2)
        expiring = rentals_service.expiring_soon(days)
        if not expiring:
            self.notice.setVisible(False)
            return
        parts = []
        for rental in expiring[:5]:
            left = rental["days_left"]
            if left is not None and left < 0:
                parts.append(f"{rental['number']} — срок истёк {abs(left)} дн. назад")
            else:
                parts.append(f"{rental['number']} заканчивается через {left} дн.")
        text = ";   ".join(parts)
        if len(expiring) > 5:
            text += f"   и ещё {len(expiring) - 5}"
        self.notice.setText(text)
        self.notice.setVisible(True)
        self.notice.setToolTip("Аренды, срок которых истекает или уже истёк")

    def selected_rental_id(self):
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.UserRole) if row >= 0 else None

    def open_selected(self) -> None:
        rental_id = self.selected_rental_id()
        if rental_id:
            self.open_rental(rental_id)

    def open_rental(self, rental_id: int) -> None:
        dialog = RentalCardDialog(self, rental_id)
        dialog.open_item.connect(self.open_item.emit)
        dialog.exec()
        self.reload()

    def _extend_selected(self) -> None:
        rental_id = self.selected_rental_id()
        if not rental_id:
            return
        if extend_rental(self, rental_id):
            self.reload()


def extend_rental(parent, rental_id: int) -> bool:
    """Пролонгация: новая дата, запись продления и документ пролонгации."""
    rental = rentals_service.get(rental_id)
    if not rental:
        return False
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Продление аренды {rental['number']}")
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    current_end = rental["date_end"]
    form.addRow("Текущая дата окончания:",
                QLabel(fmt_date(current_end) if current_end else "не определена"))
    picker = DatePicker()
    base = date.fromisoformat(current_end) if current_end else date.today()
    picker.set_value(base + timedelta(days=7))
    form.addRow("Новая дата окончания:", picker)
    comment = QLineEdit()
    comment.setPlaceholderText("Например: досъёмки")
    form.addRow("Комментарий:", comment)
    layout.addLayout(form)

    buttons = QHBoxLayout()
    ok_btn = QPushButton("Продлить и создать документ")
    ok_btn.setObjectName("Primary")
    ok_btn.clicked.connect(dialog.accept)
    cancel = QPushButton("Отмена")
    cancel.clicked.connect(dialog.reject)
    buttons.addStretch()
    buttons.addWidget(cancel)
    buttons.addWidget(ok_btn)
    layout.addLayout(buttons)

    if not dialog.exec():
        return False
    try:
        extension_id = rentals_service.extend(rental_id, picker.value(), comment.text().strip())
        document_id = documents_service.create_document(rental_id, "extension", extension_id)
    except AppError as exc:
        show_error(parent, exc)
        return False
    except Exception as exc:
        show_error(parent, exc, "Не удалось продлить аренду.")
        return False

    info(parent, "Аренда продлена, документ пролонгации создан. Исходный документ не изменён.")
    from .document_dialog import DocumentDialog
    DocumentDialog(parent, document_id).exec()
    return True


class RentalCardDialog(QDialog):
    """
    Карточка аренды версии 2.0.

    Аренда — контейнер: внутри живут позиции с собственными датами выдачи,
    периодами и пролонгациями. Здесь видно сводку, каждую позицию с её сроком
    и полную историю начислений.
    """

    open_item = Signal(int)

    def __init__(self, parent, rental_id: int):
        super().__init__(parent)
        self.rental_id = rental_id
        self.setWindowTitle("Аренда")
        self.resize(1280, 820)
        self._build()
        self.reload()

    def _build(self) -> None:
        root, footer = scrollable_body(self)
        self.header = QLabel()
        self.header.setObjectName("SectionTitle")
        root.addWidget(self.header)

        info_row = QHBoxLayout()
        self.left_box = QGroupBox("Стороны")
        self.left_form = QFormLayout(self.left_box)
        info_row.addWidget(self.left_box, 1)
        self.money_box = QGroupBox("Начисления")
        self.money_form = QFormLayout(self.money_box)
        info_row.addWidget(self.money_box, 1)
        self.state_box = QGroupBox("Состав аренды")
        self.state_form = QFormLayout(self.state_box)
        info_row.addWidget(self.state_box, 1)
        root.addLayout(info_row)

        tabs = QTabWidget()

        items_page = QWidget()
        items_layout = QVBoxLayout(items_page)
        items_layout.addWidget(muted(
            f"Цена позиции — стоимость аренды на срок до {RENTAL_PERIOD_DAYS} календарных дней. "
            "Не возвращённая к концу периода позиция автоматически пролонгируется на следующий "
            "период по полной стоимости."))
        self.items_table = make_table(
            ["Инв. №", "Наименование", "Кол-во", "Возвр.", "Цена", "Выдано", "Текущий период",
             "След. пролонгация", "Продлений", "Начислено", "Статус"], 1)
        self.items_table.setMinimumHeight(240)
        self.items_table.doubleClicked.connect(self._open_item)
        items_layout.addWidget(self.items_table)

        item_actions = QHBoxLayout()
        self.return_btn = QPushButton("Принять возврат…")
        self.return_btn.setObjectName("Primary")
        self.return_btn.clicked.connect(self._return_item)
        item_actions.addWidget(self.return_btn)
        self.add_items_btn = QPushButton("Добавить реквизит")
        self.add_items_btn.clicked.connect(self._add_items)
        item_actions.addWidget(self.add_items_btn)
        self.open_item_btn = QPushButton("Открыть карточку реквизита")
        self.open_item_btn.clicked.connect(self._open_item)
        item_actions.addWidget(self.open_item_btn)
        self.remove_item_btn = QPushButton("Удалить позицию")
        self.remove_item_btn.setObjectName("Danger")
        self.remove_item_btn.clicked.connect(self._remove_item)
        item_actions.addWidget(self.remove_item_btn)
        item_actions.addStretch()
        items_layout.addLayout(item_actions)
        tabs.addTab(items_page, "Позиции аренды")

        charges_page = QWidget()
        charges_layout = QVBoxLayout(charges_page)
        self.charges_table = make_table(["Дата", "Начисление", "Период", "Позиции", "Кол-во", "Сумма"], 3)
        charges_layout.addWidget(self.charges_table)
        tabs.addTab(charges_page, "История начислений")

        returns_page = QWidget()
        returns_layout = QVBoxLayout(returns_page)
        self.returns_table = make_table(["Дата и время", "Позиция", "Количество", "Комментарий"], 1)
        returns_layout.addWidget(self.returns_table)
        tabs.addTab(returns_page, "Возвраты")

        docs_page = QWidget()
        docs_layout = QVBoxLayout(docs_page)
        self.docs_table = make_table(["Документ", "Тип", "Создан", "Файл"], 0)
        self.docs_table.doubleClicked.connect(self._open_document)
        docs_layout.addWidget(self.docs_table)
        tabs.addTab(docs_page, "Документы")

        audit_page = QWidget()
        audit_layout = QVBoxLayout(audit_page)
        self.audit_table = make_table(["Дата и время", "Пользователь", "Действие", "Подробности", "Сумма"], 3)
        audit_layout.addWidget(self.audit_table)
        tabs.addTab(audit_page, "Журнал действий")

        root.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(Space.SM)
        doc_btn = QPushButton("Открыть документ")
        doc_btn.clicked.connect(self._open_document)
        buttons.addWidget(doc_btn)
        new_doc_btn = QPushButton("Сформировать документ заново")
        new_doc_btn.clicked.connect(self._recreate_document)
        buttons.addWidget(new_doc_btn)
        self.discount_btn = QPushButton("Изменить скидку…")
        self.discount_btn.clicked.connect(self._set_discount)
        buttons.addWidget(self.discount_btn)
        self.extend_btn = QPushButton("Продлить срок аренды")
        self.extend_btn.clicked.connect(self._extend)
        buttons.addWidget(self.extend_btn)
        self.close_btn = QPushButton("Завершить аренду")
        self.close_btn.clicked.connect(self._close_rental)
        buttons.addWidget(self.close_btn)
        buttons.addStretch()
        close = QPushButton("Закрыть окно")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        footer.addLayout(buttons)

    # ---------------------------------------------------------------- данные
    def reload(self) -> None:
        try:
            billing_service.run_prolongations()
        except Exception:
            pass
        rental = rentals_service.get(self.rental_id)
        if not rental:
            return
        self.rental = rental
        summary = rental["summary"]
        status = {"active": "Действует", "closed": "Завершена", "cancelled": "Отменена"}.get(
            rental["status"], rental["status"])
        self.header.setText(f"Аренда {rental['number']}   ·   {status}")

        for form in (self.left_form, self.money_form, self.state_form):
            while form.rowCount():
                form.removeRow(0)

        for label, value in [
            ("Клиент:", rental.get("client_name") or "—"),
            ("Телефон:", rental.get("client_phone") or "—"),
            ("Проект:", rental.get("project_name") or "—"),
            ("Продакшен:", rental.get("production_name") or "—"),
            ("Создана:", fmt_dt(rental["created_at"])),
            ("Способ оплаты:", rental["payment_label"]),
        ]:
            widget = QLabel(str(value))
            widget.setWordWrap(True)
            self.left_form.addRow(QLabel(label), widget)

        total_label = QLabel(money(rental["total_final"]))
        total_label.setObjectName("BigNumber")
        for label, value in [
            ("Первоначальная аренда:", money(summary["initial_total"])),
            ("Доборы:", money(summary["addition_total"])),
            ("Пролонгации:", money(summary["prolongation_total"])),
            ("Начислено всего:", money(summary["accrued_total"])),
            (f"Скидка {money_plain(rental['discount_percent'])} %:",
             "− " + money(rental["discount_amount"])),
        ]:
            self.money_form.addRow(QLabel(label), QLabel(str(value)))
        self.money_form.addRow(QLabel("К ОПЛАТЕ:"), total_label)
        self.money_form.addRow(QLabel("Залог:"), QLabel(money(rental["deposit"])))

        for label, value in [
            ("Позиций всего:", summary["positions"]),
            ("В аренде:", f"{summary['positions_rented']} поз. / {summary['units_rented']} шт."),
            ("Возвращено:", f"{summary['positions_returned']} поз. / {summary['units_returned']} шт."),
            ("Пролонгировано позиций:", len(summary["prolonged_positions"])),
            ("Срок аренды:", f"{fmt_date(rental['date_start'])} — "
                             f"{fmt_date(rental['date_end']) if rental['date_end'] else 'не определён'}"),
            ("Оценочная стоимость:", money(rental["total_valuation"])),
        ]:
            self.state_form.addRow(QLabel(label), QLabel(str(value)))

        self._fill_items(rental["items"])
        self._fill_charges(rental["charges"])
        self._fill_returns(rental["items"])
        self._fill_documents()
        self._fill_audit()

        active = rental["status"] == "active"
        is_admin = auth_service.is_admin()
        self.add_items_btn.setEnabled(active)
        self.return_btn.setEnabled(active)
        self.extend_btn.setEnabled(active)
        self.close_btn.setEnabled(active)
        self.discount_btn.setEnabled(is_admin)
        self.discount_btn.setToolTip("" if is_admin else "Скидку может установить только администратор")
        self.remove_item_btn.setEnabled(is_admin)
        self.remove_item_btn.setToolTip(
            "" if is_admin else "Удалить реквизит из аренды может только администратор")

    def _fill_items(self, items: list[dict]) -> None:
        self.items_table.setSortingEnabled(False)
        self.items_table.setRowCount(0)
        for item in items:
            index = self.items_table.rowCount()
            self.items_table.insertRow(index)
            self.items_table.setItem(index, 0, cell(item["inv_number_snap"], data=item["id"]))
            self.items_table.setItem(index, 1, cell(item["name_snap"]))
            self.items_table.setItem(index, 2, cell(item["quantity"], True))
            self.items_table.setItem(index, 3, cell(item["returned_qty"], True))
            self.items_table.setItem(index, 4, cell(money_plain(item["price"]), True))
            self.items_table.setItem(index, 5, cell(item["date_start_ui"]))
            self.items_table.setItem(index, 6, cell(item["period_label"]))
            self.items_table.setItem(index, 7, cell(
                fmt_date(item["next_prolongation"]) if item["status"] == "rented" else "—"))
            self.items_table.setItem(index, 8, cell(item["prolongations"], True))
            self.items_table.setItem(index, 9, cell(money_plain(item["total_amount"]), True))
            self.items_table.setItem(index, 10, cell(item["state_label"]))
            if item["state"] == "prolonged":
                color = QColor(DANGER)
            elif item["state"] == "soon":
                color = QColor(WARN)
            else:
                color = None
            if color:
                # тревожным цветом помечается только столбец состояния
                cell_item = self.items_table.item(index, 10)
                if cell_item:
                    cell_item.setForeground(color)
                    font = cell_item.font()
                    font.setBold(True)
                    cell_item.setFont(font)
        self.items_table.setSortingEnabled(True)
        self.items_table.resizeColumnsToContents()

    def _fill_charges(self, charges: list[dict]) -> None:
        self.charges_table.setRowCount(0)
        for charge in charges:
            index = self.charges_table.rowCount()
            self.charges_table.insertRow(index)
            self.charges_table.setItem(index, 0, cell(fmt_date(charge["date"])))
            self.charges_table.setItem(index, 1, cell(charge["type_label"]))
            self.charges_table.setItem(index, 2, cell(
                f"№{charge['period_number']}" if charge["type"] == "prolongation" else "№1"))
            self.charges_table.setItem(index, 3, cell(", ".join(charge["positions"])))
            self.charges_table.setItem(index, 4, cell(charge["quantity"], True))
            self.charges_table.setItem(index, 5, cell(money_plain(charge["amount"]), True))
        self.charges_table.resizeColumnsToContents()

    def _fill_returns(self, items: list[dict]) -> None:
        self.returns_table.setRowCount(0)
        for item in items:
            for entry in item["returns"]:
                index = self.returns_table.rowCount()
                self.returns_table.insertRow(index)
                self.returns_table.setItem(index, 0, cell(fmt_dt(entry["returned_at"])))
                self.returns_table.setItem(index, 1, cell(item["name_snap"]))
                self.returns_table.setItem(index, 2, cell(entry["quantity"], True))
                self.returns_table.setItem(index, 3, cell(entry["comment"]))
        self.returns_table.resizeColumnsToContents()

    def _fill_documents(self) -> None:
        self.docs_table.setRowCount(0)
        for doc in documents_service.list_for_rental(self.rental_id):
            index = self.docs_table.rowCount()
            self.docs_table.insertRow(index)
            self.docs_table.setItem(index, 0, cell(doc["number"], data=doc["id"]))
            self.docs_table.setItem(index, 1, cell(
                "Аренда" if doc["doc_type"] == "rental" else "Пролонгация"))
            self.docs_table.setItem(index, 2, cell(fmt_dt(doc["created_at"])))
            self.docs_table.setItem(index, 3, cell(doc["file_path"] or "—"))
        self.docs_table.resizeColumnsToContents()

    def _fill_audit(self) -> None:
        self.audit_table.setRowCount(0)
        entries = audit_service.for_entity("rental", self.rental_id)
        for item in self.rental["items"]:
            entries += audit_service.for_entity("rental_item", item["id"])
        entries.sort(key=lambda e: e["created_at"], reverse=True)
        for entry in entries:
            index = self.audit_table.rowCount()
            self.audit_table.insertRow(index)
            self.audit_table.setItem(index, 0, cell(fmt_dt(entry["created_at"])))
            self.audit_table.setItem(index, 1, cell(entry["user_name"]))
            self.audit_table.setItem(index, 2, cell(entry["action"]))
            self.audit_table.setItem(index, 3, cell(entry["details"]))
            self.audit_table.setItem(index, 4, cell(
                money_plain(entry["amount"]) if entry["amount"] else "", True))
        self.audit_table.resizeColumnsToContents()

    # -------------------------------------------------------------- действия
    def _selected_rental_item(self):
        row = self.items_table.currentRow()
        if row < 0:
            return None
        rental_item_id = self.items_table.item(row, 0).data(Qt.UserRole)
        return next((i for i in self.rental["items"] if i["id"] == rental_item_id), None)

    def _return_item(self) -> None:
        item = self._selected_rental_item()
        if not item:
            show_error(self, AppError("Выберите позицию, которую принимаете обратно."))
            return
        if item["status"] == "returned":
            show_error(self, AppError("Эта позиция уже возвращена полностью."))
            return
        remaining = item["remaining_qty"]
        quantity, ok = QInputDialog.getInt(
            self, "Приём реквизита", f"«{item['name_snap']}» — сколько принимаете обратно?\n"
            f"В аренде: {remaining} шт.", remaining, 1, remaining)
        if not ok:
            return
        comment, _ = QInputDialog.getText(self, "Приём реквизита", "Комментарий (состояние, повреждения):")
        try:
            billing_service.return_item(item["id"], quantity, comment)
        except AppError as exc:
            show_error(self, exc)
            return
        info(self, f"Принято {quantity} шт. Уже начисленные периоды не пересчитываются, "
                   "будущие пролонгации по возвращённому количеству прекращены.")
        self.reload()

    def _add_items(self) -> None:
        from .add_items_dialog import AddItemsDialog
        dialog = AddItemsDialog(self, date_start=None,
                                date_end=parse_date(self.rental["date_end"]))
        if not dialog.exec() or not dialog.lines:
            return
        try:
            created = rentals_service.add_items(self.rental_id, dialog.lines)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:
            show_error(self, exc, "Не удалось добавить реквизит в аренду.")
            return
        info(self, f"Добавлено позиций: {len(created)}. Дата выдачи — сегодня, "
                   f"их первый {RENTAL_PERIOD_DAYS}-дневный период считается от неё.")
        self.reload()

    def _remove_item(self) -> None:
        item = self._selected_rental_item()
        if not item:
            return
        if not confirm(self, f"Удалить позицию «{item['name_snap']}» из аренды?\n"
                             "Вместе с ней будут удалены её начисления.", "Удалить позицию"):
            return
        try:
            rentals_service.remove_item(item["id"])
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload()

    def _set_discount(self) -> None:
        percent, ok = QInputDialog.getDouble(
            self, "Скидка", "Скидка на всю начисленную сумму, %:",
            float(self.rental["discount_percent"] or 0), 0, 100, 2)
        if not ok:
            return
        try:
            rentals_service.set_discount(self.rental_id, percent)
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload()

    def _open_item(self) -> None:
        item = self._selected_rental_item()
        if item and item["item_id"]:
            self.open_item.emit(item["item_id"])
            self.accept()

    def _open_document(self) -> None:
        row = self.docs_table.currentRow()
        if row < 0:
            row = 0 if self.docs_table.rowCount() else -1
        if row < 0:
            show_error(self, AppError("По этой аренде документов пока нет."))
            return
        from .document_dialog import DocumentDialog
        DocumentDialog(self, self.docs_table.item(row, 0).data(Qt.UserRole)).exec()

    def _recreate_document(self) -> None:
        if not confirm(self, "Сформировать новый документ аренды по текущим данным?\n"
                             "Прежние документы сохранятся."):
            return
        try:
            document_id = documents_service.create_document(self.rental_id)
        except Exception as exc:
            show_error(self, exc, "Не удалось сформировать документ.")
            return
        self.reload()
        from .document_dialog import DocumentDialog
        DocumentDialog(self, document_id).exec()

    def _extend(self) -> None:
        if extend_rental(self, self.rental_id):
            self.reload()

    def _close_rental(self) -> None:
        if not confirm(self, "Завершить аренду? Весь оставшийся реквизит будет отмечен как "
                             "возвращённый, пролонгации прекратятся, история и документы сохранятся."):
            return
        try:
            rentals_service.close(self.rental_id)
            info(self, "Аренда завершена.")
            self.reload()
        except AppError as exc:
            show_error(self, exc)
