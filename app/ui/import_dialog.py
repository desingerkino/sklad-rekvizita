# -*- coding: utf-8 -*-
"""
Импорт реквизита из папки с фотографиями.

Порядок жёсткий: сначала программа показывает, что собирается создать, и
только после подтверждения что-то пишет в базу. Массовое создание карточек
вслепую — прямой путь к разгребанию вручную, поэтому шаг предпросмотра
пропустить нельзя.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QVBoxLayout)

from ..errors import AppError
from ..services.import_service import import_service
from .errors import confirm, info, show_error
from .theme import Color, Space
from .widgets.common import Card, cell, icon, make_table, muted, section_title


class ImportDialog(QDialog):
    """Выбор папки → предпросмотр → импорт."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Импорт реквизита из папки")
        self.setModal(True)
        self.resize(900, 640)
        self.plan = None
        self.imported = False
        self._build()

    # ------------------------------------------------------------ интерфейс
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        card = Card(padding=Space.LG)
        outer.addWidget(card)
        root = card.body
        root.setSpacing(Space.MD)

        root.addWidget(section_title("Импорт реквизита из папки"))
        root.addWidget(muted(
            "Выберите папку с фотографиями. Вложенные папки станут категориями, "
            "имена файлов — названиями предметов. Кадры одного предмета, "
            "пронумерованные как «стул_1», «стул_2», объединятся в одну карточку.\n\n"
            "Если имена файлов ничего не говорят (IMG_0001 и подобные), положите "
            "рядом файл «подписи.txt» со строками вида:\n"
            "IMG_0001.jpg; Телефон дисковый чёрный; 3; Рабочий, с проводом"))

        pick_row = QHBoxLayout()
        self.folder_label = QLabel("Папка не выбрана")
        self.folder_label.setWordWrap(True)
        pick_row.addWidget(self.folder_label, 1)
        pick_btn = QPushButton("  Выбрать папку…")
        pick_btn.setIcon(icon("folder"))
        pick_btn.setIconSize(QSize(18, 18))
        pick_btn.setMinimumHeight(42)
        pick_btn.setCursor(Qt.PointingHandCursor)
        pick_btn.clicked.connect(self._pick_folder)
        pick_row.addWidget(pick_btn)
        root.addLayout(pick_row)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.table = make_table(
            ["Название", "Категория", "Фото", "Кол-во", "Состояние"], 0)
        root.addWidget(self.table, 1)

        self.skipped = QLabel("")
        self.skipped.setWordWrap(True)
        self.skipped.setStyleSheet(f"color:{Color.WARNING};")
        self.skipped.setVisible(False)
        root.addWidget(self.skipped)

        self.add_photos = QCheckBox(
            "Добавлять фотографии к предметам, которые уже есть в каталоге")
        self.add_photos.setChecked(False)
        root.addWidget(self.add_photos)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        buttons = QHBoxLayout()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        buttons.addStretch()
        self.import_btn = QPushButton("  Импортировать")
        self.import_btn.setObjectName("Primary")
        self.import_btn.setIcon(icon("check", "white"))
        self.import_btn.setIconSize(QSize(18, 18))
        self.import_btn.setMinimumHeight(44)
        self.import_btn.setMinimumWidth(200)
        self.import_btn.setEnabled(False)
        self.import_btn.clicked.connect(self._run_import)
        buttons.addWidget(self.import_btn)
        root.addLayout(buttons)

    # --------------------------------------------------------------- разбор
    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка с фотографиями реквизита")
        if not folder:
            return
        self.folder_label.setText(folder)
        try:
            self.plan = import_service.scan(Path(folder))
        except AppError as exc:
            show_error(self, exc)
            return
        except Exception as exc:                                   # noqa: BLE001
            show_error(self, exc, "Не удалось прочитать папку.")
            return
        self._show_plan()

    def _show_plan(self) -> None:
        plan = self.plan
        self.table.setRowCount(0)
        for item in plan.items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, cell(item.name))
            self.table.setItem(row, 1, cell(item.category_path or "без категории"))
            self.table.setItem(row, 2, cell(len(item.photos), True))
            self.table.setItem(row, 3, cell(item.quantity, True))
            self.table.setItem(row, 4, cell("уже есть в каталоге" if item.exists
                                            else "будет создан"))
        self.table.resizeColumnsToContents()

        parts = [f"Найдено предметов: {len(plan.items)}",
                 f"фотографий: {plan.photo_count}"]
        if plan.new_items:
            parts.append(f"будет создано: {len(plan.new_items)}")
        if plan.existing_items:
            parts.append(f"уже есть в каталоге: {len(plan.existing_items)}")
        if plan.new_categories:
            parts.append("новых категорий: " + ", ".join(plan.new_categories))
        self.summary.setText("   ·   ".join(parts))

        if plan.skipped_files:
            shown = plan.skipped_files[:8]
            tail = (f"\n…и ещё {len(plan.skipped_files) - len(shown)}"
                    if len(plan.skipped_files) > len(shown) else "")
            self.skipped.setText("Пропущено:\n• " + "\n• ".join(shown) + tail)
            self.skipped.setVisible(True)
        else:
            self.skipped.setVisible(False)

        self.import_btn.setEnabled(bool(plan.items))

    # --------------------------------------------------------------- импорт
    def _run_import(self) -> None:
        plan = self.plan
        if not plan or not plan.items:
            return
        lines = [f"Будет создано карточек: {len(plan.new_items)}"]
        if plan.existing_items:
            lines.append(
                f"Уже есть в каталоге: {len(plan.existing_items)} — "
                + ("к ним добавятся фотографии" if self.add_photos.isChecked()
                   else "они будут пропущены"))
        if plan.new_categories:
            lines.append("Появятся категории: " + ", ".join(plan.new_categories))
        lines.append("\nПродолжить?")
        if not confirm(self, "\n".join(lines), "Импорт реквизита"):
            return

        self.progress.setVisible(True)
        self.progress.setRange(0, len(plan.items))
        self.import_btn.setEnabled(False)

        def report(index: int, total: int, name: str) -> None:
            self.progress.setValue(index)
            self.progress.setFormat(f"{index} из {total}: {name}")
            self.progress.repaint()

        try:
            result = import_service.apply(
                plan, add_photos_to_existing=self.add_photos.isChecked(),
                progress=report)
        except AppError as exc:
            self.progress.setVisible(False)
            self.import_btn.setEnabled(True)
            show_error(self, exc)
            return
        finally:
            self.progress.setVisible(False)

        self.imported = True
        text = (f"Создано карточек: {result.created}\n"
                f"Дополнено существующих: {result.updated}\n"
                f"Загружено фотографий: {result.photos}\n"
                f"Пропущено: {result.skipped}")
        if result.errors:
            shown = result.errors[:10]
            tail = (f"\n…и ещё {len(result.errors) - len(shown)}"
                    if len(result.errors) > len(shown) else "")
            text += ("\n\nНе удалось обработать:\n• " + "\n• ".join(shown) + tail
                     + "\n\nОстальное импортировано, эти файлы можно добавить вручную.")
        info(self, text, "Импорт завершён")
        self.accept()
