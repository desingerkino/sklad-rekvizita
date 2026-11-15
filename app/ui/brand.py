# -*- coding: utf-8 -*-
"""Фирменные ресурсы: шрифт Onest и знак «СКЛАД РЕКВИЗИТА»."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPixmap

from ..paths import assets_dir

def load_fonts() -> bool:
    """
    Подключает Onest из assets/fonts — единственную гарнитуру интерфейса.

    Шрифт идёт вместе с программой: установленный в системе Onest не нужен, а
    вид одинаков на Windows и macOS. Если файлов нет (повреждённая установка),
    интерфейс остаётся на системном шрифте из стека в theme.py — программа
    при этом работает.
    """
    from . import theme

    loaded = set()
    for name in theme.Font.FILES:
        path = assets_dir() / "fonts" / name
        if path.exists() and QFontDatabase.addApplicationFont(str(path)) >= 0:
            loaded.add(name)
    theme.Font.use_semibold("Onest-SemiBold.ttf" in loaded)
    return "Onest-Regular.ttf" in loaded


def default_font() -> QFont:
    from . import theme

    font = QFont(theme.Font.NAME)
    # размер в пикселях: в пунктах он на macOS получается на четверть мельче
    font.setPixelSize(theme.px(theme.Font.BODY))
    font.setStyleStrategy(QFont.PreferAntialias)
    return font


def ui_font(points: float, weight: int = 400) -> QFont:
    """Шрифт интерфейса заданного размера (в пунктах дизайн-системы) и начертания."""
    from . import theme

    font = QFont(theme.Font.NAME)
    font.setPixelSize(theme.px(points))
    font.setWeight(QFont.Weight(weight))
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
