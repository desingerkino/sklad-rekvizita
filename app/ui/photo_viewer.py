# -*- coding: utf-8 -*-
"""
Просмотр фотографий реквизита во весь экран.

На складе решают детали: скол на ножке, потёртость, клеймо на обороте. В
маленьком окне карточки их не видно, поэтому снимок должен открываться
крупно, с перелистыванием стрелками и колесом мыши.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..services.photo_service import PHOTO_KINDS
from .theme import Space


class PhotoViewer(QDialog):
    """Крупный просмотр списка фотографий, начиная с выбранной."""

    def __init__(self, photos: list[dict], index: int = 0, parent=None):
        super().__init__(parent)
        self.photos = photos or []
        self.index = max(0, min(index, len(self.photos) - 1)) if self.photos else 0
        self.setWindowTitle("Фотография реквизита")
        self.setModal(True)
        self.resize(980, 760)
        self._build()
        self._render()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        root.setSpacing(Space.SM)

        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(600, 500)
        root.addWidget(self.image, 1)

        self.caption = QLabel("")
        self.caption.setObjectName("Muted")
        self.caption.setAlignment(Qt.AlignCenter)
        root.addWidget(self.caption)

        buttons = QHBoxLayout()
        self.prev_btn = QPushButton("‹  Предыдущая")
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        buttons.addWidget(self.prev_btn)
        buttons.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.setObjectName("Primary")
        close_btn.setMinimumWidth(140)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        buttons.addStretch()
        self.next_btn = QPushButton("Следующая  ›")
        self.next_btn.clicked.connect(lambda: self.step(1))
        buttons.addWidget(self.next_btn)
        root.addLayout(buttons)

        # стрелки на клавиатуре — привычнее любых кнопок
        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.step(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.step(1))

    def step(self, delta: int) -> None:
        if len(self.photos) < 2:
            return
        self.index = (self.index + delta) % len(self.photos)
        self._render()

    def wheelEvent(self, event) -> None:                # noqa: N802 — метод Qt
        self.step(-1 if event.angleDelta().y() > 0 else 1)

    def resizeEvent(self, event) -> None:               # noqa: N802 — метод Qt
        super().resizeEvent(event)
        self._render()

    def _render(self) -> None:
        if not self.photos:
            self.image.setText("Фотографий нет")
            self.caption.setText("")
            return
        photo = self.photos[self.index]
        pixmap = QPixmap(photo["abs_path"])
        if pixmap.isNull():
            self.image.setText("Файл фотографии не читается")
        else:
            area = self.image.size()
            self.image.setPixmap(pixmap.scaled(
                max(200, area.width()), max(200, area.height()),
                Qt.KeepAspectRatio, Qt.SmoothTransformation))
        kind = PHOTO_KINDS.get(photo.get("kind", ""), photo.get("kind", ""))
        mark = "  ·  основная" if photo.get("is_primary") else ""
        self.caption.setText(f"{self.index + 1} из {len(self.photos)}  ·  {kind}{mark}")
        many = len(self.photos) > 1
        self.prev_btn.setVisible(many)
        self.next_btn.setVisible(many)
