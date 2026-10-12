# -*- coding: utf-8 -*-
"""Круговой индикатор доступности склада: доступно / всего × 100."""
from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QRectF, Qt, Property)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import Color, Duration


class CircularProgress(QWidget):
    """Кольцо с процентом в центре. Значение меняется плавно."""

    def __init__(self, parent=None, size: int = 116, thickness: int = 10):
        super().__init__(parent)
        self._value = 0.0
        self._size = size
        self._thickness = thickness
        self.setFixedSize(size, size)
        self._animation = QPropertyAnimation(self, b"value", self)
        self._animation.setDuration(Duration.SLOW)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

    def get_value(self) -> float:
        return self._value

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(100.0, float(value)))
        self.update()

    value = Property(float, get_value, set_value)

    def animate_to(self, percent: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._value)
        self._animation.setEndValue(max(0.0, min(100.0, float(percent))))
        self._animation.start()

    def _tone(self) -> str:
        if self._value >= 60:
            return Color.SUCCESS
        if self._value >= 25:
            return Color.WARNING
        return Color.DANGER

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        margin = self._thickness / 2 + 1
        rect = QRectF(margin, margin, self.width() - 2 * margin, self.height() - 2 * margin)

        track = QPen(QColor(Color.NEUTRAL_SOFT), self._thickness, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(track)
        painter.drawArc(rect, 0, 360 * 16)

        arc = QPen(QColor(self._tone()), self._thickness, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(arc)
        painter.drawArc(rect, 90 * 16, -int(360 * 16 * self._value / 100))

        painter.setPen(QColor(Color.TEXT))
        font = QFont(self.font())
        font.setPointSizeF(self._size / 5.2)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, f"{int(round(self._value))}%")
        painter.end()
