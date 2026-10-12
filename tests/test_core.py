# -*- coding: utf-8 -*-
"""
Проверка реальных рабочих сценариев склада (без графического интерфейса).
Запуск:  python -m tests.test_core
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# изолированная папка данных на время тестов
TMP = Path(tempfile.mkdtemp(prefix="sklad_test_"))
os.environ["SKLAD_DATA_DIR"] = str(TMP)

from app import paths                      # noqa: E402
from app.db.database import Database, set_db, get_db   # noqa: E402
from app.db.seed import seed_if_empty      # noqa: E402
from app.errors import AppError            # noqa: E402
from app.services.backup_service import backup_service        # noqa: E402
from app.services.categories_service import categories_service  # noqa: E402
from app.services.clients_service import clients_service      # noqa: E402
from app.services.documents_service import documents_service  # noqa: E402
from app.services.image_search import image_search            # noqa: E402
from app.services.inventory_service import inventory_service  # noqa: E402
from app.services.photo_service import photo_service          # noqa: E402
from app.services.productions_service import productions_service  # noqa: E402
from app.services.projects_service import projects_service    # noqa: E402
from app.services.rentals_service import (RentalDraft, RentalLine, calculate,  # noqa: E402
                                          rentals_service)
from app.services.repairs_service import repairs_service      # noqa: E402
from app.services.auth_service import auth_service            # noqa: E402
from app.services.billing_service import billing_service      # noqa: E402
from app.services.settings_service import settings_service    # noqa: E402

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    print(("  ok   " if condition else "  FAIL ") + name + (f" — {detail}" if detail and not condition else ""))


def make_photo(path: Path, color, shape=None) -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (400, 400), color)
    d = ImageDraw.Draw(img)
    if shape == "chair":
        d.rectangle([120, 120, 280, 160], fill=(90, 40, 20))
        d.rectangle([130, 160, 150, 320], fill=(90, 40, 20))
        d.rectangle([250, 160, 270, 320], fill=(90, 40, 20))
    elif shape == "lamp":
        d.polygon([(150, 90), (250, 90), (280, 180), (120, 180)], fill=(240, 220, 120))
        d.rectangle([190, 180, 210, 330], fill=(60, 60, 60))
    img.save(path, "JPEG", quality=92)
    return path


def main() -> int:
    paths.ensure_dirs()
    db = Database(TMP / "data.db")
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    print("\n=== Подготовка ===")
    # версия 2.0: работа ведётся от имени пользователя
    auth_service.create_first_admin("Тестовый администратор", "admin", "admin123")
    auth_service.login("admin", "admin123")
    check("Вход администратора выполнен", auth_service.is_admin())
    cats = categories_service.tree()
    check("Дерево категорий загружено", len(cats) > 30, f"корней: {len(cats)}")
    stulya = next(c for c in cats if c["name"] == "Мебель")["children"]
    stul_cat = next(c for c in stulya if c["name"] == "Стулья")["id"]
    lamp_cat = next(c for c in cats if c["name"] == "Лампы")["children"][0]["id"]
    check("Путь категории", categories_service.path(stul_cat) == "Мебель / Стулья",
          categories_service.path(stul_cat))

    settings_service.set_many({
        "warehouse.name": "Склад Реквизита «Кадр»", "warehouse.address": "Москва, ул. Съёмочная, 1",
        "warehouse.phone": "+7 495 000-00-00", "warehouse.email": "sklad@example.ru",
        "warehouse.inn": "7700000000", "warehouse.ogrn": "1157700000000",
        "warehouse.bank_name": "АО «Банк»", "warehouse.bank_account": "40702810000000000001",
        "warehouse.bank_bik": "044525000",
    })

    # ---------------------------------------------------------- Сценарий 1
    print("\n=== Сценарий 1: реквизит, фото, поиск ===")
    chair_id = inventory_service.create({
        "name": "Стул деревянный красный", "category_id": stul_cat,
        "description": "Венский стул, дерево, красная краска", "keywords": "стул венский красный дерево",
        "quantity_total": 10, "rental_price": 500, "valuation": 7000, "note": "Комплект из 10 шт."})
    lamp_id = inventory_service.create({
        "name": "Лампа настольная зелёная", "category_id": lamp_cat,
        "description": "Банкирская лампа с зелёным плафоном", "keywords": "лампа зелёная банкирская",
        "quantity_total": 3, "rental_price": 900, "valuation": 15000})
    table_id = inventory_service.create({
        "name": "Стол письменный дубовый", "category_id": stul_cat,
        "keywords": "стол дуб письменный", "quantity_total": 2, "rental_price": 1500, "valuation": 40000})

    chair = inventory_service.get(chair_id)
    check("Реквизит создан с инв. номером", chair["inv_number"].isdigit(), chair["inv_number"])
    check("Количество 10", chair["quantity_total"] == 10)
    check("Доступно 10", chair["available_now"] == 10)

    tmp_photos = TMP / "src"
    tmp_photos.mkdir(exist_ok=True)
    p1 = photo_service.add_photo(chair_id, make_photo(tmp_photos / "chair1.jpg", (200, 60, 50), "chair"), "main")
    photo_service.add_photo(chair_id, make_photo(tmp_photos / "chair2.jpg", (205, 65, 45), "chair"), "detail")
    photo_service.add_photo(lamp_id, make_photo(tmp_photos / "lamp.jpg", (40, 120, 60), "lamp"), "main")
    check("Фотографии сохранены", len(photo_service.photos_of(chair_id)) == 2)
    check("Файл фото существует", Path(photo_service.photos_of(chair_id)[0]["abs_path"]).exists())
    check("Миниатюра создана", Path(photo_service.photos_of(chair_id)[0]["thumb_path"]).exists())

    check("Поиск по названию", any(i["id"] == chair_id for i in inventory_service.search("стул")))
    check("Поиск по ключевому слову", any(i["id"] == chair_id for i in inventory_service.search("венский")))
    check("Поиск по инв. номеру", any(i["id"] == chair_id for i in inventory_service.search(chair["inv_number"])))
    check("Поиск по описанию", any(i["id"] == lamp_id for i in inventory_service.search("плафон")))
    check("Фильтр по категории", all(i["category_id"] == stul_cat
                                     for i in inventory_service.search("", stul_cat)))

    # ---------------------------------------------------------- Поиск по фото
    print("\n=== Сценарий: поиск по фото ===")
    query = make_photo(TMP / "query.jpg", (198, 58, 52), "chair")
    results = image_search.search(query, limit=5, min_score=0.0)
    check("Поиск по фото вернул результаты", len(results) > 0)
    check("Первым найден нужный предмет", results and results[0]["item_id"] == chair_id,
          str([(r["name"], r["percent"]) for r in results]))
    check("Процент совпадения посчитан", results and 0 <= results[0]["percent"] <= 100,
          str(results[0]["percent"]) if results else "")
    check("Совпадение выше, чем у другого предмета",
          len(results) > 1 and results[0]["score"] > results[1]["score"])

    # ---------------------------------------------------------- Сценарий 2
    print("\n=== Сценарий 2: клиент, продакшен, проект, аренда, скидка, залог, PDF ===")
    client_id = clients_service.create({
        "full_name": "Иванов Иван Иванович", "passport": "45 08 123456, выдан ОВД Москвы 12.05.2010",
        "phone": "+7 916 111-22-33", "address": "Москва, ул. Мира, 5-10", "email": "ivanov@example.ru"})
    prod_id = productions_service.create({
        "name": "Кинокомпания «Пример»", "legal_form": "ООО", "inn": "7712345678",
        "ogrn": "1157746000000", "legal_address": "Москва, Мосфильмовская, 1",
        "bank_name": "АО «Банк»", "bank_account": "40702810900000000123", "bank_bik": "044525225",
        "contact_person": "Петрова А.А.", "phone": "+7 495 123-45-67"})
    project_id = projects_service.create({"name": "Фильм «Тишина»", "production_id": prod_id})

    check("Автозаполнение клиента по префиксу",
          clients_service.suggest("Иванов")[0]["id"] == client_id)

    start = date.today()
    end = start + timedelta(days=4)
    lines = [
        RentalLine(chair_id, "Стул деревянный красный", chair["inv_number"], "Венский стул", "Царапина на правой ножке", 6, 500, 7000),
        RentalLine(lamp_id, "Лампа настольная зелёная", "", "Банкирская лампа", "", 2, 900, 15000),
        RentalLine(table_id, "Стол письменный дубовый", "", "", "Скол на краске", 1, 1500, 40000),
    ]
    totals = calculate(lines, 10, 20000)
    check("Сумма до скидки 6300", totals["total_before_discount"] == 6 * 500 + 2 * 900 + 1500,
          str(totals["total_before_discount"]))
    check("Скидка 10% = 630", totals["discount_amount"] == 630, str(totals["discount_amount"]))
    check("Итого аренда 5670", totals["total_final"] == 5670, str(totals["total_final"]))
    check("Залог не смешан со стоимостью", totals["deposit"] == 20000 and totals["total_final"] == 5670)
    check("Оценочная стоимость 112000",
          totals["total_valuation"] == 6 * 7000 + 2 * 15000 + 40000, str(totals["total_valuation"]))

    draft = RentalDraft(client_id=client_id, production_id=prod_id, project_id=project_id,
                        payment_method="bank", date_start=start, date_end=end,
                        discount_percent=10, deposit=20000, lines=lines)
    rental_id = rentals_service.create(draft)
    rental = rentals_service.get(rental_id)
    check("Номер аренды сгенерирован", rental["number"].startswith(f"АР-{start.year}-"), rental["number"])
    check("Аренда сохранена в базе", rental["total_final"] == 5670)
    check("Позиции сохранены", len(rental["items"]) == 3)
    check("Индивидуальное примечание сохранено",
          rental["items"][0]["note"] == "Царапина на правой ножке")
    check("Доступность уменьшилась", inventory_service.get(chair_id)["available_now"] == 4,
          str(inventory_service.get(chair_id)["available_now"]))

    doc_id = documents_service.create_document(rental_id)
    doc = documents_service.get(doc_id)
    pdf = Path(doc["abs_path"]) if doc["abs_path"] else None
    check("PDF создан", pdf is not None and pdf.exists())
    check("PDF настоящий (сигнатура %PDF)", pdf.read_bytes()[:4] == b"%PDF")
    check("PDF не пустой (> 3 КБ)", pdf.stat().st_size > 3000, f"{pdf.stat().st_size} байт")
    html = documents_service.render_html(doc_id)
    check("HTML-версия для печати содержит номер", rental["number"] in html)
    check("В документе есть скидка и залог", "ИТОГО АРЕНДА" in html and "ЗАЛОГ" in html)

    # ---------------------------------------------------------- Сценарий 3
    print("\n=== Сценарий 3: пересечение дат и контроль количества ===")
    over_lines = [RentalLine(chair_id, "Стул деревянный красный", "", "", "", 5, 500, 7000)]
    draft2 = RentalDraft(client_id=client_id, production_id=prod_id, project_id=project_id,
                         payment_method="cash", date_start=start + timedelta(days=2),
                         date_end=end + timedelta(days=3), lines=over_lines)
    try:
        rentals_service.create(draft2)
        check("Пересечение дат заблокировано", False, "аренда создалась, хотя не должна")
    except AppError as exc:
        check("Пересечение дат заблокировано", True)
        check("Сообщение понятное, без техножаргона",
              "доступно 4" in str(exc) and "Exception" not in str(exc), str(exc))
        print("       сообщение: " + str(exc)[:160])

    draft3 = RentalDraft(client_id=client_id, production_id=prod_id, project_id=project_id,
                         payment_method="cash", date_start=start + timedelta(days=2),
                         date_end=end + timedelta(days=3),
                         lines=[RentalLine(chair_id, "Стул", "", "", "", 4, 500, 7000)])
    rental2_id = rentals_service.create(draft3)
    check("Аренда в пределах остатка проходит", rental2_id > 0)

    draft4 = RentalDraft(client_id=client_id, production_id=prod_id, project_id=project_id,
                         payment_method="cash", date_start=end + timedelta(days=30),
                         date_end=end + timedelta(days=35),
                         lines=[RentalLine(chair_id, "Стул", "", "", "", 10, 500, 7000)])
    check("На свободные даты доступны все 10 шт.",
          inventory_service.available_for_period(chair_id, end + timedelta(days=30),
                                                 end + timedelta(days=35)) == 10)
    rentals_service.create(draft4)

    try:
        rentals_service.create(RentalDraft(client_id=None, production_id=prod_id,
                                           project_id=project_id, payment_method="cash",
                                           date_start=start, lines=lines))
        check("Проверка незаполненных полей", False)
    except AppError as exc:
        check("Проверка незаполненных полей", "физическое лицо" in str(exc), str(exc))

    # ---------------------------------------------------------- Сценарий 4
    print("\n=== Сценарий 4: продление ===")
    new_end = end + timedelta(days=2)
    ext_id = rentals_service.extend(rental_id, new_end, "Досъёмки")
    ext_doc_id = documents_service.create_document(rental_id, "extension", ext_id)
    ext_doc = documents_service.get(ext_doc_id)
    check("Дата окончания обновлена",
          rentals_service.get(rental_id)["date_end"] == new_end.isoformat())
    check("Документ пролонгации создан", Path(ext_doc["abs_path"]).exists())
    ext_snap = documents_service.get_snapshot(ext_doc_id)
    check("В пролонгации есть старая и новая дата",
          ext_snap["extension"]["old_date_end"] == end.isoformat()
          and ext_snap["extension"]["new_date_end"] == new_end.isoformat())
    check("Исходный документ не изменился",
          documents_service.get_snapshot(doc_id)["rental"]["date_end"] == end.isoformat())

    # ---------------------------------------------------------- Сценарий 5
    print("\n=== Сценарий 5: ремонт ===")
    lamp_before = inventory_service.get(lamp_id)["available_now"]
    repair_id = repairs_service.send_to_repair(lamp_id, 1, "Разбит плафон", "Замена стекла")
    lamp_after = inventory_service.get(lamp_id)["available_now"]
    check("Ремонт уменьшает доступность", lamp_after == lamp_before - 1, f"{lamp_before} -> {lamp_after}")
    try:
        rentals_service.create(RentalDraft(
            client_id=client_id, production_id=prod_id, project_id=project_id,
            payment_method="cash", date_start=start, date_end=end,
            lines=[RentalLine(lamp_id, "Лампа", "", "", "", 2, 900, 15000)]))
        check("Реквизит в ремонте недоступен", False)
    except AppError as exc:
        check("Реквизит в ремонте недоступен", "В ремонте: 1" in str(exc), str(exc))
    repairs_service.return_from_repair(repair_id, "Плафон заменён")
    check("После ремонта доступность вернулась",
          inventory_service.get(lamp_id)["available_now"] == lamp_before)
    check("История ремонта сохранена", len(repairs_service.history(lamp_id)) == 1)

    # ---------------------------------------------------------- Сценарий 6
    print("\n=== Сценарий 6: исторический snapshot ===")
    clients_service.update(client_id, {"phone": "+7 916 999-88-77"})
    check("Телефон в карточке изменён",
          clients_service.get(client_id)["phone"] == "+7 916 999-88-77")
    old_snapshot = documents_service.get_snapshot(doc_id)
    check("Старый документ хранит прежний телефон",
          old_snapshot["client"]["phone"] == "+7 916 111-22-33", old_snapshot["client"]["phone"])
    check("Старый документ хранит прежние реквизиты продакшена",
          old_snapshot["production"]["name"] == "Кинокомпания «Пример»")
    productions_service.update(prod_id, {"name": "Кинокомпания «Пример 2»"})
    check("Изменение продакшена не тронуло старый документ",
          documents_service.get_snapshot(doc_id)["production"]["name"] == "Кинокомпания «Пример»")

    # ---------------------------------------------------------- Уведомления
    print("\n=== Уведомления и история ===")
    soon_id = rentals_service.create(RentalDraft(
        client_id=client_id, production_id=prod_id, project_id=project_id, payment_method="cash",
        date_start=date.today(), date_end=date.today() + timedelta(days=2),
        lines=[RentalLine(table_id, "Стол", "", "", "", 1, 1500, 40000)]))
    soon = rentals_service.expiring_soon(2)
    check("Уведомление за 2 дня работает", any(r["id"] == soon_id for r in soon),
          str([r["number"] for r in soon]))
    check("История предмета содержит аренды", len(inventory_service.history(chair_id)) >= 3)
    check("История клиента содержит аренды", len(clients_service.rentals(client_id)) >= 4)
    check("История проекта содержит аренды", len(projects_service.rentals(project_id)) >= 4)
    check("История продакшена содержит аренды", len(productions_service.rentals(prod_id)) >= 4)

    # ---------------------------------------------------------- Сценарий 7
    print("\n=== Сценарий 7: резервное копирование и восстановление ===")
    backup_path = backup_service.create(with_photos=True)
    check("Резервная копия создана", backup_path.exists())
    check("Архив фотографий создан", backup_path.with_suffix(".photos.zip").exists())
    check("Копия видна в списке", any(b["path"] == str(backup_path) for b in backup_service.list_backups()))

    extra_id = inventory_service.create({"name": "Временный предмет", "quantity_total": 1})
    check("Добавлен предмет после бэкапа", inventory_service.get(extra_id) is not None)
    backup_service.restore(backup_path)
    check("После восстановления лишнего предмета нет", inventory_service.get(extra_id) is None)
    check("После восстановления аренда на месте", rentals_service.get(rental_id) is not None)
    check("После восстановления каталог на месте", inventory_service.get(chair_id)["name"] == "Стул деревянный красный")
    check("Рабочая копия не потеряна",
          any(p.name.startswith("before_restore_") for p in backup_service.backup_dir().glob("*.db")))

    # ---------------------------------------- Windows: занятый файл журнала
    print("\n=== Восстановление при занятом файле журнала (Windows) ===")
    from pathlib import Path as _Path

    marker = inventory_service.create({"name": "Метка перед блокировкой", "quantity_total": 1})
    locked_backup = backup_service.create(tag="locked")
    inventory_service.create({"name": "Лишний после копии", "quantity_total": 1})

    original_unlink = _Path.unlink

    def always_locked(self, *args, **kwargs):
        """
        Имитация Windows: удалить data.db-wal невозможно никогда.

        Восстановление обязано пройти всё равно: содержимое копии переписывается
        в существующий файл базы штатным механизмом SQLite, файл базы не
        удаляется и не подменяется, поэтому чужой захват журнала на него не
        влияет. Раньше именно здесь всё обрывалось с WinError 32.
        """
        if self.name.endswith("-wal") or self.name.endswith("-shm"):
            raise PermissionError(32, "Процесс не может получить доступ к файлу")
        return original_unlink(self, *args, **kwargs)

    _Path.unlink = always_locked
    try:
        backup_service.restore(locked_backup)
        survived = True
        failure = ""
    except Exception as exc:      # noqa: BLE001
        survived, failure = False, str(exc)
    finally:
        _Path.unlink = original_unlink
    check("Занятый журнал не срывает восстановление", survived, failure)
    check("Данные после восстановления на месте",
          inventory_service.get(marker) is not None
          and not inventory_service.search("Лишний после копии"))
    check("Рабочая копия прежней базы сохранена",
          any(p.name.startswith("before_restore_") for p in backup_service.backup_dir().glob("*.db")))

    # повреждённый файл не должен подменять рабочую базу
    broken = backup_service.backup_dir() / "sklad_broken.db"
    broken.write_bytes(b"not a database at all")
    before_count = len(inventory_service.search(""))
    try:
        backup_service.restore(broken)
        rejected = False
    except AppError:
        rejected = True
    check("Повреждённая копия отклонена", rejected)
    check("После отклонения база не пострадала",
          len(inventory_service.search("")) == before_count
          and inventory_service.get(marker) is not None)
    broken.unlink(missing_ok=True)

    print("\n=== Целостность базы ===")
    integrity = get_db().query_one("PRAGMA integrity_check")[0]
    check("integrity_check = ok", integrity == "ok", integrity)
    fk = get_db().query("PRAGMA foreign_key_check")
    check("Нет нарушений внешних ключей", len(fk) == 0)

    print(f"\n{'=' * 60}\nПройдено: {len(PASSED)}   Провалено: {len(FAILED)}")
    if FAILED:
        for f in FAILED:
            print("  FAIL:", f)
    print(f"Данные теста: {TMP}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    code = main()
    try:
        get_db().close()
    except Exception:
        pass
    sys.exit(code)
