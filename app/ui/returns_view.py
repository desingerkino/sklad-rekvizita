# -*- coding: utf-8 -*-
"""
Раздел «Возвраты» версии 2.3.

Показывает, что находится у клиентов и что уже принято обратно. Приём
выполняется тем же сервисом, что и раньше (billing_service.return_item),
поэтому остатки, начисления и статусы пересчитываются как прежде.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
                               QTabWidget, QVBoxLayout, QWidget)

from ..errors import AppError
from ..services.barcode_service import barcode_service
from ..services.billing_service import billing_service
from ..services.rentals_service import rentals_service
from ..db.database import get_db
from ..utils import fmt_date, fmt_dt, money_plain
from .errors import info, show_error
from .theme import Space
from .widgets.common import Card, cell, icon, make_table, muted, section_title
from .widgets.scan_input import ScanInput


class ReturnsView(QWidget):
    """Список позиций у клиентов и журнал принятых возвратов."""

    open_rental = Signal(int)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(180)
        self._debounce.timeout.connect(self.reload)
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        head = QHBoxLayout()
        head.addWidget(section_title("Приём реквизита"))
        head.addStretch()
        self.summary = muted("")
        head.addWidget(self.summary)
        root.addLayout(head)

        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("Поиск по аренде, клиенту, проекту или реквизиту…")
        self.search.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumHeight(44)
        self.search.textChanged.connect(lambda *_: self._debounce.start())
        root.addWidget(self.search)

        # Сканирование при возврате: оператор берёт вещь и считывает этикетку,
        # программа сама находит, в какой аренде она числится.
        self.scan = ScanInput("Сканируйте штрихкод возвращаемого реквизита")
        self.scan.scanned.connect(self._scanned)
        root.addWidget(self.scan)

        tabs = QTabWidget()

        pending_page = QWidget()
        pending_layout = QVBoxLayout(pending_page)
        pending_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        pending_layout.addWidget(muted(
            "Позиции, которые сейчас у клиентов. Приём не пересчитывает уже начисленные "
            "периоды, но останавливает будущие пролонгации."))
        self.pending_table = make_table(
            ["Аренда", "Клиент", "Проект", "Реквизит", "Выдано", "К возврату",
             "Текущий период", "Статус"], 3)
        self.pending_table.doubleClicked.connect(self.accept_return)
        pending_layout.addWidget(self.pending_table)

        actions = QHBoxLayout()
        self.accept_btn = QPushButton("  Принять возврат…")
        self.accept_btn.setObjectName("Primary")
        self.accept_btn.setIcon(icon("return", "white"))
        self.accept_btn.setMinimumHeight(44)
        self.accept_btn.setCursor(Qt.PointingHandCursor)
        self.accept_btn.clicked.connect(self.accept_return)
        actions.addWidget(self.accept_btn)
        open_btn = QPushButton("  Открыть аренду")
        open_btn.setIcon(icon("eye"))
        open_btn.setMinimumHeight(44)
        open_btn.clicked.connect(self._open_selected)
        actions.addWidget(open_btn)
        actions.addStretch()
        pending_layout.addLayout(actions)
        tabs.addTab(pending_page, "У клиентов")

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        self.history_table = make_table(
            ["Дата и время", "Аренда", "Клиент", "Реквизит", "Количество", "Принял",
             "Комментарий"], 3)
        self.history_table.doubleClicked.connect(self._open_history)
        history_layout.addWidget(self.history_table)
        tabs.addTab(history_page, "Принятые возвраты")
        root.addWidget(tabs, 1)

    # ---------------------------------------------------------------- данные
    def reload(self) -> None:
        text = self.search.text().strip()
        like = f"%{text}%"
        params: list = []
        sql = ("SELECT ri.id, ri.rental_id, ri.name_snap, ri.inv_number_snap, ri.quantity, "
               "ri.returned_qty, ri.date_start, ri.period_start, ri.period_end, "
               "ri.prolongations, r.number, c.full_name client, p.name project "
               "FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id "
               "LEFT JOIN clients c ON c.id = r.client_id "
               "LEFT JOIN projects p ON p.id = r.project_id "
               "WHERE r.status='active' AND ri.quantity > ri.returned_qty")
        if text:
            sql += (" AND (r.number LIKE ? OR c.full_name LIKE ? OR p.name LIKE ? "
                    "OR ri.name_snap LIKE ? OR ri.inv_number_snap LIKE ?)")
            params += [like] * 5
        sql += " ORDER BY ifnull(ri.period_end, '9999-12-31'), r.number"
        rows = get_db().query(sql, params)

        self.pending_table.setSortingEnabled(False)
        self.pending_table.setRowCount(0)
        units = 0
        for row in rows:
            index = self.pending_table.rowCount()
            self.pending_table.insertRow(index)
            remaining = row["quantity"] - row["returned_qty"]
            units += remaining
            period = (f"{fmt_date(row['period_start'])}–{fmt_date(row['period_end'])}"
                      if row["period_start"] else "—")
            status = "Пролонгировано" if row["prolongations"] else "В аренде"
            self.pending_table.setItem(index, 0, cell(row["number"], data=row["id"]))
            self.pending_table.setItem(index, 1, cell(row["client"] or "—"))
            self.pending_table.setItem(index, 2, cell(row["project"] or "—"))
            self.pending_table.setItem(index, 3, cell(row["name_snap"]))
            self.pending_table.setItem(index, 4, cell(row["quantity"], True))
            self.pending_table.setItem(index, 5, cell(remaining, True))
            self.pending_table.setItem(index, 6, cell(period))
            self.pending_table.setItem(index, 7, cell(status))
        self.pending_table.setSortingEnabled(True)
        self.pending_table.resizeColumnsToContents()
        self.summary.setText(f"У клиентов: {len(rows)} позиций / {units} шт.")

        history_sql = ("SELECT rr.returned_at, rr.quantity, rr.comment, ri.name_snap, "
                       "r.number, r.id rental_id, c.full_name client, u.full_name user_name "
                       "FROM rental_returns rr "
                       "JOIN rental_items ri ON ri.id = rr.rental_item_id "
                       "JOIN rentals r ON r.id = rr.rental_id "
                       "LEFT JOIN clients c ON c.id = r.client_id "
                       "LEFT JOIN users u ON u.id = rr.user_id WHERE 1=1")
        history_params: list = []
        if text:
            history_sql += (" AND (r.number LIKE ? OR c.full_name LIKE ? OR ri.name_snap LIKE ?)")
            history_params += [like] * 3
        history_sql += " ORDER BY rr.returned_at DESC LIMIT 300"
        self.history_table.setRowCount(0)
        for row in get_db().query(history_sql, history_params):
            index = self.history_table.rowCount()
            self.history_table.insertRow(index)
            self.history_table.setItem(index, 0, cell(fmt_dt(row["returned_at"]),
                                                      data=row["rental_id"]))
            self.history_table.setItem(index, 1, cell(row["number"]))
            self.history_table.setItem(index, 2, cell(row["client"] or "—"))
            self.history_table.setItem(index, 3, cell(row["name_snap"]))
            self.history_table.setItem(index, 4, cell(row["quantity"], True))
            self.history_table.setItem(index, 5, cell(row["user_name"] or "—"))
            self.history_table.setItem(index, 6, cell(row["comment"]))
        self.history_table.resizeColumnsToContents()

    # -------------------------------------------------------------- действия
    def _selected(self) -> tuple[int | None, int | None]:
        row = self.pending_table.currentRow()
        if row < 0:
            return None, None
        rental_item_id = self.pending_table.item(row, 0).data(Qt.UserRole)
        remaining = int(self.pending_table.item(row, 5).text())
        return rental_item_id, remaining

    def _scanned(self, code: str) -> None:
        """
        Находит предмет по штрихкоду и его текущую аренду.

        Ничего не меняется автоматически: программа подсвечивает позицию и
        открывает приём с уже подставленным количеством. Возврат — операция
        с деньгами и сроками, её подтверждает человек.

        Если предмет ни у кого не числится, показывается предупреждение и
        данные не трогаются — это может быть чужая вещь или уже принятая.
        """
        item = barcode_service.find_item(code)
        if not item:
            self.scan.show_result(
                f"Штрихкод «{barcode_service.normalize(code)}» не найден в базе.", "error")
            return

        rows = get_db().query(
            "SELECT ri.id, ri.name_snap, ri.quantity, ri.returned_qty, r.number, "
            "       ifnull(c.full_name,'') AS client "
            "FROM rental_items ri JOIN rentals r ON r.id = ri.rental_id "
            "LEFT JOIN clients c ON c.id = r.client_id "
            "WHERE ri.item_id = ? AND ri.quantity > ri.returned_qty "
            "  AND r.status IN ('active','overdue') "
            "ORDER BY ri.id", (item["id"],))

        if not rows:
            self.scan.show_result(
                f"«{item['name']}» сейчас ни в одной аренде не числится — "
                "возвращать нечего. Проверьте, тот ли это предмет.", "warn")
            return

        target = dict(rows[0])
        if len(rows) > 1:
            options = [f"{r['number']} · {r['client'] or 'без клиента'} · "
                       f"{r['quantity'] - r['returned_qty']} шт." for r in rows]
            choice, ok = QInputDialog.getItem(
                self, "Предмет в нескольких арендах",
                f"«{item['name']}» выдан по нескольким арендам. Какую принимаете?",
                options, 0, False)
            if not ok:
                self.scan.show_result("Приём отменён, данные не изменены.", "info")
                return
            target = dict(rows[options.index(choice)])

        if not self._select_pending(target["id"]):
            self.scan.show_result(
                f"«{item['name']}» найден в аренде {target['number']}, но не виден "
                "в текущем списке — снимите фильтр поиска.", "warn")
            return

        remaining = int(target["quantity"]) - int(target["returned_qty"])
        self.scan.show_result(
            f"«{item['name']}» — аренда {target['number']}, у клиента {remaining} шт. "
            "Подтвердите приём.", "ok")
        self.accept_return()

    def _select_pending(self, rental_item_id: int) -> bool:
        """Подсвечивает строку в таблице «У клиентов». Возвращает, нашлась ли она."""
        for row in range(self.pending_table.rowCount()):
            if self.pending_table.item(row, 0).data(Qt.UserRole) == rental_item_id:
                self.pending_table.selectRow(row)
                self.pending_table.scrollToItem(self.pending_table.item(row, 0))
                return True
        return False

    def accept_return(self) -> None:
        rental_item_id, remaining = self._selected()
        if not rental_item_id:
            show_error(self, AppError("Выберите позицию, которую принимаете обратно."))
            return
        name = self.pending_table.item(self.pending_table.currentRow(), 3).text()
        quantity, ok = QInputDialog.getInt(
            self, "Приём реквизита", f"«{name}» — сколько принимаете обратно?\n"
            f"У клиента: {remaining} шт.", remaining, 1, remaining)
        if not ok:
            return
        comment, _ = QInputDialog.getText(
            self, "Приём реквизита", "Комментарий (состояние, повреждения):")
        try:
            billing_service.return_item(rental_item_id, quantity, comment)
        except AppError as exc:
            show_error(self, exc)
            return
        info(self, f"Принято {quantity} шт. Остатки и статусы обновлены.")
        self.reload()
        self.changed.emit()
        self.scan.take_focus()      # следующий предмет можно сразу сканировать

    def _open_selected(self) -> None:
        row = self.pending_table.currentRow()
        if row < 0:
            return
        rental_item_id = self.pending_table.item(row, 0).data(Qt.UserRole)
        rental = get_db().query_one(
            "SELECT rental_id FROM rental_items WHERE id=?", (rental_item_id,))
        if rental:
            self.open_rental.emit(rental["rental_id"])

    def _open_history(self) -> None:
        row = self.history_table.currentRow()
        if row >= 0:
            self.open_rental.emit(self.history_table.item(row, 0).data(Qt.UserRole))
