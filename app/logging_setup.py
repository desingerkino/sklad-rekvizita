# -*- coding: utf-8 -*-
"""Логирование технических ошибок в файл. Пользователь логов не видит."""
from __future__ import annotations

import logging
import logging.handlers
import sys
import traceback

from . import paths

_SENSITIVE = ("passport", "паспорт", "password", "пароль", "smtp_password")


class _PrivacyFilter(logging.Filter):
    """Грубая защита от попадания персональных данных и паролей в лог."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = str(record.getMessage()).lower()
        except Exception:
            return True
        for word in _SENSITIVE:
            if word in msg:
                record.msg = "[запись скрыта: содержит персональные данные или пароль]"
                record.args = ()
                break
        return True


def setup(level: int = logging.INFO) -> logging.Logger:
    paths.ensure_dirs()
    logger = logging.getLogger()
    if logger.handlers:
        return logger
    logger.setLevel(level)
    handler = logging.handlers.RotatingFileHandler(
        paths.LOGS_DIR / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    handler.addFilter(_PrivacyFilter())
    logger.addHandler(handler)

    # сторонние библиотеки на уровне DEBUG выводят содержимое документов
    # и изображений — в журнал программы это попадать не должно
    for noisy in ("pdfminer", "PIL", "reportlab", "urllib3", "matplotlib", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    def _excepthook(exc_type, exc, tb):
        logger.critical("Необработанная ошибка:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            from .ui.errors import show_fatal
            show_fatal(exc)
        except Exception:
            pass

    sys.excepthook = _excepthook
    return logger
