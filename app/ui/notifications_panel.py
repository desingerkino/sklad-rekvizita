# -*- coding: utf-8 -*-
"""Панель уведомлений: список событий склада с переходом к нужной записи."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ..services.notifications_service import notifications_service
from ..utils import fmt_dt
from .theme import Color, Space, status_tone
from .widgets.common import Card, icon, muted, section_title, shadow

KIND_TONE = {"overdue": "danger", "expiring": "soon", "prolonged": "info",
             "repair": "repair", "system": "returned"}
KIND_ICON = {"overdue": "alert", "expiring": "clock", "prolonged": "calendar",
             "repair": "wrench", "system": "bell"}


class NotificationItem(Card):
    """Одно уведомление: заголовок, описание, время, состояние."""

    clicked = Signal(dict)

    def __init__(self, data: dict, parent=None):
        super().__init__(parent, padding=Space.MD)
        self.data = data
        self.setCursor(Qt.PointingHandCursor)
        tone = KIND_TONE.get(data["kind"], "info")
        color, background, border = status_tone(tone)

        row = QHBoxLayout()
        row.setSpacing(Space.MD)
        mark = QLabel()
        mark.setPixmap(icon(KIND_ICON.get(data["kind"], "bell"),
                            "danger" if tone == "danger" else "active").pixmap(20, 20))
        mark.setFixedWidth(24)
        mark.setAlignment(Qt.AlignTop)
        row.addWidget(mark)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(data["title"])
        title.setWordWrap(True)
        title.setStyleSheet(
            f"font-weight: {'600' if not data['is_read'] else '400'}; color: {Color.TEXT};")
        body = QLabel(data["body"])
        body.setWordWrap(True)
        body.setObjectName("Hint")
        meta = QLabel(f"{data['kind_label']} · {fmt_dt(data['created_at'])}")
        meta.setObjectName("Hint")
        text.addWidget(title)
        text.addWidget(body)
        text.addWidget(meta)
        row.addLayout(text, 1)

        if not data["is_read"]:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {Color.PRIMARY}; font-size: 12pt;")
            dot.setAlignment(Qt.AlignTop)
            row.addWidget(dot)
        self.body.addLayout(row)

        self.setStyleSheet(
            f"#Card {{ background: {Color.SURFACE if data['is_read'] else background};"
            f" border: 1px solid {Color.BORDER if data['is_read'] else border};"
            f" border-radius: 12px; }}")

    def mousePressEvent(self, event):
        self.clicked.emit(self.data)
        super().mousePressEvent(event)


class NotificationsPanel(QFrame):
    """Всплывающая панель под колокольчиком."""

    open_entity = Signal(str, int)
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("Card")
        self.setFixedWidth(420)
        self.setMaximumHeight(560)
        shadow(self, blur=36, y=8, alpha=40)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.LG, Space.LG, Space.LG)
        layout.setSpacing(Space.MD)

        header = QHBoxLayout()
        header.addWidget(section_title("Уведомления"))
        header.addStretch()
        self.mark_all = QPushButton("Прочитать все")
        self.mark_all.setObjectName("LinkButton")
        self.mark_all.setCursor(Qt.PointingHandCursor)
        self.mark_all.clicked.connect(self._mark_all)
        header.addWidget(self.mark_all)
        layout.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.host = QWidget()
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(Space.SM)
        self.scroll.setWidget(self.host)
        layout.addWidget(self.scroll, 1)

        self.empty = muted("Новых уведомлений нет. Здесь появятся сроки аренд, "
                           "просрочки, пролонгации и реквизит в ремонте.")
        layout.addWidget(self.empty)

    def reload(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        items = notifications_service.list(limit=40)
        for data in items:
            entry = NotificationItem(data)
            entry.clicked.connect(self._open)
            self.list_layout.addWidget(entry)
        self.list_layout.addStretch()
        self.empty.setVisible(not items)
        self.scroll.setVisible(bool(items))
        self.mark_all.setEnabled(notifications_service.unread_count() > 0)

    def show_at(self, global_point: QPoint) -> None:
        self.reload()
        self.move(global_point)
        self.show()
        animation = QPropertyAnimation(self, b"pos", self)
        animation.setDuration(150)
        animation.setStartValue(global_point - QPoint(0, 12))
        animation.setEndValue(global_point)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        animation.start(QPropertyAnimation.DeleteWhenStopped)

    def _open(self, data: dict) -> None:
        notifications_service.mark_read(data["id"])
        self.changed.emit()
        if data["entity"] and data["entity_id"]:
            self.open_entity.emit(data["entity"], data["entity_id"])
        self.hide()

    def _mark_all(self) -> None:
        notifications_service.mark_all_read()
        self.reload()
        self.changed.emit()
