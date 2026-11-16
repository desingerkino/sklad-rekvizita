# -*- coding: utf-8 -*-
"""
Проверка точности поиска по фотографии на настоящих снимках предметов.

Запуск:  python -m tools.eval_photo_search --images ПАПКА [--provider local|clip]
                                           [--limit 150] [--json out.json]

Что делает. Каждый снимок из папки становится предметом склада. Затем для
каждого предмета готовятся «новые снимки того же предмета» — так, как их
сделал бы человек на площадке:

    dark     тёмный кадр (яркость 55 %)
    warm     пересвет и тёплый свет ламп
    angle    поворот на 12° и обрезка — другой ракурс и кадрирование
    lowres   маленький размытый снимок с сильным сжатием (плохая камера)
    mirror   зеркальный кадр — предмет повёрнут другой стороной
    small    предмет занимает треть кадра на постороннем фоне
    part     выделена только часть кадра (поиск по области)

и по каждому ищется предмет. Считается, как часто нужный предмет оказался
первым (top1) и в первой пятёрке (top5).

Это измерение, а не оценка «на глаз»: цифры из него попадают в отчёт о версии.
Папка с настоящими данными пользователя не трогается — всё во временной.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VARIANTS = ("dark", "warm", "angle", "lowres", "mirror", "small", "part")


def make_variant(source: Path, kind: str, target: Path, rng: random.Random) -> Path:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps

    with Image.open(source) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    if kind == "dark":
        image = ImageEnhance.Brightness(image).enhance(0.55)
    elif kind == "warm":
        image = ImageEnhance.Brightness(image).enhance(1.35)
        red, green, blue = image.split()
        image = Image.merge("RGB", (red.point(lambda v: min(255, int(v * 1.12))), green,
                                    blue.point(lambda v: int(v * 0.82))))
    elif kind == "angle":
        image = image.rotate(12, resample=Image.BICUBIC, expand=False, fillcolor=(90, 90, 90))
        image = image.crop((int(width * 0.1), int(height * 0.1),
                            int(width * 0.9), int(height * 0.9)))
    elif kind == "lowres":
        image = image.resize((max(40, width // 5), max(40, height // 5)))
        image = image.filter(ImageFilter.GaussianBlur(0.8))
    elif kind == "mirror":
        image = ImageOps.mirror(image)
    elif kind == "small":
        canvas = Image.new("RGB", (width * 2, height * 2),
                           (rng.randrange(90, 200), rng.randrange(90, 200),
                            rng.randrange(90, 200)))
        canvas.paste(image.resize((int(width * 1.1), int(height * 1.1))),
                     (int(width * 0.45), int(height * 0.45)))
        image = canvas
    elif kind == "part":
        image = image.crop((int(width * 0.15), int(height * 0.15),
                            int(width * 0.85), int(height * 0.85)))
    image.save(target, "JPEG", quality=30 if kind == "lowres" else 88)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", required=True)
    parser.add_argument("--provider", default="local", choices=("local", "clip"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    os.environ["SKLAD_DATA_DIR"] = tempfile.mkdtemp(prefix="sklad_eval_")
    os.environ["SKLAD_KEY_DIR"] = tempfile.mkdtemp(prefix="sklad_evalkey_")

    from app import paths
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.services import image_search_providers as registry
    from app.services.auth_service import auth_service
    from app.services.image_search import image_search
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import photo_service
    from app.services.settings_service import settings_service

    paths.ensure_dirs()
    db = Database(paths.DATA_DIR / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Михаил", "admin", "Пароль!2026", "Заведующий")
    auth_service.login("admin", "Пароль!2026")

    settings_service.set(registry.SETTING_PROVIDER, args.provider)
    if args.provider == "clip":
        ready, reason = registry.get("clip").available()
        if not ready:
            print("EVAL нейросеть недоступна:", reason)
            return 2

    images = sorted(p for p in Path(args.images).iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if args.limit:
        images = images[:args.limit]
    if len(images) < 10:
        print("EVAL мало снимков для проверки:", len(images))
        return 2

    started = time.perf_counter()
    items: dict[int, Path] = {}
    for image in images:
        name = image.stem.split("_", 1)[-1].replace("_", " ")
        item_id = inventory_service.create({"name": name, "quantity_total": 1})
        photo_service.add_photo(item_id, image, "main")
        items[item_id] = image
    index_seconds = time.perf_counter() - started

    rng = random.Random(7)
    work = Path(tempfile.mkdtemp(prefix="sklad_evalq_"))
    totals = {kind: {"top1": 0, "top5": 0, "n": 0} for kind in VARIANTS}
    search_ms = []
    true_scores, other_scores = [], []      # косинусы: тот же предмет и лучший посторонний
    for item_id, image in items.items():
        for kind in VARIANTS:
            query = make_variant(image, kind, work / f"{item_id}_{kind}.jpg", rng)
            began = time.perf_counter()
            found = image_search.search(query, limit=len(items), min_score=-1.0)
            search_ms.append((time.perf_counter() - began) * 1000)
            ids = [row["item_id"] for row in found[:5]]
            mine = next((row["score"] for row in found if row["item_id"] == item_id), None)
            others = [row["score"] for row in found if row["item_id"] != item_id]
            if mine is not None and others:
                true_scores.append(mine)
                other_scores.append(max(others))
            totals[kind]["n"] += 1
            totals[kind]["top1"] += int(bool(ids) and ids[0] == item_id)
            totals[kind]["top5"] += int(item_id in ids)
            query.unlink()

    # Точный снимок предмета обязан находиться первым и с высоким сходством,
    # а посторонняя картинка (ровный шум) не должна давать «очень похоже».
    exact_ok = 0
    for item_id, image in list(items.items())[:40]:
        found = image_search.search(image, limit=1, min_score=0.0)
        exact_ok += int(bool(found) and found[0]["item_id"] == item_id
                        and found[0]["percent"] >= 80)
    from PIL import Image
    noise = work / "noise.png"
    Image.effect_noise((320, 240), 90).convert("RGB").save(noise)
    noise_found = image_search.search(noise, limit=1, min_score=0.0)
    noise_percent = noise_found[0]["percent"] if noise_found else 0

    report = {
        "provider": image_search.last_provider_name, "items": len(items),
        "index_ms_per_photo": round(index_seconds * 1000 / len(items), 1),
        "search_ms_median": round(sorted(search_ms)[len(search_ms) // 2], 1),
        "exact_top1_high": f"{exact_ok}/40", "noise_percent": noise_percent,
        "variants": {kind: {"top1": round(100 * value["top1"] / value["n"], 1),
                            "top5": round(100 * value["top5"] / value["n"], 1)}
                     for kind, value in totals.items()},
    }
    def percentiles(values: list) -> dict:
        ordered = sorted(values)
        return {f"p{p}": round(ordered[min(len(ordered) - 1, len(ordered) * p // 100)], 3)
                for p in (5, 25, 50, 75, 95)} if ordered else {}
    # распределение косинусов — по нему выставляется шкала сходства
    report["cosine_same_item"] = percentiles(true_scores)
    report["cosine_best_other"] = percentiles(other_scores)
    report["fusion"] = image_search.fusion_enabled()
    all_n = sum(v["n"] for v in totals.values())
    report["top1_all"] = round(100 * sum(v["top1"] for v in totals.values()) / all_n, 1)
    report["top5_all"] = round(100 * sum(v["top5"] for v in totals.values()) / all_n, 1)
    print("EVAL " + json.dumps(report, ensure_ascii=False))
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
