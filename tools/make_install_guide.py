# -*- coding: utf-8 -*-
"""
Инструкция по установке в картинках.

Собирает PDF: установка Python, установка Inno Setup, сборка программы
в PowerShell и первый запуск. Иллюстрации рисуются кодом (tools/install_shots).

Запуск:  python -m tools.make_install_guide [путь к pdf]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.install_shots import (ACCENT, ACCENT_SOFT, BORDER, GREEN, MUTED, RED,  # noqa: E402
                                 SOFT, TEXT, WHITE, Shot)

OUT = ROOT / "docs" / "install"


# ------------------------------------------------------------------ рисунки
def python_download() -> Path:
    c = Shot(1200, 700)
    bx, by, bw, bh = c.window(40, 40, 1120, 620, "python.org — Downloads · браузер")
    c.d.rectangle([bx, by, bx + bw, by + 46], fill=WHITE)
    c.text(bx + 16, by + 14, "https://www.python.org/downloads/", 12, MUTED, mono_font=True)
    c.text(bx + 60, by + 90, "Download Python 3.12", 30, TEXT, True)
    c.text(bx + 60, by + 140, "Скачайте установщик для Windows (64-bit)", 15, MUTED)
    w = c.button(bx + 60, by + 190, "Download Python 3.12.x", primary=True, width=340)
    c.mark(bx + 60 + w, by + 190)
    c.note(bx + 60, by + 270, 700,
           "Нужен именно Windows installer (64-bit) — обычно это первая кнопка")
    c.text(bx + 60, by + 380, "Файл сохранится в папку «Загрузки»:", 14, TEXT)
    c.text(bx + 60, by + 412, "python-3.12.x-amd64.exe", 14, ACCENT, mono_font=True)
    c.text(bx + 60, by + 452, "Запустите его двойным щелчком.", 14, TEXT)
    return c.save("01_python_download.png")


def python_installer() -> Path:
    c = Shot(1200, 700)
    bx, by, bw, bh = c.window(120, 60, 960, 580, "Python 3.12 Setup")
    c.d.rectangle([bx, by, bx + bw, by + 150], fill=(60, 90, 150))
    c.text(bx + 40, by + 40, "Install Python 3.12", 26, WHITE, True)
    c.text(bx + 40, by + 84, "Select Install Now to install with default settings",
           13, (210, 225, 245))

    c.d.rounded_rectangle([bx + 60, by + 190, bx + bw - 60, by + 250], radius=8,
                          fill=WHITE, outline=BORDER)
    c.text(bx + 80, by + 202, "Install Now", 16, TEXT, True)
    c.text(bx + 80, by + 226, "C:\\Users\\...\\Python\\Python312", 11, MUTED, mono_font=True)

    c.d.rounded_rectangle([bx + 60, by + 264, bx + bw - 60, by + 324], radius=8,
                          fill=WHITE, outline=BORDER)
    c.text(bx + 80, by + 276, "Customize installation", 16, TEXT)
    c.text(bx + 80, by + 300, "Выбор компонентов и папки установки", 11, MUTED)

    c.checkbox(bx + 60, by + 360, "Use admin privileges when installing py.exe", checked=False)
    c.checkbox(bx + 60, by + 400, "Add python.exe to PATH", checked=True, highlight=True)
    c.mark(bx + 610, by + 410)
    c.arrow(bx + 590, by + 410, bx + 500, by + 410)

    c.button(bx + bw - 200, by + bh - 70, "Cancel")
    c.note(bx + 60, by + 450, 640,
           "Эта галочка обязательна: без неё команда python не заработает",
           color=RED, soft=(254, 242, 242))
    return c.save("02_python_installer.png")


def python_check() -> Path:
    c = Shot(1200, 620)
    c.text(60, 30, "Проверка: откройте PowerShell и введите команду", 18, TEXT, True)
    bx, by, bw, bh = c.console(60, 80, 1080, 300, "Windows PowerShell", [
        ("cmd", "python --version"),
        ("ok", "Python 3.12.7"),
        ("info", ""),
        ("cmd", ""),
    ])
    c.mark(bx + 250, by + 20)
    c.note(60, 420, 1080,
           "Если ответ такой — всё в порядке. Если «python не является внутренней "
           "командой» — переустановите Python с галочкой Add to PATH.", color=GREEN,
           soft=(236, 253, 245))
    c.text(60, 510, "Как открыть PowerShell: клавиша Win → наберите powershell → Enter.",
           14, MUTED)
    c.text(60, 540, "Либо: в папке проекта правой кнопкой на пустом месте → "
                    "«Открыть в терминале».", 14, MUTED)
    return c.save("03_python_check.png")


def inno_setup() -> Path:
    c = Shot(1200, 700)
    bx, by, bw, bh = c.window(60, 40, 520, 620, "jrsoftware.org — браузер")
    c.text(bx + 16, by + 14, "https://jrsoftware.org/isdl.php", 11, MUTED, mono_font=True)
    c.text(bx + 30, by + 70, "Inno Setup Downloads", 22, TEXT, True)
    c.text(bx + 30, by + 116, "Stable Release", 14, MUTED)
    w = c.button(bx + 30, by + 150, "innosetup-6.x.x.exe", primary=True, width=280)
    c.mark(bx + 30 + w, by + 150)
    c.text(bx + 30, by + 220, "Скачайте и запустите файл.", 13, TEXT)
    c.note(bx + 30, by + 270, 420,
           "Нужен, только чтобы получить Setup.exe.\nБез него программа тоже соберётся.")

    dx, dy, dw, dh = c.window(620, 40, 520, 620, "Setup — Inno Setup")
    c.text(dx + 30, dy + 60, "Установка Inno Setup 6", 20, TEXT, True)
    c.text(dx + 30, dy + 110, "Все параметры оставьте по умолчанию:", 13, MUTED)
    for index, step in enumerate(("Принять лицензию", "Папка установки — по умолчанию",
                                  "Компоненты — по умолчанию", "Нажать Install",
                                  "Снять галочку «Launch Inno Setup»")):
        c.checkbox(dx + 30, dy + 160 + index * 44, step, checked=index < 4)
    c.button(dx + dw - 260, dy + dh - 70, "Назад")
    c.button(dx + dw - 150, dy + dh - 70, "Install", primary=True)
    c.mark(dx + dw - 60, dy + dh - 52)
    return c.save("04_inno_setup.png")


def project_folder() -> Path:
    c = Shot(1200, 620)
    c.text(60, 26, "Распакуйте архив, например в C:\\Sklad", 18, TEXT, True)
    bx, by, bw, bh = c.explorer(60, 70, 1080, 420, "C:\\Sklad\\sklad", [
        ("app", "исходный код программы", True),
        ("assets", "иконки и шрифты", True),
        ("build", "скрипт сборки build.ps1", True),
        ("docs", "руководство и схемы", True),
        ("tests", "автоматические проверки", True),
        ("tools", "генераторы иконок и документации", True),
        ("main.py", "точка входа", False),
        ("requirements.txt", "список библиотек", False),
    ])
    c.mark(bx + 300, by + 60)
    c.arrow(bx + 330, by + 60, bx + 430, by + 60)
    c.text(bx + 450, by + 48, "внутри должен быть main.py", 14, RED, True)
    c.note(60, 520, 1080,
           "Правой кнопкой по пустому месту в этой папке → «Открыть в терминале» "
           "(Windows 10: Shift + правая кнопка → «Открыть окно PowerShell здесь»)")
    return c.save("05_folder.png")


def build_console() -> Path:
    c = Shot(1200, 760)
    c.text(60, 26, "Одна команда собирает программу целиком", 18, TEXT, True)
    bx, by, bw, bh = c.console(60, 70, 1080, 560, "Windows PowerShell — C:\\Sklad\\sklad", [
        ("cmd", "powershell -ExecutionPolicy Bypass -File build\\build.ps1"),
        ("info", ""),
        ("ok", "[1/5] Проверка Python..."),
        ("info", "Python 3.12.7"),
        ("ok", "[2/5] Виртуальное окружение и зависимости..."),
        ("info", "Successfully installed PySide6 Pillow reportlab cryptography"),
        ("ok", "[3/5] Тесты бизнес-логики..."),
        ("info", "Пройдено: 66   Провалено: 0"),
        ("info", "Пройдено: 264  Провалено: 0"),
        ("info", "Пройдено: 81   Провалено: 0"),
        ("ok", "[4/5] Сборка .exe (PyInstaller)..."),
        ("info", "Готово: dist\\SkladRekvizita\\SkladRekvizita.exe"),
        ("ok", "[5/5] Установщик (Inno Setup)..."),
        ("info", "Установщик: dist\\SkladRekvizita_Setup_2.3.1.exe"),
    ])
    c.mark(bx + 640, by + 16)
    c.mark(bx + 300, by + 148)
    c.mark(bx + 470, by + 302)
    c.note(60, 660, 1080,
           "Занимает 10–15 минут. Если тест не пройдёт, сборка остановится — "
           "это защита от выпуска сломанной версии.", color=MUTED, soft=SOFT)
    return c.save("06_build.png")


def result_folder() -> Path:
    c = Shot(1200, 620)
    c.text(60, 26, "Что получилось", 18, TEXT, True)
    bx, by, bw, bh = c.explorer(60, 70, 1080, 340, "C:\\Sklad\\sklad\\dist", [
        ("SkladRekvizita", "папка с готовой программой", True),
        ("SkladRekvizita_Setup_2.3.1.exe", "установщик для переноса на другой компьютер", False),
    ])
    c.mark(bx + 300, by + 60)
    c.mark(bx + 300, by + 92)
    c.text(60, 440, "1  —  можно запускать сразу: внутри SkladRekvizita.exe", 14, TEXT)
    c.text(60, 470, "2  —  файл для установки на складской компьютер", 14, TEXT)
    c.note(60, 520, 1080,
           "На складском компьютере Python и Inno Setup уже не нужны — всё внутри.",
           color=GREEN, soft=(236, 253, 245))
    return c.save("07_result.png")


def setup_wizard() -> Path:
    c = Shot(1200, 640)
    bx, by, bw, bh = c.window(180, 40, 840, 560, "Установка — СКЛАД РЕКВИЗИТА 2.3.1")
    c.text(bx + 40, by + 50, "Установка «СКЛАД РЕКВИЗИТА 2.3.1»", 22, TEXT, True)
    c.text(bx + 40, by + 100, "Программа будет установлена в папку:", 13, MUTED)
    c.text(bx + 40, by + 130, "C:\\Program Files\\SkladRekvizita", 13, TEXT, mono_font=True)
    c.checkbox(bx + 40, by + 190, "Создать ярлык на рабочем столе", checked=True, highlight=True)
    c.mark(bx + 480, by + 200)
    c.text(bx + 40, by + 260, "Данные (база, фотографии, документы) будут храниться отдельно:",
           13, MUTED)
    c.text(bx + 40, by + 290, "%LOCALAPPDATA%\\SkladRekvizita", 13, ACCENT, mono_font=True)
    c.text(bx + 40, by + 330, "При обновлении программы они не удаляются.", 13, MUTED)
    c.button(bx + bw - 340, by + bh - 70, "Назад")
    c.button(bx + bw - 230, by + bh - 70, "Установить", primary=True, width=170)
    c.mark(bx + bw - 60, by + bh - 52)
    c.note(bx + 40, by + 380, 700,
           "Windows покажет «Неизвестный издатель» — это нормально для неподписанного "
           "файла: «Подробнее» → «Выполнить в любом случае».", color=MUTED, soft=SOFT)
    return c.save("08_setup.png")


def first_run() -> Path:
    c = Shot(1200, 640)
    bx, by, bw, bh = c.window(230, 40, 740, 560, "СКЛАД РЕКВИЗИТА 2.3.1 — первый запуск")
    c.text(bx + 40, by + 40, "Создание администратора", 22, TEXT, True)
    c.text(bx + 40, by + 84, "В программе ещё нет пользователей.", 13, MUTED)
    fields = [("Имя / ФИО:", "Иванов Михаил"), ("Логин:", "admin"),
              ("Должность:", "Заведующий складом"), ("Пароль:", "••••••••"),
              ("Повторите пароль:", "••••••••")]
    fy = by + 140
    for label, value in fields:
        c.text(bx + 40, fy + 8, label, 13, TEXT)
        c.d.rounded_rectangle([bx + 230, fy, bx + 600, fy + 34], radius=8,
                              fill=WHITE, outline=BORDER)
        c.text(bx + 244, fy + 8, value, 13, TEXT)
        fy += 50
    c.mark(bx + 620, by + 290)
    c.button(bx + 40, by + bh - 70, "Отмена")
    c.button(bx + 380, by + bh - 70, "Создать и войти", primary=True, width=220)
    c.mark(bx + 610, by + bh - 52)
    c.note(bx + 40, by + 400, 600,
           "Пароль восстановить нельзя — запишите его. Дальше: Настройки → Склад.",
           color=RED, soft=(254, 242, 242))
    return c.save("09_first_run.png")


def build_all() -> list[Path]:
    return [python_download(), python_installer(), python_check(), inno_setup(),
            project_folder(), build_console(), result_folder(), setup_wizard(), first_run()]


if __name__ == "__main__":
    for path in build_all():
        print("создано:", path.relative_to(ROOT))
