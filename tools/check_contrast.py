# -*- coding: utf-8 -*-
"""
Проверка контраста пар «текст на фоне» из токенов темы (формула WCAG 2.x).

Запуск:  python -m tools.check_contrast
Код выхода 1, если хотя бы одна пара ниже нормы. Qt не нужен.

Нормы: информационный текст >= 4.5:1; границы интерактивных полей и
индикаторы >= 3:1. Пары «декоративные границы» только выводятся для справки.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ui.theme import STATUS_TONES, Color  # noqa: E402


def _lin(v: float) -> float:
    v /= 255
    return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def pairs() -> list[tuple[str, str, str, float]]:
    """(название, цвет переднего плана, фон, норма)."""
    c = Color
    out = [
        ("текст на поверхности", c.TEXT, c.SURFACE, 4.5),
        ("текст на фоне", c.TEXT, c.BG, 4.5),
        ("второстепенный текст на поверхности", c.TEXT_SECONDARY, c.SURFACE, 4.5),
        ("второстепенный текст на фоне", c.TEXT_SECONDARY, c.BG, 4.5),
        ("приглушённый текст на поверхности", c.TEXT_MUTED, c.SURFACE, 4.5),
        ("приглушённый текст на фоне", c.TEXT_MUTED, c.BG, 4.5),
        ("текст на главной кнопке", c.TEXT_ON_PRIMARY, c.PRIMARY, 4.5),
        ("акцент-ссылка на поверхности", c.PRIMARY, c.SURFACE, 4.5),
        ("акцент на мягком фоне", c.PRIMARY, c.PRIMARY_SOFT, 4.5),
        ("граница поля к поверхности", c.INPUT_BORDER, c.SURFACE, 3.0),
        ("кольцо фокуса к поверхности", c.FOCUS, c.SURFACE, 3.0),
    ]
    for name, (fg, bg, _border) in STATUS_TONES.items():
        out.append((f"статус «{name}»", fg, bg, 4.5))
    # текст отключённой кнопки: при наличии токена
    disabled = (getattr(c, "TEXT_ON_DISABLED", None), getattr(c, "DISABLED_BG", None))
    if all(disabled):
        out.append(("текст отключённой кнопки", disabled[0], disabled[1], 4.5))
    return out


def main() -> int:
    bad = 0
    for name, fg, bg, need in pairs():
        got = ratio(fg, bg)
        ok = got >= need
        bad += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {got:5.2f} (нужно {need}) {name}: {fg} на {bg}")
    print(f"\nНиже нормы: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
