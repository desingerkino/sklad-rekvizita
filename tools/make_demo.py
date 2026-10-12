# -*- coding: utf-8 -*-
"""
Минутный обзорный ролик программы.

Это не запись экрана: PySide6 в среде сборки не запускается. Ролик собирается
из тех же схем экранов, что и руководство, — с плавным наездом камеры,
курсором, подписями и переходами. Кадры отдаются ffmpeg потоком, промежуточные
файлы не создаются.

Запуск:  python -m tools.make_demo [путь к mp4]
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ui.theme import Color  # noqa: E402
from app.version import DISPLAY_NAME, VERSION  # noqa: E402

SCREENS = ROOT / "docs" / "screens"
FONTS = ROOT / "assets" / "fonts"
WIDTH, HEIGHT, FPS = 1600, 900, 24
INTRO, OUTRO = 3.5, 3.5   # заставка и финал; всего ролик — около минуты
CAPTION_H = 96


def rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


BG = rgb(Color.BG)
INK = rgb(Color.TEXT)
MUTED = rgb(Color.TEXT_SECONDARY)
ACCENT = rgb(Color.PRIMARY)
WHITE = (255, 255, 255)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


def ease(t: float) -> float:
    """Плавное начало и завершение движения."""
    return t * t * (3 - 2 * t)


@dataclass
class Scene:
    """Один эпизод ролика."""
    screen: str
    seconds: float
    title: str
    note: str = ""
    start_box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    end_box: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    cursor: list[tuple[float, float]] = field(default_factory=list)
    clicks: list[float] = field(default_factory=list)


STORYBOARD: list[Scene] = [
    Scene("01_login.png", 4.0, "Вход в программу",
          "Экран входа открывается первым: сотрудник и пароль",
          (0.02, 0.02, 0.98, 0.98), (0.08, 0.06, 0.92, 0.94),
          cursor=[(0.55, 0.47), (0.62, 0.72)], clicks=[0.75]),
    Scene("15_dashboard.png", 6.0, "Главная страница",
          "Состояние склада, срочные аренды и последние операции",
          (0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 0.72, 0.72),
          cursor=[(0.2, 0.25), (0.42, 0.55)]),
    Scene("03_catalog.png", 6.5, "Каталог и поиск",
          "Карточки с фотографиями, статусы и цена за 14 дней",
          (0.14, 0.05, 0.94, 0.75), (0.0, 0.0, 1.0, 1.0),
          cursor=[(0.35, 0.12), (0.55, 0.35)], clicks=[0.2]),
    Scene("04_item_card.png", 5.0, "Карточка реквизита",
          "Фото, остатки, история аренд и ремонтов",
          (0.0, 0.0, 0.8, 0.8), (0.05, 0.05, 1.0, 1.0),
          cursor=[(0.25, 0.3), (0.3, 0.62)]),
    Scene("05_new_rental.png", 5.5, "Новая аренда",
          "Клиент подставляется сам, расчёт обновляется на лету",
          (0.0, 0.0, 1.0, 0.7), (0.1, 0.25, 1.0, 1.0),
          cursor=[(0.2, 0.2), (0.78, 0.8)], clicks=[0.85]),
    Scene("07_rental_card.png", 5.5, "Карточка аренды",
          "У каждой позиции свой 14-дневный период и своя пролонгация",
          (0.0, 0.05, 0.85, 0.75), (0.1, 0.2, 1.0, 0.95),
          cursor=[(0.3, 0.55), (0.6, 0.62)]),
    Scene("18_returns.png", 4.5, "Возвраты",
          "Принять можно часть количества — остатки пересчитаются сразу",
          (0.05, 0.05, 0.95, 0.8), (0.0, 0.1, 1.0, 1.0),
          cursor=[(0.3, 0.4), (0.25, 0.72)], clicks=[0.8]),
    Scene("16_reports.png", 6.5, "Отчёты",
          "Календарь аренд, поиск, фильтры и статистика за период",
          (0.0, 0.0, 1.0, 0.55), (0.0, 0.25, 0.9, 1.0),
          cursor=[(0.25, 0.2), (0.55, 0.6)]),
    Scene("17_notifications.png", 3.5, "Уведомления",
          "Сроки, просрочки и пролонгации — с переходом к аренде",
          (0.05, 0.05, 0.95, 0.95), (0.0, 0.0, 1.0, 1.0),
          cursor=[(0.5, 0.3), (0.5, 0.55)], clicks=[0.7]),
    Scene("20_statuses.png", 3.0, "Статусы",
          "Пять состояний: цвет плюс обязательный текст",
          (0.05, 0.05, 0.95, 0.95), (0.0, 0.0, 1.0, 1.0)),
    Scene("19_profile.png", 3.0, "Профиль и роли",
          "Должность и роль — разные вещи; скидки только у администратора",
          (0.05, 0.05, 0.95, 0.95), (0.0, 0.0, 1.0, 1.0)),
]

FINAL_LINES = [
    "Персональные данные и резервные копии шифруются",
    "Автоблокировка сеанса · роли и права · журнал действий",
    "779 автоматических проверок пройдено",
]


def load(name: str) -> Image.Image:
    path = SCREENS / name
    if not path.exists():
        raise FileNotFoundError(f"Нет схемы экрана {name}. Запустите tools.make_screens")
    return Image.open(path).convert("RGB")


def frame_base() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT), BG)


def draw_caption(canvas: Image.Image, title: str, note: str, progress: float) -> None:
    draw = ImageDraw.Draw(canvas)
    top = HEIGHT - CAPTION_H
    draw.rectangle([0, top, WIDTH, HEIGHT], fill=WHITE)
    draw.line([0, top, WIDTH, top], fill=rgb(Color.BORDER))
    draw.text((48, top + 20), title, font=font(26, True), fill=INK)
    if note:
        draw.text((48, top + 56), note, font=font(17), fill=MUTED)
    draw.rectangle([0, HEIGHT - 5, WIDTH, HEIGHT], fill=rgb(Color.NEUTRAL_SOFT))
    draw.rectangle([0, HEIGHT - 5, int(WIDTH * progress), HEIGHT], fill=ACCENT)
    label = f"{DISPLAY_NAME}"
    width = draw.textlength(label, font=font(15, True))
    draw.text((WIDTH - width - 48, top + 34), label, font=font(15, True), fill=ACCENT)


def draw_cursor(canvas: Image.Image, x: int, y: int, click: float = 0.0) -> None:
    draw = ImageDraw.Draw(canvas, "RGBA")
    if click > 0:
        radius = int(14 + 26 * click)
        alpha = int(150 * (1 - click))
        draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                     outline=(37, 99, 235, alpha), width=3)
    arrow = [(x, y), (x, y + 22), (x + 6, y + 16), (x + 11, y + 26),
             (x + 15, y + 24), (x + 10, y + 14), (x + 17, y + 14)]
    draw.polygon(arrow, fill=(17, 24, 39, 235), outline=(255, 255, 255, 235))


def place(canvas: Image.Image, screen: Image.Image, box: tuple[float, float, float, float]) -> tuple:
    """Вписывает кадрированную область схемы в кадр, сохраняя пропорции."""
    left, top, right, bottom = box
    crop = screen.crop((int(left * screen.width), int(top * screen.height),
                        int(right * screen.width), int(bottom * screen.height)))
    area_w, area_h = WIDTH - 80, HEIGHT - CAPTION_H - 60
    scale = min(area_w / crop.width, area_h / crop.height)
    size = (max(1, int(crop.width * scale)), max(1, int(crop.height * scale)))
    resized = crop.resize(size, Image.LANCZOS)
    x = (WIDTH - size[0]) // 2
    y = (HEIGHT - CAPTION_H - size[1]) // 2
    shadow = Image.new("RGB", (size[0] + 8, size[1] + 8), rgb(Color.BORDER))
    canvas.paste(shadow, (x - 4, y - 2))
    canvas.paste(resized, (x, y))
    return x, y, size[0], size[1]


def title_frame(progress: float, fade: float = 1.0) -> Image.Image:
    canvas = frame_base()
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=WHITE)
    logo_y = 250
    draw.rounded_rectangle([WIDTH // 2 - 42, logo_y, WIDTH // 2 + 42, logo_y + 84],
                           radius=20, outline=ACCENT, width=5)
    title = DISPLAY_NAME
    width = draw.textlength(title, font=font(58, True))
    draw.text(((WIDTH - width) / 2, logo_y + 130), title, font=font(58, True), fill=INK)
    subtitle = "Учёт кинореквизита: каталог, аренда, возвраты, отчёты"
    width = draw.textlength(subtitle, font=font(24))
    draw.text(((WIDTH - width) / 2, logo_y + 215), subtitle, font=font(24), fill=MUTED)
    note = "обзор за одну минуту"
    width = draw.textlength(note, font=font(18))
    draw.text(((WIDTH - width) / 2, logo_y + 262), note, font=font(18), fill=ACCENT)
    if fade < 1.0:
        canvas = Image.blend(frame_base(), canvas, fade)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, HEIGHT - 5, int(WIDTH * progress), HEIGHT], fill=ACCENT)
    return canvas


def final_frame(progress: float, revealed: int) -> Image.Image:
    canvas = frame_base()
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=WHITE)
    title = f"{DISPLAY_NAME}"
    width = draw.textlength(title, font=font(46, True))
    draw.text(((WIDTH - width) / 2, 250), title, font=font(46, True), fill=INK)
    for index, line in enumerate(FINAL_LINES[:revealed]):
        width = draw.textlength(line, font=font(23))
        draw.text(((WIDTH - width) / 2, 350 + index * 52), line, font=font(23), fill=MUTED)
    footer = f"версия {VERSION}"
    width = draw.textlength(footer, font=font(18, True))
    draw.text(((WIDTH - width) / 2, 560), footer, font=font(18, True), fill=ACCENT)
    draw.rectangle([0, HEIGHT - 5, int(WIDTH * progress), HEIGHT], fill=ACCENT)
    return canvas


def build(target: Path) -> Path:
    total_seconds = INTRO + sum(scene.seconds for scene in STORYBOARD) + OUTRO
    total_frames = int(total_seconds * FPS)
    target.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "ffmpeg", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
        "-an", "-vcodec", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
        "-movflags", "+faststart", str(target),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    written = 0

    def push(image: Image.Image) -> None:
        nonlocal written
        process.stdin.write(image.tobytes())
        written += 1

    # заставка
    intro_frames = int(INTRO * FPS)
    for index in range(intro_frames):
        t = index / intro_frames
        push(title_frame(written / total_frames, fade=min(1.0, t * 3)))

    # эпизоды
    for scene in STORYBOARD:
        screen = load(scene.screen)
        count = int(scene.seconds * FPS)
        for index in range(count):
            t = ease(index / max(1, count - 1))
            box = tuple(a + (b - a) * t for a, b in zip(scene.start_box, scene.end_box))
            canvas = frame_base()
            x, y, w, h = place(canvas, screen, box)
            if scene.cursor:
                start, finish = scene.cursor[0], scene.cursor[-1]
                cx = x + int(w * (start[0] + (finish[0] - start[0]) * t))
                cy = y + int(h * (start[1] + (finish[1] - start[1]) * t))
                click = 0.0
                for moment in scene.clicks:
                    delta = (index / max(1, count - 1)) - moment
                    if 0 <= delta <= 0.12:
                        click = 1 - delta / 0.12
                draw_cursor(canvas, cx, cy, click)
            draw_caption(canvas, scene.title, scene.note, written / total_frames)
            push(canvas)

    # финал
    outro_frames = int(OUTRO * FPS)
    for index in range(outro_frames):
        revealed = min(len(FINAL_LINES), 1 + int(index / (outro_frames / 4)))
        push(final_frame(min(1.0, written / total_frames), revealed))

    process.stdin.close()
    process.wait()
    return target


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "Обзор.mp4"
    path = build(out)
    print("готово:", path)
