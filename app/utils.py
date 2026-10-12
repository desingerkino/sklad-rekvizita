# -*- coding: utf-8 -*-
"""
Дата, время и деньги.

Единственный источник текущего времени — системные часы и часовой пояс Windows
(datetime.now() без указания tz берёт локальное системное время). Своих часов
внутри программы нет. В БД даты хранятся в техническом виде (ISO YYYY-MM-DD,
datetime YYYY-MM-DD HH:MM:SS), на экране и в документах — ДД.ММ.ГГГГ и ЧЧ:ММ.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

DATE_DB = "%Y-%m-%d"
DT_DB = "%Y-%m-%d %H:%M:%S"
DATE_UI = "%d.%m.%Y"
TIME_UI = "%H:%M"

FAR_FUTURE = date(9999, 12, 31)   # аренда с неизвестной датой окончания


def now() -> datetime:
    """Текущие дата и время по системным часам Windows."""
    return datetime.now()


def today() -> date:
    return datetime.now().date()


def db_date(d: Optional[date]) -> Optional[str]:
    return d.strftime(DATE_DB) if d else None


def db_dt(dt: Optional[datetime] = None) -> str:
    return (dt or now()).strftime(DT_DB)


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = s.strip()
    for fmt in (DATE_DB, DATE_UI, DT_DB):
        try:
            return datetime.strptime(s[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    for fmt in (DT_DB, "%Y-%m-%dT%H:%M:%S", DATE_DB):
        try:
            return datetime.strptime(s.strip()[:19], fmt)
        except ValueError:
            continue
    return None


def fmt_date(value) -> str:
    d = parse_date(value) if isinstance(value, str) else value
    return d.strftime(DATE_UI) if d else "—"


def fmt_dt(value) -> str:
    dt = parse_dt(value) if isinstance(value, str) else value
    return dt.strftime(f"{DATE_UI} {TIME_UI}") if dt else "—"


def days_left(date_end: Optional[str | date]) -> Optional[int]:
    """Сколько календарных дней осталось до конца аренды (от системной даты)."""
    d = parse_date(date_end) if isinstance(date_end, str) else date_end
    if not d:
        return None
    return (d - today()).days


def overlaps(a_start: date, a_end: Optional[date], b_start: date, b_end: Optional[date]) -> bool:
    """Пересечение периодов аренды по календарным дням (границы включительно)."""
    ae = a_end or FAR_FUTURE
    be = b_end or FAR_FUTURE
    return a_start <= be and b_start <= ae


def money_round(value) -> float:
    """
    Денежное округление до копеек по правилу «половина вверх».

    Встроенный round() округляет 0.125 к чётному (0.12), что для денег
    неверно: сумма скидки и итог должны считаться как в бухгалтерии.
    """
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def money(value: float) -> str:
    """1234567.5 -> '1 234 567,50 ₽'"""
    s = f"{float(value or 0):,.2f}".replace(",", " ").replace(".", ",")
    return f"{s} \u20bd"


def money_plain(value: float) -> str:
    return f"{float(value or 0):,.2f}".replace(",", " ").replace(".", ",")


def add_days(d: date, n: int) -> date:
    return d + timedelta(days=n)


def rental_days(start: date, end: Optional[date]) -> Optional[int]:
    """Длительность аренды в календарных днях, включая первый день."""
    if end is None:
        return None
    return (end - start).days + 1
