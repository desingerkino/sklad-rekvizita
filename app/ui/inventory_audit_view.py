# -*- coding: utf-8 -*-
"""
Раздел «Инвентаризация»: физическая сверка склада со сканером.

Экран устроен под реальную работу: оператор идёт вдоль полок со сканером,
поле ввода всегда в фокусе, после каждого кода видно, что засчитано и сколько
осталось. Мышь нужна только в спорных случаях.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QTextEdit, QVBoxLayout, QWidget)

from .. import paths
from ..errors import AppError
from ..services import inventory_audit_service as audit_module
from ..services.auth_service import auth_service
from ..services.inventory_audit_service import inventory_audit_service
from ..services.inventory_service import inventory_service
from ..utils import fmt_dt
from .errors import confirm, info, show_error
from .theme import Color, Space
from .widgets.common import Card, cell, icon, make_table, muted, section_title
from .widgets.scan_input import ScanInput

FILTERS = [("all", "Все позиции"), ("missing", "Не найдено"),
           ("partial", "Найдено частично"), ("extra", "Излишки"), ("ok", "Совпало")]
STATE_LABELS = {"ok": "совпало", "missing": "не найдено", "partial": "недостача",
                "extra": "излишек"}


class NewAuditDialog(QDialog):
    """Параметры новой инвентаризации."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новая инвентаризация")
        self.setModal(True)
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        root.setSpacing(Space.MD)

        root.addWidget(muted(
            "Программа запомнит, сколько каждой позиции числится на складе прямо "
            "сейчас. Аренды, оформленные во время сверки, на эти числа не повлияют."))

        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText("например: годовая сверка")
        self.title.setMinimumHeight(38)
        user = auth_service.current_user
        self.responsible = QLineEdit(user.full_name if user else "")
        self.responsible.setMinimumHeight(38)
        self.comment = QTextEdit()
        self.comment.setMaximumHeight(80)
        form.addRow("Название:", self.title)
        form.addRow("Ответственный:", self.responsible)
        form.addRow("Комментарий:", self.comment)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Начать инвентаризацию")
        buttons.button(QDialogButtonBox.Ok).setObjectName("Primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> dict:
        return {"title": self.title.text().strip(),
                "responsible": self.responsible.text().strip(),
                "comment": self.comment.toPlainText().strip()}


class InventoryAuditView(QWidget):
    """Список инвентаризаций и рабочий экран текущей сверки."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.audit_id: int | None = None
        self._build()
        self.reload()

    # ------------------------------------------------------------ интерфейс
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        head = QHBoxLayout()
        head.addWidget(section_title("Инвентаризация"))
        head.addStretch()
        self.audits_box = QComboBox()
        self.audits_box.setMinimumWidth(320)
        self.audits_box.setMinimumHeight(38)
        self.audits_box.currentIndexChanged.connect(self._audit_selected)
        head.addWidget(self.audits_box)
        self.new_btn = QPushButton("  Новая инвентаризация")
        self.new_btn.setObjectName("Primary")
        self.new_btn.setIcon(icon("plus", "white"))
        self.new_btn.setIconSize(QSize(18, 18))
        self.new_btn.setMinimumHeight(40)
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.clicked.connect(self._create)
        head.addWidget(self.new_btn)
        root.addLayout(head)

        self.header = muted("")
        root.addWidget(self.header)

        self.scan = ScanInput("Отсканируйте штрихкод предмета — он будет отмечен как найденный")
        self.scan.scanned.connect(self._scanned)
        root.addWidget(self.scan)

        # --------------------------------------------------------- сводка
        summary_card = Card(padding=Space.MD)
        summary_row = QHBoxLayout()
        summary_row.setSpacing(Space.XL)
        self.stat_labels: dict[str, QLabel] = {}
        for key, caption in (("expected", "Числится"), ("found", "Найдено"),
                             ("missing", "Не найдено"), ("extra", "Излишки"),
                             ("percent", "Соответствие")):
            column = QVBoxLayout()
            caption_label = QLabel(caption)
            caption_label.setObjectName("Muted")
            value = QLabel("—")
            value.setObjectName("StatTileValue")
            column.addWidget(caption_label)
            column.addWidget(value)
            self.stat_labels[key] = value
            summary_row.addLayout(column)
        summary_row.addStretch()
        summary_card.body.addLayout(summary_row)
        root.addWidget(summary_card)

        # --------------------------------------------------------- таблица
        filter_row = QHBoxLayout()
        self.filter_box = QComboBox()
        for key, label in FILTERS:
            self.filter_box.addItem(label, key)
        self.filter_box.setMinimumHeight(36)
        self.filter_box.currentIndexChanged.connect(self._reload_lines)
        filter_row.addWidget(self.filter_box)
        filter_row.addStretch()

        self.manual_btn = QPushButton("  Добавить вручную")
        self.manual_btn.setIcon(icon("plus"))
        self.manual_btn.setToolTip("Если этикетка повреждена и сканер не читает код")
        self.manual_btn.setMinimumHeight(36)
        self.manual_btn.clicked.connect(self._add_manually)
        filter_row.addWidget(self.manual_btn)

        self.edit_btn = QPushButton("  Исправить количество")
        self.edit_btn.setIcon(icon("edit"))
        self.edit_btn.setMinimumHeight(36)
        self.edit_btn.clicked.connect(self._edit_line)
        filter_row.addWidget(self.edit_btn)
        root.addLayout(filter_row)

        self.table = make_table(
            ["Инв. №", "Наименование", "Числится", "Найдено", "Расхождение",
             "У клиентов", "В ремонте", "Состояние", "Комментарий"], 1)
        self.table.doubleClicked.connect(self._edit_line)
        root.addWidget(self.table, 1)

        # --------------------------------------------------------- действия
        actions = QHBoxLayout()
        self.finish_btn = QPushButton("  Завершить инвентаризацию")
        self.finish_btn.setObjectName("Primary")
        self.finish_btn.setIcon(icon("check", "white"))
        self.finish_btn.setIconSize(QSize(18, 18))
        self.finish_btn.setMinimumHeight(44)
        self.finish_btn.clicked.connect(self._finish)
        actions.addWidget(self.finish_btn)

        self.pdf_btn = QPushButton("  Отчёт PDF")
        self.pdf_btn.setIcon(icon("printer"))
        self.pdf_btn.setIconSize(QSize(18, 18))
        self.pdf_btn.setMinimumHeight(44)
        self.pdf_btn.clicked.connect(self._report)
        actions.addWidget(self.pdf_btn)

        self.excel_btn = QPushButton("  Выгрузить в Excel")
        self.excel_btn.setIcon(icon("file"))
        self.excel_btn.setIconSize(QSize(18, 18))
        self.excel_btn.setMinimumHeight(44)
        self.excel_btn.setToolTip("Итоги и построчная сверка двумя листами")
        self.excel_btn.clicked.connect(self._export_excel)
        actions.addWidget(self.excel_btn)

        self.cancel_btn = QPushButton("  Отменить инвентаризацию")
        self.cancel_btn.setObjectName("Ghost")
        self.cancel_btn.setMinimumHeight(44)
        self.cancel_btn.clicked.connect(self._cancel)
        actions.addWidget(self.cancel_btn)
        actions.addStretch()
        root.addLayout(actions)

    # --------------------------------------------------------------- данные
    def reload(self) -> None:
        """Перечитывает список инвентаризаций, оставляя выбранной текущую."""
        previous = self.audit_id
        self.audits_box.blockSignals(True)
        self.audits_box.clear()
        audits = inventory_audit_service.list()
        for audit in audits:
            label = (f"{audit['number']} · {audit['status_label']}"
                     f"{' · ' + audit['title'] if audit['title'] else ''}")
            self.audits_box.addItem(label, audit["id"])
        self.audits_box.blockSignals(False)

        if not audits:
            self.audit_id = None
            self._render_empty()
            return
        target = previous if previous in [a["id"] for a in audits] else None
        if target is None:
            active = inventory_audit_service.active()
            target = active["id"] if active else audits[0]["id"]
        index = self.audits_box.findData(target)
        self.audits_box.setCurrentIndex(max(0, index))
        self.audit_id = self.audits_box.currentData()
        self._render()

    def _audit_selected(self) -> None:
        self.audit_id = self.audits_box.currentData()
        self._render()

    def _render_empty(self) -> None:
        self.header.setText(
            "Инвентаризаций ещё не было. Нажмите «Новая инвентаризация», чтобы "
            "зафиксировать текущий учёт и начать пересчёт склада.")
        self.table.setRowCount(0)
        for label in self.stat_labels.values():
            label.setText("—")
        for widget in (self.scan, self.finish_btn, self.pdf_btn, self.cancel_btn,
                       self.manual_btn, self.edit_btn, self.filter_box):
            widget.setEnabled(False)

    def _render(self) -> None:
        if not self.audit_id:
            self._render_empty()
            return
        try:
            audit = inventory_audit_service.get(self.audit_id)
        except AppError as exc:
            show_error(self, exc)
            return

        is_open = audit["status"] == audit_module.STATUS_OPEN
        is_admin = auth_service.is_admin()
        self.scan.setEnabled(is_open)
        self.manual_btn.setEnabled(is_open)
        self.edit_btn.setEnabled(is_open)
        self.filter_box.setEnabled(True)
        self.pdf_btn.setEnabled(True)
        self.excel_btn.setEnabled(True)
        self.finish_btn.setEnabled(is_open and is_admin)
        self.cancel_btn.setEnabled(is_open and is_admin)
        self.finish_btn.setToolTip(
            "" if is_admin else "Завершать инвентаризацию может только администратор")

        started = fmt_dt(audit["started_at"])
        finished = fmt_dt(audit["finished_at"]) if audit["finished_at"] else ""
        parts = [f"{audit['number']} — {audit['status_label'].lower()}",
                 f"начата {started}"]
        if finished:
            parts.append(f"завершена {finished}")
        parts.append(f"ответственный: {audit['responsible'] or audit.get('user_name') or '—'}")
        if audit["comment"]:
            parts.append(audit["comment"])
        self.header.setText("   ·   ".join(parts))

        self._reload_lines()
        if is_open:
            self.scan.take_focus()

    def _reload_lines(self) -> None:
        if not self.audit_id:
            return
        only = self.filter_box.currentData() or "all"
        lines = inventory_audit_service.lines(self.audit_id, only)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for line in lines:
            row = self.table.rowCount()
            self.table.insertRow(row)
            diff = line["diff"]
            self.table.setItem(row, 0, cell(line["inv_number"], data=line["id"]))
            self.table.setItem(row, 1, cell(line["name_snap"]))
            self.table.setItem(row, 2, cell(line["expected_qty"], True))
            self.table.setItem(row, 3, cell(line["found_qty"], True))
            self.table.setItem(row, 4, cell(f"{diff:+d}" if diff else "0", True))
            self.table.setItem(row, 5, cell(line["rented_qty"], True))
            self.table.setItem(row, 6, cell(line["repair_qty"], True))
            self.table.setItem(row, 7, cell(STATE_LABELS.get(line["state"], line["state"])))
            self.table.setItem(row, 8, cell(line["comment"]))
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()

        summary = inventory_audit_service.summary(self.audit_id)
        self.stat_labels["expected"].setText(str(summary["expected_units"]))
        self.stat_labels["found"].setText(str(summary["found_units"]))
        self.stat_labels["missing"].setText(str(summary["missing_units"]))
        self.stat_labels["extra"].setText(str(summary["extra_units"]))
        self.stat_labels["percent"].setText(f"{summary['match_percent']} %")
        for key, alert in (("missing", "true" if summary["missing_units"] else "false"),
                           ("extra", "warn" if summary["extra_units"] else "false")):
            label = self.stat_labels[key]
            label.setProperty("alert", alert)       # цвет задаёт theme.py
            label.style().unpolish(label)
            label.style().polish(label)

    # ------------------------------------------------------------- действия
    def _create(self) -> None:
        dialog = NewAuditDialog(self)
        if not dialog.exec():
            return
        try:
            audit_id = inventory_audit_service.create(**dialog.values())
        except AppError as exc:
            show_error(self, exc)
            return
        self.audit_id = audit_id
        self.reload()
        self.changed.emit()

    def _scanned(self, code: str) -> None:
        if not self.audit_id:
            return
        try:
            result = inventory_audit_service.scan(self.audit_id, code)
        except AppError as exc:
            self.scan.show_result(str(exc), "error")
            return

        status = result["status"]
        if status == audit_module.SCAN_COUNTED:
            self.scan.show_result(result["message"], "ok")
        elif status == audit_module.SCAN_UNEXPECTED:
            self.scan.show_result(result["message"], "warn")
        elif status == audit_module.SCAN_NOT_FOUND:
            self.scan.show_result(result["message"], "error")
            self._reload_lines()
            return
        elif status == audit_module.SCAN_OVER:
            # найдено больше, чем числится: без подтверждения ничего не пишем
            if confirm(self, result["message"] + "\n\nЗасчитать этот экземпляр?",
                       "Больше, чем числится"):
                try:
                    confirmed = inventory_audit_service.scan(
                        self.audit_id, code, allow_over=True)
                    self.scan.show_result(confirmed["message"], "warn")
                except AppError as exc:
                    self.scan.show_result(str(exc), "error")
            else:
                self.scan.show_result("Сканирование отменено, данные не изменены.", "info")
            self.scan.take_focus()
        else:
            self.scan.show_result(result.get("message", "Неизвестный ответ."), "warn")
        self._reload_lines()

    def _selected_line(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        line_id = self.table.item(row, 0).data(Qt.UserRole)
        for line in inventory_audit_service.lines(self.audit_id):
            if line["id"] == line_id:
                return line
        return None

    def _add_manually(self) -> None:
        if not self.audit_id:
            return
        text, ok = QInputDialog.getText(
            self, "Добавить вручную",
            "Название или инвентарный номер предмета:")
        if not ok or not text.strip():
            return
        items = inventory_service.search(text.strip(), limit=30)
        if not items:
            info(self, "Ничего не найдено.", "Поиск")
            return
        if len(items) == 1:
            chosen = items[0]
        else:
            options = [f"{i['inv_number']} · {i['name']}" for i in items]
            picked, ok = QInputDialog.getItem(self, "Выберите предмет", "Найдено:",
                                              options, 0, False)
            if not ok:
                return
            chosen = items[options.index(picked)]
        quantity, ok = QInputDialog.getInt(
            self, "Добавить вручную", f"«{chosen['name']}» — сколько нашли?", 1, 1, 100000)
        if not ok:
            return
        try:
            result = inventory_audit_service.add_manually(self.audit_id, chosen["id"], quantity)
        except AppError as exc:
            show_error(self, exc)
            return
        self.scan.show_result(result["message"], "ok")
        self._reload_lines()

    def _edit_line(self) -> None:
        line = self._selected_line()
        if not line:
            info(self, "Выберите строку в таблице.", "Ничего не выбрано")
            return
        quantity, ok = QInputDialog.getInt(
            self, "Исправить количество",
            f"«{line['name_snap']}»\nЧислится: {line['expected_qty']} шт.\n"
            "Сколько найдено на самом деле?",
            int(line["found_qty"]), 0, 100000)
        if not ok:
            return
        comment, ok = QInputDialog.getText(
            self, "Исправить количество", "Комментарий (необязательно):",
            text=line["comment"])
        if not ok:
            comment = line["comment"]
        try:
            inventory_audit_service.set_found(self.audit_id, line["id"], quantity, comment)
        except AppError as exc:
            show_error(self, exc)
            return
        self._reload_lines()

    def _finish(self) -> None:
        if not self.audit_id:
            return
        summary = inventory_audit_service.summary(self.audit_id)
        if not confirm(
                self,
                f"Числится {summary['expected_units']} шт., найдено "
                f"{summary['found_units']} шт.\n"
                f"Недостача: {summary['missing_units']} шт., излишки: "
                f"{summary['extra_units']} шт.\n\n"
                "Завершить инвентаризацию? Остатки склада при этом не меняются — "
                "программа только зафиксирует расхождения.",
                "Завершение инвентаризации"):
            return
        try:
            inventory_audit_service.finish(self.audit_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload()
        self.changed.emit()
        info(self, "Инвентаризация завершена. Отчёт можно выгрузить в PDF.")

    def _cancel(self) -> None:
        if not self.audit_id:
            return
        if not confirm(self, "Отменить инвентаризацию? Результаты пересчёта сохранятся "
                             "в истории, но сверка будет считаться несостоявшейся.",
                       "Отмена инвентаризации"):
            return
        try:
            inventory_audit_service.cancel(self.audit_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload()
        self.changed.emit()

    def _export_excel(self) -> None:
        if not self.audit_id:
            return
        from ..services.excel_service import excel_service
        try:
            audit = inventory_audit_service.get(self.audit_id)
            target = paths.DOCS_DIR / f"Инвентаризация {audit['number']}.xlsx"
            excel_service.export_audit(target, self.audit_id)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Выгрузить в Excel не удалось.")
            return
        from .settings_view import open_folder
        info(self, f"Файл сохранён:\n{target}")
        open_folder(target.parent)

    def _report(self) -> None:
        if not self.audit_id:
            return
        try:
            audit = inventory_audit_service.get(self.audit_id)
            target = paths.DOCS_DIR / f"Инвентаризация {audit['number']}.pdf"
            inventory_audit_service.report_pdf(self.audit_id, target)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Сформировать отчёт не удалось.")
            return
        from .settings_view import open_folder
        info(self, f"Отчёт сохранён:\n{target}")
        open_folder(target.parent)
