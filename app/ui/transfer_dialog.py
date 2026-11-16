# -*- coding: utf-8 -*-
"""
Перенос склада на другой компьютер: выгрузка в файл и загрузка из файла.

Окна здесь только спрашивают путь и пароль и показывают ход работы; всю
работу и все проверки делает services/transfer_service.py.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QFileDialog, QInputDialog, QLineEdit, QMessageBox,
                               QProgressDialog)

from ..errors import AppError
from ..services.transfer_service import EXTENSION, MIN_PASSWORD, transfer_service
from .errors import show_error

FILE_FILTER = f"Файл переноса склада (*{EXTENSION});;Все файлы (*)"


def _progress(parent, title: str) -> tuple[QProgressDialog, callable]:
    dialog = QProgressDialog(title, "", 0, 100, parent)
    dialog.setWindowTitle("Перенос склада")
    dialog.setCancelButton(None)            # прерывать посередине нельзя: это замена данных
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setValue(0)

    def report(done: int, total: int, _name: str = "") -> None:
        dialog.setMaximum(max(1, total))
        dialog.setValue(min(done, total))
        QApplication.processEvents()

    return dialog, report


def _mb(size: int) -> str:
    return f"{size / 1024 / 1024:,.1f} МБ".replace(",", " ")


def export_flow(parent) -> bool:
    """Выгрузка склада в файл. Возвращает True, если файл записан."""
    answer = QMessageBox.question(
        parent, "Выгрузить склад в файл",
        "Будет собран один файл со всем складом: предметы, категории, фотографии, "
        "аренды, клиенты, сотрудники и документы.\n\n"
        "Этот файл открывается на любом компьютере с программой — на Windows и на "
        "macOS. Он защищается паролем, который вы зададите сейчас. Без пароля файл "
        "не открыть, восстановить забытый пароль нельзя.\n\nПродолжить?",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    if answer != QMessageBox.Yes:
        return False
    password, ok = QInputDialog.getText(
        parent, "Пароль файла переноса",
        f"Придумайте пароль (не короче {MIN_PASSWORD} символов):", QLineEdit.Password)
    if not ok:
        return False
    if len(password) < MIN_PASSWORD:
        show_error(parent, AppError(f"Пароль должен быть не короче {MIN_PASSWORD} символов."))
        return False
    repeat, ok = QInputDialog.getText(
        parent, "Пароль файла переноса", "Повторите пароль:", QLineEdit.Password)
    if not ok:
        return False
    if repeat != password:
        show_error(parent, AppError("Пароли не совпадают."))
        return False
    name, _ = QFileDialog.getSaveFileName(
        parent, "Куда сохранить файл переноса",
        str(Path.home() / f"Склад реквизита{EXTENSION}"), FILE_FILTER)
    if not name:
        return False
    dialog, report = _progress(parent, "Собираю файл переноса…")
    try:
        result = transfer_service.export_archive(Path(name), password, report)
    except AppError as exc:
        dialog.close()
        show_error(parent, exc)
        return False
    except Exception as exc:                                       # noqa: BLE001
        dialog.close()
        show_error(parent, exc, "Собрать файл переноса не удалось.")
        return False
    dialog.close()
    QMessageBox.information(
        parent, "Файл переноса готов",
        f"Файл: {result['path']}\nРазмер: {_mb(result['size'])}\n\n"
        f"Предметов: {result['inventory_items']}\nФотографий: {result['inventory_photos']}\n"
        f"Категорий: {result['categories']}\nАренд: {result['rentals']}\n\n"
        "Перенесите файл на другой компьютер (флешка, внешний диск) и там выберите: "
        "Настройки → Резервные копии → «Загрузить склад из файла».")
    return True


def import_flow(parent, first_run: bool = False) -> bool:
    """Загрузка склада из файла. Возвращает True, если склад заменён."""
    name, _ = QFileDialog.getOpenFileName(
        parent, "Файл переноса склада", str(Path.home()), FILE_FILTER)
    if not name:
        return False
    try:
        manifest = transfer_service.inspect(Path(name))
    except AppError as exc:
        show_error(parent, exc)
        return False
    counts = manifest.get("counts", {})
    created = str(manifest.get("created", "")).replace("T", " ")
    system = {"Windows": "Windows", "Darwin": "macOS"}.get(manifest.get("source_os"), "—")
    warning = ("" if first_run else
               "\n\nСклад на ЭТОМ компьютере будет заменён содержимым файла. Прежние база "
               "и фотографии не удаляются — они сохранятся рядом, в папке данных, и их "
               "можно будет вернуть.")
    answer = QMessageBox.question(
        parent, "Загрузить склад из файла",
        f"Файл создан: {created} ({system}, версия {manifest.get('app_version')})\n\n"
        f"Предметов: {counts.get('inventory_items', '—')}\n"
        f"Фотографий: {counts.get('inventory_photos', '—')}\n"
        f"Категорий: {counts.get('categories', '—')}\n"
        f"Аренд: {counts.get('rentals', '—')}\n"
        f"Сотрудников: {counts.get('users', '—')}{warning}\n\nЗагрузить?",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if answer != QMessageBox.Yes:
        return False
    password, ok = QInputDialog.getText(
        parent, "Пароль файла переноса", "Пароль, заданный при выгрузке:", QLineEdit.Password)
    if not ok:
        return False
    dialog, report = _progress(parent, "Загружаю склад из файла…")
    try:
        result = transfer_service.import_archive(Path(name), password, report,
                                                 require_admin=not first_run)
    except AppError as exc:
        dialog.close()
        show_error(parent, exc)
        return False
    except Exception as exc:                                       # noqa: BLE001
        dialog.close()
        show_error(parent, exc, "Загрузить склад не удалось. Прежние данные возвращены на место.")
        return False
    dialog.close()
    missing = result.get("missing_photos") or []
    text = (f"Предметов: {result['inventory_items']}\nФотографий: {result['inventory_photos']}\n"
            f"Категорий: {result['categories']}\nАренд: {result['rentals']}\n"
            f"Сотрудников: {result['users']}\n\n")
    if missing:
        text += (f"Внимание: в файле не оказалось {len(missing)} фотографий, записанных "
                 "в базе. Остальное загружено.\n\n")
    text += "Сейчас откроется экран входа: войдите логином и паролем из перенесённого склада."
    QMessageBox.information(parent, "Склад загружен", text)
    return True
