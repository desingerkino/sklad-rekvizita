# -*- coding: utf-8 -*-
"""
Раздел «Машины / Комплектация»: упаковочные листы для съёмочной группы.

Слева — комплекты (машина + дата + проект), справа — коробки и их содержимое.
Предмет кладётся в коробку сканером или поиском; склад при этом не меняется —
упаковка отвечает на вопрос «где это лежит физически», а не «кому выдано».
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QPushButton,
                               QSplitter, QTextEdit, QVBoxLayout, QWidget)

from .. import paths
from ..errors import AppError
from ..services import packing_service as packing_module
from ..services.auth_service import auth_service
from ..services.excel_service import excel_service
from ..services.inventory_service import inventory_service
from ..services.packing_service import packing_service
from ..services.productions_service import productions_service
from ..services.projects_service import projects_service
from ..utils import fmt_date
from .errors import confirm, info, show_error
from .theme import Space
from .widgets.common import Card, DatePicker, cell, icon, make_table, muted, section_title
from .widgets.scan_input import ScanInput


class VehicleDialog(QDialog):
    """Машина: название, водитель, примечание."""

    def __init__(self, parent=None, data: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Машина")
        self.setModal(True)
        self.setMinimumWidth(420)
        data = data or {}
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        form = QFormLayout()
        self.name = QLineEdit(data.get("name", ""))
        self.name.setPlaceholderText("например: Машина №2 или Газель реквизита")
        self.name.setMinimumHeight(38)
        self.driver = QLineEdit(data.get("driver", ""))
        self.driver.setMinimumHeight(38)
        self.note = QLineEdit(data.get("note", ""))
        self.note.setMinimumHeight(38)
        form.addRow("Название:", self.name)
        form.addRow("Водитель:", self.driver)
        form.addRow("Примечание:", self.note)
        root.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Сохранить")
        buttons.button(QDialogButtonBox.Ok).setObjectName("Primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> dict:
        return {"name": self.name.text().strip(), "driver": self.driver.text().strip(),
                "note": self.note.text().strip()}


class PackingSetDialog(QDialog):
    """Новый комплект: машина, дата выезда, проект, ответственный."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый комплект")
        self.setModal(True)
        self.setMinimumWidth(480)
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        root.setSpacing(Space.MD)
        root.addWidget(muted(
            "Комплект — это то, что уезжает одной машиной. Склад он не меняет: "
            "предметы остаются числиться там же, где были."))

        form = QFormLayout()
        self.vehicle = QComboBox()
        self.vehicle.addItem("— не выбрана —", None)
        for vehicle in packing_service.vehicles():
            label = vehicle["name"] + (f" · {vehicle['driver']}" if vehicle["driver"] else "")
            self.vehicle.addItem(label, vehicle["id"])
        self.vehicle.setMinimumHeight(38)

        self.date = DatePicker()
        self.project = QComboBox()
        self.project.addItem("— без проекта —", None)
        for project in projects_service.list():
            self.project.addItem(project["name"], project["id"])
        self.project.setMinimumHeight(38)

        self.production = QComboBox()
        self.production.addItem("— без продакшена —", None)
        for production in productions_service.list():
            self.production.addItem(production["name"], production["id"])
        self.production.setMinimumHeight(38)

        user = auth_service.current_user
        self.responsible = QLineEdit(user.full_name if user else "")
        self.responsible.setMinimumHeight(38)
        self.comment = QTextEdit()
        self.comment.setMaximumHeight(70)

        form.addRow("Машина:", self.vehicle)
        form.addRow("Дата выезда:", self.date)
        form.addRow("Проект:", self.project)
        form.addRow("Продакшен:", self.production)
        form.addRow("Ответственный:", self.responsible)
        form.addRow("Комментарий:", self.comment)
        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Создать комплект")
        buttons.button(QDialogButtonBox.Ok).setObjectName("Primary")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> dict:
        return {"vehicle_id": self.vehicle.currentData(),
                "date_ship": self.date.date(),
                "project_id": self.project.currentData(),
                "production_id": self.production.currentData(),
                "responsible": self.responsible.text().strip(),
                "comment": self.comment.toPlainText().strip()}


class PackingView(QWidget):
    """Комплекты, коробки и упаковочные листы."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.set_id: int | None = None
        self.box_id: int | None = None
        self._build()
        self.reload()

    # ------------------------------------------------------------ интерфейс
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        head = QHBoxLayout()
        head.addWidget(section_title("Машины и комплектация"))
        head.addStretch()
        self.vehicles_btn = QPushButton("  Машины")
        self.vehicles_btn.setIcon(icon("box"))
        self.vehicles_btn.setMinimumHeight(40)
        self.vehicles_btn.clicked.connect(self._manage_vehicles)
        head.addWidget(self.vehicles_btn)
        self.new_btn = QPushButton("  Новый комплект")
        self.new_btn.setObjectName("Primary")
        self.new_btn.setIcon(icon("plus", "white"))
        self.new_btn.setIconSize(QSize(18, 18))
        self.new_btn.setMinimumHeight(40)
        self.new_btn.setCursor(Qt.PointingHandCursor)
        self.new_btn.clicked.connect(self._create_set)
        head.addWidget(self.new_btn)
        root.addLayout(head)

        splitter = QSplitter(Qt.Horizontal)

        # ------------------------------------------------ слева: комплекты
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, Space.MD, 0)
        left_layout.addWidget(muted("Комплекты"))
        self.sets_table = make_table(
            ["Номер", "Машина", "Дата", "Проект", "Коробок", "Предметов", "Состояние"], 1)
        self.sets_table.itemSelectionChanged.connect(self._set_selected)
        left_layout.addWidget(self.sets_table, 1)

        set_actions = QHBoxLayout()
        self.status_box = QComboBox()
        for key, label in packing_module.STATUS_LABELS.items():
            self.status_box.addItem(label, key)
        self.status_box.setMinimumHeight(36)
        self.status_box.setToolTip("Состояние выбранного комплекта")
        self.status_box.activated.connect(self._change_status)
        set_actions.addWidget(self.status_box)
        self.delete_set_btn = QPushButton("Удалить")
        self.delete_set_btn.setObjectName("Ghost")
        self.delete_set_btn.setMinimumHeight(36)
        self.delete_set_btn.clicked.connect(self._delete_set)
        set_actions.addWidget(self.delete_set_btn)
        left_layout.addLayout(set_actions)
        splitter.addWidget(left)

        # --------------------------------------- справа: коробки и предметы
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(Space.MD, 0, 0, 0)
        right_layout.setSpacing(Space.MD)

        self.header = muted("Выберите комплект слева или создайте новый.")
        right_layout.addWidget(self.header)

        box_row = QHBoxLayout()
        box_row.addWidget(QLabel("Коробка:"))
        self.boxes_box = QComboBox()
        self.boxes_box.setMinimumHeight(38)
        self.boxes_box.setMinimumWidth(220)
        self.boxes_box.currentIndexChanged.connect(self._box_selected)
        box_row.addWidget(self.boxes_box, 1)
        self.add_box_btn = QPushButton("  Добавить коробку")
        self.add_box_btn.setIcon(icon("plus"))
        self.add_box_btn.setMinimumHeight(38)
        self.add_box_btn.clicked.connect(self._add_box)
        box_row.addWidget(self.add_box_btn)
        self.rename_box_btn = QPushButton("Переименовать")
        self.rename_box_btn.setMinimumHeight(38)
        self.rename_box_btn.clicked.connect(self._rename_box)
        box_row.addWidget(self.rename_box_btn)
        self.delete_box_btn = QPushButton("Удалить коробку")
        self.delete_box_btn.setObjectName("Ghost")
        self.delete_box_btn.setMinimumHeight(38)
        self.delete_box_btn.clicked.connect(self._delete_box)
        box_row.addWidget(self.delete_box_btn)
        right_layout.addLayout(box_row)

        self.scan = ScanInput("Сканируйте штрихкод — предмет попадёт в выбранную коробку")
        self.scan.scanned.connect(self._scanned)
        right_layout.addWidget(self.scan)

        self.items_table = make_table(
            ["Инв. №", "Наименование", "Количество", "Примечание"], 1)
        right_layout.addWidget(self.items_table, 1)

        item_actions = QHBoxLayout()
        self.add_item_btn = QPushButton("  Добавить поиском")
        self.add_item_btn.setIcon(icon("search"))
        self.add_item_btn.setMinimumHeight(40)
        self.add_item_btn.setToolTip("Если этикетка повреждена или предмета нет под рукой")
        self.add_item_btn.clicked.connect(self._add_item_manually)
        item_actions.addWidget(self.add_item_btn)
        self.edit_item_btn = QPushButton("Изменить количество")
        self.edit_item_btn.setMinimumHeight(40)
        self.edit_item_btn.clicked.connect(self._edit_item)
        item_actions.addWidget(self.edit_item_btn)
        self.remove_item_btn = QPushButton("Убрать из коробки")
        self.remove_item_btn.setObjectName("Ghost")
        self.remove_item_btn.setMinimumHeight(40)
        self.remove_item_btn.clicked.connect(self._remove_item)
        item_actions.addWidget(self.remove_item_btn)
        item_actions.addStretch()
        right_layout.addLayout(item_actions)

        export_row = QHBoxLayout()
        self.pdf_btn = QPushButton("  Упаковочный лист")
        self.pdf_btn.setObjectName("Primary")
        self.pdf_btn.setIcon(icon("printer", "white"))
        self.pdf_btn.setIconSize(QSize(18, 18))
        self.pdf_btn.setMinimumHeight(44)
        self.pdf_btn.clicked.connect(self._packing_list)
        export_row.addWidget(self.pdf_btn)
        self.excel_btn = QPushButton("  Выгрузить в Excel")
        self.excel_btn.setIcon(icon("file"))
        self.excel_btn.setIconSize(QSize(18, 18))
        self.excel_btn.setMinimumHeight(44)
        self.excel_btn.clicked.connect(self._export_excel)
        export_row.addWidget(self.excel_btn)
        export_row.addStretch()
        right_layout.addLayout(export_row)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)
        root.addWidget(splitter, 1)

    # --------------------------------------------------------------- данные
    def reload(self) -> None:
        previous = self.set_id
        self.sets_table.setSortingEnabled(False)
        self.sets_table.setRowCount(0)
        sets = packing_service.sets()
        for data in sets:
            row = self.sets_table.rowCount()
            self.sets_table.insertRow(row)
            self.sets_table.setItem(row, 0, cell(data["number"], data=data["id"]))
            self.sets_table.setItem(row, 1, cell(data["vehicle_name"] or "—"))
            self.sets_table.setItem(row, 2, cell(
                fmt_date(data["date_ship"]) if data["date_ship"] else "—"))
            self.sets_table.setItem(row, 3, cell(data["project_name"] or "—"))
            self.sets_table.setItem(row, 4, cell(data["boxes"], True))
            self.sets_table.setItem(row, 5, cell(data["units"], True))
            self.sets_table.setItem(row, 6, cell(data["status_label"]))
        self.sets_table.setSortingEnabled(True)
        self.sets_table.resizeColumnsToContents()

        if not sets:
            self.set_id = None
            self._render_set()
            return
        target = previous if any(s["id"] == previous for s in sets) else sets[0]["id"]
        for row in range(self.sets_table.rowCount()):
            if self.sets_table.item(row, 0).data(Qt.UserRole) == target:
                self.sets_table.selectRow(row)
                break

    def _set_selected(self) -> None:
        row = self.sets_table.currentRow()
        self.set_id = (self.sets_table.item(row, 0).data(Qt.UserRole)
                       if row >= 0 else None)
        self._render_set()

    def _render_set(self) -> None:
        has_set = self.set_id is not None
        for widget in (self.add_box_btn, self.rename_box_btn, self.delete_box_btn,
                       self.scan, self.add_item_btn, self.edit_item_btn,
                       self.remove_item_btn, self.pdf_btn, self.excel_btn,
                       self.status_box, self.delete_set_btn, self.boxes_box):
            widget.setEnabled(has_set)
        if not has_set:
            self.header.setText("Комплектов пока нет. Нажмите «Новый комплект».")
            self.boxes_box.clear()
            self.items_table.setRowCount(0)
            return

        try:
            data = packing_service.get(self.set_id)
        except AppError as exc:
            show_error(self, exc)
            return
        parts = [f"{data['number']} — {data['status_label'].lower()}",
                 data["vehicle_name"] or "машина не указана"]
        if data["vehicle_driver"]:
            parts.append(f"водитель: {data['vehicle_driver']}")
        if data["date_ship"]:
            parts.append(f"выезд {fmt_date(data['date_ship'])}")
        if data["project_name"]:
            parts.append(data["project_name"])
        if data["responsible"]:
            parts.append(f"собирает: {data['responsible']}")
        if data["comment"]:
            parts.append(data["comment"])
        self.header.setText("   ·   ".join(parts))

        index = self.status_box.findData(data["status"])
        if index >= 0:
            self.status_box.blockSignals(True)
            self.status_box.setCurrentIndex(index)
            self.status_box.blockSignals(False)

        self._reload_boxes()

    def _reload_boxes(self) -> None:
        previous = self.box_id
        self.boxes_box.blockSignals(True)
        self.boxes_box.clear()
        boxes = packing_service.boxes(self.set_id) if self.set_id else []
        for box in boxes:
            self.boxes_box.addItem(f"{box['label']} · {box['units']} шт.", box["id"])
        self.boxes_box.blockSignals(False)
        if not boxes:
            self.box_id = None
            self.items_table.setRowCount(0)
            self.scan.show_result("Добавьте коробку, прежде чем складывать предметы.", "info")
            return
        target = previous if any(b["id"] == previous for b in boxes) else boxes[0]["id"]
        self.boxes_box.setCurrentIndex(max(0, self.boxes_box.findData(target)))
        self.box_id = self.boxes_box.currentData()
        self._reload_items()

    def _box_selected(self) -> None:
        self.box_id = self.boxes_box.currentData()
        self._reload_items()

    def _reload_items(self) -> None:
        self.items_table.setRowCount(0)
        if not self.box_id:
            return
        for item in packing_service.items(self.box_id):
            row = self.items_table.rowCount()
            self.items_table.insertRow(row)
            self.items_table.setItem(row, 0, cell(item["inv_number"], data=item["id"]))
            self.items_table.setItem(row, 1, cell(item["name_snap"]))
            self.items_table.setItem(row, 2, cell(item["quantity"], True))
            self.items_table.setItem(row, 3, cell(item["note"]))
        self.items_table.resizeColumnsToContents()

    # ------------------------------------------------------------- действия
    def _create_set(self) -> None:
        dialog = PackingSetDialog(self)
        if not dialog.exec():
            return
        try:
            self.set_id = packing_service.create_set(**dialog.values())
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload()
        self.changed.emit()

    def _change_status(self) -> None:
        if not self.set_id:
            return
        try:
            packing_service.set_status(self.set_id, self.status_box.currentData())
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload()

    def _delete_set(self) -> None:
        if not self.set_id:
            return
        data = packing_service.get(self.set_id)
        if not confirm(self, f"Удалить комплект {data['number']} вместе с коробками "
                             "и их содержимым?\n\nПредметы останутся на складе — "
                             "удаляется только упаковочный лист.",
                       "Удаление комплекта"):
            return
        try:
            packing_service.delete_set(self.set_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self.set_id = None
        self.reload()
        self.changed.emit()

    def _add_box(self) -> None:
        if not self.set_id:
            return
        label, ok = QInputDialog.getText(
            self, "Новая коробка", "Название (можно оставить пустым):")
        if not ok:
            return
        try:
            self.box_id = packing_service.add_box(self.set_id, label.strip())
        except AppError as exc:
            show_error(self, exc)
            return
        self._reload_boxes()
        self.scan.take_focus()

    def _rename_box(self) -> None:
        if not self.box_id:
            return
        current = self.boxes_box.currentText().split(" · ")[0]
        label, ok = QInputDialog.getText(self, "Название коробки", "Название:", text=current)
        if not ok or not label.strip():
            return
        note, ok = QInputDialog.getText(self, "Название коробки", "Примечание:")
        try:
            packing_service.update_box(self.box_id, label.strip(), note if ok else "")
        except AppError as exc:
            show_error(self, exc)
            return
        self._reload_boxes()

    def _delete_box(self) -> None:
        if not self.box_id:
            return
        if not confirm(self, "Удалить коробку вместе с её содержимым?\n\n"
                             "Предметы останутся на складе.", "Удаление коробки"):
            return
        try:
            packing_service.delete_box(self.box_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self.box_id = None
        self._reload_boxes()

    def _scanned(self, code: str) -> None:
        if not self.box_id:
            self.scan.show_result("Сначала выберите или создайте коробку.", "warn")
            return
        try:
            result = packing_service.scan_into_box(self.box_id, code)
        except AppError as exc:
            self.scan.show_result(str(exc), "error")
            return
        self.scan.show_result(result["message"], "ok" if result["found"] else "error")
        if result["found"]:
            self._reload_boxes()

    def _add_item_manually(self) -> None:
        if not self.box_id:
            info(self, "Сначала выберите или создайте коробку.", "Коробка не выбрана")
            return
        text, ok = QInputDialog.getText(
            self, "Добавить предмет", "Название или инвентарный номер:")
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
            self, "Добавить предмет", f"«{chosen['name']}» — сколько кладём?", 1, 1, 100000)
        if not ok:
            return
        try:
            result = packing_service.add_item(self.box_id, chosen["id"], quantity)
        except AppError as exc:
            show_error(self, exc)
            return
        self.scan.show_result(f"«{result['name']}» — в коробке {result['quantity']} шт.", "ok")
        self._reload_boxes()

    def _selected_item(self) -> tuple[int | None, str, int]:
        row = self.items_table.currentRow()
        if row < 0:
            return None, "", 0
        return (self.items_table.item(row, 0).data(Qt.UserRole),
                self.items_table.item(row, 1).text(),
                int(self.items_table.item(row, 2).text()))

    def _edit_item(self) -> None:
        packing_item_id, name, quantity = self._selected_item()
        if not packing_item_id:
            info(self, "Выберите строку в списке.", "Ничего не выбрано")
            return
        value, ok = QInputDialog.getInt(
            self, "Количество", f"«{name}» — сколько в коробке?", quantity, 0, 100000)
        if not ok:
            return
        try:
            packing_service.set_item_quantity(packing_item_id, value)
        except AppError as exc:
            show_error(self, exc)
            return
        self._reload_boxes()

    def _remove_item(self) -> None:
        packing_item_id, name, _ = self._selected_item()
        if not packing_item_id:
            info(self, "Выберите строку в списке.", "Ничего не выбрано")
            return
        if not confirm(self, f"Убрать «{name}» из коробки?\n\n"
                             "На складе предмет останется.", "Убрать из коробки"):
            return
        try:
            packing_service.remove_item(packing_item_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self._reload_boxes()

    # --------------------------------------------------------------- вывод
    def _packing_list(self) -> None:
        if not self.set_id:
            return
        try:
            data = packing_service.get(self.set_id)
            target = paths.DOCS_DIR / f"Упаковочный лист {data['number']}.pdf"
            packing_service.packing_list_pdf(self.set_id, target)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Сформировать упаковочный лист не удалось.")
            return
        from .settings_view import open_folder
        info(self, f"Упаковочный лист сохранён:\n{target}")
        open_folder(target.parent)

    def _export_excel(self) -> None:
        if not self.set_id:
            return
        try:
            data = packing_service.get(self.set_id)
            target = paths.DOCS_DIR / f"Комплект {data['number']}.xlsx"
            excel_service.export_packing(target, self.set_id)
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Выгрузить в Excel не удалось.")
            return
        from .settings_view import open_folder
        info(self, f"Файл сохранён:\n{target}")
        open_folder(target.parent)

    # --------------------------------------------------------------- машины
    def _manage_vehicles(self) -> None:
        """Простое управление списком машин через последовательные диалоги."""
        vehicles = packing_service.vehicles(only_active=False)
        options = ["+ Добавить машину"] + [
            f"{v['name']}{' · ' + v['driver'] if v['driver'] else ''}"
            f"{'' if v['is_active'] else '  (скрыта)'}" for v in vehicles]
        choice, ok = QInputDialog.getItem(self, "Машины", "Выберите машину:",
                                          options, 0, False)
        if not ok:
            return
        if choice == options[0]:
            dialog = VehicleDialog(self)
            if not dialog.exec():
                return
            try:
                packing_service.create_vehicle(**dialog.values())
            except AppError as exc:
                show_error(self, exc)
            return

        vehicle = vehicles[options.index(choice) - 1]
        action, ok = QInputDialog.getItem(
            self, vehicle["name"], "Что сделать?",
            ["Изменить", "Убрать из списка"], 0, False)
        if not ok:
            return
        if action == "Изменить":
            dialog = VehicleDialog(self, vehicle)
            if not dialog.exec():
                return
            try:
                packing_service.update_vehicle(vehicle["id"], **dialog.values())
            except AppError as exc:
                show_error(self, exc)
            return
        if not confirm(self, f"Убрать «{vehicle['name']}» из списка машин?\n\n"
                             "Если по ней уже есть комплекты, она сохранится в "
                             "истории и упаковочные листы останутся читаемыми.",
                       "Удаление машины"):
            return
        try:
            packing_service.delete_vehicle(vehicle["id"])
        except AppError as exc:
            show_error(self, exc)
            return
        self.reload()
