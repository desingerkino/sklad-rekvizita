# -*- coding: utf-8 -*-
"""
Карточка реквизита версии 2.1.

Раскладка повторяет референс: крупное фото с лентой миниатюр слева, блок
сведений по центру, колонка ракурсов справа, строка действий и вкладки
с текущими арендами, историей и ремонтом. Логика не менялась.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel,
                               QPushButton, QScrollArea, QSizePolicy, QTabWidget,
                               QVBoxLayout, QWidget)

from ..errors import AppError
from ..services.inventory_service import inventory_service
from ..services.photo_service import PHOTO_KINDS as SERVICE_PHOTO_KINDS, photo_service
from ..services.repairs_service import repairs_service
from ..utils import fmt_date, money
from ..version import RENTAL_PERIOD_DAYS
from .errors import confirm, info, show_error
from .theme import Color, Space
from .widgets.common import (Card, Chip, PhotoLabel, StatusBadge, cell, hint, icon,
                             make_table, muted, section_title)
from .widgets.item_tile import availability_status

PHOTO_KINDS = [("main", "Основное фото"), ("front", "Вид спереди"), ("side", "Вид сбоку"),
               ("detail", "Детали"), ("damage", "Повреждения")]
KIND_ICONS = {"main": "camera", "front": "box", "side": "box",
              "detail": "eye", "damage": "alert"}
PHOTO_LABELS = dict(SERVICE_PHOTO_KINDS)   # ключ ракурса → подпись для человека


class ItemCardView(QWidget):
    add_to_rental = Signal(int)
    open_rental = Signal(int)
    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.item_id: int | None = None
        self._photos: list[dict] = []
        self._photo_index = 0
        self._build()

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        host = QWidget()
        scroll.setWidget(host)
        outer.addWidget(scroll)

        root = QVBoxLayout(host)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.LG)

        back_btn = QPushButton("  Назад к каталогу")
        back_btn.setObjectName("Ghost")
        back_btn.setIcon(icon("arrow-left"))
        back_btn.setIconSize(QSize(18, 18))
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        back_btn.clicked.connect(self.back.emit)
        root.addWidget(back_btn)

        header = QHBoxLayout()
        header.setSpacing(Space.LG)

        # ---------------------------------------------------- фотографии
        photo_card = Card(padding=Space.MD)
        photo_card.setFixedWidth(360)

        # Крупный снимок со стрелками. Перелистывание работает и с клавиатуры:
        # оператор смотрит фотографии одной рукой, второй держит предмет.
        viewer = QHBoxLayout()
        viewer.setSpacing(Space.SM)
        self.prev_photo_btn = QPushButton("‹")
        self.prev_photo_btn.setObjectName("Ghost")
        self.prev_photo_btn.setFixedSize(32, 64)
        self.prev_photo_btn.setToolTip("Предыдущая фотография")
        self.prev_photo_btn.setCursor(Qt.PointingHandCursor)
        self.prev_photo_btn.clicked.connect(lambda: self._step_photo(-1))
        viewer.addWidget(self.prev_photo_btn, alignment=Qt.AlignVCenter)

        self.photo = PhotoLabel(300)
        self.photo.setCursor(Qt.PointingHandCursor)
        self.photo.setToolTip("Нажмите, чтобы открыть фотографию во весь экран")
        self.photo.clicked.connect(self._open_full_photo)
        viewer.addWidget(self.photo, alignment=Qt.AlignHCenter)

        self.next_photo_btn = QPushButton("›")
        self.next_photo_btn.setObjectName("Ghost")
        self.next_photo_btn.setFixedSize(32, 64)
        self.next_photo_btn.setToolTip("Следующая фотография")
        self.next_photo_btn.setCursor(Qt.PointingHandCursor)
        self.next_photo_btn.clicked.connect(lambda: self._step_photo(1))
        viewer.addWidget(self.next_photo_btn, alignment=Qt.AlignVCenter)
        photo_card.body.addLayout(viewer)

        self.photo_caption = QLabel("")
        self.photo_caption.setObjectName("Muted")
        self.photo_caption.setAlignment(Qt.AlignCenter)
        photo_card.body.addWidget(self.photo_caption)

        self.thumbs_row = QHBoxLayout()
        self.thumbs_row.setSpacing(Space.SM)
        self.thumbs_row.addStretch()
        photo_card.body.addLayout(self.thumbs_row)

        photo_actions = QHBoxLayout()
        photo_actions.setSpacing(Space.SM)
        self.add_photo_btn = QPushButton("Добавить")
        self.add_photo_btn.setToolTip("Загрузить ещё одну фотографию этого предмета")
        self.add_photo_btn.clicked.connect(lambda: self._add_photo())
        photo_actions.addWidget(self.add_photo_btn)
        self.primary_btn = QPushButton("Сделать основной")
        self.primary_btn.clicked.connect(self._make_primary)
        photo_actions.addWidget(self.primary_btn)
        self.delete_photo_btn = QPushButton("Удалить")
        self.delete_photo_btn.setObjectName("Ghost")
        self.delete_photo_btn.clicked.connect(self._delete_photo)
        photo_actions.addWidget(self.delete_photo_btn)
        photo_card.body.addLayout(photo_actions)
        header.addWidget(photo_card)

        # ------------------------------------------------------- сведения
        info_card = Card(padding=Space.LG)
        self.name_label = QLabel()
        self.name_label.setObjectName("PageTitle")
        self.name_label.setWordWrap(True)
        self.name_label.setMinimumWidth(320)
        info_card.body.addWidget(self.name_label)

        self.badges_row = QHBoxLayout()
        self.badges_row.setSpacing(Space.SM)
        self.badges_row.addStretch()
        info_card.body.addLayout(self.badges_row)

        self.info_grid = QGridLayout()
        self.info_grid.setHorizontalSpacing(Space.XL)
        self.info_grid.setVerticalSpacing(Space.SM)
        self.info_grid.setColumnStretch(1, 1)
        info_card.body.addLayout(self.info_grid)

        self.keywords_row = QHBoxLayout()
        self.keywords_row.setSpacing(Space.SM)
        self.keywords_row.addStretch()
        info_card.body.addLayout(self.keywords_row)
        info_card.body.addStretch()
        header.addWidget(info_card, 1)

        # -------------------------------------------------------- ракурсы
        kinds_column = QVBoxLayout()
        kinds_column.setSpacing(Space.MD)
        self.kind_buttons: dict[str, QPushButton] = {}
        for kind, label in PHOTO_KINDS[1:]:
            button = QPushButton("  " + label)
            button.setObjectName("PhotoTypeButton")
            button.setIcon(icon(KIND_ICONS.get(kind, "camera")))
            button.setIconSize(QSize(18, 18))
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumWidth(230)
            button.clicked.connect(lambda _=False, k=kind: self._kind_clicked(k))
            self.kind_buttons[kind] = button
            kinds_column.addWidget(button)
        kinds_column.addStretch()
        header.addLayout(kinds_column)
        root.addLayout(header)

        # ------------------------------------------------------- действия
        actions = QHBoxLayout()
        actions.setSpacing(Space.MD)
        self.rent_btn = QPushButton("  Добавить в аренду")
        self.rent_btn.setObjectName("Primary")
        self.rent_btn.setIcon(icon("plus", "white"))
        self.rent_btn.setIconSize(QSize(18, 18))
        self.rent_btn.setMinimumHeight(46)
        self.rent_btn.setCursor(Qt.PointingHandCursor)
        self.rent_btn.clicked.connect(lambda: self.add_to_rental.emit(self.item_id))
        actions.addWidget(self.rent_btn)

        self.edit_btn = QPushButton("  Редактировать")
        self.edit_btn.setIcon(icon("edit"))
        self.edit_btn.setIconSize(QSize(18, 18))
        self.edit_btn.setMinimumHeight(46)
        self.edit_btn.clicked.connect(self._edit)
        actions.addWidget(self.edit_btn)

        self.repair_btn = QPushButton("  Отправить в ремонт")
        self.repair_btn.setIcon(icon("wrench"))
        self.repair_btn.setIconSize(QSize(18, 18))
        self.repair_btn.setMinimumHeight(46)
        self.repair_btn.clicked.connect(self._to_repair)
        actions.addWidget(self.repair_btn)

        self.label_btn = QPushButton("  Печать этикетки")
        self.label_btn.setIcon(icon("printer"))
        self.label_btn.setIconSize(QSize(18, 18))
        self.label_btn.setMinimumHeight(46)
        self.label_btn.setToolTip("Этикетка со штрихкодом для наклейки на предмет или коробку")
        self.label_btn.clicked.connect(self._print_label)
        actions.addWidget(self.label_btn)
        actions.addStretch()

        self.return_btn = QPushButton("  Вернуть на склад")
        self.return_btn.setIcon(icon("return"))
        self.return_btn.setIconSize(QSize(18, 18))
        self.return_btn.setMinimumHeight(46)
        self.return_btn.clicked.connect(self._from_repair)
        actions.addWidget(self.return_btn)
        root.addLayout(actions)

        # --------------------------------------------------------- вкладки
        tabs = QTabWidget()
        current_page = QWidget()
        current_layout = QVBoxLayout(current_page)
        current_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        self.current_table = make_table(
            ["Аренда", "Проект", "Клиент", "Продакшен", "Кол-во", "С", "По"], 1)
        self.current_table.doubleClicked.connect(self._open_rental)
        current_layout.addWidget(self.current_table)
        tabs.addTab(current_page, "Текущие аренды")

        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        self.history_table = make_table(
            ["Аренда", "Проект", "Продакшен", "Клиент", "С", "По", "Кол-во", "Сумма",
             "Примечание"], 1)
        self.history_table.doubleClicked.connect(self._open_rental_history)
        history_layout.addWidget(self.history_table)
        self.history_hint = hint("")
        history_layout.addWidget(self.history_hint)
        tabs.addTab(history_page, "История аренд")

        repairs_page = QWidget()
        repairs_layout = QVBoxLayout(repairs_page)
        repairs_layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        self.repairs_table = make_table(
            ["Дата", "Кол-во", "Причина", "Описание", "Статус", "Возврат", "Комментарий"], 3)
        repairs_layout.addWidget(self.repairs_table)
        tabs.addTab(repairs_page, "Ремонт")
        root.addWidget(tabs, 1)

    # ------------------------------------------------------------- загрузка
    def load(self, item_id: int) -> None:
        self.item_id = item_id
        item = inventory_service.get(item_id)
        if not item:
            return

        self.name_label.setText(
            f"{item['name']}   ·   Инв. № <span style='color:{Color.PRIMARY}'>"
            f"{item['inv_number']}</span>")
        self.name_label.setTextFormat(Qt.RichText)

        self._clear_layout(self.badges_row)
        code, label = availability_status(item)
        self.badges_row.addWidget(StatusBadge(code, label))
        if item["reserved_now"]:
            self.badges_row.addWidget(StatusBadge("rented", f"В аренде: {item['reserved_now']}"))
        if item["in_repair"]:
            self.badges_row.addWidget(StatusBadge("repair", f"В ремонте: {item['in_repair']}"))
        self.badges_row.addStretch()

        rows = [
            ("Категория:", item["category_path"] or "—"),
            ("Штрихкод:", item.get("barcode") or "не создан (номер нельзя закодировать)"),
            ("Описание:", item["description"] or "—"),
            ("Всего:", f"{item['quantity_total']} шт."),
            ("Доступно сейчас:", f"{item['available_now']} шт."),
            ("В аренде:", f"{item['reserved_now']} шт."),
            ("В ремонте:", f"{item['in_repair']} шт."),
            (f"Цена за {RENTAL_PERIOD_DAYS} дней:", money(item["rental_price"])),
            ("Оценочная стоимость:", money(item["valuation"])),
            ("Примечание:", item["note"] or "—"),
        ]
        self._clear_grid(self.info_grid)
        for row, (label_text, value) in enumerate(rows):
            key = QLabel(label_text)
            key.setObjectName("Muted")
            value_label = QLabel(str(value))
            value_label.setWordWrap(True)
            key.setWordWrap(True)
            if label_text.startswith("Цена") or label_text.startswith("Оценочная"):
                value_label.setStyleSheet("font-weight: 600;")
            self.info_grid.addWidget(key, row, 0, Qt.AlignTop)
            self.info_grid.addWidget(value_label, row, 1)

        self._clear_layout(self.keywords_row)
        keywords = [w for w in (item["keywords"] or "").replace(",", " ").split() if w]
        if keywords:
            caption = QLabel("Ключевые слова:")
            caption.setObjectName("Muted")
            self.keywords_row.addWidget(caption)
            for word in keywords[:12]:
                self.keywords_row.addWidget(Chip(word))
        self.keywords_row.addStretch()

        self._load_photos(item_id)

        self.rent_btn.setEnabled(item["available_now"] > 0)
        self.rent_btn.setToolTip("" if item["available_now"] > 0
                                 else "Все экземпляры сейчас в аренде или в ремонте")
        has_code = bool(item.get("barcode"))
        self.label_btn.setEnabled(has_code)
        self.label_btn.setToolTip(
            "Этикетка со штрихкодом для наклейки на предмет или коробку" if has_code
            else "У предмета нет штрихкода: инвентарный номер содержит символы, "
                 "которые не кодируются в Code 128")
        open_repairs = repairs_service.open_repairs(item_id)
        self.return_btn.setEnabled(bool(open_repairs))
        self.repair_btn.setEnabled(item["available_now"] > 0)

        self._fill_current(item_id)
        self._fill_history(item_id)
        self._fill_repairs(item_id)

    # -------------------------------------------------------------- фото
    def _load_photos(self, item_id: int) -> None:
        self._photos = photo_service.photos_of(item_id)
        self._photo_index = 0
        self._render_photos()

    def _render_photos(self) -> None:
        """Перерисовывает миниатюры, крупный снимок и подпись под ним."""
        self._clear_layout(self.thumbs_row)
        for index, photo in enumerate(self._photos[:8]):
            thumb = PhotoLabel(64, radius=8)
            thumb.set_photo(photo["thumb_path"])
            thumb.setCursor(Qt.PointingHandCursor)
            thumb.setToolTip(PHOTO_LABELS.get(photo["kind"], photo["kind"]))
            thumb.clicked.connect(lambda i=index: self._show_photo(i))
            if index == self._photo_index:
                thumb.setStyleSheet(f"border: 2px solid {Color.PRIMARY}; border-radius: 8px;")
            self.thumbs_row.addWidget(thumb)
        self.thumbs_row.addStretch()

        has_photos = bool(self._photos)
        many = len(self._photos) > 1
        self.prev_photo_btn.setVisible(many)
        self.next_photo_btn.setVisible(many)
        for button in (self.primary_btn, self.delete_photo_btn):
            button.setEnabled(has_photos)

        if not has_photos:
            self.photo.clear_photo()
            self.photo_caption.setText("Фотографий пока нет")
        else:
            current = self._photos[self._photo_index]
            self.photo.set_photo(current["abs_path"])
            mark = "  ·  основная" if current.get("is_primary") else ""
            self.photo_caption.setText(
                f"{self._photo_index + 1} из {len(self._photos)}  ·  "
                f"{PHOTO_LABELS.get(current['kind'], current['kind'])}{mark}")

        kinds = {photo["kind"] for photo in self._photos}
        for kind, button in self.kind_buttons.items():
            has = kind in kinds
            # Кнопка ракурса больше не гаснет: если такого снимка ещё нет,
            # она предлагает его добавить. Серая кнопка без объяснения
            # выглядит как поломка, хотя это просто пустой ракурс.
            button.setEnabled(True)
            button.setToolTip("Показать эту фотографию" if has
                              else "Фотографии этого ракурса нет — нажмите, чтобы добавить")

    def _show_photo(self, index: int) -> None:
        if 0 <= index < len(self._photos):
            self._photo_index = index
            self._render_photos()

    def _step_photo(self, delta: int) -> None:
        if not self._photos:
            return
        self._show_photo((self._photo_index + delta) % len(self._photos))

    def _kind_clicked(self, kind: str) -> None:
        """Показывает фотографию этого ракурса, а если её нет — предлагает добавить."""
        for index, photo in enumerate(self._photos):
            if photo["kind"] == kind:
                self._show_photo(index)
                return
        if not self.item_id:
            return
        label = PHOTO_LABELS.get(kind, kind)
        if confirm(self, f"Фотографии «{label}» у этого предмета нет.\n\n"
                         "Загрузить её сейчас?", "Нет такого ракурса"):
            self._add_photo(kind)

    def _add_photo(self, kind: str = "other") -> None:
        if not self.item_id:
            return
        paths_selected, _ = QFileDialog.getOpenFileNames(
            self, "Фотографии реквизита", "",
            "Изображения (*.jpg *.jpeg *.png *.webp *.bmp *.heic *.heif)")
        if not paths_selected:
            return
        added, errors = 0, []
        for source in paths_selected:
            try:
                photo_service.add_photo(self.item_id, source, kind=kind,
                                        make_primary=not self._photos and added == 0)
                added += 1
            except Exception as exc:                               # noqa: BLE001
                errors.append(f"{Path(source).name}: {exc}")
        self._load_photos(self.item_id)
        if errors:
            info(self, "Загружено: {0}\n\nНе удалось:\n• {1}".format(
                added, "\n• ".join(errors)), "Фотографии")

    def _current_photo(self) -> Optional[dict]:
        if not self._photos:
            return None
        return self._photos[self._photo_index]

    def _make_primary(self) -> None:
        photo = self._current_photo()
        if not photo:
            return
        try:
            photo_service.set_primary(photo["id"])
        except AppError as exc:
            show_error(self, exc)
            return
        self._load_photos(self.item_id)
        self.changed.emit()

    def _delete_photo(self) -> None:
        photo = self._current_photo()
        if not photo:
            return
        if not confirm(self, "Удалить эту фотографию?\n\nСам предмет останется "
                             "в каталоге.", "Удаление фотографии"):
            return
        try:
            photo_service.delete_photo(photo["id"])
        except AppError as exc:
            show_error(self, exc)
            return
        self._photo_index = 0
        self._load_photos(self.item_id)
        self.changed.emit()

    def _open_full_photo(self) -> None:
        """Открывает снимок во весь экран — на складе важны мелкие детали."""
        photo = self._current_photo()
        if not photo:
            return
        from .photo_viewer import PhotoViewer

        viewer = PhotoViewer(self._photos, self._photo_index, self)
        viewer.exec()
        self._show_photo(viewer.index)

    # ------------------------------------------------------------- таблицы
    def _fill_current(self, item_id: int) -> None:
        from ..db.database import get_db
        rows = get_db().query(
            "SELECT r.id, r.number, ri.quantity, ri.returned_qty, ri.date_start, r.date_end, "
            "p.name project, c.full_name client, pr.name production FROM rental_items ri "
            "JOIN rentals r ON r.id=ri.rental_id LEFT JOIN projects p ON p.id=r.project_id "
            "LEFT JOIN clients c ON c.id=r.client_id LEFT JOIN productions pr ON pr.id=r.production_id "
            "WHERE ri.item_id=? AND r.status='active' ORDER BY ri.date_start", (item_id,))
        self.current_table.setRowCount(0)
        for row in rows:
            index = self.current_table.rowCount()
            self.current_table.insertRow(index)
            self.current_table.setItem(index, 0, cell(row["number"], data=row["id"]))
            self.current_table.setItem(index, 1, cell(row["project"] or "—"))
            self.current_table.setItem(index, 2, cell(row["client"] or "—"))
            self.current_table.setItem(index, 3, cell(row["production"] or "—"))
            self.current_table.setItem(
                index, 4, cell(row["quantity"] - row["returned_qty"], True))
            self.current_table.setItem(index, 5, cell(fmt_date(row["date_start"])))
            self.current_table.setItem(
                index, 6, cell(fmt_date(row["date_end"]) if row["date_end"] else "—"))
        self.current_table.resizeColumnsToContents()

    def _fill_history(self, item_id: int) -> None:
        self.history_table.setRowCount(0)
        history = inventory_service.history(item_id)
        for row in history:
            index = self.history_table.rowCount()
            self.history_table.insertRow(index)
            self.history_table.setItem(index, 0, cell(row["number"], data=row["rental_id"]))
            self.history_table.setItem(index, 1, cell(row["project"] or "—"))
            self.history_table.setItem(index, 2, cell(row["production"] or "—"))
            self.history_table.setItem(index, 3, cell(row["client"] or "—"))
            self.history_table.setItem(index, 4, cell(fmt_date(row["date_start"])))
            self.history_table.setItem(
                index, 5, cell(fmt_date(row["date_end"]) if row["date_end"] else "—"))
            self.history_table.setItem(index, 6, cell(row["quantity"], True))
            self.history_table.setItem(index, 7, cell(money(row["amount"]), True))
            note = cell(row["note"] or "—")
            if row["note"]:
                note.setForeground(QColor(Color.WARNING))
            self.history_table.setItem(index, 8, note)
        self.history_table.resizeColumnsToContents()
        self.history_hint.setText(f"Показано {len(history)} из {len(history)}")

    def _fill_repairs(self, item_id: int) -> None:
        self.repairs_table.setRowCount(0)
        for row in repairs_service.history(item_id):
            index = self.repairs_table.rowCount()
            self.repairs_table.insertRow(index)
            self.repairs_table.setItem(index, 0, cell(fmt_date(row["date_start"]), data=row["id"]))
            self.repairs_table.setItem(index, 1, cell(row["quantity"], True))
            self.repairs_table.setItem(index, 2, cell(row["reason"]))
            self.repairs_table.setItem(index, 3, cell(row["description"]))
            self.repairs_table.setItem(
                index, 4, cell("Закрыт" if row["status"] == "done" else "В ремонте"))
            self.repairs_table.setItem(
                index, 5, cell(fmt_date(row["date_end"]) if row["date_end"] else "—"))
            self.repairs_table.setItem(index, 6, cell(row["comment"]))
        self.repairs_table.resizeColumnsToContents()

    # -------------------------------------------------------------- действия
    def _edit(self) -> None:
        from .item_dialog import ItemDialog
        dialog = ItemDialog(self, self.item_id)
        if dialog.exec():
            self.load(self.item_id)

    def _to_repair(self) -> None:
        item = inventory_service.get(self.item_id)
        free = item["available_now"]
        if free <= 0:
            show_error(self, AppError("Свободных экземпляров нет: всё в аренде или уже в ремонте."))
            return
        qty, ok = QInputDialog.getInt(self, "Отправить в ремонт", "Количество:", 1, 1, free)
        if not ok:
            return
        reason, ok = QInputDialog.getText(self, "Отправить в ремонт", "Причина:")
        if not ok:
            return
        try:
            repairs_service.send_to_repair(self.item_id, qty, reason)
            info(self, "Реквизит отправлен в ремонт и недоступен для аренды.")
            self.load(self.item_id)
        except AppError as exc:
            show_error(self, exc)

    def _from_repair(self) -> None:
        open_repairs = repairs_service.open_repairs(self.item_id)
        if not open_repairs:
            return
        options = [f"{r['id']}: {r['quantity']} шт. — {r['reason'] or 'без причины'}"
                   for r in open_repairs]
        choice, ok = QInputDialog.getItem(self, "Вернуть на склад", "Ремонт:", options, 0, False)
        if not ok:
            return
        repair_id = int(choice.split(":")[0])
        comment, _ = QInputDialog.getText(self, "Вернуть на склад", "Комментарий:")
        try:
            repairs_service.return_from_repair(repair_id, comment)
            info(self, "Реквизит возвращён на склад и снова доступен для аренды.")
            self.load(self.item_id)
        except AppError as exc:
            show_error(self, exc)

    def _print_label(self) -> None:
        if not self.item_id:
            return
        from .label_dialog import LabelPrintDialog
        LabelPrintDialog([self.item_id], self).exec()

    def _open_rental(self) -> None:
        row = self.current_table.currentRow()
        if row >= 0:
            self.open_rental.emit(self.current_table.item(row, 0).data(Qt.UserRole))

    def _open_rental_history(self) -> None:
        row = self.history_table.currentRow()
        if row >= 0:
            self.open_rental.emit(self.history_table.item(row, 0).data(Qt.UserRole))

    # ------------------------------------------------------------ служебное
    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    @staticmethod
    def _clear_grid(grid: QGridLayout) -> None:
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
