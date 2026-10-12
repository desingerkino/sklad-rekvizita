# -*- coding: utf-8 -*-
"""
Поиск реквизита по фотографии.

Архитектура: провайдер векторных признаков (embedding) + хранилище векторов в БД
+ поиск ближайших по косинусной близости.

Встроенный провайдер LocalVisualProvider работает офлайн, без интернета и без
внешних сервисов: строит вектор из перцептивного хэша (форма/структура),
цветовых гистограмм HSV, сетки средних цветов 4x4 и гистограммы направлений
границ. Этого достаточно, чтобы узнать предмет на снимке со склада.

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


class LocalVisualProvider:
    """Офлайн-провайдер на Pillow. Никуда ничего не отправляет."""

    name = "local-v1"
    dim = 64 + 32 + 16 + 48 + 8   # структура + оттенок + насыщенность + сетка цветов + границы

    def embed(self, image_path: Path) -> list[float]:
        from PIL import Image, ImageFilter

        from .photo_service import enable_heic

        enable_heic()      # снимок для поиска тоже может быть с айфона

        with Image.open(image_path) as im:
            im = im.convert("RGB")
            im.thumbnail((256, 256))
            rgb = im.copy()

        gray = rgb.convert("L")

        # 1. Структура: dHash 8x8 (различия соседних пикселей)
        small = gray.resize((9, 8))
        px = list(small.getdata())
        structure = []
        for y in range(8):
            for x in range(8):
                left = px[y * 9 + x]
                right = px[y * 9 + x + 1]
                structure.append(1.0 if left > right else 0.0)

        # 2. Цвет: гистограммы тона и насыщенности
        hsv = rgb.convert("HSV")
        h_data = list(hsv.getchannel("H").getdata())
        s_data = list(hsv.getchannel("S").getdata())
        hue = [0.0] * 32
        sat = [0.0] * 16
        for h, s in zip(h_data, s_data):
            if s > 25:                       # серые пиксели тон не задают
                hue[min(31, h * 32 // 256)] += 1.0
            sat[min(15, s * 16 // 256)] += 1.0
        total = max(1, len(h_data))
        hue = [v / total for v in hue]
        sat = [v / total for v in sat]

        # 3. Композиция: средний цвет по сетке 4x4
        grid_img = rgb.resize((4, 4))
        grid = [c / 255.0 for pxl in grid_img.getdata() for c in pxl]

        # 4. Границы: доля контуров по 8 зонам изображения
        edges = gray.filter(ImageFilter.FIND_EDGES).resize((4, 2))
        edge_vals = [v / 255.0 for v in edges.getdata()]

        vec = structure + hue + sat + grid + edge_vals
        return _normalize(vec)


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


class ImageSearchService:
    def __init__(self, provider: Optional[EmbeddingProvider] = None):
        self._explicit: Optional[EmbeddingProvider] = provider
        self._fallback = LocalVisualProvider()
        self.last_provider_note = ""       # чем реально отвечал последний поиск

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
            ready, reason = chosen.available()
            if ready:
                return chosen
            if getattr(chosen, "key", "local") != "local":
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

    # ------------------------------------------------------------- индексация
    def index_photo(self, photo_id: int, item_id: int, image_path: Path) -> None:
        """
        Считает отпечаток фотографии действующим способом поиска и — если это
        не встроенный — ещё и встроенным.

        Встроенный отпечаток нужен всегда: это запасной путь. Если нейросеть
        откажет или файл модели пропадёт, поиск откатится на встроенный
        алгоритм, и ему должно быть по чему искать. Считается он за доли
        секунды, так что вторая запись почти ничего не стоит.
        """
        providers = [self.provider]
        if self.provider.name != self._fallback.name:
            providers.append(self._fallback)

        vectors = []
        for provider in providers:
            try:
                vectors.append((provider.name, provider.embed(Path(image_path))))
            except Exception:                                      # noqa: BLE001
                if provider is self._fallback:
                    raise
                log.exception("Способ %s не смог обработать %s — сохраняю "
                              "только встроенный отпечаток", provider.name, image_path)

        db = get_db()
        with db.transaction() as conn:
            for name, vec in vectors:
                conn.execute(
                    "INSERT INTO photo_embeddings(photo_id, item_id, provider, dim, vector) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(photo_id, provider) DO UPDATE SET "
                    "vector=excluded.vector, dim=excluded.dim, item_id=excluded.item_id",
                    (photo_id, item_id, name, len(vec), _pack(vec)),
                )

    def reindex_all(self, progress=None) -> int:
        """Переиндексация всей базы фотографий (после смены провайдера)."""
        from .photo_service import abs_path

        db = get_db()
        rows = db.query("SELECT id, item_id, rel_path FROM inventory_photos")
        done = 0
        for i, row in enumerate(rows, 1):
            path = abs_path(row["rel_path"])
            if not path.exists():
                continue
            try:
                self.index_photo(row["id"], row["item_id"], path)
                done += 1
            except Exception:
                log.exception("Не удалось проиндексировать %s", path)
            if progress:
                progress(i, len(rows))
        return done

    # ------------------------------------------------------------------ поиск
    def search(self, image_path: Path, limit: int = 10, min_score: float = 0.5) -> list[dict]:
        """
        Возвращает список кандидатов:
        [{'item_id', 'name', 'inv_number', 'score' (0..1), 'percent', 'photo_id'}, ...]
        Для каждого предмета берётся его лучшая фотография.
        """
        # Каскад: сначала выбранный провайдер, при сбое — встроенный.
        # Нейросеть может отвалиться прямо во время поиска (пропал интернет,
        # сервис ответил ошибкой). Оператору в этот момент нужен результат, а
        # не сообщение об ошибке, поэтому поиск продолжается прежним способом,
        # а чем именно он отвечал — видно в last_provider_note.
        provider = self.provider
        self.last_provider_note = ""
        try:
            query_vec = provider.embed(Path(image_path))
        except Exception as exc:                                   # noqa: BLE001
            if provider is self._fallback:
                raise
            log.warning("Провайдер %s не смог обработать снимок (%s) — "
                        "переключаюсь на встроенный поиск", provider.name, exc)
            self.last_provider_note = (
                f"«{getattr(provider, 'title', provider.name)}» не ответил, "
                "поиск выполнен встроенным алгоритмом.")
            provider = self._fallback
            query_vec = provider.embed(Path(image_path))

        db = get_db()
        rows = db.query(
            "SELECT e.photo_id, e.item_id, e.vector, i.name, i.inv_number "
            "FROM photo_embeddings e JOIN inventory_items i ON i.id = e.item_id "
            "WHERE e.provider = ? AND i.is_archived = 0",
            (provider.name,),
        )
        if not rows and provider is not self._fallback:
            # фотографии проиндексированы прежним способом: пока не сделана
            # переиндексация, отвечаем встроенным алгоритмом, а не пустотой
            log.info("Нет векторов для провайдера %s — отвечаю встроенным поиском",
                     provider.name)
            self.last_provider_note = (
                "Фотографии ещё не переиндексированы под выбранный способ поиска — "
                "результат получен встроенным алгоритмом.")
            provider = self._fallback
            query_vec = provider.embed(Path(image_path))
            rows = db.query(
                "SELECT e.photo_id, e.item_id, e.vector, i.name, i.inv_number "
                "FROM photo_embeddings e JOIN inventory_items i ON i.id = e.item_id "
                "WHERE e.provider = ? AND i.is_archived = 0",
                (provider.name,),
            )
        best: dict[int, dict] = {}
        for row in rows:
            score = cosine(query_vec, _unpack(row["vector"]))
            prev = best.get(row["item_id"])
            if prev is None or score > prev["score"]:
                best[row["item_id"]] = {
                    "item_id": row["item_id"],
                    "photo_id": row["photo_id"],
                    "name": row["name"],
                    "inv_number": row["inv_number"],
                    "score": score,
                }
        results = sorted(best.values(), key=lambda r: r["score"], reverse=True)
        results = [r for r in results if r["score"] >= min_score][:limit]
        for r in results:
            r["percent"] = round(max(0.0, min(1.0, r["score"])) * 100)
        return results

    def stats(self) -> dict:
        db = get_db()
        indexed = db.query_one("SELECT COUNT(*) c FROM photo_embeddings WHERE provider=?",
                               (self.provider.name,))["c"]
        total = db.query_one("SELECT COUNT(*) c FROM inventory_photos")["c"]
        return {"provider": self.provider.name, "indexed": indexed, "photos": total}


image_search = ImageSearchService()
