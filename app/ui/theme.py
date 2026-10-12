# -*- coding: utf-8 -*-
"""
Дизайн-система «Склад Реквизита 2.1».

Единственный источник цветов, скруглений, отступов и типографики: все экраны
берут стиль отсюда, своих значений не заводят. Палитра снята с визуального
референса — светлый холодный фон, белые поверхности, синий акцент, мягкие
«пилюли» статусов и тонкие серые границы.
"""
from __future__ import annotations


class Color:
    """Цветовые токены. Значения соответствуют референсу версии 2.1."""

    # поверхности
    BG = "#F5F7FA"              # фон рабочей области
    SURFACE = "#FFFFFF"         # карточки, панели, таблицы
    SURFACE_ALT = "#F9FAFB"     # чередование строк, вложенные блоки
    SIDEBAR = "#FFFFFF"         # боковое меню
    TOPBAR = "#FFFFFF"          # верхняя панель
    OVERLAY = "#0F172A"         # затемнение под модальными окнами

    # акценты
    PRIMARY = "#2563EB"
    PRIMARY_HOVER = "#1D4ED8"
    PRIMARY_PRESSED = "#1E40AF"
    PRIMARY_SOFT = "#EFF6FF"    # фон активного пункта меню и выделения
    SECONDARY = "#0EA5E9"       # вторичный акцент (информация, ссылки)

    # текст
    TEXT = "#111827"
    TEXT_SECONDARY = "#4B5563"       # 7.6:1 на белом
    TEXT_MUTED = "#667085"           # 4.9:1 на белом, 4.5+ на фоне — подсказки, подписи
    TEXT_ON_PRIMARY = "#FFFFFF"

    # границы
    BORDER = "#E5E7EB"
    BORDER_STRONG = "#D1D5DB"   # разделители, наведение
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

    SELECTED = "#EFF6FF"
    HOVER = "#F3F6FA"


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
    FAMILY = "'Segoe UI', 'Inter', 'Helvetica Neue', Arial"
    H1 = 20
    H2 = 15
    H3 = 13
    BODY = 10.5
    SMALL = 9.5
    TINY = 9


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
    "partial": (PARTIAL_TEXT, PARTIAL_SOFT, PARTIAL_BORDER),
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


def build_qss() -> str:
    """Собирает таблицу стилей приложения из токенов."""
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
#SidebarLogo {{ font-size: {f.H2}pt; font-weight: 700; color: {c.TEXT}; }}
#SidebarLogoSub {{ font-size: {f.SMALL}pt; color: {c.TEXT_SECONDARY}; }}
#NavButton {{
    background: transparent; border: none; text-align: left;
    padding: 8px 12px; margin: 1px 12px; border-radius: {r.MD}px;
    color: {c.TEXT_SECONDARY}; font-size: {f.BODY}pt;
}}
#NavButton:hover {{ background: {c.HOVER}; color: {c.TEXT}; }}
#NavButton:checked {{
    background: {c.PRIMARY_SOFT}; color: {c.PRIMARY}; font-weight: 600;
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
    background: {c.PRIMARY_SOFT}; color: {c.PRIMARY}; font-weight: 600;
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
    background: {c.DANGER}; color: #FFFFFF; border-radius: 9px;
    font-size: {f.TINY}pt; font-weight: 700;
}}
#UserCard, #StatCard {{
    background: {c.SURFACE}; border: 1px solid {c.BORDER};
    border-radius: {r.MD}px;
}}
#UserName {{ font-weight: 600; font-size: {f.BODY}pt; }}
#UserRole {{ color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt; }}
#StatCaption {{ color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt; }}
#StatValue {{ color: {c.TEXT}; font-size: {f.H2}pt; font-weight: 700; }}
#StatValueOk {{ color: {c.SUCCESS}; font-size: {f.H2}pt; font-weight: 700; }}
#StatHint {{ color: {c.TEXT_MUTED}; font-size: {f.TINY}pt; }}
#VersionLabel {{ color: {c.TEXT_MUTED}; font-size: {f.TINY}pt; }}

/* --------------------------------------------------------- верхняя панель */
#TopBar {{ background: {c.TOPBAR}; border-bottom: 1px solid {c.BORDER}; }}
#PageTitle {{ font-size: {f.H1}pt; font-weight: 700; letter-spacing: -0.2px; }}
#PageSubtitle {{ color: {c.TEXT_SECONDARY}; font-size: {f.SMALL}pt; }}
#Muted {{ color: {c.TEXT_SECONDARY}; }}
#Hint {{ color: {c.TEXT_MUTED}; font-size: {f.SMALL}pt; }}
#SectionTitle {{ font-size: {f.H2}pt; font-weight: 600; }}

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
    border: 1px solid {c.PRIMARY}; font-weight: 600; padding: 10px 18px;
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
    background: transparent; border: none; color: {c.PRIMARY}; padding: 4px 2px;
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
QTreeWidget::item {{ padding: 7px 4px; border-radius: {r.SM}px; }}
QTreeWidget::item:selected {{ background: {c.PRIMARY_SOFT}; color: {c.PRIMARY}; }}
QTreeWidget::item:hover {{ background: {c.HOVER}; }}
QListWidget::item {{ padding: 6px; border-radius: {r.SM}px; }}
QListWidget::item:selected {{ background: {c.PRIMARY_SOFT}; color: {c.TEXT}; }}

/* ----------------------------------------------------------- контейнеры */
QGroupBox {{
    border: 1px solid {c.BORDER}; border-radius: {r.LG}px; margin-top: 16px;
    background: {c.SURFACE}; padding: {s.LG}px; font-size: {f.BODY}pt;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 14px; padding: 0 6px;
    font-weight: 600; color: {c.TEXT};
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
    background: {c.SURFACE}; color: {c.PRIMARY}; font-weight: 600;
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
QMenu::item:selected {{ background: {c.PRIMARY_SOFT}; color: {c.PRIMARY}; }}
QCalendarWidget QAbstractItemView:enabled {{
    background: {c.SURFACE}; selection-background-color: {c.PRIMARY};
    selection-color: #FFFFFF;
}}
#Badge {{ border-radius: {r.SM}px; padding: 5px 10px; font-size: {f.SMALL}pt; font-weight: 600; }}
#Notice {{
    background: {c.WARNING_SOFT}; border: 1px solid {c.WARNING_BORDER};
    border-radius: {r.MD}px; padding: 10px 14px; color: {c.WARNING}; font-weight: 600;
}}
#SavedLabel {{ color: {c.SUCCESS}; font-size: {f.SMALL}pt; }}
#StatTileValue {{ font-size: {f.H1}pt; font-weight: 700; color: {c.TEXT}; }}
#StatTileValue[alert="true"] {{ color: {c.DANGER}; }}
#IconTile {{ background: {c.PRIMARY_SOFT}; border-radius: {r.MD}px; }}
#CardTitle {{ font-weight: 600; }}
#Card[interactive="true"]:hover {{ border: 1px solid {c.PRIMARY}; }}
#BigNumber {{ font-size: {f.H1}pt; font-weight: 700; color: {c.PRIMARY}; }}
"""


QSS = build_qss()
