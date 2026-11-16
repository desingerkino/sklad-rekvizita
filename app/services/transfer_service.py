# -*- coding: utf-8 -*-
"""
Перенос склада между компьютерами (Windows ↔ macOS) одним файлом.

Зачем отдельный механизм. Обычные резервные копии шифруются ключом, который
хранится на этом компьютере и привязан к учётной записи (на Windows — через
DPAPI). Это защищает данные, но значит и другое: копию нельзя открыть на
другом компьютере, а после переустановки системы — и на этом. Файл переноса
решает обе задачи: он защищён паролем, который задаёт человек, и содержит
всё, что нужно, чтобы склад заработал на новом месте.

Состав файла (*.skladpack, обычный ZIP):
    manifest.json        описание: версии, счётчики, контрольная сумма базы
    data.db.enc          снимок базы, зашифрован паролем (scrypt + AES-256-GCM)
    master.key.enc       ключ шифрования личных данных, зашифрован тем же паролем
    photos/…             фотографии реквизита: рабочие копии, миниатюры, исходники
    avatars/…            фотографии профилей сотрудников
    documents/….enc      оформленные документы (в них личные данные — зашифрованы)
    extra/logo.*         логотип склада, если задан

Пути внутри архива всегда с прямой косой чертой и относительные, поэтому файл,
собранный на Windows, читается на macOS и наоборот. Связи предметов с
фотографиями не теряются: в базе хранятся те же относительные пути.

Загрузка ЗАМЕНЯЕТ склад на этом компьютере содержимым файла (а не сливает два
склада — так не возникает дублей). Перед заменой прежние база, ключ и папка
фотографий сохраняются рядом; при любой ошибке всё возвращается на место.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import shutil
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from .. import paths
from ..db.database import Database, get_db, set_db
from ..errors import AppError
from ..version import DB_SCHEMA_VERSION, VERSION

log = logging.getLogger(__name__)

FORMAT = 1
EXTENSION = ".skladpack"
MIN_PASSWORD = 6
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
_CHUNK = 1024 * 1024

Progress = Optional[Callable[[int, int, str], None]]


def _derive(password: str, salt: bytes, params: dict) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(params["n"]),
                          r=int(params["r"]), p=int(params["p"]), dklen=32,
                          maxmem=256 * 1024 * 1024)


def _encrypt(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, None)


def _decrypt(key: bytes, blob: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).decrypt(blob[:12], blob[12:], None)


def _safe_member(name: str) -> Optional[PurePosixPath]:
    """Путь внутри архива, если он безопасен (без выхода из папки и абсолютных путей)."""
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts or ":" in path.parts[0]:
        return None
    return path


class TransferService:
    # --------------------------------------------------------------- выгрузка
    def export_archive(self, target: Path | str, password: str,
                       progress: Progress = None) -> dict:
        """
        Собирает файл переноса. Возвращает сводку: что и сколько записано.

        База снимается штатным онлайн-копированием SQLite, поэтому выгружать
        можно во время работы — снимок получается целостным.
        """
        from .auth_service import auth_service
        from .audit_service import audit_service
        from .crypto_service import crypto_service
        from .settings_service import settings_service

        auth_service.require_admin("Выгружать склад для переноса может только администратор.")
        if len(password or "") < MIN_PASSWORD:
            raise AppError(f"Пароль файла переноса — не короче {MIN_PASSWORD} символов.")
        if not crypto_service.available:
            raise AppError("На этом компьютере недоступно шифрование — файл переноса "
                           "собрать нельзя. Переустановите программу.")
        target = Path(target)
        if target.suffix.lower() != EXTENSION:
            target = target.with_name(target.name + EXTENSION)
        target.parent.mkdir(parents=True, exist_ok=True)

        db = get_db()
        snapshot = paths.TEMP_DIR / f"transfer_{datetime.now():%H%M%S%f}.db"
        partial = target.with_name(target.name + ".part")
        try:
            with closing(sqlite3.connect(str(snapshot))) as dest:
                with dest:
                    db.conn.backup(dest)
            plain = snapshot.read_bytes()
            salt = os.urandom(16)
            key = _derive(password, salt, _SCRYPT)
            master = crypto_service._load_key()                    # noqa: SLF001

            files: list[tuple[Path, str, bool]] = []       # (файл, имя в архиве, шифровать)
            for folder, prefix, secret in ((paths.PHOTOS_DIR, "photos", False),
                                           (paths.AVATARS_DIR, "avatars", False),
                                           (paths.DOCS_DIR, "documents", True)):
                if not folder.exists():
                    continue
                for path in sorted(folder.rglob("*")):
                    relative = path.relative_to(folder)
                    if not path.is_file() or relative.parts[0] == "_trash":
                        continue                    # корзина удалённых фото не переносится
                    name = f"{prefix}/{relative.as_posix()}" + (".enc" if secret else "")
                    files.append((path, name, secret))
            logo = Path(settings_service.get("warehouse.logo_path") or "")
            logo_name = ""
            if str(logo) not in ("", ".") and logo.is_file():
                logo_name = f"extra/logo{logo.suffix.lower()}"
                files.append((logo, logo_name, False))

            counts = {table: db.query_one(f"SELECT COUNT(*) c FROM {table}")["c"]   # noqa: S608
                      for table in ("inventory_items", "inventory_photos", "categories",
                                    "rentals", "clients", "users")}
            manifest = {
                "format": FORMAT, "app_version": VERSION, "schema_version": DB_SCHEMA_VERSION,
                "created": datetime.now().isoformat(timespec="seconds"),
                "source_os": platform.system(), "counts": counts,
                "files": len(files), "logo": logo_name,
                "kdf": {"name": "scrypt", "salt": base64.b64encode(salt).decode("ascii"),
                        **_SCRYPT},
                "db_sha256": hashlib.sha256(plain).hexdigest(),
            }
            total = len(files) + 2
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.writestr("manifest.json",
                                 json.dumps(manifest, ensure_ascii=False, indent=1))
                archive.writestr("data.db.enc", _encrypt(key, plain))
                archive.writestr("master.key.enc", _encrypt(key, master))
                if progress:
                    progress(2, total, "база данных")
                for index, (path, name, secret) in enumerate(files, 3):
                    if secret:
                        archive.writestr(name, _encrypt(key, path.read_bytes()))
                    else:
                        archive.write(path, name)
                    if progress and (index % 25 == 0 or index == total):
                        progress(index, total, name)
            with zipfile.ZipFile(partial) as check:
                broken = check.testzip()
                if broken:
                    raise AppError(f"Файл переноса записан с ошибкой ({broken}). "
                                   "Проверьте диск и повторите.")
            os.replace(partial, target)
        except OSError as exc:
            raise AppError(f"Не удалось записать файл переноса: {exc}") from exc
        finally:
            snapshot.unlink(missing_ok=True)
            partial.unlink(missing_ok=True)

        audit_service.log("Склад выгружен для переноса", entity="backup",
                          details=f"{target.name}: предметов {counts['inventory_items']}, "
                                  f"файлов {len(files)}")
        return {"path": target, "size": target.stat().st_size, "files": len(files), **counts}

    # --------------------------------------------------------------- просмотр
    def inspect(self, source: Path | str) -> dict:
        """Описание файла переноса без пароля: откуда, когда, сколько предметов."""
        source = Path(source)
        try:
            with zipfile.ZipFile(source) as archive:
                manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
            raise AppError("Это не файл переноса склада или он повреждён.") from exc
        if manifest.get("format") != FORMAT:
            raise AppError("Файл переноса создан другой версией программы и здесь "
                           "не читается. Обновите программу.")
        if int(manifest.get("schema_version", 0)) > DB_SCHEMA_VERSION:
            raise AppError(
                f"Файл создан более новой версией программы ({manifest.get('app_version')}). "
                "Сначала обновите программу на этом компьютере.")
        return manifest

    # --------------------------------------------------------------- загрузка
    def import_archive(self, source: Path | str, password: str,
                       progress: Progress = None, require_admin: bool = True) -> dict:
        """
        Заменяет склад на этом компьютере содержимым файла переноса.

        require_admin=False используется только при первом запуске, когда
        сотрудников в базе ещё нет и входить некому.
        """
        from .auth_service import auth_service
        from .crypto_service import crypto_service

        if require_admin:
            auth_service.require_admin("Загружать склад из файла может только администратор.")
        source = Path(source)
        manifest = self.inspect(source)
        if not crypto_service.available:
            raise AppError("На этом компьютере недоступно шифрование — файл переноса "
                           "загрузить нельзя. Переустановите программу.")

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        staged = paths.TEMP_DIR / f"transfer_in_{stamp}.db"
        try:
            with zipfile.ZipFile(source) as archive:
                kdf = manifest["kdf"]
                key = _derive(password or "", base64.b64decode(kdf["salt"]), kdf)
                try:
                    master = _decrypt(key, archive.read("master.key.enc"))
                    plain = _decrypt(key, archive.read("data.db.enc"))
                except Exception as exc:                           # noqa: BLE001
                    raise AppError("Неверный пароль файла переноса.") from exc
                if hashlib.sha256(plain).hexdigest() != manifest.get("db_sha256"):
                    raise AppError("База в файле переноса повреждена (не сошлась "
                                   "контрольная сумма). Соберите файл заново.")
                staged.write_bytes(plain)
                with closing(sqlite3.connect(f"file:{staged.as_posix()}?mode=ro",
                                             uri=True)) as probe:
                    if probe.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise AppError("База в файле переноса повреждена.")
                    probe.execute("SELECT COUNT(*) FROM inventory_items").fetchone()
                broken = archive.testzip()
                if broken:
                    raise AppError(f"Файл переноса повреждён ({broken}). Скопируйте его "
                                   "заново с исходного компьютера.")
                result = self._replace(archive, manifest, key, master, staged, stamp, progress)
        except (zipfile.BadZipFile, sqlite3.Error, OSError) as exc:
            raise AppError(f"Не удалось загрузить файл переноса: {exc}") from exc
        finally:
            staged.unlink(missing_ok=True)
        return result

    def _replace(self, archive: zipfile.ZipFile, manifest: dict, key: bytes, master: bytes,
                 staged: Path, stamp: str, progress: Progress) -> dict:
        from .crypto_service import _KEY_FILE, crypto_service, key_dir

        backups = paths.BACKUPS_DIR
        backups.mkdir(parents=True, exist_ok=True)
        safety_db = backups / f"before_transfer_{stamp}.db.plain"
        safety_key = backups / f"master.key.before_transfer_{stamp}"
        key_file = key_dir() / _KEY_FILE
        moved: list[tuple[Path, Path]] = []          # (новое место прежней папки, её место)
        db = get_db()
        had_db = paths.DB_PATH.exists()

        # 1. Сохраняем прежнее состояние. Снимок базы пишется БЕЗ шифрования
        #    прежним ключом — иначе после смены ключа его нечем было бы открыть;
        #    он лежит в папке копий на этом же компьютере.
        if had_db:
            with closing(sqlite3.connect(str(safety_db))) as dest:
                with dest:
                    db.conn.backup(dest)
        if key_file.exists():
            shutil.copyfile(key_file, safety_key)
        old_key = crypto_service._key                              # noqa: SLF001

        try:
            for folder in (paths.PHOTOS_DIR, paths.AVATARS_DIR, paths.DOCS_DIR):
                if folder.exists() and any(folder.iterdir()):
                    parked = folder.with_name(f"{folder.name}_before_transfer_{stamp}")
                    os.replace(folder, parked)
                    moved.append((parked, folder))
                folder.mkdir(parents=True, exist_ok=True)

            # 2. Файлы
            names = [n for n in archive.namelist() if not n.endswith("/")]
            roots = {"photos": paths.PHOTOS_DIR, "avatars": paths.AVATARS_DIR,
                     "documents": paths.DOCS_DIR}
            written = 0
            logo_target: Optional[Path] = None
            for index, name in enumerate(names, 1):
                member = _safe_member(name)
                if member is None or member.parts[0] not in (*roots, "extra"):
                    continue
                if member.parts[0] == "extra":
                    if name == manifest.get("logo"):
                        logo_target = paths.DATA_DIR / member.name
                        logo_target.write_bytes(archive.read(name))
                    continue
                relative = Path(*member.parts[1:])
                secret = member.parts[0] == "documents" and name.endswith(".enc")
                if secret:
                    relative = relative.with_name(relative.name[:-len(".enc")])
                target = roots[member.parts[0]] / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if secret:
                    target.write_bytes(_decrypt(key, archive.read(name)))
                else:
                    with archive.open(name) as stream, open(target, "wb") as out:
                        shutil.copyfileobj(stream, out, _CHUNK)
                written += 1
                if progress and (index % 25 == 0 or index == len(names)):
                    progress(index, len(names), name)

            # 3. База: страницы переписываются на месте, как при восстановлении
            db.close()
            with closing(sqlite3.connect(str(staged))) as src, \
                    closing(sqlite3.connect(str(paths.DB_PATH))) as dest:
                src.backup(dest)
                try:
                    dest.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass

            # 4. Ключ личных данных — запечатывается уже для этого компьютера
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(crypto_service._seal(master))     # noqa: SLF001
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            crypto_service._key = master                           # noqa: SLF001

            new_db = Database()
            new_db.initialize()                    # заодно обновит схему старого файла
            set_db(new_db)
            if logo_target is not None:
                new_db.execute(
                    "INSERT INTO settings(key, value) VALUES ('warehouse.logo_path', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(logo_target),))
        except Exception:
            log.exception("Загрузка файла переноса не удалась — возвращаю прежние данные")
            self._rollback(had_db, safety_db, safety_key, key_file, old_key, moved)
            raise

        # 5. Проверка результата: каждая фотография из базы должна быть на диске
        from .image_search import image_search
        from .photo_service import abs_path

        image_search.invalidate()
        missing = [row["rel_path"] for row in new_db.query(
            "SELECT rel_path FROM inventory_photos") if not abs_path(row["rel_path"]).exists()]
        counts = {table: new_db.query_one(f"SELECT COUNT(*) c FROM {table}")["c"]  # noqa: S608
                  for table in ("inventory_items", "inventory_photos", "categories",
                                "rentals", "clients", "users")}
        try:
            new_db.execute(
                "INSERT INTO audit_log(action, entity, details) VALUES (?,?,?)",
                ("Склад загружен из файла переноса", "backup",
                 f"предметов {counts['inventory_items']}, файлов {written}; прежние данные "
                 f"сохранены с отметкой {stamp}"))
        except sqlite3.Error:
            log.info("Запись в журнал действий о переносе не добавлена")
        log.info("Склад загружен из файла переноса: %s", counts)
        return {**counts, "files": written, "missing_photos": missing, "stamp": stamp,
                "expected": manifest.get("counts", {}),
                "previous_kept": [str(parked) for parked, _ in moved]}

    @staticmethod
    def _rollback(had_db: bool, safety_db: Path, safety_key: Path, key_file: Path,
                  old_key, moved: list[tuple[Path, Path]]) -> None:
        from .crypto_service import crypto_service

        try:
            get_db().close()
        except Exception:                                          # noqa: BLE001
            pass
        try:
            if had_db and safety_db.exists():
                with closing(sqlite3.connect(str(safety_db))) as src, \
                        closing(sqlite3.connect(str(paths.DB_PATH))) as dest:
                    src.backup(dest)
            if safety_key.exists():
                shutil.copyfile(safety_key, key_file)
            crypto_service._key = old_key                          # noqa: SLF001
            for parked, original in moved:
                if original.exists():
                    shutil.rmtree(original, ignore_errors=True)
                os.replace(parked, original)
        except Exception:                                          # noqa: BLE001
            log.exception("Не удалось полностью вернуть прежние данные после сбоя переноса")
        restored = Database()
        restored.initialize()
        set_db(restored)


transfer_service = TransferService()
