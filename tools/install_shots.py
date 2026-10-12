# -*- coding: utf-8 -*-
"""
Рисунки для инструкции по установке.

Схемы окон Windows (установщик Python, Inno Setup, PowerShell, проводник)
рисуются кодом: это не скриншоты, а наглядные схемы с подписями и выносками,
показывающие, куда нажимать.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FONTS = ROOT / "assets" / "fonts"
OUT = ROOT / "docs" / "install"

from tools.ui_mockups import (ACCENT, ACCENT_SOFT, BORDER, GREEN, MUTED, RED,  # noqa: E402
                              SOFT, TEXT, WHITE, font)

WIN_BG = (243, 243, 243)
TITLEBAR = (250, 250, 250)
CONSOLE_BG = (23, 26, 33)
CONSOLE_TEXT = (226, 232, 240)
CONSOLE_GREEN = (134, 239, 172)
CONSOLE_BLUE = (147, 197, 253)


def mono(size: int) -> ImageFont.FreeTypeFont:
    path = FONTS / "DejaVuSansMono.ttf"
    return ImageFont.truetype(str(path), size) if path.exists() else font(size)


class Shot:
    """Холст с примитивами окон Windows."""

    def __init__(self, width: int, height: int, bg=(232, 236, 241)):
        self.img = Image.new("RGB", (width, height), bg)
        self.d = ImageDraw.Draw(self.img)
        self.w, self.h = width, height
        self._mark = 0

    # ----------------------------------------------------------- примитивы
    def text(self, x, y, value, size=15, color=TEXT, bold=False, mono_font=False):
        f = mono(size) if mono_font else font(size, bold)
        self.d.text((x, y), value, font=f, fill=color)

    def window(self, x, y, w, h, title, dark=False):
        """Рамка окна с заголовком и кнопками управления."""
        body = CONSOLE_BG if dark else WIN_BG
        self.d.rounded_rectangle([x, y, x + w, y + h], radius=10, fill=body, outline=BORDER)
        bar = CONSOLE_BG if dark else TITLEBAR
        self.d.rounded_rectangle([x, y, x + w, y + 40], radius=10, fill=bar)
        self.d.rectangle([x, y + 24, x + w, y + 40], fill=bar)
        if not dark:
            self.d.line([x, y + 40, x + w, y + 40], fill=BORDER)
        self.text(x + 16, y + 11, title, 13, CONSOLE_TEXT if dark else TEXT, True)
        # свернуть, развернуть, закрыть
        grey, red = (140, 140, 140), (200, 60, 60)
        close_x = x + w - 26
        self.d.line([close_x - 5, y + 15, close_x + 5, y + 25], fill=red, width=2)
        self.d.line([close_x - 5, y + 25, close_x + 5, y + 15], fill=red, width=2)
        max_x = x + w - 56
        self.d.rectangle([max_x - 5, y + 15, max_x + 5, y + 25], outline=grey, width=2)
        min_x = x + w - 86
        self.d.line([min_x - 5, y + 25, min_x + 5, y + 25], fill=grey, width=2)
        return x, y + 40, w, h - 40

    def button(self, x, y, label, primary=False, width=None, size=13):
        f = font(size, primary)
        tw = self.d.textlength(label, font=f)
        w = width or int(tw + 34)
        self.d.rounded_rectangle([x, y, x + w, y + 36], radius=6,
                                 fill=ACCENT if primary else WHITE,
                                 outline=ACCENT if primary else BORDER)
        self.d.text((x + (w - tw) / 2, y + 9), label, font=f, fill=WHITE if primary else TEXT)
        return w

    def checkbox(self, x, y, label, checked=False, highlight=False, size=13):
        box = 20
        if highlight:
            self.d.rounded_rectangle([x - 8, y - 6, x + 520, y + box + 6], radius=8,
                                     fill=ACCENT_SOFT, outline=ACCENT, width=2)
        self.d.rounded_rectangle([x, y, x + box, y + box], radius=4,
                                 fill=ACCENT if checked else WHITE, outline=ACCENT if checked else MUTED)
        if checked:
            self.d.line([x + 5, y + 10, x + 9, y + 15], fill=WHITE, width=2)
            self.d.line([x + 9, y + 15, x + 15, y + 5], fill=WHITE, width=2)
        self.text(x + box + 12, y + 1, label, size, TEXT, highlight)

    def console(self, x, y, w, h, title, lines):
        """Окно PowerShell с текстом команд и ответов."""
        bx, by, bw, bh = self.window(x, y, w, h, title, dark=True)
        ty = by + 14
        for kind, value in lines:
            color = {"cmd": CONSOLE_BLUE, "ok": CONSOLE_GREEN,
                     "err": (252, 165, 165), "info": CONSOLE_TEXT}[kind]
            prefix = "PS> " if kind == "cmd" else "    "
            self.text(bx + 16, ty, prefix + value, 12, color, mono_font=True)
            ty += 22
        return bx, by, bw, bh

    def explorer(self, x, y, w, h, path, files):
        bx, by, bw, bh = self.window(x, y, w, h, "Проводник")
        self.d.rectangle([bx, by, bx + bw, by + 38], fill=WHITE)
        self.d.line([bx, by + 38, bx + bw, by + 38], fill=BORDER)
        self.text(bx + 14, by + 10, path, 12, MUTED, mono_font=True)
        ry = by + 54
        for name, note, is_folder in files:
            self.d.rounded_rectangle([bx + 12, ry, bx + 34, ry + 20], radius=4,
                                     fill=ACCENT_SOFT if is_folder else WHITE,
                                     outline=ACCENT if is_folder else MUTED)
            self.text(bx + 46, ry + 1, name, 13, TEXT, is_folder)
            self.text(bx + 320, ry + 2, note, 11, MUTED)
            ry += 32
        return bx, by, bw, bh

    def mark(self, x, y, number=None):
        """Красный кружок-выноска."""
        if number is None:
            self._mark += 1
            number = self._mark
        r = 15
        self.d.ellipse([x - r, y - r, x + r, y + r], fill=RED, outline=WHITE, width=3)
        f = font(15, True)
        tw = self.d.textlength(str(number), font=f)
        self.d.text((x - tw / 2, y - 10), str(number), font=f, fill=WHITE)

    def arrow(self, x1, y1, x2, y2, color=RED):
        """Стрелка от выноски к элементу; наконечник направлен в конечную точку."""
        self.d.line([x1, y1, x2, y2], fill=color, width=3)
        dx, dy = x2 - x1, y2 - y1
        length = max(1.0, (dx * dx + dy * dy) ** 0.5)
        ux, uy = dx / length, dy / length
        left = (x2 - 13 * ux - 6 * uy, y2 - 13 * uy + 6 * ux)
        right = (x2 - 13 * ux + 6 * uy, y2 - 13 * uy - 6 * ux)
        self.d.polygon([(x2, y2), left, right], fill=color)

    def note(self, x, y, w, text_value, color=ACCENT, soft=ACCENT_SOFT):
        self.d.rounded_rectangle([x, y, x + w, y + 52], radius=8, fill=soft, outline=color)
        self.text(x + 16, y + 16, text_value, 13, color, True)

    def save(self, name: str) -> Path:
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / name
        self.img.save(path, "PNG")
        return path
