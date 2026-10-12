# -*- coding: utf-8 -*-
"""
Пользователи, роли и текущая сессия.

Пароли хранятся только в виде хэша PBKDF2-HMAC-SHA256 со случайной солью
(240 000 итераций). Ни в коде, ни в базе нет пароля в открытом виде и нет
единого «зашитого» пароля администратора: первый администратор создаётся
мастером первоначальной настройки при первом запуске.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from ..db.database import get_db
from ..errors import AuthError, PermissionDeniedError, ValidationError
from ..utils import db_dt

log = logging.getLogger(__name__)

ITERATIONS = 240_000            # прежний PBKDF2 — только для проверки старых паролей
SCRYPT_N = 2 ** 15              # memory-hard параметры по рекомендациям OWASP
SCRYPT_R = 8
SCRYPT_P = 2
SCRYPT_MAXMEM = 96 * 1024 * 1024   # памяти нужно 128·N·r·p, лимит по умолчанию мал
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLE_LABELS = {ROLE_ADMIN: "Администратор", ROLE_USER: "Пользователь"}
SYSTEM_USER_NAME = "Система"


def _now_monotonic() -> float:
    return time.monotonic()


# --------------------------------------------------------------------- хэши
def _argon2():
    """Argon2id, если библиотека установлена. Иначе используется scrypt."""
    try:
        from argon2 import PasswordHasher
        return PasswordHasher()
    except Exception:
        return None


def hash_password(password: str) -> str:
    """
    Пароль не хранится и не может быть восстановлен: сохраняется только хэш.
    Приоритет — Argon2id; если библиотеки нет, применяется scrypt из стандартной
    библиотеки (тоже memory-hard). Старый формат PBKDF2 остаётся читаемым.
    """
    hasher = _argon2()
    if hasher is not None:
        return hasher.hash(password)
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                            p=SCRYPT_P, dklen=32, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    try:
        if stored.startswith("$argon2"):
            hasher = _argon2()
            if hasher is None:
                return False
            try:
                return hasher.verify(stored, password)
            except Exception:
                return False
        parts = stored.split("$")
        if parts[0] == "scrypt":
            _, n, r, p, salt_hex, digest_hex = parts
            digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                    n=int(n), r=int(r), p=int(p), dklen=32,
                                    maxmem=SCRYPT_MAXMEM)
            return hmac.compare_digest(digest.hex(), digest_hex)
        if parts[0] == "pbkdf2_sha256":       # пароли, созданные до версии 2.3.1
            _, iterations, salt_hex, digest_hex = parts
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
            return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError, IndexError):
        return False
    return False


def needs_rehash(stored: str) -> bool:
    """Старый формат пароля пора обновить при первом успешном входе."""
    return bool(stored) and stored.split("$")[0] == "pbkdf2_sha256"


@dataclass(frozen=True)
class User:
    id: int
    login: str
    full_name: str
    role: str
    is_active: bool = True
    first_name: str = ""
    last_name: str = ""
    position: str = ""
    avatar_path: str = ""

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def role_label(self) -> str:
        """Роль — это права в программе, а не должность сотрудника."""
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def display_name(self) -> str:
        name = f"{self.first_name} {self.last_name}".strip()
        return name or self.full_name

    @property
    def position_label(self) -> str:
        return self.position or "должность не указана"

    @property
    def initials(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p] or self.full_name.split()
        return "".join(p[0].upper() for p in parts[:2]) or "?"


class AuthService:
    def __init__(self) -> None:
        self.pending_recovery_code: str = ""   # одноразовая передача кода в UI
        self._current: Optional[User] = None
        self._last_activity: float = 0.0
        self._session_started: float = 0.0

    # ------------------------------------------------------------- сессия
    @property
    def current_user(self) -> Optional[User]:
        return self._current

    def current_name(self) -> str:
        return self._current.full_name if self._current else SYSTEM_USER_NAME

    def current_id(self) -> Optional[int]:
        return self._current.id if self._current else None

    def is_admin(self) -> bool:
        return bool(self._current and self._current.is_admin)

    def require_login(self) -> User:
        if not self._current:
            raise PermissionDeniedError("Операция требует входа в программу.")
        return self._current

    def require_admin(self, message: str = "Эту операцию может выполнить только администратор.") -> User:
        """Проверка прав в бизнес-логике: интерфейс механизмом безопасности не является."""
        user = self.require_login()
        if not user.is_admin:
            from .audit_service import audit_service
            audit_service.log("Отказано в доступе", details=message)
            raise PermissionDeniedError(message)
        return user

    # -------------------------------------------------------------- вход
    def has_users(self) -> bool:
        return get_db().query_one("SELECT COUNT(*) c FROM users")["c"] > 0

    def login_users(self) -> list[dict]:
        """Список активных пользователей для экрана входа."""
        return [dict(r) for r in get_db().query(
            "SELECT id, login, full_name, first_name, last_name, position, avatar_path, role "
            "FROM users WHERE is_active=1 "
            "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, full_name")]

    def login(self, login: str, password: str) -> User:
        from .audit_service import audit_service
        row = get_db().query_one("SELECT * FROM users WHERE login=?", (login.strip(),))
        if not row:
            audit_service.log("Неудачный вход", details=f"неизвестный логин: {login.strip()}",
                              user_name=login.strip())
            raise AuthError("Пользователь не найден.")
        if not row["is_active"]:
            audit_service.log("Неудачный вход", entity="user", entity_id=row["id"],
                              details="учётная запись отключена", user_name=row["full_name"])
            raise AuthError("Учётная запись отключена. Обратитесь к администратору.")
        if not verify_password(password, row["password_hash"]):
            audit_service.log("Неудачный вход", entity="user", entity_id=row["id"],
                              details="неверный пароль", user_name=row["full_name"])
            raise AuthError("Неверный пароль.")

        self._current = self._to_user(row)
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE users SET last_login=? WHERE id=?", (db_dt(), row["id"]))
            if needs_rehash(row["password_hash"]):
                # пароль перешёл со старого алгоритма — пересохраняем усиленным
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (hash_password(password), row["id"]))
        self._session_started = _now_monotonic()
        self.touch()
        audit_service.log("Вход в программу", entity="user", entity_id=row["id"])
        log.info("Вход пользователя %s (%s)", row["login"], row["role"])
        return self._current

    @staticmethod
    def _to_user(row) -> User:
        keys = row.keys()
        return User(
            row["id"], row["login"], row["full_name"], row["role"], bool(row["is_active"]),
            row["first_name"] if "first_name" in keys else "",
            row["last_name"] if "last_name" in keys else "",
            row["position"] if "position" in keys else "",
            row["avatar_path"] if "avatar_path" in keys else "")

    def refresh_current(self) -> Optional[User]:
        """Перечитывает профиль текущего пользователя после его изменения."""
        if not self._current:
            return None
        row = get_db().query_one("SELECT * FROM users WHERE id=?", (self._current.id,))
        if row:
            self._current = self._to_user(row)
        return self._current

    def logout(self) -> None:
        from .audit_service import audit_service
        if self._current:
            audit_service.log("Выход из программы", entity="user", entity_id=self._current.id)
        self._current = None
        self._last_activity = 0.0
        self._session_started = 0.0

    # ------------------------------------------------------ блокировка сессии
    def touch(self) -> None:
        """Отмечает активность пользователя — для автоблокировки по бездействию."""
        self._last_activity = _now_monotonic()

    def idle_seconds(self) -> float:
        if not self._current or not self._last_activity:
            return 0.0
        return max(0.0, _now_monotonic() - self._last_activity)

    def is_idle_expired(self, limit_minutes: int) -> bool:
        return bool(self._current) and self.idle_seconds() >= limit_minutes * 60

    def verify_current_password(self, password: str) -> bool:
        """Проверка пароля при снятии блокировки — сессия при этом не меняется."""
        if not self._current:
            return False
        row = get_db().query_one("SELECT password_hash FROM users WHERE id=?",
                                 (self._current.id,))
        ok = bool(row) and verify_password(password, row["password_hash"])
        if ok:
            self.touch()
        return ok

    # ---------------------------------------------------- управление людьми
    def create_first_admin(self, full_name: str, login: str, password: str,
                           position: str = "Заведующий складом") -> int:
        """
        Мастер первоначальной настройки. Доступен, только пока нет пользователей.

        Здесь же выдаётся код аварийного восстановления: он нужен именно на
        случай потери единственного пароля, поэтому создаётся сразу, а не
        когда-нибудь потом. Код в открытом виде кладётся в pending_recovery_code
        и забирается интерфейсом один раз — в базе остаётся только его хэш.
        """
        if self.has_users():
            raise PermissionDeniedError("Пользователи уже созданы, мастер недоступен.")
        user_id = self._insert_user(full_name, login, password, ROLE_ADMIN, system=True)
        parts = full_name.split()
        with get_db().transaction() as conn:
            conn.execute("UPDATE users SET last_name=?, first_name=?, position=? WHERE id=?",
                         (parts[0] if parts else "", " ".join(parts[1:]), position, user_id))
        try:
            from .recovery_service import recovery_service
            self.pending_recovery_code = recovery_service.issue(
                user_id, reason="создание первого администратора")
        except Exception:
            # склад важнее: если код не выдался, администратор создан и работает,
            # а выдать код можно вручную в настройках безопасности
            log.exception("Не удалось выдать код восстановления при создании администратора")
            self.pending_recovery_code = ""
        return user_id

    def pop_recovery_code(self) -> str:
        """
        Отдаёт выданный код ровно один раз и стирает его из памяти.
        Повторно получить тот же код нельзя — только выпустить новый.
        """
        code, self.pending_recovery_code = getattr(self, "pending_recovery_code", ""), ""
        return code

    def create_user(self, full_name: str, login: str, password: str, role: str = ROLE_USER,
                    position: str = "") -> int:
        self.require_admin("Создавать пользователей может только администратор.")
        user_id = self._insert_user(full_name, login, password, role)
        parts = full_name.split()
        with get_db().transaction() as conn:
            conn.execute("UPDATE users SET last_name=?, first_name=?, position=? WHERE id=?",
                         (parts[0] if parts else "", " ".join(parts[1:]), position, user_id))
        return user_id

    def _insert_user(self, full_name: str, login: str, password: str, role: str,
                     system: bool = False) -> int:
        from .audit_service import audit_service
        full_name = (full_name or "").strip()
        login = (login or "").strip()
        if not full_name:
            raise ValidationError("Укажите имя пользователя.")
        if not login:
            raise ValidationError("Укажите логин.")
        if len(password or "") < 4:
            raise ValidationError("Пароль должен быть не короче 4 символов.")
        if role not in ROLE_LABELS:
            raise ValidationError("Неизвестная роль.")
        db = get_db()
        if db.query_one("SELECT id FROM users WHERE login=?", (login,)):
            raise ValidationError(f"Логин «{login}» уже занят.")
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO users(login, full_name, role, password_hash) VALUES (?,?,?,?)",
                (login, full_name, role, hash_password(password)))
            user_id = int(cur.lastrowid)
        audit_service.log("Создан пользователь", entity="user", entity_id=user_id,
                          details=f"{full_name} ({login}), роль: {ROLE_LABELS[role]}",
                          user_name=SYSTEM_USER_NAME if system else None)
        return user_id

    def update_user(self, user_id: int, full_name: Optional[str] = None,
                    role: Optional[str] = None, is_active: Optional[bool] = None) -> None:
        from .audit_service import audit_service
        self.require_admin("Изменять пользователей может только администратор.")
        user = self.get_user(user_id)
        if not user:
            raise ValidationError("Пользователь не найден.")
        new_role = role or user["role"]
        new_active = user["is_active"] if is_active is None else int(bool(is_active))
        if (user["role"] == ROLE_ADMIN and (new_role != ROLE_ADMIN or not new_active)
                and self._active_admins() <= 1):
            raise ValidationError("Это единственный администратор — его нельзя отключить "
                                  "или понизить в правах.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE users SET full_name=?, role=?, is_active=? WHERE id=?",
                         ((full_name or user["full_name"]).strip(), new_role, new_active, user_id))
        details = f"{(full_name or user['full_name'])}, роль: {ROLE_LABELS.get(new_role, new_role)}, " \
                  f"{'активен' if new_active else 'отключён'}"
        audit_service.log("Изменён пользователь", entity="user", entity_id=user_id, details=details)
        if new_role != user["role"]:
            audit_service.log("Изменена роль", entity="user", entity_id=user_id,
                              details=f"{ROLE_LABELS.get(user['role'])} → {ROLE_LABELS.get(new_role)}")
        if self._current and self._current.id == user_id:
            self.refresh_current()

    def update_profile(self, user_id: int, first_name: str = None, last_name: str = None,
                       position: str = None) -> None:
        """
        Профиль сотрудника: имя, фамилия, должность. Свой профиль правит любой
        пользователь, чужой — только администратор.
        """
        from .audit_service import audit_service
        own = bool(self._current and self._current.id == user_id)
        if not own:
            self.require_admin("Изменять чужой профиль может только администратор.")
        else:
            self.require_login()
        user = self.get_user(user_id)
        if not user:
            raise ValidationError("Пользователь не найден.")
        first = (first_name if first_name is not None else user["first_name"]).strip()
        last = (last_name if last_name is not None else user["last_name"]).strip()
        job = (position if position is not None else user["position"]).strip()
        full = f"{last} {first}".strip() or user["full_name"]
        db = get_db()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE users SET first_name=?, last_name=?, position=?, full_name=? WHERE id=?",
                (first, last, job, full, user_id))
        audit_service.log("Изменён профиль", entity="user", entity_id=user_id,
                          details=f"{full}, должность: {job or '—'}")
        self.refresh_current()

    def set_avatar(self, user_id: int, source_path) -> str:
        """Загружает или заменяет аватар. Файл копируется в хранилище программы."""
        import shutil
        import uuid
        from pathlib import Path

        from .. import paths
        own = bool(self._current and self._current.id == user_id)
        if not own:
            self.require_admin("Менять чужой аватар может только администратор.")
        else:
            self.require_login()
        source = Path(source_path)
        if not source.exists():
            raise ValidationError("Файл изображения не найден.")
        paths.AVATARS_DIR.mkdir(parents=True, exist_ok=True)
        rel = f"{user_id}_{uuid.uuid4().hex[:8]}.png"
        target = paths.AVATARS_DIR / rel
        try:
            from PIL import Image
            with Image.open(source) as img:
                img = img.convert("RGB")
                side = min(img.size)
                left = (img.width - side) // 2
                top = (img.height - side) // 2
                img = img.crop((left, top, left + side, top + side)).resize((256, 256))
                img.save(target, "PNG")
        except Exception as exc:
            raise ValidationError("Не удалось прочитать изображение. "
                                  "Подойдут JPG, PNG, BMP или WEBP.") from exc
        old = self.get_user(user_id)["avatar_path"]
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE users SET avatar_path=? WHERE id=?", (rel, user_id))
        if old:
            try:
                (paths.AVATARS_DIR / old).unlink(missing_ok=True)
            except OSError:
                log.warning("Не удалось удалить прежний аватар %s", old)
        from .audit_service import audit_service
        audit_service.log("Изменён аватар", entity="user", entity_id=user_id)
        self.refresh_current()
        return rel

    def delete_avatar(self, user_id: int) -> None:
        from .. import paths
        own = bool(self._current and self._current.id == user_id)
        if not own:
            self.require_admin("Менять чужой аватар может только администратор.")
        else:
            self.require_login()
        user = self.get_user(user_id)
        if not user or not user["avatar_path"]:
            return
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE users SET avatar_path='' WHERE id=?", (user_id,))
        try:
            (paths.AVATARS_DIR / user["avatar_path"]).unlink(missing_ok=True)
        except OSError:
            pass
        from .audit_service import audit_service
        audit_service.log("Удалён аватар", entity="user", entity_id=user_id)
        self.refresh_current()

    def avatar_file(self, user_id: int):
        """Абсолютный путь к аватару или None."""
        from .. import paths
        user = self.get_user(user_id)
        if not user or not user["avatar_path"]:
            return None
        path = paths.AVATARS_DIR / user["avatar_path"]
        return path if path.exists() else None

    def set_password(self, user_id: int, password: str) -> None:
        from .audit_service import audit_service
        own = bool(self._current and self._current.id == user_id)
        if not own:
            self.require_admin("Менять пароли других пользователей может только администратор.")
        if len(password or "") < 4:
            raise ValidationError("Пароль должен быть не короче 4 символов.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (hash_password(password), user_id))
        audit_service.log("Изменён пароль", entity="user", entity_id=user_id)

    def delete_user(self, user_id: int) -> None:
        from .audit_service import audit_service
        self.require_admin("Удалять пользователей может только администратор.")
        user = self.get_user(user_id)
        if not user:
            return
        if user["role"] == ROLE_ADMIN and self._active_admins() <= 1:
            raise ValidationError("Нельзя удалить последнего администратора.")
        if self._current and self._current.id == user_id:
            raise ValidationError("Нельзя удалить пользователя, под которым вы работаете.")
        db = get_db()
        with db.transaction() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        audit_service.log("Удалён пользователь", entity="user", entity_id=user_id,
                          details=f"{user['full_name']} ({user['login']})")

    def _active_admins(self) -> int:
        return get_db().query_one(
            "SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1")["c"]

    def get_user(self, user_id: int) -> Optional[dict]:
        row = get_db().query_one("SELECT * FROM users WHERE id=?", (user_id,))
        return dict(row) if row else None

    def all_users(self) -> list[dict]:
        return [dict(r) for r in get_db().query(
            "SELECT id, login, full_name, first_name, last_name, position, avatar_path, "
            "role, is_active, created_at, last_login "
            "FROM users ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, full_name")]


auth_service = AuthService()
