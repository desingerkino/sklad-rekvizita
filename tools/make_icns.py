# -*- coding: utf-8 -*-
"""Готовит папку assets/app.iconset для macOS (iconutil превращает её в app.icns).
Те же фирменные знак и плитка, что и в иконке Windows; каждый размер рисуется отдельно."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.make_brand import draw_mark  # noqa: E402

SIZES = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2),
         (512, 1), (512, 2)]


def main() -> int:
    out = ROOT / "assets" / "app.iconset"
    out.mkdir(parents=True, exist_ok=True)
    for base, scale in SIZES:
        px = base * scale
        slots = 2 if px <= 32 else 3
        name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
        draw_mark(px, "white", slots=slots, tile=True).save(out / name)
    print("iconset:", len(list(out.glob("*.png"))), "файлов")
    return 0


if __name__ == "__main__":
    sys.exit(main())
