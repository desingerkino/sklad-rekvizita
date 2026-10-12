# -*- coding: utf-8 -*-
"""Сборка PDF «Установка в картинках». Запуск: python -m tools.make_install_pdf [pdf]"""
from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, ListFlowable, ListItem, PageBreak, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FONTS = ROOT / "assets" / "fonts"
SHOTS = ROOT / "docs" / "install"

from app.version import DISPLAY_NAME, VERSION  # noqa: E402

ACCENT = colors.HexColor("#2563EB")
MUTED = colors.HexColor("#6B7280")
BORDER = colors.HexColor("#CDD5DC")
LIGHT = colors.HexColor("#EEF3F7")
WARN = colors.HexColor("#FFF7ED")
WARN_LINE = colors.HexColor("#EA580C")

pdfmetrics.registerFont(TTFont("Man", str(FONTS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Man-Bold", str(FONTS / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("Mono", str(FONTS / "DejaVuSansMono.ttf")))
pdfmetrics.registerFontFamily("Man", normal="Man", bold="Man-Bold",
                              italic="Man", boldItalic="Man-Bold")
base = getSampleStyleSheet()["Normal"]


def st(name, size=10, bold=False, align=TA_LEFT, space=5, color=colors.black, leading=None):
    return ParagraphStyle(name, parent=base, fontName="Man-Bold" if bold else "Man",
                          fontSize=size, leading=leading or size * 1.5, alignment=align,
                          spaceAfter=space, textColor=color)


S = {
    "title": st("t", 28, True, TA_CENTER, space=8),
    "sub": st("s", 13, False, TA_CENTER, color=MUTED, space=4),
    "h1": st("h1", 17, True, space=8, color=ACCENT),
    "h2": st("h2", 12, True, space=5),
    "p": st("p", 10, align=TA_JUSTIFY, space=6),
    "li": st("li", 10, space=3),
    "cap": st("cap", 8.5, align=TA_CENTER, color=MUTED, space=10),
    "cell": st("cell", 9),
    "code": ParagraphStyle("code", parent=base, fontName="Mono", fontSize=9, leading=14,
                           textColor=colors.HexColor("#111827"), spaceAfter=4),
}


def decor(canvas, doc):
    if canvas.getPageNumber() == 1:
        return
    canvas.saveState()
    canvas.setFont("Man", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, f"{DISPLAY_NAME} — установка в картинках")
    canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(canvas.getPageNumber()))
    canvas.setStrokeColor(BORDER)
    canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
    canvas.restoreState()


def shot(name: str, caption: str, width_mm: float = 168) -> list:
    from PIL import Image as PILImage
    path = SHOTS / name
    with PILImage.open(path) as img:
        ratio = img.height / img.width
    picture = Image(str(path), width=width_mm * mm, height=width_mm * mm * ratio)
    picture.hAlign = "CENTER"
    return [Spacer(1, 2 * mm), picture, Paragraph(caption, S["cap"])]


def code(text: str) -> Table:
    table = Table([[Paragraph(text, S["code"])]], colWidths=[168 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F3F4F6")),
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table


def box(title: str, text: str, tone: str = "info") -> Table:
    fill, line = (LIGHT, ACCENT) if tone == "info" else (WARN, WARN_LINE)
    table = Table([[Paragraph(f"<b>{title}</b><br/>{text}", S["cell"])]], colWidths=[168 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("LINEBEFORE", (0, 0), (0, -1), 2.5, line),
        ("BOX", (0, 0), (-1, -1), 0.4, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def steps(items: list[str]) -> ListFlowable:
    return ListFlowable([ListItem(Paragraph(t, S["li"]), leftIndent=14) for t in items],
                        bulletType="1", start="1", bulletFontName="Man-Bold",
                        bulletFontSize=10, leftIndent=16)


def marks(items: list[tuple[str, str]]) -> Table:
    rows = [[Paragraph(f"<b>{n}</b>", S["cell"]), Paragraph(text, S["cell"])] for n, text in items]
    table = Table(rows, colWidths=[9 * mm, 159 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#DC2626")),
    ]))
    return table


def build(target: Path) -> Path:
    doc = SimpleDocTemplate(str(target), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=f"{DISPLAY_NAME} — установка в картинках")
    f: list = [Spacer(1, 40 * mm),
               Paragraph("Установка в картинках", S["title"]),
               Paragraph(f"{DISPLAY_NAME}", S["sub"]),
               Paragraph("Как получить работающую программу на компьютере с Windows",
                         S["sub"]),
               Spacer(1, 10 * mm)]
    f += shot("06_build.png", "", 140)
    f += [Spacer(1, 6 * mm),
          box("Коротко", "Нужна одна обязательная программа — <b>Python</b>. Вторая, "
                         "<b>Inno Setup</b>, нужна только для установщика Setup.exe; "
                         "без неё программа тоже соберётся. На складском компьютере, где "
                         "работают сотрудники, не нужно ни то, ни другое."),
          PageBreak()]

    f += [Paragraph("Шаг 1. Скачать Python", S["h1"]),
          Paragraph("Python — это то, из чего собирается программа. Устанавливается один "
                    "раз на компьютере, где вы делаете сборку.", S["p"])]
    f += shot("01_python_download.png", "Схема 1. Страница загрузки python.org")
    f += [marks([("1", "Кнопка загрузки. Нужен Windows installer (64-bit).")]),
          Spacer(1, 3 * mm),
          Paragraph("Скачанный файл называется примерно так: "
                    "<b>python-3.12.x-amd64.exe</b>. Запустите его двойным щелчком.",
                    S["p"]),
          PageBreak()]

    f += [Paragraph("Шаг 2. Установить Python", S["h1"])]
    f += shot("02_python_installer.png", "Схема 2. Окно установщика Python")
    f += [marks([("1", "Галочка «Add python.exe to PATH» — обязательна. Поставьте её "
                       "до нажатия Install Now.")]),
          Spacer(1, 3 * mm),
          Paragraph("Порядок действий:", S["h2"]),
          steps(["Поставить галочку «Add python.exe to PATH» внизу окна.",
                 "Нажать «Install Now».",
                 "Дождаться окончания и нажать «Close».",
                 "Если в конце будет кнопка «Disable path length limit» — нажмите её."]),
          Spacer(1, 3 * mm),
          box("Если галочку забыли", "Пуск → «Установка и удаление программ» → Python → "
              "«Изменить» → Modify → Next → отметить «Add Python to environment "
              "variables» → Install.", tone="warn"),
          PageBreak()]

    f += [Paragraph("Шаг 3. Проверить, что Python виден", S["h1"]),
          Paragraph("PowerShell — окно, куда вводят команды текстом. Нажмите клавишу Win, "
                    "наберите <b>powershell</b>, нажмите Enter.", S["p"])]
    f += shot("03_python_check.png", "Схема 3. Проверка в PowerShell")
    f += [marks([("1", "Ответ вида «Python 3.12.x» означает, что всё готово.")]),
          Spacer(1, 3 * mm), code("python --version"),
          Paragraph("Команду можно скопировать отсюда и вставить в PowerShell "
                    "сочетанием Ctrl+V.", S["p"]),
          PageBreak()]

    f += [Paragraph("Шаг 4. Установить Inno Setup (по желанию)", S["h1"]),
          Paragraph("Нужен только чтобы получить один файл Setup.exe — им удобно "
                    "устанавливать программу на другие компьютеры. Пропустите этот шаг, "
                    "если собираете для себя.", S["p"])]
    f += shot("04_inno_setup.png", "Схема 4. Загрузка и установка Inno Setup")
    f += [marks([("1", "Ссылка на стабильную версию innosetup-6.x.x.exe."),
                 ("2", "Все параметры установки оставьте по умолчанию.")]),
          PageBreak()]

    f += [Paragraph("Шаг 5. Распаковать архив программы", S["h1"])]
    f += shot("05_folder.png", "Схема 5. Папка проекта после распаковки")
    f += [marks([("1", "Проверьте, что рядом с папками лежит файл main.py. Если внутри "
                       "оказалась ещё одна папка sklad — зайдите в неё.")]),
          Spacer(1, 3 * mm),
          Paragraph("Удобно распаковать в <b>C:\\Sklad</b>, тогда путь будет "
                    "<b>C:\\Sklad\\sklad</b>.", S["p"]),
          PageBreak()]

    f += [Paragraph("Шаг 6. Собрать программу", S["h1"]),
          Paragraph("В папке проекта щёлкните правой кнопкой по пустому месту и выберите "
                    "«Открыть в терминале». В Windows 10: Shift + правая кнопка → "
                    "«Открыть окно PowerShell здесь». Затем вставьте команду:", S["p"]),
          code("powershell -ExecutionPolicy Bypass -File build\\build.ps1")]
    f += shot("06_build.png", "Схема 6. Ход сборки в PowerShell")
    f += [marks([("1", "Команда сборки."),
                 ("2", "Устанавливаются библиотеки — идёт из интернета, несколько минут."),
                 ("3", "Прогоняются автоматические проверки. Если хоть одна не пройдёт, "
                       "сборка остановится.")]),
          Spacer(1, 3 * mm),
          box("Сколько ждать", "10–15 минут при первом запуске. Окно можно не трогать: "
              "строки [1/5] … [5/5] показывают, что происходит."),
          PageBreak()]

    f += [Paragraph("Шаг 7. Что получилось", S["h1"])]
    f += shot("07_result.png", "Схема 7. Папка dist после сборки")
    f += [marks([("1", "Готовая программа — внутри SkladRekvizita.exe, запускается сразу."),
                 ("2", "Установщик для переноса на складской компьютер (если ставили "
                       "Inno Setup).")]),
          Spacer(1, 3 * mm),
          box("Без Inno Setup", "Вы увидите строку «Inno Setup не найден» — это не ошибка. "
              "Скопируйте папку dist\\SkladRekvizita куда удобно и сделайте ярлык "
              "к SkladRekvizita.exe правой кнопкой → «Отправить» → «Рабочий стол»."),
          PageBreak()]

    f += [Paragraph("Шаг 8. Установка на складской компьютер", S["h1"])]
    f += shot("08_setup.png", "Схема 8. Мастер установки")
    f += [marks([("1", "Ярлык на рабочем столе — оставьте галочку."),
                 ("2", "Кнопка «Установить».")]),
          Spacer(1, 3 * mm),
          box("Предупреждение Windows", "SmartScreen скажет «Неизвестный издатель» — так "
              "бывает с любым неподписанным файлом. Нажмите «Подробнее» → «Выполнить "
              "в любом случае».", tone="warn"),
          PageBreak()]

    f += [Paragraph("Шаг 9. Первый запуск", S["h1"])]
    f += shot("09_first_run.png", "Схема 9. Создание администратора")
    f += [marks([("1", "Пароль минимум 4 символа. Восстановить его нельзя — запишите."),
                 ("2", "«Создать и войти» — программа откроется под этой учётной записью.")]),
          Spacer(1, 3 * mm),
          Paragraph("Дальше:", S["h2"]),
          steps(["Настройки → Склад: название, адрес, телефон, ИНН, ОГРН, банк, логотип — "
                 "они попадают в шапку документов.",
                 "Настройки → Пользователи: заведите сотрудников. Обычный пользователь "
                 "не может назначать скидки и удалять позиции из аренды.",
                 "Каталог → «Добавить реквизит»: внесите первые позиции с фотографиями."]),
          Spacer(1, 4 * mm),
          box("Где лежат данные", "База, фотографии, документы и копии — в "
              "%LOCALAPPDATA%\\SkladRekvizita. Ключ шифрования персональных данных — "
              "отдельно, в %APPDATA%\\SkladRekvizita\\keys, и привязан к учётной записи "
              "Windows. При переносе на другой компьютер копируйте обе папки."),
          PageBreak()]

    f += [Paragraph("Если что-то пошло не так", S["h1"])]
    rows = [[Paragraph("<b>Что видите</b>", S["cell"]), Paragraph("<b>Что делать</b>", S["cell"])]]
    for problem, fix in [
        ("python не является внутренней командой",
         "Python установлен без галочки PATH. Переустановите или добавьте PATH через «Изменить»."),
        ("невозможно загрузить файл build.ps1",
         "Запускайте команду целиком, вместе с -ExecutionPolicy Bypass."),
        ("Inno Setup не найден",
         "Не ошибка: программа собрана, нет только Setup.exe. Установите Inno Setup "
         "и повторите: ISCC.exe build\\installer.iss"),
        ("Ошибка при установке библиотек",
         "Обновите pip: .\\.venv\\Scripts\\python.exe -m pip install --upgrade pip"),
        ("Антивирус ругается на собранный .exe",
         "Обычная реакция на PyInstaller. Добавьте папку dist в исключения."),
        ("Тест не пройден, сборка остановилась",
         "Пришлите последние строки из окна PowerShell — по ним видно, что именно упало."),
    ]:
        rows.append([Paragraph(problem, S["cell"]), Paragraph(fix, S["cell"])])
    table = Table(rows, colWidths=[62 * mm, 106 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    f += [table, Spacer(1, 8 * mm),
          box("Об иллюстрациях", "Окна на рисунках нарисованы схематично: они показывают, "
              "где находятся нужные кнопки и галочки. Реальные окна Windows выглядят "
              "похоже, но могут отличаться оформлением в зависимости от версии системы."),
          Spacer(1, 8 * mm),
          Paragraph(f"{DISPLAY_NAME} · версия {VERSION}", S["cap"])]

    doc.build(f, onFirstPage=decor, onLaterPages=decor)
    return target


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "Установка_в_картинках.pdf"
    print("готово:", build(out))
