# -*- coding: utf-8 -*-
"""
Аварийное восстановление доступа администратора.

Задача: потеря пароля единственного администратора не должна означать потерю
склада. Решение — одноразовый код восстановления, который печатается и кладётся
в сейф.

Чего здесь намеренно НЕТ:
  • код не хранится в открытом виде — только хэш тем же алгоритмом, что и пароли;
  • код не пишется в журнал аудита, в лог-файлы и в настройки;
  • нет «универсального пароля разработчика» и нет обхода авторизации:
    код позволяет только одно действие — задать администратору новый пароль;
  • использованный код становится недействительным навсегда.

Ключ шифрования данных при восстановлении не трогается: он привязан к
учётной записи Windows (DPAPI) и от пароля администратора не зависит,
поэтому смена пароля не делает данные нечитаемыми.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..db.database import get_db
from ..errors import AppError, AuthError, ValidationError
from ..utils import db_dt
from .audit_service import audit_service
from .auth_service import ROLE_ADMIN, auth_service, hash_password, verify_password

log = logging.getLogger(__name__)

# Алфавит без похожих символов: нет 0/O, 1/I/L, 5/S, 8/B — код диктуют по телефону
ALPHABET = "23456789ACDEFGHJKMNPQRTUVWXYZ"
GROUPS, GROUP_LEN = 4, 4          # XXXX-XXXX-XXXX-XXXX
ATTEMPT_KIND = "recovery"
MAX_ATTEMPTS = 5                  # после этого — временная блокировка
LOCK_MINUTES = 15                 # блокировка удваивается при повторных сериях
LOCK_MAX_MINUTES = 24 * 60


@dataclass(frozen=True)
class RecoveryResult:
    """Результат успешного восстановления: кому и какой новый код выдан."""
    user_id: int
    login: str
    full_name: str
    new_code: str


class RecoveryService:
    # ------------------------------------------------------------ генерация
    @staticmethod
    def generate_code() -> str:
        """
        Криптографически случайный код.

        29 символов алфавита × 16 позиций — около 78 бит энтропии. Перебором
        его не взять даже без ограничения попыток, а ограничение есть.
        """
        raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
        return "-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))

    @staticmethod
    def normalize(code: str) -> str:
        """Приводит ввод пользователя к каноническому виду: без дефисов и пробелов."""
        cleaned = "".join(ch for ch in (code or "").upper() if ch.isalnum())
        return "-".join(cleaned[i:i + GROUP_LEN] for i in range(0, len(cleaned), GROUP_LEN))

    def issue(self, user_id: int, reason: str = "") -> str:
        """
        Выдаёт новый код администратору и гасит все прежние.

        Возвращает код в открытом виде РОВНО ОДИН РАЗ — вызывающий код обязан
        сразу показать его пользователю. Больше получить его неоткуда.
        """
        db = get_db()
        user = db.query_one("SELECT id, login, role, is_active FROM users WHERE id=?", (user_id,))
        if not user:
            raise AppError("Пользователь не найден.")
        if user["role"] != ROLE_ADMIN:
            raise ValidationError("Код восстановления выдаётся только администратору.")

        code = self.generate_code()
        with db.transaction() as conn:
            conn.execute(
                "UPDATE recovery_codes SET revoked_at=? "
                "WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL",
                (db_dt(), user_id))
            conn.execute(
                "INSERT INTO recovery_codes(user_id, code_hash, created_at) VALUES (?,?,?)",
                (user_id, hash_password(code), db_dt()))
        audit_service.log("Создан код восстановления", entity="user", entity_id=user_id,
                          details=reason or "выдан новый код")   # сам код не записывается
        log.info("Выдан код восстановления для пользователя %s", user["login"])
        return code

    # -------------------------------------------------------------- статус
    def has_active_code(self, user_id: int) -> bool:
        row = get_db().query_one(
            "SELECT 1 FROM recovery_codes WHERE user_id=? AND used_at IS NULL "
            "AND revoked_at IS NULL LIMIT 1", (user_id,))
        return row is not None

    def admins_without_code(self) -> list[dict]:
        """Администраторы, у которых нет действующего кода — их надо предупредить."""
        rows = get_db().query(
            "SELECT u.id, u.login, u.full_name FROM users u "
            "WHERE u.role=? AND u.is_active=1 AND NOT EXISTS ("
            "  SELECT 1 FROM recovery_codes r WHERE r.user_id=u.id "
            "  AND r.used_at IS NULL AND r.revoked_at IS NULL)", (ROLE_ADMIN,))
        return [dict(r) for r in rows]

    def status(self) -> dict:
        """Сводка для экрана настроек безопасности. Код в неё не попадает."""
        db = get_db()
        total = db.query_one(
            "SELECT COUNT(*) c FROM recovery_codes WHERE used_at IS NULL AND revoked_at IS NULL"
        )["c"]
        last = db.query_one(
            "SELECT created_at FROM recovery_codes ORDER BY id DESC LIMIT 1")
        lock = self._lock_state()
        return {
            "active_codes": total,
            "last_issued": last["created_at"] if last else "",
            "admins_without_code": self.admins_without_code(),
            "locked_until": lock.get("locked_until") or "",
            "failed_attempts": lock.get("fails", 0),
        }

    # ------------------------------------------------------ защита перебора
    def _lock_state(self) -> dict:
        row = get_db().query_one(
            "SELECT fails, locked_until FROM security_attempts WHERE kind=? AND subject=''",
            (ATTEMPT_KIND,))
        return dict(row) if row else {"fails": 0, "locked_until": None}

    def lock_seconds_left(self) -> int:
        state = self._lock_state()
        until = state.get("locked_until")
        if not until:
            return 0
        try:
            deadline = datetime.strptime(until, "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return 0
        left = (deadline - datetime.now()).total_seconds()
        return int(left) if left > 0 else 0

    def _register_failure(self) -> int:
        """Считает неудачную попытку и при необходимости включает блокировку."""
        db = get_db()
        state = self._lock_state()
        fails = int(state.get("fails") or 0) + 1
        locked_until = None
        if fails % MAX_ATTEMPTS == 0:
            series = fails // MAX_ATTEMPTS               # 1, 2, 3, ...
            minutes = min(LOCK_MINUTES * (2 ** (series - 1)), LOCK_MAX_MINUTES)
            locked_until = (datetime.now() + timedelta(minutes=minutes)) \
                .strftime("%Y-%m-%d %H:%M:%S")
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO security_attempts(kind, subject, fails, locked_until, updated_at) "
                "VALUES (?,'',?,?,?) ON CONFLICT(kind, subject) DO UPDATE SET "
                "fails=excluded.fails, locked_until=excluded.locked_until, "
                "updated_at=excluded.updated_at",
                (ATTEMPT_KIND, fails, locked_until, db_dt()))
        return fails

    def _reset_failures(self) -> None:
        with get_db().transaction() as conn:
            conn.execute("DELETE FROM security_attempts WHERE kind=? AND subject=''",
                         (ATTEMPT_KIND,))

    # ---------------------------------------------------------- проверка
    def _match(self, code: str) -> Optional[dict]:
        """
        Ищет действующий код с таким значением.

        Сравниваются хэши всех непогашенных кодов: их единицы, а вычисление
        хэша заведомо медленное — это и есть защита от подбора.
        """
        normalized = self.normalize(code)
        if not normalized:
            return None
        rows = get_db().query(
            "SELECT r.id, r.code_hash, r.user_id, u.login, u.full_name, u.role, u.is_active "
            "FROM recovery_codes r JOIN users u ON u.id=r.user_id "
            "WHERE r.used_at IS NULL AND r.revoked_at IS NULL")
        for row in rows:
            if verify_password(normalized, row["code_hash"]):
                return dict(row)
        return None

    def check(self, code: str) -> dict:
        """
        Проверяет код, ничего не меняя. Бросает AuthError, если код не подходит.
        Возвращает данные администратора, которому код принадлежит.
        """
        left = self.lock_seconds_left()
        if left > 0:
            audit_service.log("Восстановление доступа заблокировано",
                              details=f"осталось {left} с", user_name="—")
            raise AuthError(
                f"Слишком много неверных попыток. Восстановление заблокировано ещё "
                f"на {self._human(left)}.")

        found = self._match(code)
        if not found:
            fails = self._register_failure()
            audit_service.log("Неудачная попытка восстановления доступа",
                              details=f"попытка №{fails}", user_name="—")
            remaining = MAX_ATTEMPTS - (fails % MAX_ATTEMPTS or MAX_ATTEMPTS)
            if remaining:
                raise AuthError(
                    f"Код восстановления не подходит. Осталось попыток: {remaining}.")
            raise AuthError(
                "Код восстановления не подходит. Восстановление временно заблокировано.")
        if not found["is_active"] or found["role"] != ROLE_ADMIN:
            self._register_failure()
            audit_service.log("Неудачная попытка восстановления доступа",
                              entity="user", entity_id=found["user_id"],
                              details="учётная запись отключена или больше не администратор",
                              user_name="—")
            raise AuthError("Учётная запись этого кода отключена. Обратитесь к администратору.")
        return found

    # ------------------------------------------------------ восстановление
    def reset_password(self, code: str, new_password: str) -> RecoveryResult:
        """
        Единственное, что позволяет код: задать администратору новый пароль.

        Порядок строгий — сначала проверка кода, потом смена пароля, потом
        гашение старого кода и выдача нового. Если что-то падает посередине,
        транзакция откатывается и старый код остаётся рабочим: лучше повторить
        попытку, чем остаться и без пароля, и без кода.
        """
        found = self.check(code)
        if len(new_password or "") < 4:
            raise ValidationError("Пароль должен быть не короче 4 символов.")

        db = get_db()
        user_id = found["user_id"]
        with db.transaction() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (hash_password(new_password), user_id))
            conn.execute("UPDATE recovery_codes SET used_at=? WHERE id=?",
                         (db_dt(), found["id"]))
            conn.execute(
                "UPDATE recovery_codes SET revoked_at=? "
                "WHERE user_id=? AND used_at IS NULL AND revoked_at IS NULL",
                (db_dt(), user_id))
        self._reset_failures()
        audit_service.log("Восстановлен доступ администратора", entity="user",
                          entity_id=user_id,
                          details="пароль задан заново по коду восстановления",
                          user_name=found["full_name"])
        log.warning("Пароль администратора %s изменён по коду восстановления", found["login"])

        new_code = self.issue(user_id, reason="после восстановления доступа")
        return RecoveryResult(user_id=user_id, login=found["login"],
                              full_name=found["full_name"], new_code=new_code)

    def reissue_for_current_admin(self) -> str:
        """Ручной перевыпуск кода из настроек. Требует прав администратора."""
        user = auth_service.require_admin(
            "Выдавать коды восстановления может только администратор.")
        return self.issue(user.id, reason="перевыпуск вручную")

    # ----------------------------------------------------------- документ
    def code_document_pdf(self, code: str, target, user_name: str = "") -> "object":
        """
        Печатная форма кода для сейфа. Файл формируется по требованию и не
        сохраняется в папке данных — иначе смысл секрета теряется.
        """
        from pathlib import Path

        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas as pdfcanvas

        from .pdf_service import FONT, FONT_BOLD, _register_fonts
        from ..version import DISPLAY_NAME

        _register_fonts()
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        pdf = pdfcanvas.Canvas(str(target), pagesize=A4)
        pdf.setTitle("Код восстановления доступа")
        width, height = A4

        y = height - 35 * mm
        pdf.setFont(FONT_BOLD, 18)
        pdf.drawCentredString(width / 2, y, "КОД ВОССТАНОВЛЕНИЯ ДОСТУПА")
        y -= 8 * mm
        pdf.setFont(FONT, 10)
        pdf.drawCentredString(width / 2, y, DISPLAY_NAME)
        y -= 6 * mm
        if user_name:
            pdf.drawCentredString(width / 2, y, f"Администратор: {user_name}")
            y -= 6 * mm
        pdf.drawCentredString(width / 2, y,
                              f"Выдан: {datetime.now().strftime('%d.%m.%Y %H:%M')}")

        y -= 22 * mm
        pdf.setLineWidth(1)
        pdf.rect(30 * mm, y - 6 * mm, width - 60 * mm, 24 * mm)
        pdf.setFont(FONT_BOLD, 22)
        pdf.drawCentredString(width / 2, y + 3 * mm, code)

        y -= 30 * mm
        pdf.setFont(FONT_BOLD, 11)
        pdf.drawCentredString(width / 2, y, "Храните этот лист в сейфе.")
        y -= 8 * mm
        pdf.setFont(FONT, 10)
        for line in [
            "Код показывается один раз и в программе больше не отображается.",
            "Он нужен, если забыт пароль администратора: код позволяет задать",
            "новый пароль и ничего больше.",
            "",
            "После использования код становится недействительным, и программа",
            "сразу выдаёт новый — его тоже нужно распечатать и положить в сейф.",
            "",
            "Если потеряны и пароль, и код, восстановить доступ будет невозможно.",
        ]:
            pdf.drawCentredString(width / 2, y, line)
            y -= 6 * mm
        pdf.save()
        return target

    @staticmethod
    def _human(seconds: int) -> str:
        if seconds >= 3600:
            return f"{seconds // 3600} ч {seconds % 3600 // 60} мин"
        if seconds >= 60:
            return f"{seconds // 60} мин"
        return f"{seconds} с"


recovery_service = RecoveryService()
