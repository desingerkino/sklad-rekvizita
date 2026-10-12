# -*- coding: utf-8 -*-
"""
Штрихкоды предметов и печать этикеток.

Отдельной системы идентификаторов не вводится: штрихкод — это инвентарный
номер предмета, закодированный в Code 128. Поэтому напечатанная этикетка и
запись в базе не могут разойтись, а старые предметы получают штрихкод
автоматически при обновлении базы.

Этикетка рассчитана на реальный принтер этикеток (по умолчанию 58×40 мм —
самый распространённый размер термопринтеров), а не на лист A4 с одним
огромным кодом. Для массовой печати есть режим листа A4 с сеткой этикеток.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from ..db.database import get_db
from ..errors import AppError, ValidationError
from . import code128
from .audit_service import audit_service
from .auth_service import auth_service

log = logging.getLogger(__name__)

BARCODE_TYPE = "code128"
_ALLOWED = re.compile(r"^[\x20-\x7E]+$")


@dataclass(frozen=True)
class LabelFormat:
    """Размеры этикетки в миллиметрах."""
    key: str
    title: str
    width_mm: float
    height_mm: float
    columns: int = 1
    rows: int = 1
    sheet: bool = False          # True — печать на листе A4 сеткой
    margin_x_mm: float = 0.0
    margin_y_mm: float = 0.0


LABEL_FORMATS = {
    "thermal_58x40": LabelFormat("thermal_58x40", "Термоэтикетка 58 × 40 мм", 58, 40),
    "thermal_58x30": LabelFormat("thermal_58x30", "Термоэтикетка 58 × 30 мм", 58, 30),
    "thermal_43x25": LabelFormat("thermal_43x25", "Термоэтикетка 43 × 25 мм", 43, 25),
    "sheet_a4_3x8": LabelFormat("sheet_a4_3x8", "Лист A4, 24 этикетки (70 × 37 мм)",
                                70, 37, columns=3, rows=8, sheet=True,
                                margin_x_mm=0, margin_y_mm=4.5),
    "sheet_a4_2x7": LabelFormat("sheet_a4_2x7", "Лист A4, 14 этикеток (105 × 42 мм)",
                                105, 42, columns=2, rows=7, sheet=True,
                                margin_x_mm=0, margin_y_mm=1.5),
}
DEFAULT_FORMAT = "thermal_58x40"


class BarcodeService:
    # ------------------------------------------------------------ значения
    @staticmethod
    def normalize(value: str) -> str:
        """Приводит ввод сканера к каноническому виду: без пробелов и в верхнем регистре."""
        return (value or "").strip().upper()

    @staticmethod
    def value_for(inv_number: str) -> str:
        """
        Штрихкод предмета — его инвентарный номер.

        Инвентарные номера в программе числовые («000123»), поэтому код
        кодируется компактным набором C. Если номер задан вручную латиницей,
        используется набор B — это тоже допустимо.
        """
        value = BarcodeService.normalize(inv_number)
        if not value:
            raise ValidationError("У предмета нет инвентарного номера — штрихкод не создать.")
        if not _ALLOWED.match(value):
            raise ValidationError(
                f"Инвентарный номер «{inv_number}» содержит символы, которые не "
                "кодируются в Code 128 (например, кириллицу). Штрихкод для такого "
                "предмета не создаётся — переименуйте номер латиницей или цифрами.")
        return value

    def ensure_for_item(self, item_id: int) -> str:
        """Создаёт штрихкод предмету, если его ещё нет. Возвращает значение."""
        db = get_db()
        row = db.query_one(
            "SELECT id, inv_number, barcode FROM inventory_items WHERE id=?", (item_id,))
        if not row:
            raise AppError("Предмет не найден.")
        if row["barcode"]:
            return row["barcode"]
        value = self.value_for(row["inv_number"])
        with db.transaction() as conn:
            conn.execute("UPDATE inventory_items SET barcode=?, barcode_type=? WHERE id=?",
                         (value, BARCODE_TYPE, item_id))
        return value

    def find_item(self, scanned: str) -> Optional[dict]:
        """
        Поиск предмета по отсканированному коду.

        Сканер отдаёт то, что напечатано на этикетке. Ищем сначала по колонке
        barcode, затем по инвентарному номеру — чтобы работали и старые
        этикетки, напечатанные до версии 2.4, и ручной ввод номера.
        """
        value = self.normalize(scanned)
        if not value:
            return None
        db = get_db()
        row = db.query_one("SELECT * FROM inventory_items WHERE barcode=?", (value,))
        if not row:
            row = db.query_one("SELECT * FROM inventory_items WHERE inv_number=?", (value,))
        if not row:
            # номер мог быть напечатан без ведущих нулей
            digits = value.lstrip("0")
            if digits.isdigit():
                row = db.query_one(
                    "SELECT * FROM inventory_items WHERE CAST(inv_number AS INTEGER)=? "
                    "AND inv_number GLOB '[0-9]*'", (int(digits),))
        return dict(row) if row else None

    def backfill(self) -> int:
        """
        Проставляет штрихкоды всем предметам, у которых их ещё нет.
        Вызывается при миграции базы и из настроек. Возвращает число предметов.
        """
        db = get_db()
        rows = db.query("SELECT id, inv_number FROM inventory_items WHERE barcode=''")
        done = 0
        skipped = 0
        with db.transaction() as conn:
            for row in rows:
                try:
                    value = self.value_for(row["inv_number"])
                except ValidationError:
                    skipped += 1
                    continue
                try:
                    conn.execute(
                        "UPDATE inventory_items SET barcode=?, barcode_type=? WHERE id=?",
                        (value, BARCODE_TYPE, row["id"]))
                    done += 1
                except Exception:
                    # дубликат инвентарного номера в старой базе — предмет останется без кода
                    skipped += 1
        if skipped:
            log.warning("Штрихкод не создан для %s предметов (недопустимый инв. номер)", skipped)
        return done

    # ------------------------------------------------------------- картинка
    def render_png(self, value: str, target: Path, module_px: int = 2,
                   height_px: int = 60, with_text: bool = True) -> Path:
        """PNG со штрихкодом — для предпросмотра на экране."""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as exc:
            raise AppError("Библиотека Pillow не установлена — предпросмотр недоступен.") from exc

        bars = code128.encode_bars(value)
        quiet = code128.QUIET_ZONE_MODULES * module_px
        width = sum(w for _, w in bars) * module_px + 2 * quiet
        text_h = 16 if with_text else 0
        image = Image.new("L", (width, height_px + text_h), 255)
        draw = ImageDraw.Draw(image)
        x = quiet
        for black, modules in bars:
            w = modules * module_px
            if black:
                draw.rectangle([x, 0, x + w - 1, height_px - 1], fill=0)
            x += w
        if with_text:
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
            box = draw.textbbox((0, 0), value, font=font)
            draw.text(((width - (box[2] - box[0])) / 2, height_px + 1), value, fill=0, font=font)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        return target

    # ------------------------------------------------------------ этикетки
    def labels_pdf(self, item_ids: Sequence[int], target: Path,
                   fmt: str = DEFAULT_FORMAT, copies: int = 1) -> Path:
        """
        PDF с этикетками. Один предмет — одна этикетка (или copies штук).

        Для термопринтера каждая этикетка становится отдельной страницей
        точно своего размера: принтер печатает страницу за страницей без
        подгонки. Для листа A4 этикетки раскладываются сеткой.
        """
        auth_service.require_login()
        if not item_ids:
            raise ValidationError("Не выбрано ни одного предмета.")
        if copies < 1:
            copies = 1
        layout = LABEL_FORMATS.get(fmt) or LABEL_FORMATS[DEFAULT_FORMAT]

        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as pdfcanvas

        from .pdf_service import FONT, FONT_BOLD, _register_fonts
        _register_fonts()

        items = self.items_for_labels(item_ids)
        if not items:
            raise ValidationError("Выбранные предметы не найдены.")
        queue = [item for item in items for _ in range(copies)]

        target.parent.mkdir(parents=True, exist_ok=True)
        page = A4 if layout.sheet else (layout.width_mm * mm, layout.height_mm * mm)
        pdf = pdfcanvas.Canvas(str(target), pagesize=page)
        pdf.setTitle("Этикетки — Склад Реквизита")

        if layout.sheet:
            self._draw_sheet(pdf, queue, layout, FONT, FONT_BOLD, mm, A4)
        else:
            for item in queue:
                self._draw_label(pdf, item, 0, 0, layout.width_mm * mm,
                                 layout.height_mm * mm, FONT, FONT_BOLD, mm)
                pdf.showPage()
        pdf.save()

        audit_service.log("Печать этикеток", entity="item",
                          details=f"{len(queue)} шт., формат {layout.title}")
        log.info("Этикетки: %s шт. -> %s", len(queue), target)
        return target

    @staticmethod
    def items_for_labels(item_ids: Iterable[int]) -> list[dict]:
        """Данные предметов для этикеток; недостающие штрихкоды создаются на лету."""
        db = get_db()
        out = []
        for item_id in item_ids:
            row = db.query_one(
                "SELECT i.id, i.inv_number, i.barcode, i.name, c.name AS category "
                "FROM inventory_items i LEFT JOIN categories c ON c.id=i.category_id "
                "WHERE i.id=?", (item_id,))
            if row:
                data = dict(row)
                if not data["barcode"]:
                    try:
                        data["barcode"] = barcode_service.ensure_for_item(item_id)
                    except ValidationError:
                        data["barcode"] = ""
                out.append(data)
        return out

    def _draw_sheet(self, pdf, queue, layout, font, font_bold, mm, page_size) -> None:
        page_w, page_h = page_size
        cell_w, cell_h = layout.width_mm * mm, layout.height_mm * mm
        grid_w = cell_w * layout.columns
        grid_h = cell_h * layout.rows
        left = (page_w - grid_w) / 2
        top = page_h - (page_h - grid_h) / 2 - layout.margin_y_mm * mm
        per_page = layout.columns * layout.rows
        for index, item in enumerate(queue):
            if index and index % per_page == 0:
                pdf.showPage()
            slot = index % per_page
            col, row = slot % layout.columns, slot // layout.columns
            x = left + col * cell_w
            y = top - (row + 1) * cell_h
            self._draw_label(pdf, item, x, y, cell_w, cell_h, font, font_bold, mm)
        pdf.showPage()

    def _draw_label(self, pdf, item: dict, x: float, y: float, width: float,
                    height: float, font: str, font_bold: str, mm) -> None:
        """Одна этикетка: название, штрихкод, инвентарный номер."""
        pad = 2 * mm
        inner_w = width - 2 * pad
        value = item.get("barcode") or ""

        # --- название (до двух строк, длинное обрезается)
        name = (item.get("name") or "").strip()
        pdf.setFont(font_bold, 8)
        lines = self._wrap(pdf, name, font_bold, 8, inner_w, max_lines=2)
        text_y = y + height - pad - 6
        for line in lines:
            pdf.drawString(x + pad, text_y, line)
            text_y -= 8.5

        # --- категория мелким шрифтом, если помещается
        category = (item.get("category") or "").strip()
        if category and height > 30 * mm:
            pdf.setFont(font, 6)
            pdf.drawString(x + pad, text_y, self._clip(pdf, category, font, 6, inner_w))
            text_y -= 7

        # --- номер снизу
        inv = item.get("inv_number") or ""
        pdf.setFont(font_bold, 9)
        inv_y = y + pad
        pdf.drawString(x + pad, inv_y, inv)

        # --- штрихкод в оставшемся пространстве
        bar_bottom = inv_y + 11
        bar_top = text_y - 1
        bar_height = max(8, bar_top - bar_bottom)
        if not value:
            pdf.setFont(font, 6)
            pdf.drawString(x + pad, bar_bottom + 2, "штрихкод не создан")
            return
        bars = code128.encode_bars(value)
        modules = sum(w for _, w in bars) + 2 * code128.QUIET_ZONE_MODULES
        module_w = inner_w / modules
        bx = x + pad + code128.QUIET_ZONE_MODULES * module_w
        pdf.setFillGray(0)
        for black, w in bars:
            bw = w * module_w
            if black:
                pdf.rect(bx, bar_bottom, bw, bar_height, stroke=0, fill=1)
            bx += bw

    @staticmethod
    def _clip(pdf, text: str, font: str, size: float, width: float) -> str:
        if pdf.stringWidth(text, font, size) <= width:
            return text
        while text and pdf.stringWidth(text + "…", font, size) > width:
            text = text[:-1]
        return text + "…"

    @staticmethod
    def _wrap(pdf, text: str, font: str, size: float, width: float,
              max_lines: int = 2) -> list[str]:
        words, lines, current = text.split(), [], ""
        for word in words:
            probe = f"{current} {word}".strip()
            if pdf.stringWidth(probe, font, size) <= width:
                current = probe
                continue
            if current:
                lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
        if current and len(lines) < max_lines:
            lines.append(current)
        if not lines:
            return [""]
        if len(lines) == max_lines and pdf.stringWidth(lines[-1], font, size) > width:
            lines[-1] = BarcodeService._clip(pdf, lines[-1], font, size, width)
        return lines


barcode_service = BarcodeService()
