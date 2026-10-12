# -*- coding: utf-8 -*-
"""
Генератор фирменных ресурсов «СКЛАД РЕКВИЗИТА»: знак, логотипы, иконка Windows.

Запуск:  python -m tools.make_brand
Нужны: Pillow и fontTools (только для генерации, в программу не входят),
шрифт Inter (SIL OFL) в assets/fonts.

Идея знака: открытый складской контейнер в изометрии. Правая грань прорезана
тремя вертикальными пазами, как ячейки стеллажа: «хранение + каталог».
Знак строится из шести точек шестиугольника, поэтому одинаково хорошо
масштабируется и до 16 px, и до плаката. Файлы SVG и PNG строятся из одной
геометрии, так что они всегда совпадают.

Результат:
  assets/brand/*.svg, *.png   — логотипы и знак
  assets/app.ico              — иконка Windows (16, 24, 32, 48, 64, 128, 256)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "assets" / "brand"
FONTS = ROOT / "assets" / "fonts"
BOLD = FONTS / "Inter-Bold.otf"
SEMI = FONTS / "Inter-SemiBold.otf"

# палитра бренда (совпадает с theme.py)
BLUE = "#2563EB"
BLUE_DARK = "#1E40AF"
SKY = "#60A5FA"
PALE = "#DBEAFE"
INK = "#172B4D"
WHITE = "#FFFFFF"

TAN = 0.57735  # tan(30°): наклон рёбер изометрии


def _lerp(a: str, b: str, t: float) -> tuple[int, int, int]:
    pa = tuple(int(a[i:i + 2], 16) for i in (1, 3, 5))
    pb = tuple(int(b[i:i + 2], 16) for i in (1, 3, 5))
    return tuple(round(pa[k] + (pb[k] - pa[k]) * t) for k in range(3))


# ----------------------------------------------------------------- геометрия
def mark_geometry(cx: float, cy: float, radius: float, slots: int = 3):
    """Грани знака и пазы. Возвращает словарь многоугольников (списки точек)."""
    half = radius * math.sqrt(3) / 2          # смещение по x до боковых вершин
    top = (cx, cy - radius)
    ur = (cx + half, cy - radius / 2)
    lr = (cx + half, cy + radius / 2)
    bottom = (cx, cy + radius)
    ll = (cx - half, cy + radius / 2)
    ul = (cx - half, cy - radius / 2)
    center = (cx, cy)
    faces = {
        "top": [top, ur, center, ul],
        "left": [ul, center, bottom, ll],
        "right": [center, ur, lr, bottom],
    }

    def y_top(x):    # верхнее ребро правой грани
        return cy - (x - cx) * TAN

    def y_bot(x):    # нижнее ребро правой грани
        return cy + radius - (x - cx) * TAN

    pad_y = radius * 0.17
    gap = half * 0.14
    width = (half - gap * (slots + 1)) / slots
    cut = []
    for i in range(slots):
        x1 = cx + gap * (i + 1) + width * i
        x2 = x1 + width
        cut.append([(x1, y_top(x1) + pad_y), (x2, y_top(x2) + pad_y),
                    (x2, y_bot(x2) - pad_y), (x1, y_bot(x1) - pad_y)])
    return faces, cut


def _pts(points) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in points)


def _path(points) -> str:
    return "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z"


# ---------------------------------------------------------------------- SVG
def mark_svg_body(cx, cy, radius, scheme="color", uid="m", slots=3) -> str:
    """Тело знака без обёртки <svg>. scheme: color | white | mono:<цвет>."""
    faces, cut = mark_geometry(cx, cy, radius, slots)
    stroke = max(radius * 0.045, 0.5)
    parts = []
    if scheme == "color":
        parts.append(
            f'<defs>'
            f'<linearGradient id="{uid}t" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{PALE}"/><stop offset="1" stop-color="{SKY}"/></linearGradient>'
            f'<linearGradient id="{uid}l" x1="0" y1="0" x2="0.4" y2="1">'
            f'<stop offset="0" stop-color="{SKY}"/><stop offset="1" stop-color="{BLUE}"/></linearGradient>'
            f'<linearGradient id="{uid}r" x1="0" y1="0" x2="0.3" y2="1">'
            f'<stop offset="0" stop-color="{BLUE}"/><stop offset="1" stop-color="{BLUE_DARK}"/></linearGradient>'
            f'</defs>')
        fills = {"top": f"url(#{uid}t)", "left": f"url(#{uid}l)", "right": f"url(#{uid}r)"}
        strokes = {"top": SKY, "left": BLUE, "right": BLUE_DARK}
        slot_fill, slot_op = PALE, "0.95"
    elif scheme == "white":
        fills = {"top": WHITE, "left": "#DBEAFE", "right": "#BFDBFE"}
        strokes = fills
        slot_fill, slot_op = BLUE, "0.9"
    else:
        color = scheme.split(":", 1)[1]
        fills = {k: color for k in faces}
        strokes = fills
        slot_fill, slot_op = None, "1"
    if slot_fill is None:
        # монохром: пазы — настоящие отверстия (even-odd), работает на любом фоне
        d = " ".join(_path(p) for p in faces.values()) + " " + " ".join(_path(p) for p in cut)
        parts.append(f'<path d="{d}" fill="{fills["top"]}" fill-rule="evenodd" '
                     f'stroke="{fills["top"]}" stroke-width="{stroke:.2f}" stroke-linejoin="round"/>')
        # перекрытые грани нужны только как контур; отдельные грани не рисуем
        return "".join(parts)
    for name in ("left", "right", "top"):
        parts.append(f'<polygon points="{_pts(faces[name])}" fill="{fills[name]}" '
                     f'stroke="{strokes[name]}" stroke-width="{stroke:.2f}" stroke-linejoin="round"/>')
    for slot in cut:
        parts.append(f'<polygon points="{_pts(slot)}" fill="{slot_fill}" fill-opacity="{slot_op}"/>')
    return "".join(parts)


def text_outline(text: str, font_path: Path, size: float, x: float, baseline: float,
                 tracking: float = 0.0) -> tuple[str, float]:
    """Контуры текста как SVG-путь (шрифт не нужен при показе). Возвращает (d, ширина)."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont

    font = TTFont(str(font_path))
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    upm = font["head"].unitsPerEm
    scale = size / upm
    pen_x = x
    chunks = []
    for ch in text:
        name = cmap.get(ord(ch))
        if name is None:
            continue
        glyph = glyphs[name]
        pen = SVGPathPen(glyphs)
        glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, pen_x, baseline)))
        chunks.append(pen.getCommands())
        pen_x += glyph.width * scale + tracking
    return " ".join(chunks), pen_x - x - tracking


def svg_doc(width, height, body, title="СКЛАД РЕКВИЗИТА", background=None) -> str:
    bg = f'<rect width="{width}" height="{height}" fill="{background}"/>' if background else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{title}">'
            f'<title>{title}</title>{bg}{body}</svg>\n')


def build_svgs() -> dict[str, str]:
    out: dict[str, str] = {}
    out["logo-mark.svg"] = svg_doc(256, 256, mark_svg_body(128, 128, 112, "color", "a"))
    out["logo-mark-mono.svg"] = svg_doc(256, 256, mark_svg_body(128, 128, 112, f"mono:{INK}"))
    out["logo-mark-on-blue.svg"] = svg_doc(
        256, 256, mark_svg_body(128, 128, 112, "white"), background=BLUE)

    def horizontal(text_color, scheme, uid):
        mark = mark_svg_body(60, 60, 52, scheme, uid)
        d1, w1 = text_outline("СКЛАД", BOLD, 54, 132, 62)
        d2, w2 = text_outline("РЕКВИЗИТА", SEMI, 21, 134, 100, tracking=7.4)
        width = int(max(w1 + 132, w2 + 134) + 16)
        body = (mark + f'<path d="{d1}" fill="{text_color}"/><path d="{d2}" fill="{text_color}"/>')
        return width, 120, body

    w, h, body = horizontal(INK, "color", "h")
    out["logo-horizontal.svg"] = svg_doc(w, h, body)
    w, h, body = horizontal(WHITE, "white", "hb")
    out["logo-horizontal-on-blue.svg"] = svg_doc(w, h, body, background=BLUE)
    w, h, body = horizontal(INK, f"mono:{INK}", "hm")
    out["logo-horizontal-mono.svg"] = svg_doc(w, h, body)

    # основной (вертикальный) логотип
    mark = mark_svg_body(210, 120, 104, "color", "f")
    d1, w1 = text_outline("СКЛАД", BOLD, 86, 0, 0)
    d2, w2 = text_outline("РЕКВИЗИТА", SEMI, 34, 0, 0, tracking=13)
    x1 = (420 - w1) / 2
    x2 = (420 - w2) / 2
    d1, _ = text_outline("СКЛАД", BOLD, 86, x1, 320)
    d2, _ = text_outline("РЕКВИЗИТА", SEMI, 34, x2, 378, tracking=13)
    out["logo-full.svg"] = svg_doc(420, 410, mark + f'<path d="{d1}" fill="{INK}"/>'
                                   f'<path d="{d2}" fill="{INK}"/>')

    # иконка приложения: синяя плитка со светлым знаком
    tile = (f'<defs><linearGradient id="ig" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{SKY}"/><stop offset="1" stop-color="{BLUE}"/>'
            f'</linearGradient></defs>'
            f'<rect x="8" y="8" width="240" height="240" rx="56" fill="url(#ig)"/>')
    out["app-icon.svg"] = svg_doc(256, 256, tile + mark_svg_body(128, 128, 82, "white"))
    return out


# --------------------------------------------------------------------- растр
SS = 4  # суперсэмплинг для гладких краёв


def _gradient(size, c1, c2, direction=(1, 1)) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    px = img.load()
    dx, dy = direction
    norm = (abs(dx) * w + abs(dy) * h) or 1
    for y in range(h):
        for x in range(w):
            t = (x * dx + y * dy) / norm
            px[x, y] = _lerp(c1, c2, min(1, max(0, t)))
    return img


def _paint_poly(canvas: Image.Image, points, c1, c2, direction):
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    box = mask.getbbox()
    if not box:
        return
    grad = _gradient((box[2] - box[0], box[3] - box[1]), c1, c2, direction)
    canvas.paste(grad, (box[0], box[1]), mask.crop(box))


def draw_mark(size: int, scheme="color", slots=3, tile=False, tile_color=(BLUE, SKY)) -> Image.Image:
    """Знак (и, при tile=True, плитка иконки) в RGBA заданного размера."""
    big = size * SS
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if tile:
        mask = Image.new("L", (big, big), 0)
        r = int(big * 56 / 256)
        m = int(big * 8 / 256)
        ImageDraw.Draw(mask).rounded_rectangle((m, m, big - m, big - m), radius=r, fill=255)
        bg = _gradient((big, big), tile_color[1], tile_color[0], (1, 1)).convert("RGBA")
        canvas.paste(bg, (0, 0), mask)
    cx = cy = big / 2
    radius = big * (82 / 256 if tile else 112 / 256)
    faces, cut = mark_geometry(cx, cy, radius, slots)
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if scheme == "color":
        _paint_poly(layer, faces["left"], SKY, BLUE, (0.4, 1))
        _paint_poly(layer, faces["right"], BLUE, BLUE_DARK, (0.3, 1))
        _paint_poly(layer, faces["top"], PALE, SKY, (1, 1))
        slot_rgb = _lerp(PALE, PALE, 0)
    elif scheme == "white":
        d = ImageDraw.Draw(layer)
        d.polygon(faces["left"], fill=(219, 234, 254, 255))
        d.polygon(faces["right"], fill=(191, 219, 254, 255))
        d.polygon(faces["top"], fill=(255, 255, 255, 255))
        slot_rgb = _lerp(BLUE, BLUE, 0)
    else:
        color = scheme.split(":", 1)[1]
        rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        d = ImageDraw.Draw(layer)
        for poly in faces.values():
            d.polygon(poly, fill=rgb + (255,))
        slot_rgb = None
    d = ImageDraw.Draw(layer)
    for slot in cut:
        if slot_rgb is None:       # монохром: настоящее отверстие
            hole = Image.new("L", (big, big), 0)
            ImageDraw.Draw(hole).polygon(slot, fill=255)
            layer.paste((0, 0, 0, 0), (0, 0), hole)
        else:
            d.polygon(slot, fill=slot_rgb + (242,))
    canvas.alpha_composite(layer)
    return canvas.resize((size, size), Image.LANCZOS)


def draw_text(img: Image.Image, xy, text, font_path, size, fill, tracking=0.0):
    font = ImageFont.truetype(str(font_path), size)
    d = ImageDraw.Draw(img)
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + tracking
    return x - xy[0] - tracking


def build_pngs() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    draw_mark(512).save(BRAND / "logo-mark.png")
    draw_mark(512, f"mono:{INK}").save(BRAND / "logo-mark-mono.png")
    draw_mark(256, tile=True, scheme="white").save(BRAND / "app-icon-256.png")

    # горизонтальный логотип 4x для чёткого текста
    k = 4
    w, h = 560 * k, 120 * k
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    mark = draw_mark(120 * k, "color")
    img.alpha_composite(mark, (0, 0))
    ink = tuple(int(INK[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
    draw_text(img, (132 * k, 62 * k), "СКЛАД", BOLD, 54 * k, ink)
    draw_text(img, (134 * k, 100 * k), "РЕКВИЗИТА", SEMI, 21 * k, ink, tracking=7.4 * k)
    img = img.crop(img.getbbox()).resize((0, 0)) if False else img
    img.resize((w // 2, h // 2), Image.LANCZOS).save(BRAND / "logo-horizontal.png")

    # иконка Windows: каждый размер рисуется отдельно, на малых — два крупных паза
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for s in sizes:
        slots = 2 if s <= 32 else 3
        frames.append(draw_mark(s, "white", slots=slots, tile=True))
    frames[-1].save(ROOT / "assets" / "app.ico", format="ICO",
                    sizes=[(s, s) for s in sizes], append_images=frames[:-1])


def main() -> int:
    BRAND.mkdir(parents=True, exist_ok=True)
    for name, text in build_svgs().items():
        (BRAND / name).write_text(text, encoding="utf-8")
        print("  svg", name)
    build_pngs()
    print("  png/ico готово")
    return 0


if __name__ == "__main__":
    sys.exit(main())
