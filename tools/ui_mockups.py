# -*- coding: utf-8 -*-
"""
Отрисовка схем экранов программы для руководства пользователя.

Это не скриншоты: изображения строятся кодом по фактической разметке
интерфейса (те же разделы меню, колонки таблиц, названия кнопок и цвета темы,
что и в app/ui). Используются, чтобы показать расположение элементов.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FONTS = ROOT / "assets" / "fonts"

from app.ui.theme import Color  # noqa: E402


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


# Цвета берутся из той же дизайн-системы, что и приложение, — схемы
# в руководстве не расходятся с тем, что видит пользователь на экране.
BG = rgb(Color.BG)
WHITE = rgb(Color.SURFACE)
SIDEBAR = rgb(Color.SIDEBAR)
SIDEBAR_HOVER = rgb(Color.HOVER)
ACCENT = rgb(Color.PRIMARY)
ACCENT_SOFT = rgb(Color.PRIMARY_SOFT)
TEXT = rgb(Color.TEXT)
MUTED = rgb(Color.TEXT_SECONDARY)
BORDER = rgb(Color.BORDER)
HEADER_BG = rgb(Color.SURFACE)
GREEN = rgb(Color.SUCCESS)
GREEN_SOFT = rgb(Color.SUCCESS_SOFT)
ORANGE = rgb(Color.WARNING)
ORANGE_SOFT = rgb(Color.WARNING_SOFT)
RED = rgb(Color.DANGER)
GREY = rgb(Color.NEUTRAL)
SOFT = rgb(Color.SURFACE_ALT)

# В самой программе пункты меню и кнопки идут со значками (🔍, ➕, 📦, 📷, 💾).
# В схемах значки опущены: шрифт документа их не содержит.
# Структура меню версии 2.3.1: группы + разделы (как в приложении)
MENU_GROUPS = [
    ("ОСНОВНЫЕ", ["Главная", "Каталог", "Новая аренда", "Аренды", "Возвраты", "История"]),
    ("КЛИЕНТЫ", ["Физ. лица", "Юр. лица", "Проекты"]),
    ("АНАЛИТИКА", ["Отчёты"]),
    ("УПРАВЛЕНИЕ", ["Настройки"]),
]
MENU = [item for _, items in MENU_GROUPS for item in items]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


class Canvas:
    def __init__(self, width: int, height: int, bg=BG):
        self.img = Image.new("RGB", (width, height), bg)
        self.d = ImageDraw.Draw(self.img)
        self.w, self.h = width, height

    # ---------------------------------------------------------- примитивы
    def box(self, x, y, w, h, fill=WHITE, border=BORDER, radius=14):
        self.d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=border)

    def text(self, x, y, value, size=15, color=TEXT, bold=False):
        self.d.text((x, y), value, font=font(size, bold), fill=color)

    def button(self, x, y, label, primary=False, size=14, width=None, disabled=False):
        f = font(size, primary)
        tw = self.d.textlength(label, font=f)
        w = width or int(tw + 28)
        h = 34
        if disabled:
            fill, border, color = (241, 243, 245), BORDER, (154, 165, 173)
        elif primary:
            fill, border, color = ACCENT, ACCENT, WHITE
        else:
            fill, border, color = WHITE, BORDER, TEXT
        self.d.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=fill, outline=border)
        self.d.text((x + (w - tw) / 2, y + (h - size) / 2 - 2), label, font=f, fill=color)
        return w

    def buttons(self, x, y, items, gap=10):
        """items: [(подпись, primary, disabled), ...]  →  [(x, ширина), ...]"""
        cur, placed = x, []
        for label, primary, disabled in items:
            w = self.button(cur, y, label, primary, disabled=disabled)
            placed.append((cur, w))
            cur += w + gap
        return placed

    def mark(self, x, y, number=None):
        """Выноска в левом верхнем углу элемента, чтобы не закрывать подпись."""
        self.callout(x - 13, y - 13, number)

    def field(self, x, y, w, label, value="", size=14, placeholder=False):
        self.d.rounded_rectangle([x, y, x + w, y + 32], radius=10, fill=WHITE, outline=BORDER)
        color = MUTED if placeholder else TEXT
        self.d.text((x + 10, y + 7), value or label, font=font(size), fill=color)

    def form_row(self, x, y, label, value, label_w=200, field_w=320, placeholder=False):
        self.d.text((x, y + 7), label, font=font(14), fill=TEXT, anchor="la")
        self.field(x + label_w, y, field_w, "", value, placeholder=placeholder)
        return y + 42

    def table(self, x, y, w, headers, rows, widths, row_h=32, colors=None, header_h=34):
        total = sum(widths)
        if total > w:                       # колонки всегда вписываются в область
            widths = [int(cw * w / total) for cw in widths]
        self.d.rounded_rectangle([x, y, x + w, y + header_h + row_h * len(rows)],
                                 radius=14, fill=WHITE, outline=BORDER)
        self.d.line([x + 8, y + header_h, x + w - 8, y + header_h], fill=BORDER)
        cx = x
        for head, cw in zip(headers, widths):
            self.d.text((cx + 8, y + 9), head, font=font(12, True), fill=TEXT)
            cx += cw
        for i, row in enumerate(rows):
            ry = y + header_h + i * row_h
            self.d.line([x + 8, ry, x + w - 8, ry], fill=BORDER)
            cx = x
            row_color = (colors or {}).get(i)
            for cell, cw in zip(row, widths):
                self.d.text((cx + 8, ry + 8), str(cell), font=font(12), fill=row_color or TEXT)
                cx += cw
        return y + header_h + row_h * len(rows)

    def badge(self, x, y, label, color, soft=None):
        """«Пилюля» статуса: цветной текст на мягкой подложке."""
        f = font(11, True)
        tw = self.d.textlength(label, font=f)
        background = soft or SOFT
        self.d.rounded_rectangle([x, y, x + tw + 22, y + 26], radius=6,
                                 fill=background, outline=color)
        self.d.text((x + 11, y + 6), label, font=f, fill=color)
        return tw + 22

    def donut(self, x, y, size, percent, color=None):
        """Круговой индикатор доступности склада."""
        color = color or ACCENT
        thickness = max(8, size // 11)
        self.d.ellipse([x, y, x + size, y + size], outline=SOFT, width=thickness)
        self.d.arc([x, y, x + size, y + size], start=-90,
                   end=-90 + int(360 * percent / 100), fill=color, width=thickness)
        f = font(int(size / 4.4), True)
        label = f"{percent}%"
        tw = self.d.textlength(label, font=f)
        self.d.text((x + size / 2 - tw / 2, y + size / 2 - size / 7), label, font=f, fill=TEXT)

    def avatar(self, x, y, size, initials="МИ"):
        self.d.ellipse([x, y, x + size, y + size], fill=ACCENT_SOFT)
        f = font(int(size / 2.6), True)
        tw = self.d.textlength(initials, font=f)
        self.d.text((x + size / 2 - tw / 2, y + size / 2 - size / 3.4), initials,
                    font=f, fill=ACCENT)

    def icon_ph(self, x, y, size=18, color=None):
        """Условная иконка: квадрат со скруглением в стиле линейного набора."""
        self.d.rounded_rectangle([x, y, x + size, y + size], radius=5,
                                 outline=color or MUTED, width=2)

    def photo_ph(self, x, y, w, h, label="фото"):
        self.d.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=SOFT, outline=BORDER)
        f = font(12)
        tw = self.d.textlength(label, font=f)
        self.d.text((x + (w - tw) / 2, y + h / 2 - 8), label, font=f, fill=(144, 164, 174))

    def callout(self, x, y, number: int | None = None):
        """Кружок-выноска. Номера идут по порядку вызовов — так же, как в тексте."""
        if number is None:
            self._callout_no = getattr(self, "_callout_no", 0) + 1
            number = self._callout_no
        r = 13
        self.d.ellipse([x, y, x + r * 2, y + r * 2], fill=RED, outline=WHITE, width=2)
        f = font(13, True)
        tw = self.d.textlength(str(number), font=f)
        self.d.text((x + r - tw / 2, y + r - 9), str(number), font=f, fill=WHITE)

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.img.save(path, "PNG")
        return path


# ------------------------------------------------------------------ каркасы
def window(title: str, active: int = 0, width=1500, height=940,
           user="Иван Петров", role="Администратор",
           subtitle="Раздел программы") -> tuple[Canvas, int, int, int]:
    """Главное окно версии 2.3.1: меню с группами, профиль и состояние склада."""
    c = Canvas(width, height)
    sw = 276
    c.d.rectangle([0, 0, sw, height], fill=SIDEBAR)
    c.d.line([sw, 0, sw, height], fill=BORDER)

    c.d.rounded_rectangle([24, 24, 56, 56], radius=9, outline=ACCENT, width=2)
    c.text(70, 22, "СКЛАД", 15, TEXT, True)
    c.text(70, 42, "РЕКВИЗИТА", 15, TEXT, True)
    c.text(70, 64, "версия 2.3.1", 10, MUTED)

    y = 100
    index = 0
    for group, items in MENU_GROUPS:
        c.text(26, y, group, 9, MUTED, True)
        y += 22
        for item in items:
            if index == active:
                c.d.rounded_rectangle([12, y, sw - 12, y + 38], radius=10, fill=ACCENT_SOFT)
            c.icon_ph(26, y + 10, 18, ACCENT if index == active else MUTED)
            c.text(56, y + 10, item, 13, ACCENT if index == active else MUTED, index == active)
            y += 40
            index += 1
        y += 8

    card_y = height - 312
    c.d.rounded_rectangle([16, card_y, sw - 16, card_y + 86], radius=10, fill=WHITE, outline=BORDER)
    c.avatar(30, card_y + 14, 46, "МИ")
    c.text(88, card_y + 16, user, 13, TEXT, True)
    c.text(88, card_y + 36, "Художник по реквизиту", 10, MUTED)
    c.badge(88, card_y + 54, role, ACCENT, ACCENT_SOFT)

    stat_y = card_y + 100
    c.d.rounded_rectangle([16, stat_y, sw - 16, stat_y + 130], radius=10, fill=WHITE, outline=BORDER)
    c.text(32, stat_y + 12, "СОСТОЯНИЕ СКЛАДА", 9, MUTED, True)
    c.text(32, stat_y + 34, "Всего на складе", 10, MUTED)
    c.text(32, stat_y + 50, "1 248", 19, TEXT, True)
    c.text(32, stat_y + 78, "Доступно", 10, MUTED)
    c.text(32, stat_y + 94, "824", 16, GREEN, True)
    c.donut(sw - 132, stat_y + 22, 100, 66)

    c.text(32, height - 62, "Сохранить всё", 12, MUTED)
    c.text(32, height - 38, "Выйти", 12, MUTED)

    # верхняя панель: поиск с камерой, уведомления, главное действие
    c.d.rectangle([sw, 0, width, 78], fill=WHITE)
    c.d.line([sw, 78, width, 78], fill=BORDER)
    c.text(sw + 24, 16, title, 19, TEXT, True)
    c.text(sw + 24, 46, subtitle, 10, MUTED)

    search_x = width - 700
    c.d.rounded_rectangle([search_x, 18, search_x + 360, 60], radius=10, fill=SOFT, outline=BORDER)
    c.icon_ph(search_x + 14, 30, 18, MUTED)
    c.text(search_x + 44, 31, "Поиск реквизита…", 13, MUTED)
    c.icon_ph(search_x + 326, 30, 18, ACCENT)

    bell_x = width - 300
    c.d.rounded_rectangle([bell_x, 18, bell_x + 42, 60], radius=10, fill=WHITE, outline=BORDER)
    c.icon_ph(bell_x + 12, 30, 18, MUTED)
    c.d.ellipse([bell_x + 26, 20, bell_x + 44, 38], fill=RED)
    f = font(9, True)
    c.d.text((bell_x + 32, 24), "3", font=f, fill=WHITE)

    c.d.rounded_rectangle([width - 240, 18, width - 24, 60], radius=10, fill=ACCENT)
    c.text(width - 214, 31, "+  Новая аренда", 12, WHITE, True)

    c.d.rectangle([sw, height - 34, width, height], fill=WHITE)
    c.d.line([sw, height - 34, width, height - 34], fill=BORDER)
    c.text(sw + 16, height - 25, "Все изменения сохранены · 05.09.2026 14:32", 11, GREEN)
    c.text(width - 660, height - 25,
           "Пользователь: Иван Петров (Администратор) · Предметов: 1 248 · "
           "Действующих аренд: 17 · 05.09.2026 14:32", 11, MUTED)
    return c, sw + 24, 106, width - 24


def dialog(title: str, width=980, height=680) -> Canvas:
    c = Canvas(width, height, BG)
    c.d.rectangle([0, 0, width, 62], fill=WHITE)
    c.d.line([0, 62, width, 62], fill=BORDER)
    c.text(24, 20, title, 17, TEXT, True)
    return c
