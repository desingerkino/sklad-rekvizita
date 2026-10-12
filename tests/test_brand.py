# -*- coding: utf-8 -*-
"""Фирменные ресурсы на месте и пригодны к использованию.

Запуск:  python -m tests.test_brand
"""
from __future__ import annotations

import sys
import xml.dom.minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def main() -> int:
    brand = ROOT / "assets" / "brand"
    print("=== Логотип и иконка ===")
    for name in ("logo-mark.svg", "logo-mark-mono.svg", "logo-mark-on-blue.svg",
                 "logo-horizontal.svg", "logo-horizontal-on-blue.svg",
                 "logo-horizontal-mono.svg", "logo-full.svg", "app-icon.svg"):
        path = brand / name
        ok = path.exists()
        if ok:
            try:
                xml.dom.minidom.parse(str(path))
            except Exception as exc:                      # noqa: BLE001
                ok = False
                print("   ", name, exc)
        check(f"{name}: файл есть и это корректный SVG", ok)
    for name in ("logo-mark.png", "logo-horizontal.png", "app-icon-256.png"):
        check(f"{name} есть", (brand / name).exists())

    from PIL import Image
    ico = Image.open(ROOT / "assets" / "app.ico")
    sizes = sorted(ico.info.get("sizes", []))
    check("Иконка Windows содержит размеры 16–256", sizes == [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)],
          str(sizes))

    print("\n=== Шрифт Inter ===")
    fonts = ROOT / "assets" / "fonts"
    for name in ("Inter-Regular.otf", "Inter-Medium.otf", "Inter-SemiBold.otf", "Inter-Bold.otf"):
        check(f"{name} лежит в assets/fonts", (fonts / name).exists())
    check("Лицензия шрифта приложена", (fonts / "Inter-LICENSE.txt").exists())
    from app.ui import theme
    check("Inter первый в стеке шрифтов", theme.Font.FAMILY.startswith("'Inter'"))
    spec = (ROOT / "build" / "sklad.spec").read_text(encoding="utf-8")
    check("Папка assets (шрифты, логотип) попадает в сборку", '(str(ROOT / "assets"), "assets")' in spec)

    print(f"\nПройдено: {len(PASSED)}, не пройдено: {len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
