# -*- coding: utf-8 -*-
"""Ошибки уровня бизнес-логики: их текст показывается пользователю как есть."""


class AppError(Exception):
    """Понятная человеку ошибка. Никаких stack trace на экране."""


class ValidationError(AppError):
    pass


class AvailabilityError(AppError):
    pass


class NotFoundError(AppError):
    pass


class PermissionDeniedError(AppError):
    """Операция запрещена текущей роли. Проверяется в бизнес-логике, а не в интерфейсе."""


class AuthError(AppError):
    """Ошибка входа: неверный пароль, отключённая учётная запись и т.п."""
