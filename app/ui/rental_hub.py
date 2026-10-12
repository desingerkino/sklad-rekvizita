# -*- coding: utf-8 -*-
"""
Несколько незавершённых аренд в одном окне.

Оператор оформляет выдачу первому клиенту, приходит второй — оператор
открывает ещё одно оформление и спокойно переключается между ними. Отдельных
окон при этом не появляется: все формы живут внутри раздела «Новая аренда»,
переключение идёт через подпункты бокового меню.

Каждая форма привязана к своему незавершённому оформлению в базе: состояние
сохраняется при переключении и переживает закрытие программы.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..errors import AppError
from ..services.rental_drafts_service import rental_drafts_service
from .errors import confirm, show_error
from .rental_new_view import NewRentalView
from .theme import Space
from .widgets.common import Card, icon, muted, section_title

log = logging.getLogger(__name__)


class EmptyRentalPage(QWidget):
    """Что показать, когда ни одного оформления не открыто."""

    start_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.XL)
        root.addStretch()
        card = Card(padding=Space.XL)
        card.setMaximumWidth(560)
        card.body.addWidget(section_title("Открытых оформлений нет"))
        card.body.addWidget(muted(
            "Нажмите «Начать оформление» — аренда получит номер и появится "
            "подпунктом в меню слева. Можно вести несколько выдач сразу и "
            "переключаться между ними: набранное не пропадёт."))
        button = QPushButton("  Начать оформление")
        button.setObjectName("Primary")
        button.setIcon(icon("plus", "white"))
        button.setMinimumHeight(46)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(self.start_requested.emit)
        card.body.addWidget(button)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(card)
        row.addStretch()
        root.addLayout(row)
        root.addStretch()


class NewRentalHub(QStackedWidget):
    """
    Раздел «Новая аренда»: сколько угодно параллельных оформлений.

    Снаружи ведёт себя как одна страница — главному окну не приходится знать,
    сколько форм внутри. Методы add_item, reload_references и _save_draft
    переадресуются текущей форме, чтобы остальной код остался прежним.
    """

    rental_created = Signal(int)
    drafts_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pages: dict[int, NewRentalView] = {}
        self.empty = EmptyRentalPage()
        self.empty.start_requested.connect(self.start_new)
        self.addWidget(self.empty)
        self.restore_open()

    # ----------------------------------------------------------- оформления
    def restore_open(self) -> None:
        """Возвращает на место незавершённые оформления после запуска программы."""
        try:
            drafts = rental_drafts_service.list_open()
        except Exception:                                          # noqa: BLE001
            log.exception("Не удалось загрузить незавершённые аренды")
            return
        for draft in drafts:
            self._add_page(draft)
        if drafts:
            self.show_draft(drafts[0]["id"])
        self.drafts_changed.emit()

    def start_new(self) -> Optional[int]:
        try:
            draft = rental_drafts_service.open_new()
        except AppError as exc:
            show_error(self, exc)
            return None
        self._add_page(draft)
        self.show_draft(draft["id"])
        self.drafts_changed.emit()
        return draft["id"]

    def _add_page(self, draft: dict) -> NewRentalView:
        page = self.pages.get(draft["id"])
        if page is not None:
            return page
        page = NewRentalView(draft_id=draft["id"], number=draft["number"])
        page.rental_created.connect(self.rental_created.emit)
        page.draft_finished.connect(self.close_draft)
        if draft.get("payload"):
            try:
                page.apply_draft(draft["payload"])
            except Exception:                                      # noqa: BLE001
                log.exception("Не удалось восстановить оформление %s", draft["number"])
        self.pages[draft["id"]] = page
        self.addWidget(page)
        return page

    def show_draft(self, draft_id: int) -> None:
        page = self.pages.get(draft_id)
        if page is not None:
            self.setCurrentWidget(page)

    def close_draft(self, draft_id: int) -> None:
        """Убирает оформление из окна: выдано или отменено."""
        page = self.pages.pop(draft_id, None)
        if page is not None:
            self.removeWidget(page)
            page.deleteLater()
        if self.pages:
            self.setCurrentWidget(next(iter(self.pages.values())))
        else:
            self.setCurrentWidget(self.empty)
        self.drafts_changed.emit()

    def cancel_draft(self, draft_id: int) -> None:
        page = self.pages.get(draft_id)
        number = page.draft_number if page else ""
        if not confirm(self, f"Отменить оформление аренды {number}?\n\n"
                             "Набранные позиции будут потеряны. Номер останется "
                             "занятым — переиспользовать его нельзя.",
                       "Отмена оформления"):
            return
        try:
            rental_drafts_service.cancel(draft_id)
        except AppError as exc:
            show_error(self, exc)
            return
        self.close_draft(draft_id)

    # ------------------------------------------------------------- сведения
    def open_drafts(self) -> list[dict]:
        """Список для подпунктов меню: номер, число позиций, клиент."""
        out = []
        for draft_id, page in self.pages.items():
            out.append({
                "id": draft_id,
                "number": page.draft_number,
                "lines": len(page.lines),
                "client": page.client_edit.text().strip(),
            })
        return out

    def current_draft_id(self) -> Optional[int]:
        current = self.currentWidget()
        for draft_id, page in self.pages.items():
            if page is current:
                return draft_id
        return None

    # ------------------------------- переадресация текущей форме
    def _current_form(self) -> Optional[NewRentalView]:
        current = self.currentWidget()
        return current if isinstance(current, NewRentalView) else None

    def add_item(self, item_id: int) -> None:
        """
        Добавление реквизита из каталога. Если открытых оформлений нет, оно
        создаётся само: оператор нажал «Добавить в аренду», значит выдача
        начинается — заставлять его сначала жать другую кнопку незачем.
        """
        form = self._current_form()
        if form is None:
            if self.start_new() is None:
                return
            form = self._current_form()
        if form is not None:
            form.add_item(item_id)

    def reload_references(self) -> None:
        form = self._current_form()
        if form is not None:
            form.reload_references()

    def save_open_drafts(self) -> None:
        """Сохраняет состояние всех форм — при закрытии программы и по Ctrl+S."""
        for page in self.pages.values():
            try:
                page._save_draft()
            except Exception:                                      # noqa: BLE001
                log.exception("Не удалось сохранить оформление %s", page.draft_number)

    # прежнее имя: главное окно вызывает его при выходе
    def _save_draft(self) -> None:
        self.save_open_drafts()
