# -*- coding: utf-8 -*-
"""
Резервное копирование.

Используется штатный механизм онлайн-бэкапа SQLite: копия снимается на работающей
базе и всегда остаётся целостной. Рабочая копия никогда не удаляется — перед
восстановлением из бэкапа текущая база сохраняется отдельным файлом.
"""
from __future__ import annotations

import gc
import logging
import sqlite3
import time
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import paths
from ..db.database import get_db, set_db, Database
from ..errors import AppError
from .audit_service import audit_service
from .auth_service import auth_service
from .crypto_service import crypto_service
from .settings_service import settings_service

log = logging.getLogger(__name__)


class BackupService:
    def backup_dir(self) -> Path:
        custom = settings_service.get("backup.dir", "")
        directory = Path(custom) if custom else paths.BACKUPS_DIR
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def create(self, with_photos: bool = False, tag: str = "") -> Path:
        """Создаёт копию базы. Опционально — вместе с архивом фотографий."""
        auth_service.require_login()
        db = get_db()
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        name = f"sklad_{stamp}{('_' + tag) if tag else ''}.db"
        target = self.backup_dir() / name
        # копия сначала снимается во временный файл рядом, затем шифруется:
        # незашифрованная копия с персональными данными не остаётся на диске
        raw = target.with_suffix(".db.tmp")
        try:
            with closing(sqlite3.connect(str(raw))) as dest:
                with dest:
                    db.conn.backup(dest)
            if crypto_service.available:
                crypto_service.encrypt_file(raw, target)
                raw.unlink(missing_ok=True)
            else:
                raw.replace(target)
                log.error("Резервная копия сохранена без шифрования: нет библиотеки cryptography")
        except sqlite3.Error as exc:
            raw.unlink(missing_ok=True)
            raise AppError("Не удалось создать резервную копию базы данных.") from exc
        finally:
            if raw.exists():
                raw.unlink(missing_ok=True)

        if with_photos and paths.PHOTOS_DIR.exists():
            zip_path = target.with_suffix(".photos.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in paths.PHOTOS_DIR.rglob("*"):
                    if file.is_file():
                        zf.write(file, file.relative_to(paths.PHOTOS_DIR))

        settings_service.set("backup.last_run", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.rotate()
        audit_service.log("Создана резервная копия", entity="backup", details=target.name)
        log.info("Создана резервная копия %s", target)
        return target

    # ------------------------------------------------------- проверка копии
    def verify(self, backup_path: str | Path, deep: bool = True) -> dict:
        """
        Проверяет, что из копии действительно можно восстановиться.

        Резервная копия, которую невозможно восстановить, копией не является,
        поэтому проверка идёт до конца: файл открывается, при необходимости
        расшифровывается, проверяется целостность страниц SQLite, и данные
        реально читаются — таблицы, аренды, реквизит, пользователи.

        При deep=True делается ещё и тестовое восстановление: содержимое копии
        переписывается во временную базу в стороне от рабочей. Рабочая база при
        этом не затрагивается никак.

        Возвращает словарь с полем ok и человеческим описанием.
        """
        source = Path(backup_path)
        checks: list[str] = []
        problems: list[str] = []

        if not source.exists():
            return self._verdict(False, ["Файла копии нет на месте."], [], source)
        if source.stat().st_size < 1024:
            return self._verdict(False, ["Файл копии подозрительно мал — он повреждён."],
                                 [], source)
        checks.append("файл на месте")

        plain = source
        temporary: Optional[Path] = None
        encrypted = crypto_service.is_encrypted_file(source)
        if encrypted:
            temporary = paths.TEMP_DIR / f"verify_{datetime.now():%H%M%S_%f}.db"
            try:
                crypto_service.decrypt_file(source, temporary)
            except ValueError as exc:
                temporary.unlink(missing_ok=True)
                return self._verdict(
                    False, [f"Расшифровать копию не удалось: {exc}. "
                            "Возможно, она сделана на другом компьютере или под другой "
                            "учётной записью Windows."], checks, source)
            plain = temporary
            checks.append("расшифровка прошла")
        else:
            checks.append("копия не зашифрована")
            problems.append("копия хранится без шифрования")

        try:
            with closing(sqlite3.connect(f"file:{plain}?mode=ro", uri=True)) as probe:
                integrity = probe.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise sqlite3.DatabaseError(f"проверка целостности: {integrity}")
                checks.append("структура базы цела")

                counts = {}
                for table in ("inventory_items", "rentals", "rental_items", "users",
                              "categories", "clients"):
                    counts[table] = probe.execute(
                        f"SELECT COUNT(*) FROM {table}").fetchone()[0]   # noqa: S608
                version = probe.execute("PRAGMA user_version").fetchone()[0]
                checks.append(f"данные читаются: реквизита {counts['inventory_items']}, "
                              f"аренд {counts['rentals']}, пользователей {counts['users']}")
                if counts["users"] == 0:
                    problems.append("в копии нет ни одного пользователя")
        except sqlite3.Error as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            return self._verdict(False, [f"Файл повреждён или это не копия этой "
                                         f"программы ({exc})."], checks, source)

        if deep:
            probe_db = paths.TEMP_DIR / f"restore_probe_{datetime.now():%H%M%S_%f}.db"
            try:
                with closing(sqlite3.connect(str(plain))) as src, \
                        closing(sqlite3.connect(str(probe_db))) as dest:
                    src.backup(dest)
                    dest.execute("PRAGMA integrity_check").fetchone()
                    dest.execute("SELECT COUNT(*) FROM inventory_items").fetchone()
                checks.append("тестовое восстановление прошло")
            except sqlite3.Error as exc:
                if temporary:
                    temporary.unlink(missing_ok=True)
                return self._verdict(
                    False, [f"Тестовое восстановление не удалось: {exc}."], checks, source)
            finally:
                for suffix in ("", "-wal", "-shm"):
                    Path(str(probe_db) + suffix).unlink(missing_ok=True)

        if temporary:
            temporary.unlink(missing_ok=True)

        result = self._verdict(True, problems, checks, source)
        result["schema_version"] = version
        result["counts"] = counts
        result["encrypted"] = encrypted
        return result

    @staticmethod
    def _verdict(ok: bool, problems: list[str], checks: list[str], source: Path) -> dict:
        if ok and not problems:
            summary = "Резервная копия проверена: восстановление возможно."
        elif ok:
            summary = ("Резервная копия читается, но есть замечания: "
                       + "; ".join(problems) + ".")
        else:
            summary = "ПРОВЕРКА НЕ ПРОЙДЕНА. " + " ".join(problems)
        return {"ok": ok, "summary": summary, "checks": checks,
                "problems": problems, "name": source.name, "path": str(source)}

    # ------------------------------------------------- отправка на носитель
    def export_to(self, backup_path: str | Path, provider) -> str:
        """
        Отправляет готовую копию во внешнее хранилище.

        Перед отправкой копия проверяется: отправлять наружу заведомо битый
        файл бессмысленно и опасно — им потом попытаются восстановиться.
        """
        auth_service.require_admin("Выгружать резервные копии может только администратор.")
        source = Path(backup_path)
        verdict = self.verify(source, deep=False)
        if not verdict["ok"]:
            raise AppError("Копия не прошла проверку и не была выгружена. "
                           + verdict["summary"])
        location = provider.upload(source)
        photos = source.with_suffix(".photos.zip")
        if photos.exists():
            try:
                provider.upload(photos)
            except AppError:
                log.warning("Архив фотографий не выгружен: %s", photos.name)
        settings_service.set("backup.last_export", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        settings_service.set("backup.last_export_where", str(location))
        audit_service.log("Резервная копия выгружена", entity="backup",
                          details=f"{source.name} → {location}")
        log.info("Копия %s выгружена в %s", source.name, location)
        return location

    def create_verified(self, with_photos: bool = False, tag: str = "",
                        provider=None) -> dict:
        """
        Создаёт копию и сразу её проверяет.

        Пользователю нельзя показывать «успешно», если файл на самом деле
        повреждён, поэтому результат создания — это всегда результат проверки.
        Если задан провайдер, проверенная копия ещё и выгружается наружу.
        """
        path = self.create(with_photos=with_photos, tag=tag)
        verdict = self.verify(path)
        verdict["path"] = str(path)
        verdict["exported_to"] = ""
        if not verdict["ok"]:
            audit_service.log("Резервная копия не прошла проверку", entity="backup",
                              details=verdict["summary"])
            log.error("Копия %s не прошла проверку: %s", path.name, verdict["summary"])
            return verdict
        if provider is not None:
            try:
                verdict["exported_to"] = self.export_to(path, provider)
            except AppError as exc:
                verdict["problems"].append(str(exc))
                verdict["summary"] = (
                    "Копия создана и проверена, но выгрузить её на носитель не "
                    f"удалось: {exc}")
        return verdict

    def rotate(self) -> None:
        """Ротация: остаются N последних копий, остальные удаляются."""
        keep = max(1, settings_service.get_int("backup.keep", 14))
        files = sorted(self.backup_dir().glob("sklad_*.db"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        for old in files[keep:]:
            try:
                photos_zip = old.with_suffix(".photos.zip")
                old.unlink()
                if photos_zip.exists():
                    photos_zip.unlink()
            except OSError:
                log.warning("Не удалось удалить старую копию %s", old)

    def list_backups(self) -> list[dict]:
        out = []
        for f in sorted(self.backup_dir().glob("sklad_*.db"),
                        key=lambda p: p.stat().st_mtime, reverse=True):
            stat = f.stat()
            out.append({
                "path": str(f),
                "name": f.name,
                "size_mb": round(stat.st_size / 1_048_576, 2),
                "created": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                "photos": f.with_suffix(".photos.zip").exists(),
                "encrypted": crypto_service.is_encrypted_file(f),
            })
        return out

    def auto_backup_if_needed(self) -> Optional[Path]:
        """Ежедневная автоматическая копия при запуске программы."""
        if not settings_service.get_bool("backup.enabled", True):
            return None
        last = settings_service.get("backup.last_run", "")
        if last[:10] == datetime.now().strftime("%Y-%m-%d"):
            return None
        try:
            return self.create(tag="auto")
        except Exception:
            log.exception("Автоматическое резервное копирование не выполнено")
            return None

    @staticmethod
    def _remove_locked(path: Path, attempts: int = 10, pause: float = 0.2) -> bool:
        """
        Пытается удалить файл журнала. Никогда не прерывает восстановление.

        Windows освобождает data.db-wal и data.db-shm не мгновенно: антивирус,
        индексатор или служба поиска могут держать их несколько секунд. Раньше
        неудача здесь роняла всё восстановление, хотя сами журналы к этому
        моменту уже не нужны — база переписывается постранично, а не файлом.
        Поэтому теперь это уборка «по возможности»: не вышло — пишем в журнал
        и идём дальше.
        """
        if not path.exists():
            return True
        gc.collect()                     # освобождаем ссылки на закрытые соединения
        for _ in range(attempts):
            try:
                path.unlink()
                return True
            except PermissionError:
                time.sleep(pause)
            except FileNotFoundError:
                return True
        log.warning("Файл %s занят другой программой, оставлен на месте", path.name)
        return False

    def restore(self, backup_path: str | Path) -> None:
        """
        Восстановление из резервной копии.

        Файл базы НЕ удаляется и не подменяется: содержимое копии переписывается
        в существующий файл постранично штатным механизмом SQLite (backup API).
        Так восстановление не зависит от того, держит ли Windows открытым
        data.db-wal — раньше именно это давало «Процесс не может получить
        доступ к файлу» и обрывало восстановление на середине.

        Прежняя база перед заменой сохраняется отдельным файлом
        (before_restore_*.db), поэтому откатиться можно всегда.
        """
        auth_service.require_admin(
            "Восстанавливать базу из резервной копии может только администратор.")
        source = Path(backup_path)
        if not source.exists():
            raise AppError("Файл резервной копии не найден.")

        # зашифрованная копия расшифровывается во временный файл в папке данных
        plain = source
        temporary: Optional[Path] = None
        if crypto_service.is_encrypted_file(source):
            temporary = paths.TEMP_DIR / f"restore_{datetime.now():%H%M%S}.db"
            try:
                crypto_service.decrypt_file(source, temporary)
            except ValueError as exc:
                temporary.unlink(missing_ok=True)
                raise AppError(str(exc)) from exc
            plain = temporary

        try:
            with closing(sqlite3.connect(f"file:{plain}?mode=ro", uri=True)) as probe:
                probe.execute("SELECT COUNT(*) FROM rentals").fetchone()
                probe.execute("SELECT COUNT(*) FROM inventory_items").fetchone()
        except sqlite3.Error as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise AppError(
                "Файл повреждён или это не резервная копия этой программы.") from exc

        db = get_db()
        safety = self.backup_dir() / f"before_restore_{datetime.now():%Y-%m-%d_%H-%M-%S}.db"
        self._safety_copy(db, safety, temporary)
        db.close()

        try:
            with closing(sqlite3.connect(str(plain))) as src, \
                    closing(sqlite3.connect(str(paths.DB_PATH))) as dest:
                src.backup(dest)                       # переписываем страницы на месте
                try:
                    dest.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
        except sqlite3.Error as exc:
            # база осталась прежней: backup API либо переписывает всё, либо ничего
            new_db = Database()
            new_db.initialize()
            set_db(new_db)
            if temporary:
                temporary.unlink(missing_ok=True)
            raise AppError(
                "Не удалось записать восстановленную базу. Данные не изменены — "
                f"прежняя база на месте, её копия сохранена как {safety.name}.") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)   # расшифрованная копия не остаётся

        # журналы уже сведены; если Windows их держит — это не мешает работе
        gc.collect()
        for suffix in ("-wal", "-shm"):
            self._remove_locked(Path(str(paths.DB_PATH) + suffix))

        new_db = Database()
        new_db.initialize()
        set_db(new_db)
        audit_service.log("Восстановление из резервной копии", entity="backup",
                          details=f"{source.name}; прежняя база сохранена как {safety.name}")
        log.info("База восстановлена из %s (прежняя сохранена как %s)", source, safety.name)

    def _safety_copy(self, db, safety: Path, temporary: Optional[Path]) -> None:
        """
        Снимок прежней базы перед заменой. Снимается на живом соединении тем же
        онлайн-бэкапом, что и обычные копии, — файл гарантированно целостный.
        """
        if not paths.DB_PATH.exists():
            return
        raw = safety.with_suffix(".db.tmp")
        try:
            with closing(sqlite3.connect(str(raw))) as dest:
                with dest:
                    db.conn.backup(dest)
            if crypto_service.available:
                crypto_service.encrypt_file(raw, safety)
            else:
                raw.replace(safety)
        except (sqlite3.Error, OSError) as exc:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise AppError(
                "Не удалось сохранить копию текущей базы перед восстановлением. "
                "Восстановление отменено, данные не тронуты.") from exc
        finally:
            if raw.exists():
                raw.unlink(missing_ok=True)

    def restore_photos(self, zip_path: str | Path) -> None:
        path = Path(zip_path)
        if not path.exists():
            raise AppError("Архив фотографий не найден.")
        with zipfile.ZipFile(path) as zf:
            zf.extractall(paths.PHOTOS_DIR)


backup_service = BackupService()
