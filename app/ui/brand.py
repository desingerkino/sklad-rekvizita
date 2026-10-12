# -*- coding: utf-8 -*-
"""Фирменные ресурсы: шрифт Inter и знак «СКЛАД РЕКВИЗИТА»."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPixmap

from ..paths import assets_dir

INTER_FILES = ("Inter-Regular.otf", "Inter-Medium.otf", "Inter-SemiBold.otf", "Inter-Bold.otf")


def load_fonts() -> bool:
    """Подключает Inter из assets/fonts (лицензия SIL OFL, есть кириллица).

    Шрифт идёт вместе с программой, поэтому вид одинаков на любом компьютере.
    Если файлов нет, интерфейс остаётся на системном шрифте из стека в theme.py.
    """
    loaded = 0
    for name in INTER_FILES:
        path = assets_dir() / "fonts" / name
        if path.exists() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded += 1
    return loaded > 0


def default_font() -> QFont:
    font = QFont("Inter", 10)
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def mark_path() -> Path:
    return assets_dir() / "brand" / "logo-mark.svg"


def mark_pixmap(size: int) -> QPixmap | None:
    """Знак бренда нужного размера или None, если файл не найден."""
    path = mark_path()
    if not path.exists():
        return None
    pixmap = QIcon(str(path)).pixmap(size, size)
    return None if pixmap.isNull() else pixmap
