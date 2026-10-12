# -*- coding: utf-8 -*-
"""
Провайдеры хранения резервных копий.

Копия, лежащая на том же диске, что и рабочая база, спасает от ошибки
оператора, но не спасает от смерти диска. Поэтому копию нужно уметь
отправлять наружу — на внешний диск, флешку, сетевую папку, в облако.

Здесь описан общий интерфейс BackupProvider и реализован тот провайдер,
который работает без настроек и внешних служб: копирование в любую
доступную папку. Этого достаточно для внешнего HDD, SSD, флешки и сетевого
диска — в Windows все они выглядят как обычный путь.

Облачные провайдеры (S3, Google Drive, OneDrive) в поставку не входят: им
нужны ключи доступа и сторонние библиотеки, которых в программе нет. Но
интерфейс сделан так, чтобы такой провайдер добавлялся отдельным классом,
без правок остальной программы — регистрируется он вызовом register().

ЧЕСТНО: на сегодня реально работает только локальный провайдер (включая
сетевые пути). Облачные — это подготовленное место, а не работающая
функция; список доступных провайдеров программа показывает как есть.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from ..errors import AppError, ValidationError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteFile:
    """Файл в хранилище провайдера."""
    name: str
    size: int
    modified: str
    location: str          # человекочитаемое «где это лежит»


class BackupProvider(Protocol):
    """
    Куда можно положить резервную копию.

    Реализация обязана быть честной: если отправка не удалась, метод
    поднимает ошибку, а не молчит. Копия, о которой программа сказала
    «отправлена», должна существовать в хранилище.
    """

    key: str               # идентификатор для настроек
    title: str             # что видит пользователь
    needs_setup: bool      # требуется ли настройка (ключи, авторизация)

    def available(self) -> tuple[bool, str]:
        """Готов ли провайдер к работе и почему нет, если не готов."""

    def upload(self, source: Path) -> str:
        """Кладёт файл в хранилище. Возвращает путь или идентификатор копии."""

    def list_files(self) -> list[RemoteFile]:
        """Что уже лежит в хранилище."""

    def download(self, name: str, target: Path) -> Path:
        """Забирает копию обратно — для восстановления."""


class LocalFolderProvider:
    """
    Копирование в папку: внешний диск, флешка, сетевой диск, любая папка.

    В Windows съёмный диск, сетевая шара и обычная папка — это один и тот же
    путь, поэтому отдельных провайдеров для них не нужно. Путь задаётся
    пользователем при отправке или берётся из настроек.
    """

    key = "local"
    title = "Папка или внешний диск"
    needs_setup = False

    def __init__(self, folder: Optional[Path | str] = None):
        self.folder = Path(folder) if folder else None

    def available(self) -> tuple[bool, str]:
        if not self.folder:
            return False, "Не выбрана папка для копий."
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"Папка недоступна: {exc}"
        if not self.folder.is_dir():
            return False, "Указанный путь — не папка."
        probe = self.folder / ".sklad_write_test"
        try:
            probe.write_bytes(b"x")
            probe.unlink()
        except OSError:
            return False, ("В эту папку нельзя писать. Проверьте, подключён ли диск "
                           "и есть ли права на запись.")
        return True, ""

    def upload(self, source: Path) -> str:
        ready, reason = self.available()
        if not ready:
            raise AppError(reason)
        source = Path(source)
        if not source.exists():
            raise AppError("Файл резервной копии не найден.")
        target = self.folder / source.name
        free = self._free_space()
        if free is not None and free < source.stat().st_size * 1.2:
            raise AppError(
                f"На носителе мало места: свободно {free / 1_048_576:.0f} МБ, "
                f"нужно около {source.stat().st_size / 1_048_576:.0f} МБ.")
        # Пишем во временное имя и переименовываем: если носитель выдернут
        # посреди копирования, рядом не останется файла, похожего на целый.
        # Недописанный кусок при этом убираем сами — оставлять мусор на чужой
        # флешке нехорошо, да и в следующий раз он будет только мешать.
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            shutil.copy2(source, temporary)
            temporary.replace(target)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass                     # носителя уже нет — убирать нечего
            raise AppError(
                f"Не удалось скопировать файл на носитель: {exc}. "
                "Проверьте, не отключён ли диск.") from exc
        return str(target)

    def list_files(self) -> list[RemoteFile]:
        ready, _ = self.available()
        if not ready:
            return []
        out = []
        for file in sorted(self.folder.glob("sklad_*.db"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            stat = file.stat()
            from datetime import datetime
            out.append(RemoteFile(
                name=file.name, size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                location=str(file)))
        return out

    def download(self, name: str, target: Path) -> Path:
        ready, reason = self.available()
        if not ready:
            raise AppError(reason)
        source = self.folder / name
        if not source.exists():
            raise AppError(f"В хранилище нет файла «{name}».")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def _free_space(self) -> Optional[int]:
        try:
            return shutil.disk_usage(self.folder).free
        except OSError:
            return None


class CloudProviderStub:
    """
    Место для облачного хранилища.

    Это не работающая отправка в облако, и притворяться ею она не должна:
    любая попытка отправки честно сообщает, что провайдер не подключён.
    Реальная реализация (S3, Google Drive, OneDrive) добавляется отдельным
    классом с теми же методами и регистрируется через register() — остальная
    программа при этом не меняется.
    """

    needs_setup = True

    def __init__(self, key: str, title: str, requirement: str):
        self.key = key
        self.title = title
        self.requirement = requirement

    def available(self) -> tuple[bool, str]:
        return False, (f"{self.title}: хранилище не подключено. {self.requirement}")

    def upload(self, source: Path) -> str:
        raise AppError(self.available()[1])

    def list_files(self) -> list[RemoteFile]:
        return []

    def download(self, name: str, target: Path) -> Path:
        raise AppError(self.available()[1])


_PROVIDERS: dict[str, BackupProvider] = {}


def register(provider: BackupProvider) -> None:
    """Добавляет провайдера в список доступных."""
    _PROVIDERS[provider.key] = provider


def providers() -> list[BackupProvider]:
    return list(_PROVIDERS.values())


def get_provider(key: str) -> BackupProvider:
    provider = _PROVIDERS.get(key)
    if not provider:
        raise ValidationError(f"Хранилище «{key}» не подключено к программе.")
    return provider


def local_provider(folder: Path | str) -> LocalFolderProvider:
    """Провайдер для конкретной папки — путь выбирается в момент отправки."""
    return LocalFolderProvider(folder)


register(LocalFolderProvider())
register(CloudProviderStub(
    "s3", "S3-совместимое хранилище",
    "Нужны адрес сервера, ключ доступа и секретный ключ, а также библиотека boto3."))
register(CloudProviderStub(
    "gdrive", "Google Drive",
    "Нужна учётная запись Google и файл ключа приложения."))
register(CloudProviderStub(
    "onedrive", "OneDrive",
    "Нужна учётная запись Microsoft и разрешение приложения."))
