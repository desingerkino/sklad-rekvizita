# -*- coding: utf-8 -*-
"""
Поиск реквизита по фотографии.

Архитектура: провайдер векторных признаков (embedding) + хранилище векторов в БД
+ поиск ближайших по косинусной близости.

Встроенный провайдер LocalVisualProvider работает офлайн, без интернета и без
внешних сервисов. С версии 4.0 главный признак у него — форма: гистограммы
направлений контуров по сетке (как в HOG) и силуэт предмета; цвет учитывается,
но с меньшим весом. Раньше цвет перевешивал, и красный чемодан оказывался
«похож» на красный стул.

Версия 4.0 также:
  • сравнивает векторы матрицей (numpy), а не циклом — поиск по десяткам тысяч
    фотографий занимает миллисекунды; без numpy работает прежний цикл;
  • ищет по нескольким видам снимка (весь кадр и центральная часть): предмет
    на складе снят крупно, а на снимке для поиска он часто занимает полкадра;
  • умеет сужать поиск текстом и категорией, искать по выделенной части кадра
    и показывает честную шкалу сходства вместо «процента совпадения».

Провайдера можно заменить на внешний AI/API (CLIP, облачный сервис) — достаточно
реализовать интерфейс EmbeddingProvider и указать его в настройках
(`imagesearch.provider`). Приложение не привязано к конкретному поставщику.
"""
from __future__ import annotations

import array
import logging
import math
from pathlib import Path
from typing import Iterable, Optional, Protocol

from ..db.database import get_db

log = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, image_path: Path) -> list[float]:
        ...


def _open_rgb(image_path, box: Optional[tuple] = None):
    """Открывает снимок с учётом поворота из EXIF; box — доли кадра (l, t, r, b)."""
    from PIL import Image, ImageOps

    from .photo_service import enable_heic

    enable_heic()      # снимок для поиска тоже может быть с айфона
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source) or source
        image = image.convert("RGB")
    if box:
        width, height = image.size
        left, top, right, bottom = box
        area = (int(left * width), int(top * height),
                max(int(left * width) + 8, int(right * width)),
                max(int(top * height) + 8, int(bottom * height)))
        image = image.crop(area)
    return image


class LocalVisualProvider:
    """
    Офлайн-провайдер на Pillow. Никуда ничего не отправляет.

    Вектор собран из четырёх частей с разным весом:
      • форма — направления контуров по сетке 4×4 (8 направлений в ячейке);
      • силуэт — карта контуров 8×8;
      • структура — перцептивный хэш 8×8;
      • цвет — гистограммы тона, насыщенности и яркости.
    Веса подобраны так, чтобы форма решала больше цвета.
    """

    name = "local-v2"
    dim = 128 + 64 + 64 + 40
    W_SHAPE, W_EDGE, W_HASH, W_COLOR = 0.50, 0.20, 0.10, 0.20

    def embed(self, image_path: Path, box: Optional[tuple] = None) -> list[float]:
        return self.embed_image(_open_rgb(image_path, box))

    def embed_image(self, rgb) -> list[float]:
        from PIL import Image, ImageFilter

        rgb = rgb.copy()
        rgb.thumbnail((256, 256))
        gray = rgb.convert("L")

        # 1. Форма: гистограмма направлений градиента по сетке 4×4
        cell, bins, side = 16, 8, 64
        small = gray.resize((side + 2, side + 2), Image.BILINEAR)
        px = small.load()
        shape = [0.0] * (16 * bins)
        for y in range(side):
            for x in range(side):
                gx = px[x + 2, y + 1] - px[x, y + 1]
                gy = px[x + 1, y + 2] - px[x + 1, y]
                magnitude = math.hypot(gx, gy)
                if magnitude < 6:               # шум и ровный фон формы не задают
                    continue
                angle = math.atan2(gy, gx) % math.pi          # направление без знака
                shape[((y // cell) * 4 + x // cell) * bins
                      + min(bins - 1, int(angle / math.pi * bins))] += magnitude
        for start in range(0, len(shape), bins):              # нормировка по ячейке
            norm = math.sqrt(sum(v * v for v in shape[start:start + bins])) or 1.0
            for index in range(start, start + bins):
                shape[index] /= norm

        # 2. Силуэт: где на кадре проходят контуры
        edges = gray.filter(ImageFilter.FIND_EDGES).resize((8, 8), Image.BILINEAR)
        edge = [v / 255.0 for v in edges.getdata()]

        # 3. Структура: dHash 8×8
        tiny = list(gray.resize((9, 8), Image.BILINEAR).getdata())
        structure = [1.0 if tiny[y * 9 + x] > tiny[y * 9 + x + 1] else 0.0
                     for y in range(8) for x in range(8)]

        # 4. Цвет: тон (24), насыщенность (8), яркость (8)
        hsv = rgb.resize((64, 64)).convert("HSV")
        hue, sat, val = [0.0] * 24, [0.0] * 8, [0.0] * 8
        for h, s, v in hsv.getdata():
            if s > 40 and v > 40:            # серое и тёмное тон не задают
                hue[min(23, h * 24 // 256)] += 1.0
            sat[min(7, s * 8 // 256)] += 1.0
            val[min(7, v * 8 // 256)] += 1.0
        color = [v / 4096.0 for v in hue + sat + val]

        vector: list[float] = []
        for part, weight in ((shape, self.W_SHAPE), (edge, self.W_EDGE),
                             (structure, self.W_HASH), (color, self.W_COLOR)):
            norm = math.sqrt(sum(v * v for v in part)) or 1.0
            scale = math.sqrt(weight) / norm
            vector += [v * scale for v in part]
        return _normalize(vector)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _pack(vec: Iterable[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _unpack(blob: bytes) -> list[float]:
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


def cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    return sum(a[i] * b[i] for i in range(n))


def _numpy():
    """numpy, если он загружается на этом компьютере; иначе None."""
    try:
        import numpy
        return numpy
    except Exception:                                              # noqa: BLE001
        return None


# Названия основных цветов по тону (HSV, тон 0..255 как в Pillow).
_HUES = ((11, "красный"), (25, "оранжевый"), (42, "жёлтый"), (110, "зелёный"),
         (140, "бирюзовый"), (185, "синий"), (215, "фиолетовый"), (240, "розовый"),
         (256, "красный"))


def dominant_colors(image_path, box: Optional[tuple] = None, limit: int = 2) -> list[str]:
    """
    Основные цвета предмета словами: ['коричневый', 'чёрный'].

    Смотрит на центральную часть кадра (предмет обычно там, фон — по краям).
    Это простое правило по тону, насыщенности и яркости, а не распознавание:
    оно называет цвет, но не материал.
    """
    try:
        image = _open_rgb(image_path, box)
    except Exception:                                              # noqa: BLE001
        return []
    width, height = image.size
    image = image.crop((int(width * 0.2), int(height * 0.2),
                        int(width * 0.8), int(height * 0.8))).resize((48, 48)).convert("HSV")
    votes: dict[str, int] = {}
    for h, s, v in image.getdata():
        if v < 50:
            name = "чёрный"
        elif s < 38:
            name = "белый" if v > 205 else "серый"
        elif h < 30 and v < 150 and s < 190:
            name = "коричневый"
        elif 25 <= h < 45 and s < 110 and v > 150:
            name = "бежевый"
        else:
            name = next(label for edge, label in _HUES if h < edge)
        votes[name] = votes.get(name, 0) + 1
    total = sum(votes.values()) or 1
    ranked = sorted(votes.items(), key=lambda pair: pair[1], reverse=True)
    return [name for name, count in ranked[:limit] if count / total >= 0.18]


# Шкала сходства. Косинус сам по себе обманчив: у нейросети два случайных
# снимка дают 0.5–0.6, и «60 %» выглядело бы как неплохое совпадение. Поэтому
# показывается не косинус, а положение на шкале от «случайные снимки» до
# «тот же предмет». Границы выбраны по замерам на реальных фотографиях.
# Замер встроенного алгоритма на 191 снимке: лучший ПОСТОРОННИЙ предмет даёт
# в среднем 0.78 (95 % случаев — ниже 0.85), тот же предмет на новом снимке —
# в среднем 0.90. Для нейросети границы взяты по типичным значениям CLIP и
# проверяются в CI на тех же снимках (tools/eval_photo_search.py).
_SCALES = {"clip": (0.55, 0.92), "local": (0.74, 0.97), "remote": (0.50, 0.95)}
LEVELS = ((80, "Очень похоже"), (55, "Похоже"), (30, "Есть сходство"), (0, "Отдалённо"))


def scale_for(provider_name: str) -> tuple[float, float]:
    if provider_name.startswith("clip"):
        return _SCALES["clip"]
    if provider_name.startswith("remote"):
        return _SCALES["remote"]
    return _SCALES["local"]


def similarity_percent(score: float, provider_name: str) -> int:
    low, high = scale_for(provider_name)
    return round(max(0.0, min(1.0, (score - low) / (high - low))) * 100)


def similarity_level(percent: int) -> str:
    return next(label for edge, label in LEVELS if percent >= edge)


class ImageSearchService:
    # Виды снимка для запроса: весь кадр и центральная часть. Предмет на
    # складской фотографии занимает весь кадр, а на снимке «с площадки» —
    # часто только середину; сравнение по обоим видам это выравнивает.
    QUERY_VIEWS = (None, (0.15, 0.15, 0.85, 0.85))

    def __init__(self, provider: Optional[EmbeddingProvider] = None):
        self._explicit: Optional[EmbeddingProvider] = provider
        self._fallback = LocalVisualProvider()
        self.last_provider_note = ""       # чем реально отвечал последний поиск
        self.last_provider_name = ""
        self._generation = 0               # растёт при каждом изменении индекса
        self._matrices: dict[str, tuple] = {}

    @property
    def provider(self) -> EmbeddingProvider:
        """
        Действующий провайдер: выбранный в настройках, если он готов к работе,
        иначе встроенный. Встроенный доступен всегда, поэтому поиск по фото не
        может «сломаться» из-за ненастроенной нейросети — он просто станет
        прежним, как в версии 2.3.
        """
        if self._explicit is not None:
            return self._explicit
        try:
            from . import image_search_providers as registry

            chosen = registry.get(registry.selected_key())
            if getattr(chosen, "key", "local") == "local":
                return self._fallback
            ready, reason = chosen.available()
            if ready:
                return chosen
            log.info("Провайдер «%s» недоступен (%s) — работает встроенный поиск",
                     getattr(chosen, "title", chosen), reason)
        except Exception:                                          # noqa: BLE001
            log.exception("Не удалось определить провайдера поиска по фото")
        return self._fallback

    def set_provider(self, provider: Optional[EmbeddingProvider]) -> None:
        """
        Жёстко задать провайдера (в обход настроек). None — вернуться к выбору
        из настроек.
        """
        self._explicit = provider
        self.invalidate()

    def invalidate(self) -> None:
        """Сбрасывает кэш векторов: индекс изменился или база заменена."""
        self._generation += 1
        self._matrices.clear()

    # ------------------------------------------------------------- индексация
    def _providers_for_index(self) -> list:
        providers = [self.provider]
        if self.provider.name != self._fallback.name:
            providers.append(self._fallback)
        return providers

    def index_photo(self, photo_id: int, item_id: int, image_path: Path,
                    only_missing: bool = False) -> None:
        """
        Считает отпечаток фотографии действующим способом поиска и — если это
        не встроенный — ещё и встроенным.

        Встроенный отпечаток нужен всегда: это запасной путь. Если нейросеть
        откажет или файл модели пропадёт, поиск откатится на встроенный
        алгоритм, и ему должно быть по чему искать. Считается он за доли
        секунды, так что вторая запись почти ничего не стоит.
        """
        db = get_db()
        have = set()
        if only_missing:
            have = {row["provider"] for row in db.query(
                "SELECT provider FROM photo_embeddings WHERE photo_id=?", (photo_id,))}
        vectors = []
        for provider in self._providers_for_index():
            if provider.name in have:
                continue
            try:
                vectors.append((provider.name, provider.embed(Path(image_path))))
            except Exception:                                      # noqa: BLE001
                if provider is self._fallback:
                    raise
                log.exception("Способ %s не смог обработать %s — сохраняю "
                              "только встроенный отпечаток", provider.name, image_path)
        if not vectors:
            return
        with db.transaction() as conn:
            for name, vec in vectors:
                conn.execute(
                    "INSERT INTO photo_embeddings(photo_id, item_id, provider, dim, vector) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(photo_id, provider) DO UPDATE SET "
                    "vector=excluded.vector, dim=excluded.dim, item_id=excluded.item_id",
                    (photo_id, item_id, name, len(vec), _pack(vec)),
                )
        self.invalidate()

    def reindex_all(self, progress=None) -> int:
        """Переиндексация всей базы фотографий (после смены провайдера)."""
        return self._index_rows(get_db().query(
            "SELECT id, item_id, rel_path FROM inventory_photos"), progress, False)

    def missing_count(self) -> int:
        """Сколько фотографий ещё не проиндексировано действующим способом."""
        names = [p.name for p in self._providers_for_index()]
        return get_db().query_one(
            "SELECT COUNT(*) c FROM inventory_photos p WHERE (SELECT COUNT(*) FROM "
            f"photo_embeddings e WHERE e.photo_id = p.id AND e.provider IN "
            f"({','.join('?' * len(names))})) < ?", names + [len(names)])["c"]

    def index_missing(self, progress=None, should_stop=None) -> int:
        """
        Доиндексирует только те фотографии, у которых нет вектора действующего
        способа поиска. Вызывается в фоне при запуске: после обновления
        программы или смены способа поиска склад индексируется сам, без
        кнопки в настройках. Можно прервать — продолжит со следующего запуска.
        """
        names = [p.name for p in self._providers_for_index()]
        rows = get_db().query(
            "SELECT p.id, p.item_id, p.rel_path FROM inventory_photos p WHERE (SELECT COUNT(*) "
            f"FROM photo_embeddings e WHERE e.photo_id = p.id AND e.provider IN "
            f"({','.join('?' * len(names))})) < ? ORDER BY p.id", names + [len(names)])
        return self._index_rows(rows, progress, True, should_stop)

    def index_next_missing(self, batch: int = 1, skip: Optional[set] = None) -> tuple[int, set]:
        """
        Индексирует несколько следующих фотографий без вектора и возвращает
        (сколько сделано, id фотографий, которые обработать не удалось).

        Этим методом главное окно доиндексирует склад маленькими шагами в
        простое, не мешая работе: один шаг — одна-две фотографии.
        """
        from .photo_service import abs_path

        skip = set(skip or ())
        names = [p.name for p in self._providers_for_index()]
        rows = get_db().query(
            "SELECT p.id, p.item_id, p.rel_path FROM inventory_photos p WHERE (SELECT COUNT(*) "
            f"FROM photo_embeddings e WHERE e.photo_id = p.id AND e.provider IN "
            f"({','.join('?' * len(names))})) < ? ORDER BY p.id LIMIT ?",
            names + [len(names), batch + len(skip)])
        done = 0
        for row in rows:
            if row["id"] in skip:
                continue
            if done >= batch:
                break
            path = abs_path(row["rel_path"])
            try:
                if not path.exists():
                    raise FileNotFoundError(path)
                self.index_photo(row["id"], row["item_id"], path, only_missing=True)
                done += 1
            except Exception:                                      # noqa: BLE001
                log.warning("Фотография %s пропущена при индексации", row["rel_path"])
                skip.add(row["id"])
        return done, skip

    def _index_rows(self, rows, progress, only_missing: bool, should_stop=None) -> int:
        from .photo_service import abs_path

        done = 0
        for i, row in enumerate(rows, 1):
            if should_stop and should_stop():
                break
            path = abs_path(row["rel_path"])
            if path.exists():
                try:
                    self.index_photo(row["id"], row["item_id"], path, only_missing)
                    done += 1
                except Exception:                                  # noqa: BLE001
                    log.exception("Не удалось проиндексировать %s", path)
            if progress:
                progress(i, len(rows))
        return done

    # ------------------------------------------------------------- хранилище
    def _load_vectors(self, provider_name: str) -> tuple:
        """
        Векторы способа поиска: (id фото, id предметов, матрица | список).

        Читаются из базы один раз и держатся в памяти, пока индекс не
        изменится: 10 000 фотографий нейросети — это около 20 МБ.
        """
        db = get_db()
        mark = db.query_one(
            "SELECT COUNT(*) c, ifnull(MAX(rowid),0) m FROM photo_embeddings WHERE provider=?",
            (provider_name,))
        key = (self._generation, id(db), mark["c"], mark["m"])
        cached = self._matrices.get(provider_name)
        if cached and cached[0] == key:
            return cached[1]
        rows = db.query(
            "SELECT e.photo_id, e.item_id, e.dim, e.vector FROM photo_embeddings e "
            "JOIN inventory_items i ON i.id = e.item_id "
            "WHERE e.provider = ? AND i.is_archived = 0", (provider_name,))
        np = _numpy()
        photo_ids = [row["photo_id"] for row in rows]
        item_ids = [row["item_id"] for row in rows]
        if np is not None and rows:
            dim = max(row["dim"] for row in rows)
            matrix = np.zeros((len(rows), dim), dtype=np.float32)
            for index, row in enumerate(rows):
                vector = np.frombuffer(row["vector"], dtype=np.float32)
                matrix[index, :len(vector)] = vector[:dim]
            data = (photo_ids, item_ids, matrix)
        else:
            data = (photo_ids, item_ids, [_unpack(row["vector"]) for row in rows])
        self._matrices = {provider_name: (key, data)}      # держим один набор
        return data

    @staticmethod
    def _scores(vectors, queries: list[list[float]]) -> list[float]:
        """Лучшее сходство каждой фотографии с любым из видов запроса."""
        np = _numpy()
        if np is not None and not isinstance(vectors, list):
            query = np.asarray(queries, dtype=np.float32)
            if query.shape[1] != vectors.shape[1]:
                width = min(query.shape[1], vectors.shape[1])
                return (vectors[:, :width] @ query[:, :width].T).max(axis=1).tolist()
            return (vectors @ query.T).max(axis=1).tolist()
        return [max(cosine(query, vector) for query in queries) for vector in vectors]

    def _embed_views(self, provider, image_path: Path, box: Optional[tuple]) -> list[list[float]]:
        """Векторы запроса: выделенная область либо весь кадр и его центр."""
        views = (box,) if box else self.QUERY_VIEWS
        result = []
        if hasattr(provider, "embed_image"):
            # Встроенный алгоритм сравнивает контуры и потому не узнаёт предмет,
            # снятый с другой стороны (зеркально). Третий вид — отражённый кадр —
            # поднимает точность на таких снимках с 60 до 90 с лишним процентов.
            from PIL import ImageOps
            for view in views:
                image = _open_rgb(image_path, view)
                result.append(provider.embed_image(image))
            result.append(provider.embed_image(ImageOps.mirror(_open_rgb(image_path, box))))
            return result
        for view in views:
            if view is None:
                result.append(provider.embed(Path(image_path)))
                continue
            try:
                result.append(provider.embed(Path(image_path), view))
            except TypeError:
                # провайдер не умеет резать кадр сам — режем во временный файл
                result.append(self._embed_cropped(provider, image_path, view))
        return result

    @staticmethod
    def _embed_cropped(provider, image_path: Path, box: tuple) -> list[float]:
        import os
        import tempfile

        handle, name = tempfile.mkstemp(suffix=".png", prefix="sklad_crop_")
        os.close(handle)
        try:
            _open_rgb(image_path, box).save(name)
            return provider.embed(Path(name))
        finally:
            try:
                os.unlink(name)
            except OSError:
                pass

    # ------------------------------------------------------------------ поиск
    def search(self, image_path: Path, limit: int = 10, min_score: float = 0.5,
               text: str = "", category_id: Optional[int] = None,
               box: Optional[tuple] = None, exclude_item_id: Optional[int] = None,
               min_percent: Optional[int] = None) -> list[dict]:
        """
        Возвращает кандидатов от самого похожего к менее похожим:
        [{'item_id', 'photo_id', 'name', 'inv_number', 'score' (косинус),
          'percent' (положение на шкале сходства 0..100), 'level'}, ...]
        Для каждого предмета берётся его лучшая фотография.

        text и category_id сужают поиск: остаются только предметы, подходящие
        под текстовый запрос и лежащие в категории. box — выделенная часть
        кадра в долях (лево, верх, право, низ), если предметов на снимке
        несколько. min_percent отсекает слабые совпадения по шкале сходства.
        """
        # Каскад: сначала выбранный провайдер, при сбое — встроенный.
        # Нейросеть может отвалиться прямо во время поиска (пропал интернет,
        # сервис ответил ошибкой). Оператору в этот момент нужен результат, а
        # не сообщение об ошибке, поэтому поиск продолжается прежним способом,
        # а чем именно он отвечал — видно в last_provider_note.
        provider = self.provider
        self.last_provider_note = ""
        try:
            queries = self._embed_views(provider, Path(image_path), box)
        except Exception as exc:                                   # noqa: BLE001
            if provider is self._fallback:
                raise
            log.warning("Провайдер %s не смог обработать снимок (%s) — "
                        "переключаюсь на встроенный поиск", provider.name, exc)
            self.last_provider_note = (
                f"«{getattr(provider, 'title', provider.name)}» не ответил, "
                "поиск выполнен встроенным алгоритмом.")
            provider = self._fallback
            queries = self._embed_views(provider, Path(image_path), box)

        photo_ids, item_ids, vectors = self._load_vectors(provider.name)
        if not photo_ids and provider is not self._fallback:
            # фотографии проиндексированы прежним способом: пока не сделана
            # переиндексация, отвечаем встроенным алгоритмом, а не пустотой
            log.info("Нет векторов для провайдера %s — отвечаю встроенным поиском",
                     provider.name)
            self.last_provider_note = (
                "Фотографии ещё не переиндексированы под выбранный способ поиска — "
                "результат получен встроенным алгоритмом.")
            provider = self._fallback
            queries = self._embed_views(provider, Path(image_path), box)
            photo_ids, item_ids, vectors = self._load_vectors(provider.name)
        self.last_provider_name = provider.name
        if not photo_ids:
            return []

        allowed: Optional[set] = None
        if (text or "").strip() or category_id:
            from .inventory_service import inventory_service
            allowed = set(inventory_service.matching_ids(
                text=text or "", category_id=category_id))
            if not allowed:
                return []

        best: dict[int, tuple] = {}
        for photo_id, item_id, score in zip(photo_ids, item_ids,
                                            self._scores(vectors, queries)):
            if item_id == exclude_item_id or (allowed is not None and item_id not in allowed):
                continue
            previous = best.get(item_id)
            if previous is None or score > previous[0]:
                best[item_id] = (score, photo_id)
        ranked = sorted(best.items(), key=lambda pair: pair[1][0], reverse=True)

        results = []
        for item_id, (score, photo_id) in ranked:
            if score < min_score:
                break
            percent = similarity_percent(score, provider.name)
            if min_percent is not None and percent < min_percent:
                break
            results.append({"item_id": item_id, "photo_id": photo_id, "score": float(score),
                            "percent": percent, "level": similarity_level(percent)})
            if len(results) >= limit:
                break
        if results:
            marks = ",".join("?" * len(results))
            names = {row["id"]: row for row in get_db().query(
                f"SELECT id, name, inv_number FROM inventory_items WHERE id IN ({marks})",
                [r["item_id"] for r in results])}
            for result in results:
                row = names.get(result["item_id"])
                result["name"] = row["name"] if row else ""
                result["inv_number"] = row["inv_number"] if row else ""
        return results

    def similar_to_item(self, item_id: int, limit: int = 12,
                        min_percent: int = 30) -> list[dict]:
        """Похожие предметы по главной фотографии карточки (сам предмет исключён)."""
        from .photo_service import photo_service

        photo = photo_service.primary_photo(item_id)
        if not photo or not Path(photo["abs_path"]).exists():
            return []
        return self.search(Path(photo["abs_path"]), limit=limit, min_score=0.0,
                           exclude_item_id=item_id, min_percent=min_percent)

    def duplicate_pairs(self, threshold: Optional[float] = None, limit: int = 100) -> list[dict]:
        """
        Пары РАЗНЫХ предметов с почти одинаковыми фотографиями — кандидаты в дубли.

        Порог высокий намеренно: это список «проверьте глазами», и в нём должны
        быть действительно одинаковые снимки, а не просто похожие вещи.
        """
        provider = self.provider
        photo_ids, item_ids, vectors = self._load_vectors(provider.name)
        if not photo_ids and provider is not self._fallback:
            provider = self._fallback
            photo_ids, item_ids, vectors = self._load_vectors(provider.name)
        if len(photo_ids) < 2:
            return []
        if threshold is None:
            threshold = 0.975 if provider.name.startswith("clip") else 0.985
        np = _numpy()
        pairs: dict[tuple, float] = {}
        if np is not None and not isinstance(vectors, list):
            items = np.asarray(item_ids)
            for start in range(0, len(photo_ids), 512):
                block = vectors[start:start + 512] @ vectors.T
                rows, cols = np.nonzero(block >= threshold)
                for row, col in zip(rows.tolist(), cols.tolist()):
                    first, second = int(items[start + row]), int(items[col])
                    if first < second:
                        key = (first, second)
                        pairs[key] = max(pairs.get(key, 0.0), float(block[row, col]))
        else:
            for i in range(len(photo_ids)):
                for j in range(i + 1, len(photo_ids)):
                    if item_ids[i] == item_ids[j]:
                        continue
                    score = cosine(vectors[i], vectors[j])
                    if score >= threshold:
                        key = tuple(sorted((item_ids[i], item_ids[j])))
                        pairs[key] = max(pairs.get(key, 0.0), score)
        ranked = sorted(pairs.items(), key=lambda pair: pair[1], reverse=True)[:limit]
        if not ranked:
            return []
        ids = sorted({i for pair, _ in ranked for i in pair})
        info = {}
        db = get_db()
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            for row in db.query(
                    f"SELECT id, inv_number, name FROM inventory_items "
                    f"WHERE id IN ({','.join('?' * len(chunk))})", chunk):
                info[row["id"]] = dict(row)
        return [{"reason": "Почти одинаковые фотографии",
                 "percent": similarity_percent(score, provider.name),
                 "items": [info[first], info[second]]}
                for (first, second), score in ranked if first in info and second in info]

    def stats(self) -> dict:
        db = get_db()
        indexed = db.query_one("SELECT COUNT(*) c FROM photo_embeddings WHERE provider=?",
                               (self.provider.name,))["c"]
        total = db.query_one("SELECT COUNT(*) c FROM inventory_photos")["c"]
        return {"provider": self.provider.name, "indexed": indexed, "photos": total,
                "title": getattr(self.provider, "title", "Встроенный (без интернета)")}


image_search = ImageSearchService()
