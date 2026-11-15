# -*- coding: utf-8 -*-
"""
Короткое уведомление о результате действия.

«Сохранено», «Перенесено 12 предметов» — такие сообщения не должны
останавливать работу окном с кнопкой «ОК». Уведомление появляется внизу окна
на несколько секунд и исчезает само; опасные действия и ошибки по-прежнему
показываются диалогами.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        host.installEventFilter(self)

    def show_message(self, text: str, kind: str = "ok", msecs: int = 3500) -> None:
        self.setProperty("kind", kind)
        self.style().unpolish(self)
        self.style().polish(self)
        self.setText(text)
        self._place()
        self.show()
        self.raise_()
        self._timer.start(msecs)

    def _place(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        width = min(560, max(240, host.width() - 80))
        self.setFixedWidth(width)
        self.adjustSize()
        self.move((host.width() - self.width()) // 2, host.height() - self.height() - 56)

    def eventFilter(self, watched, event) -> bool:           # noqa: N802 — интерфейс Qt
        if event.type() == QEvent.Resize and self.isVisible():
            self._place()
        return super().eventFilter(watched, event)


def toast(widget: QWidget, text: str, kind: str = "ok") -> None:
    """
    Показывает уведомление в окне, которому принадлежит widget.

    kind: 'ok' — успешно, 'info' — к сведению, 'warn' — внимание.
    Если окно ещё не показано (тесты, служебные вызовы), ничего не делает.
    """
    window = widget.window() if widget is not None else None
    if window is None:
        return
    holder = getattr(window, "_toast", None)
    if holder is None:
        holder = Toast(window)
        window._toast = holder
    holder.show_message(text, kind)
    # текст дублируется в строке состояния главного окна: его можно перечитать
    bar = getattr(window, "statusBar", None)
    if callable(bar):
        try:
            window.statusBar().showMessage(text, 8000)
        except Exception:                                    # noqa: BLE001
            pass
