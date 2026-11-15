# -*- coding: utf-8 -*-
"""
Дизайн-система «Склад Реквизита» (светлая и тёмная темы, версия 4.0).

Единственный источник цветов, скруглений, отступов и типографики: все экраны
берут стиль отсюда, своих значений не заводят. Палитра снята с визуального
референса — светлый холодный фон, белые поверхности, синий акцент, мягкие
«пилюли» статусов и тонкие серые границы.
"""
from __future__ import annotations


class Color:
    """Цветовые токены. Значения соответствуют референсу версии 2.1."""

    # поверхности
    BG = "#F7F9FC"              # фон рабочей области
    SURFACE = "#FFFFFF"         # карточки, панели, таблицы
    SURFACE_ALT = "#F1F5F9"     # чередование строк, вложенные блоки
    SIDEBAR = "#FFFFFF"         # боковое меню
    TOPBAR = "#FFFFFF"          # верхняя панель
    OVERLAY = "#0F172A"         # затемнение под модальными окнами

    # акценты
    PRIMARY = "#2563EB"
    PRIMARY_HOVER = "#1D4ED8"
    PRIMARY_PRESSED = "#1E40AF"
    PRIMARY_SOFT = "#EEF4FF"    # фон выделения под текстом акцентного цвета (контраст >= 4.5)
    BRAND_TINT = "#DBEAFE"      # светло-голубой бренда: плитки значков, подложки без текста
    SECONDARY = "#60A5FA"       # голубой акцент бренда (градиент #2563EB → #60A5FA)

    # текст
    TEXT = "#172B4D"
    TEXT_SECONDARY = "#475569"       # 7.6:1 на белом
    TEXT_MUTED = "#5B6B82"           # 5.4:1 на белом, 5.0 на фоне — подсказки, подписи
    TEXT_ON_PRIMARY = "#FFFFFF"

    # границы
    BORDER = "#E2E8F0"
    BORDER_STRONG = "#CBD5E1"   # разделители, наведение
    INPUT_BORDER = "#8A94A6"    # граница полей ввода: >= 3:1 к поверхности
    FOCUS = "#2563EB"           # кольцо фокуса: заметное, >= 3:1
    DISABLED_BG = "#E8EBF0"     # отключённая кнопка
    TEXT_ON_DISABLED = "#5B6472"

    # состояния
    SUCCESS = "#15803D"
    SUCCESS_SOFT = "#ECFDF5"
    SUCCESS_BORDER = "#A7F3D0"
    WARNING = "#B45309"
    WARNING_SOFT = "#FFF7ED"
    WARNING_BORDER = "#FED7AA"
    DANGER = "#B91C1C"
    DANGER_SOFT = "#FEF2F2"
    DANGER_BORDER = "#FECACA"
    INFO = "#1D4ED8"
    INFO_SOFT = "#EFF6FF"
    INFO_BORDER = "#BFDBFE"
    NEUTRAL = "#4B5563"
    FINISHED = "#1F2937"            # «Аренда завершена» — тёмный, почти чёрный
    FINISHED_SOFT = "#F3F4F6"
    FINISHED_BORDER = "#D1D5DB"
    NEUTRAL_SOFT = "#F3F4F6"
    NEUTRAL_BORDER = "#E5E7EB"

    SELECTED = "#EAF2FF"
    HOVER = "#EEF3FA"

    # «частично возвращено» — голубой
    PARTIAL = "#0369A1"
    PARTIAL_SOFT = "#F0F9FF"
    PARTIAL_BORDER = "#BAE6FD"

    # значки и служебное
    DANGER_SOLID = "#B91C1C"    # сплошная красная подложка под белым текстом (счётчик)
    ICON = "#6B7280"            # обычный значок
    PHOTO_BG = "#EEF1F5"        # подложка под фотографией (поля вокруг снимка)
    STAR = "#D97706"            # отметка «в избранном»
    SHADOW = "#0F172A"
    ON_PHOTO = "#FFFFFF"        # подписи и рамки поверх фотографии


class Radius:
    SM = 6
    MD = 10
    LG = 14
    PILL = 999


class Space:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Font:
    """
    Типографика. Единственная гарнитура интерфейса — Onest; файлы шрифта лежат
    в assets/fonts и идут вместе с программой, поэтому вид одинаков на Windows
    и macOS и не зависит от шрифтов, установленных в системе.

    Размеры заданы в пунктах Windows и переводятся в пиксели функцией px()
    (1 pt = 4/3 px), итог в пикселях:
        H1 23 — заголовок раздела;   H2 18 — подзаголовок;
        H3 16 — название предмета;   BODY 14 — основной текст и кнопки;
        SMALL 13 и TINY 12 — вспомогательные подписи.

    Начертания: Regular 400 — текст; Medium 500 — кнопки, названия, элементы
    управления; Bold 700 — только акценты (цена, крупные числа). В присланном
    наборе Onest нет файла SemiBold (600), поэтому заголовки набираются Bold,
    а «полужирные» элементы управления — Medium. Если положить в assets/fonts
    файл Onest-SemiBold.ttf, оба значения сами переключатся на 600.
    """

    NAME = "Onest"
    FAMILY = "'Onest', 'Segoe UI', 'Helvetica Neue', Arial"
    FILES = ("Onest-Regular.ttf", "Onest-Medium.ttf", "Onest-SemiBold.ttf", "Onest-Bold.ttf")
    H1 = 17.25
    H2 = 13.5
    H3 = 12
    BODY = 10.5
    SMALL = 9.75
    TINY = 9

    W_REGULAR = 400
    W_MEDIUM = 500
    W_SEMI = 500        # элементы управления и названия (600, если есть SemiBold)
    W_HEADING = 700     # заголовки (600, если есть SemiBold)
    W_BOLD = 700

    @classmethod
    def use_semibold(cls, available: bool) -> None:
        cls.W_SEMI = 600 if available else 500
        cls.W_HEADING = 600 if available else 700


class Duration:
    """Длительность анимаций, мс. Быстро и ненавязчиво."""

    FAST = 120
    NORMAL = 180
    SLOW = 260


# ------------------------------------------------------------------ статусы
# Пять статусов версии 2.3.1: зелёный, оранжевый, красный, серый, чёрный.
PARTIAL_TEXT = "#0369A1"
PARTIAL_SOFT = "#F0F9FF"
PARTIAL_BORDER = "#BAE6FD"

STATUS_TONES = {
    "in_stock": (Color.SUCCESS, Color.SUCCESS_SOFT, Color.SUCCESS_BORDER),
    "rented": (Color.WARNING, Color.WARNING_SOFT, Color.WARNING_BORDER),
    "prolonged": (Color.DANGER, Color.DANGER_SOFT, Color.DANGER_BORDER),
    "open_set": (Color.NEUTRAL, Color.NEUTRAL_SOFT, Color.NEUTRAL_BORDER),
    "finished": (Color.FINISHED, Color.FINISHED_SOFT, Color.FINISHED_BORDER),
    "repair": (Color.WARNING, Color.WARNING_SOFT, Color.WARNING_BORDER),
    "success": (Color.SUCCESS, Color.SUCCESS_SOFT, Color.SUCCESS_BORDER),
    "info": (Color.INFO, Color.INFO_SOFT, Color.INFO_BORDER),
    "soon": (Color.WARNING, Color.WARNING_SOFT, Color.WARNING_BORDER),
    "danger": (Color.DANGER, Color.DANGER_SOFT, Color.DANGER_BORDER),
    "archived": (Color.NEUTRAL, Color.NEUTRAL_SOFT, Color.NEUTRAL_BORDER),
    "returned": (Color.NEUTRAL, Color.NEUTRAL_SOFT, Color.NEUTRAL_BORDER),
    "partial": (Color.PARTIAL, Color.PARTIAL_SOFT, Color.PARTIAL_BORDER),
    "out": (Color.DANGER, Color.DANGER_SOFT, Color.DANGER_BORDER),
}


def _build_status_tones() -> dict:
    """Тона статусов из текущей палитры — пересобираются при смене темы."""
    return {
        "in_stock": (Color.SUCCESS, Color.SUCCESS_SOFT, Color.SUCCESS_BORDER),
        "rented": (Color.WARNING, Color.WARNING_SOFT, Color.WARNING_BORDER),
        "prolonged": (Color.DANGER, Color.DANGER_SOFT, Color.DANGER_BORDER),
        "open_set": (Color.NEUTRAL, Color.NEUTRAL_SOFT, Color.NEUTRAL_BORDER),
        "finished": (Color.FINISHED, Color.FINISHED_SOFT, Color.FINISHED_BORDER),
        "repair": (Color.WARNING, Color.WARNING_SOFT, Color.WARNING_BORDER),
        "success": (Color.SUCCESS, Color.SUCCESS_SOFT, Color.SUCCESS_BORDER),
        "info": (Color.INFO, Color.INFO_SOFT, Color.INFO_BORDER),
        "soon": (Color.WARNING, Color.WARNING_SOFT, Color.WARNING_BORDER),
        "danger": (Color.DANGER, Color.DANGER_SOFT, Color.DANGER_BORDER),
        "archived": (Color.NEUTRAL, Color.NEUTRAL_SOFT, Color.NEUTRAL_BORDER),
        "returned": (Color.NEUTRAL, Color.NEUTRAL_SOFT, Color.NEUTRAL_BORDER),
        "partial": (Color.PARTIAL, Color.PARTIAL_SOFT, Color.PARTIAL_BORDER),
        "out": (Color.DANGER, Color.DANGER_SOFT, Color.DANGER_BORDER),
    }


def status_tone(status: str) -> tuple[str, str, str]:
    """Возвращает (цвет текста, фон, граница) для «пилюли» статуса."""
    return STATUS_TONES.get(status, STATUS_TONES["info"])


def status_color(status: str) -> str:
    return status_tone(status)[0]


# совместимость с кодом версии 2.0
ACCENT = Color.PRIMARY
OK = Color.SUCCESS
WARN = Color.WARNING
DANGER = Color.DANGER
MUTED = Color.TEXT_SECONDARY


# ------------------------------------------------------------------ темы
# Светлая тема — значения класса Color выше. Тёмная задаётся таблицей ниже:
# те же токены, другие значения. Экраны обращаются к Color.* в момент
# построения, поэтому смена темы — это подмена значений и пересборка окна.
LIGHT = {name: value for name, value in vars(Color).items()
         if name.isupper() and isinstance(value, str)}

DARK = {
    # Тёмная тема для долгой работы: не чёрный фон и не белый текст, а
    # графит и светло-серый — меньше контраст по краям, глаза устают медленнее.
    "BG": "#15181D", "SURFACE": "#1D2128", "SURFACE_ALT": "#242933",
    "SIDEBAR": "#191C22", "TOPBAR": "#1D2128", "OVERLAY": "#000000",
    "PRIMARY": "#4F7FE8", "PRIMARY_HOVER": "#6390EE", "PRIMARY_PRESSED": "#4270D6",
    "PRIMARY_SOFT": "#1F2B45", "BRAND_TINT": "#25324D", "SECONDARY": "#60A5FA",
    "TEXT": "#E4E7EC", "TEXT_SECONDARY": "#B3BAC6", "TEXT_MUTED": "#98A1B0",
    "TEXT_ON_PRIMARY": "#FFFFFF",
    "BORDER": "#2D333E", "BORDER_STRONG": "#3D4553", "INPUT_BORDER": "#6B7587",
    "FOCUS": "#7FA6F5", "DISABLED_BG": "#2A2F39", "TEXT_ON_DISABLED": "#98A1B0",
    "SUCCESS": "#6FCF97", "SUCCESS_SOFT": "#172A20", "SUCCESS_BORDER": "#27543B",
    "WARNING": "#F0A85B", "WARNING_SOFT": "#2D2416", "WARNING_BORDER": "#5C4522",
    "DANGER": "#F28B82", "DANGER_SOFT": "#321D1C", "DANGER_BORDER": "#6E302C",
    "INFO": "#8DB4F8", "INFO_SOFT": "#1B2740", "INFO_BORDER": "#2F4C82",
    "NEUTRAL": "#B3BAC6", "FINISHED": "#D8DCE4", "FINISHED_SOFT": "#272C35",
    "FINISHED_BORDER": "#3D4553", "NEUTRAL_SOFT": "#272C35", "NEUTRAL_BORDER": "#363D49",
    "SELECTED": "#1F2B45", "HOVER": "#262C36",
    "PARTIAL": "#7CC8F2", "PARTIAL_SOFT": "#152935", "PARTIAL_BORDER": "#204C66",
    "DANGER_SOLID": "#D5453B", "ICON": "#A3ACBA", "PHOTO_BG": "#262B34", "STAR": "#F2B544", "SHADOW": "#000000",
}
# В тёмной теме текст-ссылка акцентного цвета должен читаться на тёмной
# поверхности, а белый текст — на акцентной кнопке. Одного значения на оба
# случая не хватает, поэтому у текста свой, более светлый оттенок.
LIGHT["PRIMARY_TEXT"] = LIGHT["PRIMARY"]
DARK["PRIMARY_TEXT"] = "#8DB4F8"
Color.PRIMARY_TEXT = LIGHT["PRIMARY_TEXT"]

# Цвета линий в файлах значков (assets/icons): по ним значки перекрашиваются
# под тёмную тему.
ICON_SOURCE_COLORS = {"": "#6B7280", "active": "#2563EB", "danger": "#DC2626"}

THEMES = {"light": ("Светлая", LIGHT), "dark": ("Тёмная", DARK)}
_current = "light"
QSS = ""


def current() -> str:
    """Ключ действующей темы: 'light' или 'dark'."""
    return _current


def is_dark() -> bool:
    return _current == "dark"


def apply(name: str) -> str:
    """
    Включает тему: подменяет значения токенов и пересобирает таблицу стилей.

    Возвращает таблицу стилей; её нужно передать в QApplication.setStyleSheet.
    Уже построенные окна держат старые встроенные стили, поэтому после смены
    темы главное окно пересоздаётся (так делает MainWindow.switch_theme).
    """
    global _current, QSS
    if name not in THEMES:
        name = "light"
    for token, value in THEMES[name][1].items():
        setattr(Color, token, value)
    _current = name
    STATUS_TONES.clear()
    STATUS_TONES.update(_build_status_tones())
    globals().update(ACCENT=Color.PRIMARY, OK=Color.SUCCESS, WARN=Color.WARNING,
                     DANGER=Color.DANGER, MUTED=Color.TEXT_SECONDARY)
    QSS = build_qss()
    return QSS


def saved_name() -> str:
    """Тема, выбранная пользователем на этом компьютере (по умолчанию светлая)."""
    try:
        from PySide6.QtCore import QSettings
        value = QSettings("SkladRekvizita", "Interface").value("theme", "light")
        return value if value in THEMES else "light"
    except Exception:                                              # noqa: BLE001
        return "light"


def save_name(name: str) -> None:
    from PySide6.QtCore import QSettings
    QSettings("SkladRekvizita", "Interface").setValue("theme", name)


def px(points: float) -> int:
    """
    Размер шрифта в пикселях по значению в пунктах Windows (96 точек на дюйм).

    Qt переводит пункты в пиксели по-разному: на Windows 10 pt = 13 px, на
    macOS те же 10 pt = 10 px. Из-за этого на Маке весь текст был на четверть
    мельче, чем задумано. В пикселях размер одинаков на обеих системах.
    """
    return int(round(points * 96 / 72))


def _points_to_pixels(qss: str) -> str:
    import re
    return re.sub(r"font-size:\s*([0-9]+(?:\.[0-9]+)?)pt",
                  lambda match: f"font-size: {px(float(match.group(1)))}px", qss)


def build_qss() -> str:
    """Собирает таблицу стилей приложения из токенов."""
    return _points_to_pixels(_build_qss_points())


def _build_qss_points() -> str:
    c, r, s, f = Color, Radius, Space, Font
    return f"""
/* ---------------------------------------------------------------- база */
QWidget {{
    font-family: {f.FAMILY};
    font-size: {f.BODY}pt;
    color: {c.TEXT};
}}
QMainWindow, QDialog {{ background: {c.BG}; }}
QToolTip {{
    background: {c.TEXT}; color: {c.SURFACE}; border: none;
    padding: 6px 9px; border-radius: {r.SM}px; font-size: {f.SMALL}pt;
}}

/* прозрачные области прокрутки: иначе вылезает системный серый */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* ------------------------------------------------------------ сайдбар */
#Sidebar {{ background: {c.SIDEBAR}; border-right: 1px solid {c.BORDER}; }}
#SidebarLogo {{ font-size: {f.H1}pt; font-weight: {f.W_HEADING}; color: {c.TEXT}; }}
#SidebarLogoWord {{ font-size: {f.SMALL}pt; font-weight: {f.W_SEMI}; color: {c.TEXT_SECONDARY}; }}
#SidebarLogoSub {{ font-size: {f.SMALL}pt; color: {c.TEXT_SECONDARY}; }}
#NavButton {{
    background: transparent; border: none; text-align: left;
    padding: 8px 12px; margin: 1px 12px; border-radius: {r.MD}px;
    color: {c.TEXT_SECONDARY}; font-size: {f.BODY}pt;
}}
#NavButton:hover {{ background: {c.HOVER}; color: {c.TEXT}; }}
#NavButton:checked {{
    background: {c.PRIMARY}; color: {c.TEXT_ON_PRIMARY}; font-weight: {f.W_SEMI};
}}
#NavButtonSmall {{
    background: transparent; border: none; text-align: left;
    padding: 6px 12px; margin: 0 12px; border-radius: {r.SM}px;
    color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt;
}}
#NavButtonSmall:hover {{ background: {c.HOVER}; color: {c.TEXT}; }}
#NavSubButton {{
    background: transparent; border: none; text-align: left;
    padding: 6px 10px; border-radius: {r.SM}px;
    color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt;
}}
#NavSubButton:hover {{ background: {c.HOVER}; color: {c.TEXT}; }}
#NavSubButton:checked {{
    background: {c.PRIMARY_SOFT}; color: {c.PRIMARY_TEXT}; font-weight: {f.W_SEMI};
}}
#NavSubClose {{
    background: transparent; border: none; color: {c.TEXT_MUTED};
    font-size: {f.SMALL}pt; padding: 4px;
}}
#NavSubClose:hover {{ color: {c.DANGER}; }}
#NavGroup {{
    color: {c.TEXT_MUTED}; font-size: {f.TINY}pt; font-weight: 700;
    letter-spacing: 0.6px;
}}
#NotifyBadge {{
    background: {c.DANGER_SOLID}; color: #FFFFFF; border-radius: 9px;
    font-size: {f.TINY}pt; font-weight: 700;
}}
#UserCard, #StatCard {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER};
    border-radius: {r.MD}px;
}}
#UserName {{ font-weight: {f.W_SEMI}; font-size: {f.BODY}pt; }}
#UserRole {{ color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt; }}
#StatCaption {{ color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt; }}
#StatValue {{ color: {c.TEXT}; font-size: {f.H2}pt; font-weight: 700; }}
#StatValueOk {{ color: {c.SUCCESS}; font-size: {f.H2}pt; font-weight: 700; }}
#StatHint {{ color: {c.TEXT_MUTED}; font-size: {f.TINY}pt; }}
#VersionLabel {{ color: {c.TEXT_MUTED}; font-size: {f.TINY}pt; }}

/* --------------------------------------------------------- верхняя панель */
#TopBar {{ background: {c.TOPBAR}; border-bottom: 1px solid {c.BORDER}; }}
#PageTitle {{ font-size: {f.H1}pt; font-weight: {f.W_HEADING}; letter-spacing: -0.2px; }}
#PageSubtitle {{ color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt; }}
#Muted {{ color: {c.TEXT_SECONDARY}; }}
#Hint {{ color: {c.TEXT_MUTED}; font-size: {f.SMALL}pt; }}
#SectionTitle {{ font-size: {f.H2}pt; font-weight: {f.W_SEMI}; }}

/* ------------------------------------------------------------- поля ввода */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit, QPlainTextEdit {{
    background: {c.SURFACE};
    border: 1px solid {c.INPUT_BORDER};
    border-radius: {r.MD}px;
    padding: 8px 10px;
    selection-background-color: {c.PRIMARY};
    selection-color: #FFFFFF;
}}
QLineEdit:hover, QComboBox:hover, QDateEdit:hover,
QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {c.TEXT_SECONDARY}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 2px solid {c.FOCUS};
    padding: 7px 9px;
    background: {c.SURFACE};
}}
QLineEdit:read-only {{ background: {c.SURFACE_ALT}; color: {c.TEXT_SECONDARY}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QDateEdit:disabled {{
    background: {c.DISABLED_BG}; color: {c.TEXT_ON_DISABLED}; border-color: {c.BORDER};
}}
#SearchBox {{
    font-size: {f.H3}pt; padding: 10px 14px; border-radius: {r.MD}px;
    background: {c.SURFACE_ALT}; border: 1px solid {c.BORDER};
}}
#SearchBox:focus {{ background: {c.SURFACE}; border: 2px solid {c.FOCUS}; padding: 9px 13px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER};
    border-radius: {r.MD}px; padding: 4px;
    selection-background-color: {c.PRIMARY_SOFT}; selection-color: {c.TEXT};
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 17px; height: 17px; border: 1px solid {c.INPUT_BORDER};
    border-radius: 5px; background: {c.SURFACE};
}}
QCheckBox::indicator:hover {{ border-color: {c.PRIMARY}; }}
QCheckBox::indicator:checked {{ background: {c.PRIMARY}; border-color: {c.PRIMARY}; }}

/* --------------------------------------------------------------- кнопки */
QPushButton {{
    background: {c.SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {r.MD}px;
    padding: 9px 16px;
    color: {c.TEXT};
}}
QPushButton:hover {{ background: {c.HOVER}; border-color: {c.BORDER_STRONG}; }}
QPushButton:pressed {{ background: {c.NEUTRAL_SOFT}; }}
QPushButton:focus {{ border: 2px solid {c.FOCUS}; padding: 8px 15px; }}
QPushButton:disabled {{
    background: {c.DISABLED_BG}; color: {c.TEXT_ON_DISABLED}; border-color: {c.BORDER};
}}
QPushButton#Primary {{
    background: {c.PRIMARY}; color: {c.TEXT_ON_PRIMARY};
    border: 1px solid {c.PRIMARY}; font-weight: {f.W_SEMI}; padding: 10px 18px;
}}
QPushButton#Primary:hover {{ background: {c.PRIMARY_HOVER}; border-color: {c.PRIMARY_HOVER}; }}
QPushButton#Primary:pressed {{ background: {c.PRIMARY_PRESSED}; }}
QPushButton#Primary:disabled {{
    background: {c.DISABLED_BG}; border-color: {c.DISABLED_BG}; color: {c.TEXT_ON_DISABLED};
}}
QPushButton#Danger {{
    background: {c.SURFACE}; color: {c.DANGER}; border: 1px solid {c.DANGER_BORDER};
}}
QPushButton#Danger:hover {{ background: {c.DANGER_SOFT}; }}
QPushButton#Danger:disabled {{ color: {c.TEXT_ON_DISABLED}; border-color: {c.BORDER}; }}
QPushButton#Ghost {{ background: transparent; border: none; color: {c.TEXT_SECONDARY}; }}
QPushButton#Ghost:hover {{ background: {c.HOVER}; color: {c.TEXT}; }}
QPushButton#IconButton {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER};
    border-radius: {r.MD}px; padding: 8px;
}}
QPushButton#IconButton:hover {{ background: {c.HOVER}; }}
QPushButton#PhotoTypeButton {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER}; border-radius: {r.MD}px;
    padding: 14px 16px; text-align: left; color: {c.TEXT};
}}
QPushButton#PhotoTypeButton:hover {{ border-color: {c.PRIMARY}; background: {c.PRIMARY_SOFT}; }}
QPushButton#LinkButton {{
    background: transparent; border: none; color: {c.PRIMARY_TEXT}; padding: 4px 2px;
}}
QPushButton#LinkButton:hover {{ color: {c.PRIMARY_HOVER}; text-decoration: underline; }}

/* -------------------------------------------------------------- таблицы */
QTableView, QTreeWidget, QListWidget {{
    background: {c.SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {r.LG}px;
    gridline-color: {c.BORDER};
    selection-background-color: {c.SELECTED};
    selection-color: {c.TEXT};
    outline: none;
    padding: 2px;
}}
QHeaderView::section {{
    background: {c.SURFACE};
    color: {c.TEXT_SECONDARY};
    padding: 12px 10px;
    border: none;
    border-bottom: 1px solid {c.BORDER};
    font-size: {f.TINY}pt;
    font-weight: 700;
    text-transform: uppercase;
}}
QTableView::item {{ padding: 10px 8px; border-bottom: 1px solid {c.BORDER}; }}
QTableView::item:selected {{ background: {c.SELECTED}; color: {c.TEXT}; }}
QTreeWidget::item {{ padding: 7px 4px; }}
QTreeWidget::item:selected {{ background: {c.PRIMARY_SOFT}; color: {c.PRIMARY_TEXT}; }}
QTreeWidget::item:hover {{ background: {c.HOVER}; }}
QTreeWidget {{ selection-background-color: transparent; }}
QListWidget::item {{ padding: 6px; border-radius: {r.SM}px; }}
QListWidget::item:selected {{ background: {c.PRIMARY_SOFT}; color: {c.TEXT}; }}

/* ----------------------------------------------------------- контейнеры */
QGroupBox {{
    border: 1px solid {c.BORDER}; border-radius: {r.LG}px; margin-top: 16px;
    background: {c.SURFACE}; padding: {s.LG}px; font-size: {f.BODY}pt;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 14px; padding: 0 6px;
    font-weight: {f.W_SEMI}; color: {c.TEXT};
}}
#Card {{ background: {c.SURFACE}; border: 1px solid {c.BORDER}; border-radius: {r.LG}px; }}
#CardAlt {{ background: {c.SURFACE_ALT}; border: 1px solid {c.BORDER}; border-radius: {r.LG}px; }}
#PhotoFrame {{
    background: {c.SURFACE_ALT}; border: 1px solid {c.BORDER}; border-radius: {r.LG}px;
    color: {c.TEXT_MUTED};
}}
#DialogFooter {{
    background: {c.SURFACE}; border-top: 1px solid {c.BORDER};
}}
#Chip {{
    background: {c.NEUTRAL_SOFT}; color: {c.NEUTRAL};
    border-radius: {r.SM}px; padding: 4px 10px; font-size: {f.SMALL}pt;
}}

/* ----------------------------------------------------------------- табы */
QTabWidget::pane {{
    border: 1px solid {c.BORDER}; background: {c.SURFACE};
    border-radius: {r.LG}px; top: -1px;
}}
QTabBar {{ qproperty-drawBase: 0; }}
QTabBar::tab {{
    background: transparent; color: {c.TEXT_SECONDARY};
    padding: 10px 18px; margin-right: 4px;
    border: 1px solid transparent; border-top-left-radius: {r.MD}px;
    border-top-right-radius: {r.MD}px;
}}
QTabBar::tab:hover {{ color: {c.TEXT}; background: {c.HOVER}; }}
QTabBar::tab:selected {{
    background: {c.SURFACE}; color: {c.PRIMARY_TEXT}; font-weight: {f.W_SEMI};
    border: 1px solid {c.BORDER}; border-bottom-color: {c.SURFACE};
}}

/* ----------------------------------------------------------- прокрутка */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px; }}
QScrollBar::handle:vertical {{
    background: {c.BORDER_STRONG}; border-radius: 5px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {c.TEXT_MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 4px; }}
QScrollBar::handle:horizontal {{
    background: {c.BORDER_STRONG}; border-radius: 5px; min-width: 32px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --------------------------------------------------------- прочие блоки */
QProgressBar {{
    background: {c.NEUTRAL_SOFT}; border: none; border-radius: 4px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {c.PRIMARY}; border-radius: 4px; }}
QStatusBar {{ background: {c.SURFACE}; border-top: 1px solid {c.BORDER}; }}
QStatusBar::item {{ border: none; }}
QSplitter::handle {{ background: transparent; width: 10px; }}
QMenu {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER};
    border-radius: {r.MD}px; padding: 6px;
}}
QMenu::item {{ padding: 8px 16px; border-radius: {r.SM}px; }}
QMenu::item:selected {{ background: {c.PRIMARY_SOFT}; color: {c.PRIMARY_TEXT}; }}
QCalendarWidget QAbstractItemView:enabled {{
    background: {c.SURFACE}; selection-background-color: {c.PRIMARY};
    selection-color: #FFFFFF;
}}
#Badge {{ border-radius: {r.SM}px; padding: 5px 10px; font-size: {f.SMALL}pt; font-weight: {f.W_SEMI}; }}
#Notice {{
    background: {c.WARNING_SOFT}; border: 1px solid {c.WARNING_BORDER};
    border-radius: {r.MD}px; padding: 10px 14px; color: {c.WARNING}; font-weight: {f.W_SEMI};
}}
#SavedLabel {{ color: {c.SUCCESS}; font-size: {f.SMALL}pt; }}
#StatTileValue {{ font-size: {f.H1}pt; font-weight: {f.W_HEADING}; color: {c.TEXT}; }}
#StatTileValue[alert="true"] {{ color: {c.DANGER}; }}
#StatTileValue[alert="warn"] {{ color: {c.WARNING}; }}
#ErrorText {{ color: {c.DANGER}; }}
#WarningText {{ color: {c.WARNING}; }}
#EmptyNote {{ color: {c.TEXT_SECONDARY}; padding: 40px; }}
#SubTitle {{ font-size: {f.H3}pt; font-weight: {f.W_SEMI}; }}
#ValueStrong {{ font-weight: {f.W_SEMI}; }}
#Card[tile="true"]:hover {{ border: 1px solid {c.TEXT_MUTED}; }}
#Card[tile="true"][selected="true"] {{
    background: {c.PRIMARY_SOFT}; border: 1px solid {c.PRIMARY};
}}
#TileTitle {{ font-weight: {f.W_SEMI}; }}
#TilePrice {{ font-weight: 700; color: {c.TEXT}; }}
#IconTile {{ background: {c.BRAND_TINT}; border-radius: {r.MD}px; }}
#IconTile[alert="true"] {{ background: {c.DANGER_SOFT}; }}
#CardTitle {{ font-weight: {f.W_SEMI}; }}
#Card[interactive="true"]:hover {{ border: 1px solid {c.PRIMARY}; }}
#BigNumber {{ font-size: {f.H1}pt; font-weight: {f.W_HEADING}; color: {c.PRIMARY_TEXT}; }}

/* ------------------------------------------------------- версия 4.0 */
#SegmentButton {{
    background: transparent; border: 1px solid transparent; border-radius: {r.MD}px;
    padding: 7px 14px; color: {c.TEXT_SECONDARY};
}}
#SegmentButton:hover {{ background: {c.HOVER}; color: {c.TEXT}; }}
#SegmentButton:checked {{
    background: {c.PRIMARY_SOFT}; color: {c.PRIMARY_TEXT}; font-weight: {f.W_SEMI};
    border: 1px solid {c.BORDER};
}}
#SegmentButton:disabled {{ color: {c.TEXT_ON_DISABLED}; }}
#SelectionBar {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER}; border-radius: {r.LG}px;
}}
#Toast {{
    background: {c.TEXT}; color: {c.SURFACE}; border-radius: {r.MD}px;
    padding: 12px 18px; font-weight: {f.W_SEMI};
}}
#Toast[kind="warn"] {{ background: {c.WARNING}; color: {c.SURFACE}; }}
#EmptyTitle {{ font-size: {f.H2}pt; font-weight: {f.W_SEMI}; color: {c.TEXT}; }}
QListView#CatalogGrid {{ background: transparent; border: none; outline: none; }}
QListView#CatalogGrid::item {{ background: transparent; border: none; }}
QTableView#CatalogTable {{ border: none; }}
QTableView {{ alternate-background-color: {c.SURFACE_ALT}; }}
QTableView::item:hover {{ background: {c.HOVER}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QTreeWidget#CategoryTree {{ border: none; }}
#PhotoStrip {{ background: transparent; border: none; }}
#SimilarityBar {{ background: {c.NEUTRAL_SOFT}; border-radius: 3px; }}
#Pill {{
    background: {c.NEUTRAL_SOFT}; color: {c.TEXT_SECONDARY}; border-radius: {r.SM}px;
    padding: 3px 8px; font-size: {f.SMALL}pt;
}}
#StockBar {{ background: {c.NEUTRAL_SOFT}; border: none; border-radius: 3px; }}
#StockBar::chunk {{ background: {c.SUCCESS}; border-radius: 3px; }}
#ResultCard {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER}; border-radius: {r.LG}px;
}}
#ResultCard:hover {{ border: 1px solid {c.TEXT_MUTED}; }}
#ResultCard[selected="true"] {{ border: 2px solid {c.PRIMARY}; background: {c.PRIMARY_SOFT}; }}
"""


QSS = build_qss()
# Дополнения 4.0 подключаются в конце файла (см. _extra_qss).
