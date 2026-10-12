# -*- coding: utf-8 -*-
"""
Пути к пользовательским данным.

Рабочая база, фотографии, логи и резервные копии НИКОГДА не лежат рядом с .exe
и не лежат во временных папках Windows. Используется %LOCALAPPDATA%\\SkladRekvizita.
Благодаря этому обновление/переустановка программы не уничтожает данные,
а перенос папки приложения не ломает ссылки на фотографии (они относительные).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .version import APP_ID


def _base_data_dir() -> Path:
    env = os.environ.get("SKLAD_DATA_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / APP_ID
        return Path.home() / "AppData" / "Local" / APP_ID
    # Linux/macOS — только для разработки и тестов
    return Path.home() / ".local" / "share" / APP_ID


DATA_DIR = _base_data_dir()
DB_PATH = DATA_DIR / "data.db"
PHOTOS_DIR = DATA_DIR / "photos"
BACKUPS_DIR = DATA_DIR / "backups"
LOGS_DIR = DATA_DIR / "logs"
DOCS_DIR = DATA_DIR / "documents"
AVATARS_DIR = DATA_DIR / "avatars"
TEMP_DIR = DATA_DIR / "temp"


def app_root() -> Path:
    """Папка с ресурсами приложения (работает и в PyInstaller onefile)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def assets_dir() -> Path:
    return app_root() / "assets"


def ensure_dirs() -> None:
    for d in (DATA_DIR, PHOTOS_DIR, BACKUPS_DIR, LOGS_DIR, DOCS_DIR, TEMP_DIR, AVATARS_DIR):
        d.mkdir(parents=True, exist_ok=True)
