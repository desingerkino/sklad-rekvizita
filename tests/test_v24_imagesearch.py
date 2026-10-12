# -*- coding: utf-8 -*-
"""
Тесты версии 2.4, часть 5: модульный поиск по фото.

Проверяется главное: подключение нейросетевого провайдера не должно ломать
существующий поиск ни при каких обстоятельствах — ни когда модель не
установлена, ни когда сервис отвалился посреди работы.

Запуск:  python -m tests.test_v24_imagesearch
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_v24s_"))
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


def make_photo(path: Path, color, shape=None) -> Path:
    from PIL import Image, ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (320, 240), color)
    if shape:
        draw = ImageDraw.Draw(image)
        draw.rectangle(shape, fill=(20, 20, 20))
    image.save(path)
    return path


def main() -> int:
    from app.db.database import Database, get_db, set_db
    from app.db.seed import seed_if_empty
    from app.errors import AppError, ValidationError
    from app.services import image_search_providers as registry
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.image_search import LocalVisualProvider, image_search
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import photo_service
    from app.services.settings_service import settings_service

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Иван", "admin", "1234")
    auth_service.pop_recovery_code()
    auth_service.login("admin", "1234")
    cat = categories_service.tree()[0]["id"]

    section("Подготовка каталога с фотографиями")
    chair = inventory_service.create({"name": "Стул красный", "category_id": cat,
                                      "quantity_total": 1})
    lamp = inventory_service.create({"name": "Лампа синяя", "category_id": cat,
                                     "quantity_total": 1})
    chair_photo = make_photo(_TMP / "chair.jpg", (200, 40, 30), (60, 60, 260, 200))
    lamp_photo = make_photo(_TMP / "lamp.jpg", (30, 60, 200), (120, 20, 200, 220))
    photo_service.add_photo(chair, chair_photo, make_primary=True)
    photo_service.add_photo(lamp, lamp_photo, make_primary=True)
    check(image_search.stats()["indexed"] == 2, "Обе фотографии проиндексированы")

    section("Встроенный поиск работает как прежде")
    query = make_photo(_TMP / "query_chair.jpg", (205, 45, 35), (55, 65, 255, 205))
    found = image_search.search(query, limit=5, min_score=0.3)
    check(bool(found), "Поиск по фотографии что-то нашёл")
    check(found[0]["item_id"] == chair,
          f"Нашёлся нужный предмет: {found[0]['name'] if found else '—'}")
    check(0 <= found[0]["percent"] <= 100, "Процент совпадения в разумных пределах")
    check(image_search.last_provider_note == "",
          "Про подмену провайдера ничего не сообщается — работал основной")

    section("Реестр провайдеров")
    keys = {p.key for p in registry.providers()}
    check(keys == {"local", "clip", "remote"},
          f"Зарегистрированы три способа поиска: {sorted(keys)}")
    check(registry.selected_key() == "local", "По умолчанию выбран встроенный")
    local_ready, _ = registry.get("local").available()
    check(local_ready, "Встроенный доступен всегда")

    try:
        registry.get("нет-такого")
        unknown_rejected = False
    except ValidationError:
        unknown_rejected = True
    check(unknown_rejected, "Неизвестный способ поиска отклоняется")

    section("Нейросеть: настоящий расчёт через onnxruntime")
    _check_clip(check, section, make_photo, _TMP)

    section("Нейросеть не установлена — честный отказ")
    clip = registry.get("clip")
    ready, reason = clip.available()
    check(not ready, "Провайдер ONNX сообщает, что не готов")
    check("onnxruntime" in reason or "модел" in reason.lower(),
          f"И объясняет, чего не хватает: {reason[:70]}")
    try:
        clip.embed(chair_photo)
        pretended = True
    except AppError:
        pretended = False
    except Exception:                                              # noqa: BLE001
        pretended = False
    check(not pretended, "Неготовый провайдер не делает вид, что посчитал вектор")

    remote = registry.get("remote")
    ready, reason = remote.available()
    check(not ready and "адрес" in reason.lower(),
          f"Внешний сервис не настроен: {reason[:60]}")
    settings_service.set(registry.SETTING_ENDPOINT, "https://example.invalid/embed")
    ready, reason = remote.available()
    check(not ready and "ключ" in reason.lower(),
          f"Без ключа доступа сервис не используется: {reason[:60]}")
    settings_service.set(registry.SETTING_ENDPOINT, "не-адрес")
    settings_service.set_secret(registry.SETTING_KEY_NAME, "секретный-ключ")
    ready, reason = remote.available()
    check(not ready and "http" in reason.lower(), "Кривой адрес отклоняется")

    stored = db.query_one("SELECT value FROM settings WHERE key=?",
                          (registry.SETTING_KEY_NAME,))
    check(stored is None or "секретный-ключ" not in str(stored["value"]),
          "Ключ доступа не лежит в базе открытым текстом")

    section("Выбор недоступного способа не ломает поиск")
    settings_service.set(registry.SETTING_PROVIDER, "clip")
    check(image_search.provider.name == LocalVisualProvider().name,
          "При недоступной нейросети действует встроенный провайдер")
    found = image_search.search(query, limit=5, min_score=0.3)
    check(bool(found) and found[0]["item_id"] == chair,
          "Поиск по фото продолжает находить предмет")

    settings_service.set(registry.SETTING_PROVIDER, "remote")
    found = image_search.search(query, limit=5, min_score=0.3)
    check(bool(found), "С ненастроенным внешним сервисом поиск тоже работает")
    settings_service.set(registry.SETTING_PROVIDER, "local")

    section("Провайдер отвалился посреди работы")

    class BrokenProvider:
        """Нейросеть, которая падает при обращении: сервис лёг, сеть пропала."""
        key = "broken"
        name = "broken-v1"
        title = "Сломанный сервис"
        dim = 16

        def available(self):
            return True, ""

        def embed(self, image_path):
            raise RuntimeError("сервис не отвечает")

    image_search.set_provider(BrokenProvider())
    found = image_search.search(query, limit=5, min_score=0.3)
    check(bool(found) and found[0]["item_id"] == chair,
          "Отказ провайдера не сорвал поиск — ответил встроенный алгоритм")
    check("не ответил" in image_search.last_provider_note,
          f"Пользователю есть что показать: {image_search.last_provider_note[:60]}")

    class EmptyIndexProvider(BrokenProvider):
        """Работает, но векторов под него в базе ещё нет."""
        name = "fresh-v1"
        title = "Новый способ"

        def embed(self, image_path):
            return [0.1] * 16

    image_search.set_provider(EmptyIndexProvider())
    found = image_search.search(query, limit=5, min_score=0.3)
    check(bool(found), "Пока фотографии не переиндексированы, поиск не пустой")
    check("переиндексированы" in image_search.last_provider_note,
          f"И объяснено почему: {image_search.last_provider_note[:60]}")

    image_search.set_provider(None)
    check(image_search.provider.name == LocalVisualProvider().name,
          "Возврат к настройкам работает")

    section("Переиндексация и сосуществование способов")
    photos_total = image_search.stats()["photos"]
    before = image_search.stats()["indexed"]
    done = image_search.reindex_all()
    check(done == photos_total,
          f"Переиндексированы все фотографии ({done} из {photos_total})")
    check(image_search.stats()["indexed"] == before,
          "Векторы обновлены, а не задвоены")

    # Главное: векторы разных способов живут рядом. Иначе переход на нейросеть
    # стирал бы встроенный индекс, и откат назад давал бы пустой поиск.
    rows = get_db().query(
        "SELECT provider, COUNT(*) c FROM photo_embeddings GROUP BY provider")
    providers_in_index = {r["provider"]: r["c"] for r in rows}
    check("local-v1" in providers_in_index,
          "Встроенные векторы на месте")
    if any(p.startswith("clip-onnx") for p in providers_in_index):
        # эта часть имеет смысл только там, где нейросеть реально считала
        check(len(providers_in_index) >= 2,
              f"Векторы разных способов лежат рядом: {providers_in_index}")
        check("local-v1" in providers_in_index,
              "Встроенные векторы не затёрты переиндексацией нейросетью")
    else:
        print("   ПРОПУЩЕНО: сосуществование с нейросетью — нейросеть на этом компьютере не запускалась")

    section("Состояние для настроек")
    rows = registry.status()
    check(len(rows) == 3, "Состояние показывается по всем трём способам")
    local_row = [r for r in rows if r["key"] == "local"][0]
    check(local_row["ready"] and local_row["selected"],
          "Встроенный отмечен готовым и выбранным")
    for row in rows:
        if row["key"] != "local":
            check(not row["ready"] and bool(row["reason"]),
                  f"«{row['title']}» — не готов, причина указана")

    section("Обновление базы прошлой версии")
    check(_migration_keeps_vectors(), "Старые векторы переносятся, ключ пересобран")

    section("Модель, встроенная в программу")
    _check_bundled(check, make_photo, _TMP)

    section("Движок нейросети проверяется в отдельном процессе")
    from app.services import onnx_probe

    probe_src = (Path(__file__).resolve().parent.parent / "app" / "services"
                 / "onnx_probe.py").read_text(encoding="utf-8")
    check("subprocess.run" in probe_src,
          "Проверка идёт отдельным процессом — его падение не роняет программу")
    main_src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    check("--probe-onnx" in main_src,
          "Собранная программа умеет запустить саму себя для проверки")
    providers_src = (Path(__file__).resolve().parent.parent / "app" / "services"
                     / "image_search_providers.py").read_text(encoding="utf-8")
    clip_part = providers_src.split("class ClipOnnxProvider")[1].split("def available")[1]
    check(clip_part.index("runtime_ok") < clip_part.index("import onnxruntime"),
          "Движок проверяется до того, как импортируется в программу")
    ok, reason = onnx_probe.runtime_ok(force=True)
    check(isinstance(ok, bool) and (ok or reason),
          f"Проверка возвращает ответ и причину ({'работает' if ok else reason[:60]})")

    section("Сломанные библиотеки не роняют программу")
    check("numpy>=1.26,<2" in (Path(__file__).resolve().parent.parent
                               / "requirements.txt").read_text(encoding="utf-8"),
          "Версия numpy зафиксирована: сборки 2.5 не грузятся на части Windows 10")

    providers_src = (Path(__file__).resolve().parent.parent / "app" / "services"
                     / "image_search_providers.py").read_text(encoding="utf-8")
    # берём именно метод available() класса ClipOnnxProvider
    clip_available = providers_src.split("class ClipOnnxProvider")[1]
    clip_available = clip_available.split("def available")[1].split("    def ")[0]
    check("import numpy" in clip_available,
          "Готовность нейросети проверяет и загрузку numpy, а не только onnxruntime")
    check("except Exception" in clip_available,
          "Сбой загрузки библиотеки перехватывается, а не роняет программу")
    check("не загружается на этом" in clip_available,
          "Пользователю объясняют, что именно не работает")

    settings_src = (Path(__file__).resolve().parent.parent / "app" / "ui"
                    / "settings_view.py").read_text(encoding="utf-8")
    check("Не удалось скачать модель." in settings_src,
          "Ошибка загрузки модели показывается понятным текстом")
    check("Модель скачана, но проверить её не удалось." in settings_src,
          "Сбой самопроверки не превращается в «непредвиденную ошибку»")

    section("Интерфейс")
    settings = (Path(__file__).resolve().parent.parent / "app" / "ui"
                / "settings_view.py").read_text(encoding="utf-8")
    check("image_search_providers" in settings, "Настройки знают о провайдерах")
    check("SETTING_PROVIDER" in settings or "imagesearch.provider" in settings,
          "Способ поиска выбирается в настройках")
    check("set_secret" in settings, "Ключ доступа сохраняется зашифрованным")

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки поиска по фото пройдены.")
    return 0


def _migration_keeps_vectors() -> bool:
    """
    База версии 6 (ключ только по фотографии) обновляется до 7 без потерь.

    Проверка нужна потому, что изменить первичный ключ в SQLite нельзя —
    таблицу приходится пересобирать, а это самая опасная операция в миграции.
    """
    import sqlite3
    from contextlib import closing

    from app.db.database import Database, get_db, set_db

    current = get_db()
    legacy = _TMP / "legacy_emb.db"
    # копируем штатным механизмом SQLite: простое копирование файла оставило бы
    # свежие записи в журнале WAL, и копия оказалась бы неполной
    with closing(sqlite3.connect(str(legacy))) as destination:
        current.conn.backup(destination)

    connection = sqlite3.connect(str(legacy))
    connection.executescript(
        "CREATE TABLE old_emb(photo_id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL,"
        " provider TEXT NOT NULL, dim INTEGER NOT NULL, vector BLOB NOT NULL,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));"
        "INSERT OR IGNORE INTO old_emb"
        " SELECT photo_id,item_id,provider,dim,vector,created_at FROM photo_embeddings;"
        "DROP TABLE photo_embeddings;"
        "ALTER TABLE old_emb RENAME TO photo_embeddings;"
        "PRAGMA user_version=6;")
    connection.commit()
    before = connection.execute("SELECT COUNT(*) FROM photo_embeddings").fetchone()[0]
    connection.close()

    migrated = Database(legacy)
    migrated.initialize()
    try:
        schema = migrated.query_one(
            "SELECT sql FROM sqlite_master WHERE name='photo_embeddings'")["sql"]
        after = migrated.query_one("SELECT COUNT(*) c FROM photo_embeddings")["c"]
        items = migrated.query_one("SELECT COUNT(*) c FROM inventory_items")["c"]
        version = migrated.query_one("PRAGMA user_version")[0]
        from app.version import DB_SCHEMA_VERSION

        ok = ("PRIMARY KEY (photo_id, provider)" in schema
              and after == before and items > 0 and version == DB_SCHEMA_VERSION)
    except Exception as exc:                                       # noqa: BLE001
        print(f"   (миграция упала: {exc})")
        ok = False
    finally:
        migrated.close()
        set_db(current)
    return bool(ok)


def _check_bundled(check, make_photo, tmp: Path) -> None:
    """
    Модель, положенная в assets/models при сборке, работает на новом
    компьютере сразу: без загрузки и без похода в настройки.
    """
    # Движок проверяется в отдельном процессе: на старом процессоре он не
    # бросает ошибку, а роняет процесс целиком — вместе с этим тестом и всей
    # сборкой, без единого сообщения.
    from app.services.onnx_probe import runtime_ok

    engine_ready, engine_reason = runtime_ok()
    if not engine_ready:
        print(f"   ПРОПУЩЕНО: нейросеть на этом компьютере не запускается. {engine_reason}")
        return

    from app.db.database import get_db
    from app.services import image_search_providers as registry
    from app.services.categories_service import categories_service
    from app.services.image_search import image_search
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import photo_service
    from app.services.settings_service import settings_service
    from tests.helpers import tiny_onnx

    clip_cls = registry.ClipOnnxProvider
    clip = registry.get("clip")
    bundled = tmp / "program" / "assets" / "models" / "clip_image.onnx"
    downloaded = clip_cls.default_path()
    original_bundled = clip_cls.bundled_path

    settings_service.set(registry.SETTING_PROVIDER, "")
    settings_service.set(registry.SETTING_MODEL_PATH, "")
    downloaded.unlink(missing_ok=True)
    clip_cls.bundled_path = staticmethod(lambda: bundled)
    clip._session = None
    try:
        check(clip.model_path() is None, "Без модели в поставке и без загрузки модели нет")
        check(registry.selected_key() == "local",
              "Пока модели нет, по умолчанию работает встроенный поиск")

        tiny_onnx.build(bundled, pad_bytes=6 * 1024 * 1024)
        check(clip.model_path() == bundled, "Модель из поставки находится сама")
        check(clip.is_bundled(), "Программа знает, что модель пришла с установщиком")
        check(clip.available()[0], "Нейросеть готова без загрузки из интернета")
        check(registry.selected_key() == "clip",
              "Раз модель в поставке, нейросеть включается по умолчанию")

        settings_service.set(registry.SETTING_PROVIDER, "local")
        check(registry.selected_key() == "local",
              "Явный выбор пользователя главнее значения по умолчанию")
        settings_service.set(registry.SETTING_PROVIDER, "")

        small = tmp / "program2" / "assets" / "models" / "clip_image.onnx"
        small.parent.mkdir(parents=True, exist_ok=True)
        small.write_bytes(b"<html>404</html>")
        clip_cls.bundled_path = staticmethod(lambda: small)
        check(clip.model_path() is None,
              "Крошечный файл вместо модели не принимается за модель")
        clip_cls.bundled_path = staticmethod(lambda: bundled)

        tiny_onnx.build(downloaded, pad_bytes=6 * 1024 * 1024)
        check(clip.model_path() == downloaded,
              "Скачанная модель главнее встроенной")
        clip.remove_model()
        check(not downloaded.exists() and bundled.exists(),
              "Удаляется только скачанная — встроенная остаётся на месте")
        check(clip.model_path() == bundled, "После удаления снова работает встроенная")

        # новая фотография получает оба отпечатка: нейросетевой и запасной
        clip._session = None
        cat = categories_service.tree()[0]["id"]
        item = inventory_service.create({"name": "Ваза встроенная", "category_id": cat,
                                         "quantity_total": 1})
        photo_id = photo_service.add_photo(
            item, make_photo(tmp / "bundled_vase.png", (120, 180, 60)), make_primary=True)
        kinds = {r["provider"] for r in get_db().query(
            "SELECT provider FROM photo_embeddings WHERE photo_id=?", (photo_id,))}
        check(any(k.startswith("clip-onnx") for k in kinds) and "local-v1" in kinds,
              f"Новая фотография проиндексирована обоими способами: {sorted(kinds)}")
        found = image_search.search(tmp / "bundled_vase.png", limit=3, min_score=0.0)
        check(found and found[0]["item_id"] == item,
              "Поиск по фото сразу находит предмет встроенной моделью")
        check(image_search.last_provider_note == "", "Считала именно нейросеть")
    finally:
        clip_cls.bundled_path = original_bundled
        clip._session = None
        settings_service.set(registry.SETTING_PROVIDER, "local")
        settings_service.set(registry.SETTING_MODEL_PATH, "")

    build = (Path(__file__).resolve().parent.parent / "build" / "build.ps1") \
        .read_text(encoding="utf-8-sig")
    check("assets\\models\\clip_image.onnx" in build and "LOCALAPPDATA" in build,
          "Сборка сама подхватывает скачанную модель")
    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    check("assets/models/*.onnx" in ignore,
          "Файл модели не попадёт в репозиторий по ошибке")


def _check_clip(check, section, make_photo, tmp: Path) -> None:
    """
    Проверка нейросетевого провайдера на настоящем движке onnxruntime.

    Модель CLIP скачивается пользователем и весит сотни мегабайт — держать её
    в тестах невозможно. Поэтому здесь собирается крошечная ONNX-модель того
    же вида: тот же вход 1×3×224×224, тот же выход-вектор. Она проверяет ровно
    то, что может сломаться в программе: предобработку кадра, запуск сессии,
    разбор ответа, нормализацию и работу каскада. Качество узнавания — это
    свойство самой CLIP, а не нашего кода.
    """
    from app.services import image_search_providers as registry
    from app.services.image_search import image_search
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import photo_service
    from app.services.settings_service import settings_service
    from app.services.categories_service import categories_service

    # Движок проверяется в отдельном процессе: на старом процессоре он не
    # бросает ошибку, а роняет процесс целиком — вместе с этим тестом и всей
    # сборкой, без единого сообщения.
    from app.services.onnx_probe import runtime_ok

    engine_ready, engine_reason = runtime_ok()
    if not engine_ready:
        print(f"   ПРОПУЩЕНО: нейросеть на этом компьютере не запускается. {engine_reason}")
        return

    from tests.helpers import tiny_onnx

    clip = registry.get("clip")
    ready, reason = clip.available()
    check(not ready and "не скачан" in reason,
          "Без файла модели провайдер честно говорит, что не готов")

    model = tiny_onnx.build(tmp / "models" / "tiny_vision.onnx")
    settings_service.set(registry.SETTING_MODEL_PATH, str(model))
    ready, reason = clip.available()
    check(ready, f"С файлом модели провайдер готов ({reason})")

    near_a = make_photo(tmp / "clip_a.png", (200, 40, 30))
    near_b = make_photo(tmp / "clip_b.png", (203, 43, 33))
    far = make_photo(tmp / "clip_c.png", (30, 60, 200))

    vector = clip.embed(near_a)
    check(len(vector) >= 32, f"Модель вернула вектор из {len(vector)} чисел")
    length = sum(v * v for v in vector) ** 0.5
    check(abs(length - 1.0) < 1e-6, f"Вектор нормирован (длина {length:.6f})")

    def cosine(first, second):
        return sum(x * y for x, y in zip(first, second))

    near = cosine(vector, clip.embed(near_b))
    distant = cosine(vector, clip.embed(far))
    check(near > distant,
          f"Похожие снимки ближе разных ({near:.3f} против {distant:.3f})")

    # кадр другой формы не должен ломать предобработку
    wide = make_photo(tmp / "clip_wide.png", (200, 40, 30))
    from PIL import Image

    with Image.open(wide) as image:
        image.resize((900, 300)).save(wide)
    check(len(clip.embed(wide)) == len(vector),
          "Широкий кадр обрабатывается: короткая сторона к 224, центр обрезается")

    ok, message = clip.check_model()
    check(ok, f"Самопроверка модели проходит: {message}")

    # --- сквозной сценарий: индексация и поиск через нейросеть ---
    cat = categories_service.tree()[0]["id"]
    red = inventory_service.create({"name": "Кресло красное", "category_id": cat,
                                    "quantity_total": 1})
    blue = inventory_service.create({"name": "Тумба синяя", "category_id": cat,
                                     "quantity_total": 1})
    photo_service.add_photo(red, near_a, make_primary=True)
    photo_service.add_photo(blue, far, make_primary=True)

    settings_service.set(registry.SETTING_PROVIDER, "clip")
    check(image_search.provider.name.startswith("clip-onnx"),
          f"Действует нейросетевой провайдер ({image_search.provider.name})")

    done = image_search.reindex_all()
    check(done >= 2, f"Фотографии переиндексированы нейросетью ({done})")
    check(image_search.stats()["indexed"] >= 2,
          "Векторы сохранены под именем нейросетевой модели")

    results = image_search.search(near_b, limit=5, min_score=0.0)
    check(bool(results), "Поиск нейросетью возвращает результаты")
    check(results[0]["item_id"] == red,
          f"Нашёлся нужный предмет: {results[0]['name'] if results else '—'}")
    check(image_search.last_provider_note == "",
          "Подмены провайдера не было — считала именно нейросеть")

    # битый файл модели не должен ронять поиск
    broken = tmp / "models" / "broken.onnx"
    broken.write_bytes(b"not a model at all" * 1000)
    settings_service.set(registry.SETTING_MODEL_PATH, str(broken))
    clip._session = None
    results = image_search.search(near_b, limit=5, min_score=0.0)
    check(bool(results), "С испорченным файлом модели поиск всё равно отвечает")
    check("не ответил" in image_search.last_provider_note
          or "переиндексированы" in image_search.last_provider_note,
          f"И объясняет, почему: {image_search.last_provider_note[:60]}")

    settings_service.set(registry.SETTING_MODEL_PATH, "")
    settings_service.set(registry.SETTING_PROVIDER, "local")
    check(len(registry.MODEL_SOURCES) >= 2,
          "Есть несколько источников модели на случай недоступности зеркала")
    check(all(m["url"].startswith("https://") for m in registry.MODEL_SOURCES),
          "Модель качается только по защищённому соединению")


if __name__ == "__main__":
    raise SystemExit(main())
