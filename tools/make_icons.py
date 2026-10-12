# -*- coding: utf-8 -*-
"""
Единый набор линейных иконок интерфейса 2.1.

Иконки рисуются кодом в SVG: одна толщина линии, одинаковые скругления,
сетка 24×24 — как в референсе. Для каждой иконки создаются два файла:
обычный (серый) и активный (акцентный синий).

Запуск:  python -m tools.make_icons
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ui.theme import Color  # noqa: E402

OUT = ROOT / "assets" / "icons"

# путь SVG для каждой иконки (сетка 24×24, только обводка)
PATHS: dict[str, str] = {
    # --- разделы приложения (UI Kit, верхние два ряда)
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.4 15.4L21 21"/>',
    "rental-new": '<rect x="3" y="5" width="18" height="15" rx="2.5"/>'
                  '<path d="M7.5 3v4M16.5 3v4M3 9.5h18"/>'
                  '<circle cx="17.5" cy="16.5" r="4.2" fill="none"/>'
                  '<path d="M17.5 14.6v3.8M15.6 16.5h3.8"/>',
    "box": '<path d="M5 3h8l5 5v13a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 4 21V4.5A1.5 1.5 0 0 1 5.5 3z"/>'
           '<path d="M13 3v5h5"/><path d="M7 9h4M7 12.5h6"/>'
           '<circle cx="15" cy="16.5" r="4"/><path d="M13.3 16.6l1.2 1.2 2.2-2.4"/>',
    "return": '<path d="M3.5 9h15l-3.5-3.5"/><path d="M20.5 15h-15l3.5 3.5"/>',
    "folder": '<rect x="3" y="7" width="18" height="13" rx="2"/>'
              '<path d="M9 7V5.5A1.5 1.5 0 0 1 10.5 4h3A1.5 1.5 0 0 1 15 5.5V7"/>'
              '<path d="M9.5 12h5"/>',
    "users": '<circle cx="12" cy="8" r="3.6"/>'
             '<path d="M5 20.5c0-3.9 3.1-6.5 7-6.5s7 2.6 7 6.5"/>',
    "chart": '<rect x="3.5" y="14" width="4" height="6" rx="1"/>'
             '<rect x="10" y="9.5" width="4" height="10.5" rx="1"/>'
             '<rect x="16.5" y="5" width="4" height="15" rx="1"/>',
    "alert": '<path d="M12 3.6l9 15.4a1 1 0 0 1-.9 1.5H3.9a1 1 0 0 1-.9-1.5z"/>'
             '<path d="M12 9.5v4.5M12 17.3v.4"/>',
    # --- действия с реквизитом (нижний ряд UI Kit)
    "settings": '<circle cx="12" cy="12" r="3.4"/>'
                '<path d="M12 3.2l1.7 2.1 2.6-.6.5 2.6 2.4 1.1-1.2 2.4 1.2 2.4-2.4 1.1-.5 2.6-2.6-.6L12 20.8l-1.7-2.1-2.6.6-.5-2.6-2.4-1.1 1.2-2.4-1.2-2.4 2.4-1.1.5-2.6 2.6.6z"/>',
    "front-view": '<path d="M12 2.8l8 4.4v9.6l-8 4.4-8-4.4V7.2z"/><path d="M4 7.2l8 4.4 8-4.4M12 11.6V21"/>',
    "side-view": '<path d="M12 2.8l8 4.4v9.6l-8 4.4-8-4.4V7.2z"/><path d="M4 7.2l8 4.4 8-4.4"/>'
                 '<path d="M12 11.6V21" stroke-dasharray="2.5 2.5"/>',
    "details": '<circle cx="12" cy="12" r="9"/><path d="M12 10.6v6M12 7.7v.4"/>',
    "damage": '<path d="M12 3.6l9 15.4a1 1 0 0 1-.9 1.5H3.9a1 1 0 0 1-.9-1.5z"/>'
              '<path d="M13.4 8.5l-2.6 3.6h2.4l-2.4 4"/>',
    "plus-circle": '<circle cx="12" cy="12" r="9"/><path d="M12 7.8v8.4M7.8 12h8.4"/>',
    "edit": '<path d="M4 20.2l4.6-1.1L20 7.7a2 2 0 0 0 0-2.8l-.9-.9a2 2 0 0 0-2.8 0L4.9 15.4z"/>'
            '<path d="M15.4 6.2l2.4 2.4"/>',
    "wrench": '<path d="M20.2 5.4a5 5 0 0 1-6.6 6.5l-7 7a2.3 2.3 0 0 1-3.3-3.3l7-7a5 5 0 0 1 6.5-6.6l-3.2 3.2 2.9 2.9z"/>',
    "return-stock": '<path d="M20 18.5a6.5 6.5 0 0 0-6.5-6.5H4.5"/><path d="M9 7.5L4 12l5 4.5"/>',
    # --- вспомогательные, в том же стиле
    "grid": '<rect x="3.5" y="3.5" width="7" height="7" rx="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="2"/>'
            '<rect x="3.5" y="13.5" width="7" height="7" rx="2"/><rect x="13.5" y="13.5" width="7" height="7" rx="2"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3.6 6h.01M3.6 12h.01M3.6 18h.01"/>',
    "logout": '<path d="M14.5 4h3.5A2 2 0 0 1 20 6v12a2 2 0 0 1-2 2h-3.5"/><path d="M10 16l-4-4 4-4M6 12h10"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
            '<path d="M8 3v6h7V3M8 21v-6h8v6"/>',
    "bell": '<path d="M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6z"/><path d="M10.5 19a2 2 0 0 0 3 0"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "camera": '<path d="M3 8a2 2 0 0 1 2-2h2.2l1.3-2h6l1.3 2H19a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
              '<circle cx="12" cy="12.5" r="3.6"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M8 3v4M16 3v4M3 10h18"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "arrow-left": '<path d="M15 5l-7 7 7 7"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/>'
           '<circle cx="12" cy="12" r="3.2"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "trash": '<path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>'
             '<path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M10 11v6M14 11v6"/>',
    "printer": '<path d="M6 9V4h12v5"/><rect x="3" y="9" width="18" height="7" rx="2"/><path d="M7 16h10v5H7z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7l8.5 6 8.5-6"/>',
    "file": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5z"/><path d="M14 3v5h5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "tag": '<path d="M3 11.5V5a2 2 0 0 1 2-2h6.5L21 12.5 12.5 21 3 11.5z"/><circle cx="7.5" cy="7.5" r="1.4"/>',
}

TEMPLATE = ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
            'fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
            'stroke-linejoin="round">{body}</svg>')

# В UI Kit разделы-справочники нарисованы серым, действия — синим.
GREY_BY_DEFAULT = {"folder", "users", "chart", "alert", "settings", "return", "damage"}

VARIANTS = {
    "": Color.TEXT_SECONDARY,
    "_active": Color.PRIMARY,
    "_white": "#FFFFFF",
    "_danger": Color.DANGER,
}


def build_all() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, body in PATHS.items():
        for suffix, color in VARIANTS.items():
            (OUT / f"{name}{suffix}.svg").write_text(
                TEMPLATE.format(color=color, body=body), encoding="utf-8")
            count += 1
    return count


if __name__ == "__main__":
    print("создано иконок:", build_all(), "в", OUT)
