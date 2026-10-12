# -*- coding: utf-8 -*-
"""Начальное наполнение: дерево категорий и настройки склада по умолчанию."""
from __future__ import annotations

from .database import Database

CATEGORY_TREE: dict[str, list[str]] = {
    "Мебель": ["Стулья", "Табуретки", "Столы", "Журнальные столы", "Диваны", "Кровати",
               "Кресла", "Скамейки и лавки", "Этажерки", "Реквизит", "Пуфы", "Банкетки",
               "Комоды", "Тумбы", "Консоли", "Ширмы", "Шкафы", "Картотеки", "Буфет", "Вешалки"],
    "Авто": ["Автомобильные номера", "Дорожные знаки"],
    "Оружие": ["Пистолеты", "Автоматы", "Спецсредства", "Патроны"],
    "Игрушки": [],
    "Детские": ["Детские коляски"],
    "Бытовая техника": ["Кухонная", "Музыкальная", "Климатическая", "Офисная", "Телефоны", "Камеры"],
    "Посуда": ["Чашки", "Тарелки", "Вилки", "Ложки", "Ножи", "Сервизы", "Чайные наборы",
               "Чайники", "Фужеры", "Подносы"],
    "Праздничный": [],
    "Деревенский быт": [],
    "Бутафория": [],
    "Часы": ["Настенные", "Настольные"],
    "Лампы": ["Настольные", "Торшеры", "Висячие", "Бра", "Уличные"],
    "Чемоданы": ["Чемоданы", "Сумки", "Ручная кладь"],
    "Письменный набор": [],
    "Граммофон": [],
    "Зеркала": ["Настольные", "Настенные", "Трюмо"],
    "Подушки": [], "Кубки": [], "Медали": [], "Награды": [], "Ковры": [], "Абажуры": [],
    "Люстры": [], "Чучела": [], "Манекены": [], "Керосиновые лампы": [], "Подсвечники": [],
    "Пледы": [], "Покрывала": [], "Скатерти": [], "Салфетки": [], "Шторы": [],
    "Постельное белье": [],
    "Цветы": ["Цветы", "Растения", "Вазоны"],
    "Велосипеды": [], "Самокаты": [], "Санки": [], "Спорт": [], "Музыкальные инструменты": [],
    "Сейфы": [], "Офисная мебель": [], "Животные": [], "Печатные машинки": [], "Полки": [],
}

DEFAULT_SETTINGS = {
    "warehouse.name": "Склад Реквизита",
    "warehouse.address": "",
    "warehouse.phone": "",
    "warehouse.email": "",
    "warehouse.inn": "",
    "warehouse.ogrn": "",
    "warehouse.bank_name": "",
    "warehouse.bank_account": "",
    "warehouse.bank_bik": "",
    "warehouse.bank_corr": "",
    "warehouse.logo_path": "",
    "warehouse.extra": "",
    "email.smtp_host": "",
    "email.smtp_port": "465",
    "email.use_ssl": "1",
    "email.login": "",
    "email.from": "",
    "backup.enabled": "1",
    "backup.keep": "14",
    "backup.dir": "",
    "backup.last_run": "",
    "rental.number_prefix": "АР",
    "notify.days_before_end": "2",
    "security.lock_minutes": "15",
}


def seed_if_empty(db: Database) -> None:
    row = db.query_one("SELECT COUNT(*) c FROM categories")
    if row and row["c"] == 0:
        with db.transaction() as conn:
            for i, (parent, children) in enumerate(CATEGORY_TREE.items()):
                cur = conn.execute(
                    "INSERT INTO categories(parent_id, name, sort_order) VALUES (NULL,?,?)",
                    (parent, i),
                )
                pid = cur.lastrowid
                for j, child in enumerate(children):
                    conn.execute(
                        "INSERT INTO categories(parent_id, name, sort_order) VALUES (?,?,?)",
                        (pid, child, j),
                    )
    with db.transaction() as conn:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
