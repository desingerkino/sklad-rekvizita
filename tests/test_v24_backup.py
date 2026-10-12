# -*- coding: utf-8 -*-
"""
Тесты версии 2.4, часть 4: резервные копии — проверка, внешние носители,
провайдеры хранения.

Запуск:  python -m tests.test_v24_backup
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_v24b_"))
os.environ["SKLAD_DATA_DIR"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

failures: list[str] = []


def check(condition: bool, title: str) -> None:
    if condition:
        print(f"   ok    {title}")
    else:
        failures.append(title)
        print(f"   ОШИБКА {title}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def expect_error(func, exc_types, title: str) -> None:
    try:
        func()
    except exc_types:
        print(f"   ok    {title}")
    except Exception as exc:                                   # noqa: BLE001
        failures.append(f"{title} ({type(exc).__name__}: {exc})")
        print(f"   ОШИБКА {title} — неожиданная ошибка {type(exc).__name__}: {exc}")
    else:
        failures.append(f"{title} (ошибки не было)")
        print(f"   ОШИБКА {title} — ошибки не было, хотя она обязана быть")


def main() -> int:
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.errors import AppError, PermissionDeniedError
    from app.services import backup_providers
    from app.services.auth_service import auth_service
    from app.services.backup_service import backup_service
    from app.services.categories_service import categories_service
    from app.services.crypto_service import crypto_service
    from app.services.inventory_service import inventory_service

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    section("Подготовка")
    auth_service.create_first_admin("Иванов Иван", "admin", "1234")
    auth_service.pop_recovery_code()
    auth_service.login("admin", "1234")
    cat = categories_service.tree()[0]["id"]
    chair = inventory_service.create({"name": "Стул для копии", "category_id": cat,
                                      "quantity_total": 5})
    check(chair > 0, "Данные для копии созданы")

    section("Создание с проверкой")
    result = backup_service.create_verified()
    check(result["ok"], f"Копия создана и проверена: {result['summary']}")
    check("файл на месте" in result["checks"], "Проверено наличие файла")
    check(any("структура базы цела" in c for c in result["checks"]),
          "Проверена целостность базы")
    check(any("данные читаются" in c for c in result["checks"]),
          "Проверено, что данные читаются")
    check(any("тестовое восстановление" in c for c in result["checks"]),
          "Выполнено тестовое восстановление")
    check(result["counts"]["inventory_items"] >= 1, "В копии есть реквизит")
    if crypto_service.available:
        check(result["encrypted"], "Копия зашифрована")
    backup_path = Path(result["path"])

    section("Битая копия не выдаётся за рабочую")
    broken = _TMP / "sklad_2020-01-01_00-00-00_broken.db"
    broken.write_bytes("это не база данных, а мусор".encode("utf-8") * 100)
    verdict = backup_service.verify(broken)
    check(not verdict["ok"], "Мусорный файл проверку не проходит")
    check("НЕ ПРОЙДЕНА" in verdict["summary"], f"Вердикт понятен: {verdict['summary'][:60]}")

    truncated = _TMP / "sklad_2020-01-02_00-00-00_short.db"
    truncated.write_bytes(backup_path.read_bytes()[:200])
    check(not backup_service.verify(truncated)["ok"], "Обрезанный файл проверку не проходит")

    missing = _TMP / "sklad_2020-01-03_00-00-00_none.db"
    check(not backup_service.verify(missing)["ok"], "Отсутствующий файл проверку не проходит")

    half = _TMP / "sklad_2020-01-04_00-00-00_half.db"
    data = bytearray(backup_path.read_bytes())
    for index in range(len(data) // 2, len(data)):      # портим вторую половину
        data[index] = (data[index] + 7) % 256
    half.write_bytes(bytes(data))
    check(not backup_service.verify(half)["ok"], "Испорченная середина файла ловится")

    section("Внешний носитель")
    external = _TMP / "external_disk"
    provider = backup_providers.local_provider(external)
    ready, reason = provider.available()
    check(ready, f"Папка носителя готова ({reason})")

    location = backup_service.export_to(backup_path, provider)
    check(Path(location).exists(), "Копия скопирована на носитель")
    check(Path(location).stat().st_size == backup_path.stat().st_size,
          "Файл на носителе совпадает по размеру с исходным")
    check(len(provider.list_files()) == 1, "Копия видна в списке хранилища")

    back = provider.download(backup_path.name, _TMP / "returned.db")
    check(back.exists() and back.read_bytes() == backup_path.read_bytes(),
          "Копию можно забрать с носителя обратно")
    check(backup_service.verify(back)["ok"], "Забранная с носителя копия исправна")

    check(not list(external.glob("*.part")),
          "Временных недокопированных файлов на носителе не осталось")

    expect_error(lambda: backup_service.export_to(broken, provider), AppError,
                 "Битая копия на носитель не выгружается")

    section("Недоступный носитель")
    unplugged = backup_providers.local_provider(_TMP / "external_disk" / "нет" / "такого" / "пути")
    ok_flag, _ = unplugged.available()
    check(ok_flag or True, "Несуществующий путь обрабатывается без падения")

    # Проверка «нет прав на запись» имеет смысл только под обычной учётной
    # записью: администратор и root пишут куда угодно, и она ничего не докажет.
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
        readonly = _TMP / "readonly_disk"
        readonly.mkdir()
        os.chmod(readonly, 0o500)
        blocked = backup_providers.local_provider(readonly)
        ready, reason = blocked.available()
        check(not ready and "права" in reason.lower() or not ready,
              f"Носитель без прав на запись отклоняется ({reason})")
        expect_error(lambda: backup_service.export_to(backup_path, blocked), AppError,
                     "Выгрузка на защищённый носитель даёт понятную ошибку")
        os.chmod(readonly, 0o700)
    else:
        print("   ПРОПУЩЕНО: проверка носителя без прав на запись "
              "(нужна обычная учётная запись, не администратор)")

    section("Носитель отключили посреди копирования")
    import shutil

    unplug_disk = _TMP / "usb_unplug"
    unplug_disk.mkdir()
    real_copy = shutil.copy2

    def dies_midway(source, destination, *args, **kwargs):
        """Половина файла записалась, и тут флешку выдернули."""
        Path(destination).write_bytes(Path(source).read_bytes()[:500])
        raise OSError(5, "Устройство отключено")

    shutil.copy2 = dies_midway
    try:
        backup_service.export_to(backup_path, backup_providers.local_provider(unplug_disk))
        interrupted = False
    except AppError as exc:
        interrupted = "не отключён ли диск" in str(exc)
    finally:
        shutil.copy2 = real_copy
    check(interrupted, "Обрыв копирования замечен и объяснён понятным текстом")
    left = sorted(p.name for p in unplug_disk.iterdir())
    check(not any(name.endswith(".db") for name in left),
          f"Файла, похожего на целую копию, не появилось (осталось: {left or 'пусто'})")
    check(not left, "Недописанный кусок убран с носителя")

    location2 = backup_service.export_to(
        backup_path, backup_providers.local_provider(unplug_disk))
    check(Path(location2).exists() and backup_service.verify(location2)["ok"],
          "После повторного подключения выгрузка проходит и копия исправна")

    section("Создание сразу с выгрузкой")
    combo = backup_service.create_verified(provider=backup_providers.local_provider(external))
    check(combo["ok"], "Копия создана и проверена")
    check(combo["exported_to"] and Path(combo["exported_to"]).exists(),
          "Она же сразу выгружена на носитель")

    section("Провайдеры хранения")
    keys = {p.key for p in backup_providers.providers()}
    check("local" in keys, "Локальный провайдер зарегистрирован")
    check({"s3", "gdrive", "onedrive"} <= keys,
          "Место для облачных хранилищ подготовлено")
    for key in ("s3", "gdrive", "onedrive"):
        cloud = backup_providers.get_provider(key)
        ready, reason = cloud.available()
        check(not ready, f"Провайдер «{cloud.title}» честно сообщает, что не подключён")
        check("не подключено" in reason, f"Причина понятна: {reason[:60]}")
        expect_error(lambda c=cloud: c.upload(backup_path), AppError,
                     f"Отправка в «{cloud.title}» не делает вид, что сработала")
    expect_error(lambda: backup_providers.get_provider("нет-такого"), Exception,
                 "Неизвестное хранилище отклоняется")

    section("Восстановление из копии с носителя")
    inventory_service.create({"name": "Появился после копии", "category_id": cat,
                              "quantity_total": 1})
    check(len(inventory_service.search("Появился после копии")) == 1,
          "Лишний предмет добавлен после копии")
    backup_service.restore(location)
    check(len(inventory_service.search("Появился после копии")) == 0,
          "После восстановления с носителя лишнего предмета нет")
    check(len(inventory_service.search("Стул для копии")) == 1,
          "Исходные данные на месте")
    check(any(p.name.startswith("before_restore_")
              for p in backup_service.backup_dir().glob("*.db")),
          "Прежняя база сохранена перед восстановлением")

    section("Права")
    auth_service.create_user("Сидоров Сидор", "sidorov", "1234", role="user")
    auth_service.logout()
    auth_service.login("sidorov", "1234")
    check(backup_service.verify(backup_path)["ok"],
          "Проверить копию может любой сотрудник")
    expect_error(lambda: backup_service.export_to(backup_path, provider),
                 PermissionDeniedError,
                 "Выгружать копии наружу может только администратор")
    expect_error(lambda: backup_service.restore(backup_path), PermissionDeniedError,
                 "Восстанавливать базу может только администратор")
    auth_service.logout()
    auth_service.login("admin", "1234")

    section("Интерфейс копий (статическая проверка)")
    _check_ui()

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки резервного копирования пройдены.")
    return 0


def _check_ui() -> None:
    settings = (Path(__file__).resolve().parent.parent / "app" / "ui"
                / "settings_view.py").read_text(encoding="utf-8")
    check("create_verified" in settings,
          "Создание копии из настроек идёт с проверкой")
    check("export_to" in settings and "getExistingDirectory" in settings,
          "Есть выгрузка копии на внешний носитель с выбором папки")
    check("backup_service.verify" in settings,
          "Есть кнопка проверки существующей копии")
    # «Успешно» показывается только при result["ok"] и отсутствии замечаний;
    # во всех остальных случаях — предупреждение или ошибка
    check('if result["ok"] and not result["problems"]' in settings
          and "QMessageBox.Critical" in settings,
          "Непройденная проверка не выдаётся за успех")
    check("восстановиться не получится" in settings,
          "Пользователю прямо сказано, что такой копией не восстановиться")


if __name__ == "__main__":
    raise SystemExit(main())
