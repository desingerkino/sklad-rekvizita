# -*- coding: utf-8 -*-
"""
Печать этикеток со штрихкодами.

Два пути наружу, оба настоящие:
  • «Печать» — рисование прямо на принтере через Qt. Размер страницы задаётся
    в миллиметрах, поэтому термопринтер этикеток получает ровно тот формат,
    который выбран, без подгонки под A4.
  • «Сохранить PDF» / «Открыть PDF» — файл, собранный тем же кодом, что и
    складские документы. Годится для обычного принтера и для отправки.

Предпросмотр показывает штрихкод как он будет напечатан.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, QSizeF, Qt
from PySide6.QtGui import QPainter, QPageSize, QPixmap
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QPushButton, QSpinBox, QVBoxLayout)

from .. import paths
from ..errors import AppError
from ..services import code128
from ..services.barcode_service import DEFAULT_FORMAT, LABEL_FORMATS, barcode_service
from .errors import info, show_error
from .theme import Color, Space
from .widgets.common import Card, icon, muted, section_title


class LabelPrintDialog(QDialog):
    """Печать этикеток для одного или нескольких предметов."""

    def __init__(self, item_ids: list[int], parent=None):
        super().__init__(parent)
        self.item_ids = [int(i) for i in item_ids if i]
        self.setWindowTitle("Печать этикеток")
        self.setModal(True)
        self.setFixedWidth(560)
        self._items: list[dict] = []
        self._build()
        self._reload_preview()

    # ------------------------------------------------------------- интерфейс
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        card = Card(padding=Space.XL, with_shadow=True)
        outer.addWidget(card)
        root = card.body
        root.setSpacing(Space.MD)

        root.addWidget(section_title("Этикетки со штрихкодом"))
        count = len(self.item_ids)
        self.summary = muted(f"Выбрано предметов: {count}")
        root.addWidget(self.summary)

        form = QFormLayout()
        form.setSpacing(Space.MD)
        self.format_box = QComboBox()
        for key, layout in LABEL_FORMATS.items():
            self.format_box.addItem(layout.title, key)
        index = self.format_box.findData(DEFAULT_FORMAT)
        self.format_box.setCurrentIndex(max(0, index))
        self.format_box.setMinimumHeight(38)
        self.format_box.currentIndexChanged.connect(self._reload_preview)
        form.addRow("Формат:", self.format_box)

        self.copies = QSpinBox()
        self.copies.setRange(1, 50)
        self.copies.setValue(1)
        self.copies.setMinimumHeight(38)
        form.addRow("Экземпляров каждой:", self.copies)
        root.addLayout(form)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(110)
        self.preview.setStyleSheet(
            f"background:{Color.SURFACE_ALT}; border:1px solid {Color.BORDER}; "
            "border-radius:8px; padding:10px;")
        root.addWidget(self.preview)

        self.note = muted("")
        root.addWidget(self.note)

        actions = QHBoxLayout()
        actions.setSpacing(Space.MD)
        self.print_btn = QPushButton("  Печать")
        self.print_btn.setObjectName("Primary")
        self.print_btn.setIcon(icon("printer", "white"))
        self.print_btn.setIconSize(QSize(18, 18))
        self.print_btn.setMinimumHeight(44)
        self.print_btn.setCursor(Qt.PointingHandCursor)
        self.print_btn.clicked.connect(self._print)
        actions.addWidget(self.print_btn)

        self.pdf_btn = QPushButton("  Сохранить PDF…")
        self.pdf_btn.setIcon(icon("save"))
        self.pdf_btn.setIconSize(QSize(18, 18))
        self.pdf_btn.setMinimumHeight(44)
        self.pdf_btn.clicked.connect(self._save_pdf)
        actions.addWidget(self.pdf_btn)

        self.open_btn = QPushButton("  Открыть PDF")
        self.open_btn.setIcon(icon("file"))
        self.open_btn.setIconSize(QSize(18, 18))
        self.open_btn.setMinimumHeight(44)
        self.open_btn.clicked.connect(self._open_pdf)
        actions.addWidget(self.open_btn)
        root.addLayout(actions)

        footer = QHBoxLayout()
        footer.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        footer.addWidget(close_btn)
        root.addLayout(footer)

    # -------------------------------------------------------------- данные
    def _layout(self):
        return LABEL_FORMATS.get(self.format_box.currentData()) or LABEL_FORMATS[DEFAULT_FORMAT]

    def _load_items(self) -> list[dict]:
        if not self._items:
            self._items = barcode_service.items_for_labels(self.item_ids)
        return self._items

    def _reload_preview(self) -> None:
        items = self._load_items()
        if not items:
            self.preview.setText("Предметы не найдены.")
            return
        first = items[0]
        no_code = [i["inv_number"] for i in items if not i.get("barcode")]
        layout = self._layout()
        if layout.sheet:
            self.note.setText(
                f"На листе A4 помещается {layout.columns * layout.rows} этикеток "
                f"размером {layout.width_mm:g} × {layout.height_mm:g} мм.")
        else:
            self.note.setText(
                f"Каждая этикетка печатается отдельной страницей "
                f"{layout.width_mm:g} × {layout.height_mm:g} мм — формат для принтера этикеток.")
        if no_code:
            self.note.setText(
                self.note.text() + "  Без штрихкода останутся: " + ", ".join(no_code[:5]) +
                (" и другие" if len(no_code) > 5 else "") +
                " — их инвентарные номера нельзя закодировать.")

        if not first.get("barcode"):
            self.preview.setText("У этого предмета нет штрихкода.")
            return
        try:
            path = barcode_service.render_png(
                first["barcode"], paths.TEMP_DIR / "label_preview.png", module_px=2, height_px=64)
            pixmap = QPixmap(str(path))
            self.preview.setPixmap(pixmap)
        except Exception:                                          # noqa: BLE001
            self.preview.setText(first["barcode"])

    # -------------------------------------------------------------- печать
    def _print(self) -> None:
        """Прямая печать: страница нужного размера, штрихкод рисуется на принтере."""
        items = self._load_items()
        if not items:
            return
        layout = self._layout()
        printer = QPrinter(QPrinter.HighResolution)
        if layout.sheet:
            printer.setPageSize(QPageSize(QPageSize.A4))
        else:
            # размер страницы = размер этикетки: принтер печатает ленту, а не A4
            printer.setPageSize(QPageSize(QSizeF(layout.width_mm, layout.height_mm),
                                          QPageSize.Millimeter, "Этикетка"))
        printer.setFullPage(True)

        dialog = QPrintDialog(printer, self)
        dialog.setWindowTitle("Печать этикеток")
        if not dialog.exec():
            return
        try:
            self._paint(printer, items, layout)
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Напечатать этикетки не удалось.")
            return
        from ..services.audit_service import audit_service
        audit_service.log("Печать этикеток", entity="item",
                          details=f"{len(items) * self.copies.value()} шт., {layout.title}")
        self.accept()

    def _paint(self, printer: QPrinter, items: list[dict], layout) -> None:
        queue = [item for item in items for _ in range(self.copies.value())]
        painter = QPainter()
        if not painter.begin(printer):
            raise AppError("Принтер недоступен.")
        try:
            page = printer.pageRect(QPrinter.DevicePixel)
            if layout.sheet:
                per_page = layout.columns * layout.rows
                cell_w = page.width() / layout.columns
                cell_h = page.height() / layout.rows
                for index, item in enumerate(queue):
                    if index and index % per_page == 0:
                        printer.newPage()
                    slot = index % per_page
                    col, row = slot % layout.columns, slot // layout.columns
                    self._paint_label(painter, item,
                                      page.x() + col * cell_w, page.y() + row * cell_h,
                                      cell_w, cell_h)
            else:
                for index, item in enumerate(queue):
                    if index:
                        printer.newPage()
                    self._paint_label(painter, item, page.x(), page.y(),
                                      page.width(), page.height())
        finally:
            painter.end()

    def _paint_label(self, painter: QPainter, item: dict, x: float, y: float,
                     width: float, height: float) -> None:
        """Одна этикетка: название сверху, штрихкод по центру, номер снизу."""
        painter.save()
        pad = width * 0.04
        inner = width - 2 * pad
        font = painter.font()

        font.setPointSizeF(max(5.0, height * 0.10))
        font.setBold(True)
        painter.setFont(font)
        name_h = height * 0.28
        painter.drawText(int(x + pad), int(y + pad), int(inner), int(name_h),
                         int(Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap),
                         item.get("name", ""))

        inv = item.get("inv_number", "")
        font.setPointSizeF(max(5.0, height * 0.11))
        painter.setFont(font)
        inv_h = height * 0.16
        painter.drawText(int(x + pad), int(y + height - pad - inv_h), int(inner), int(inv_h),
                         int(Qt.AlignLeft | Qt.AlignVCenter), inv)

        value = item.get("barcode") or ""
        if not value:
            painter.restore()
            return
        bar_top = y + pad + name_h
        bar_height = height - pad - inv_h - (pad + name_h)
        if bar_height <= 0:
            painter.restore()
            return
        bars = code128.encode_bars(value)
        modules = sum(w for _, w in bars) + 2 * code128.QUIET_ZONE_MODULES
        module_w = inner / modules
        left = x + pad + code128.QUIET_ZONE_MODULES * module_w
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.black)
        for black, w in bars:
            bar_w = w * module_w
            if black:
                painter.drawRect(int(left), int(bar_top), max(1, int(bar_w)), int(bar_height))
            left += bar_w
        painter.restore()

    # ----------------------------------------------------------------- PDF
    def _build_pdf(self, target: Path) -> Path:
        return barcode_service.labels_pdf(
            self.item_ids, target, fmt=self.format_box.currentData(),
            copies=self.copies.value())

    def _save_pdf(self) -> None:
        default = ("Этикетки.pdf" if len(self.item_ids) > 1
                   else f"Этикетка {self._load_items()[0]['inv_number']}.pdf")
        target, _ = QFileDialog.getSaveFileName(self, "Сохранить этикетки", default,
                                                "PDF (*.pdf)")
        if not target:
            return
        try:
            self._build_pdf(Path(target))
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Сохранить этикетки не удалось.")
            return
        info(self, f"Этикетки сохранены:\n{target}")

    def _open_pdf(self) -> None:
        try:
            path = self._build_pdf(paths.TEMP_DIR / "labels.pdf")
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Сформировать этикетки не удалось.")
            return
        try:
            if os.name == "nt":
                os.startfile(str(path))          # noqa: S606 — штатное открытие в Windows
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Открыть файл не удалось.")
