# -*- coding: utf-8 -*-
"""
Формирует настоящий документ аренды средствами самой программы и превращает
первую страницу в картинку для руководства. Это не рисунок: PDF создаёт тот же
код, что работает на складе.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(out_png: Path) -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="sklad_sample_"))
    os.environ["SKLAD_DATA_DIR"] = str(tmp)
    sys.path.insert(0, str(ROOT))

    from app import paths
    paths.ensure_dirs()
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    db = Database(paths.DB_PATH)
    db.initialize()
    set_db(db)
    seed_if_empty(db)

    from app.services.auth_service import auth_service
    from app.services.billing_service import billing_service
    from app.services.clients_service import clients_service
    from app.services.documents_service import documents_service
    from app.services.inventory_service import inventory_service
    from app.services.productions_service import productions_service
    from app.services.projects_service import projects_service
    from app.services.rentals_service import RentalDraft, RentalLine, rentals_service
    from app.services.settings_service import settings_service

    auth_service.create_first_admin("Иван Петров", "admin", "admin123")
    auth_service.login("admin", "admin123")
    settings_service.set_protected({
        "warehouse.name": "Склад реквизита «Кадр»",
        "warehouse.address": "Москва, ул. Съёмочная, д. 1, стр. 4",
        "warehouse.phone": "+7 (495) 000-00-00", "warehouse.email": "sklad@kadr-props.ru",
        "warehouse.inn": "7712345678", "warehouse.ogrn": "1157746000000",
        "warehouse.bank_name": "АО «Банк»", "warehouse.bank_account": "40702810900000000123",
        "warehouse.bank_bik": "044525225", "warehouse.bank_corr": "30101810400000000225",
    })

    chair = inventory_service.create({"name": "Стул венский красный", "quantity_total": 20,
                                      "description": "Венский стул, дерево, красная краска",
                                      "rental_price": 1000, "valuation": 7000})
    lamp = inventory_service.create({"name": "Лампа настольная зелёная", "quantity_total": 10,
                                     "description": "Банкирская лампа с зелёным плафоном",
                                     "rental_price": 1000, "valuation": 15000})
    table = inventory_service.create({"name": "Стол письменный дубовый", "quantity_total": 5,
                                      "description": "Дуб, две тумбы, вторая половина XX века",
                                      "rental_price": 1500, "valuation": 40000})
    client = clients_service.create({
        "full_name": "Иванов Иван Иванович",
        "passport": "45 08 123456, выдан ОВД г. Москвы 12.05.2010",
        "phone": "+7 916 111-22-33", "address": "Москва, ул. Мира, д. 5, кв. 10",
        "email": "ivanov@example.ru"})
    production = productions_service.create({
        "name": "Кинокомпания «Кадр»", "legal_form": "ООО", "inn": "7798765432",
        "ogrn": "1157746111111", "legal_address": "Москва, Мосфильмовская ул., д. 1",
        "bank_name": "АО «Банк»", "bank_account": "40702810900000000999",
        "bank_bik": "044525225", "contact_person": "Петрова А. А.", "phone": "+7 495 123-45-67"})
    project = projects_service.create({"name": "Фильм «Тишина»", "production_id": production})

    start = date.today() - timedelta(days=4)
    rental = rentals_service.create(RentalDraft(
        client_id=client, production_id=production, project_id=project, payment_method="bank",
        date_start=start, date_end=start + timedelta(days=13),
        discount_percent=10, deposit=20000,
        lines=[
            RentalLine(chair, "Стул венский красный", "000001",
                       "Венский стул, дерево, красная краска", "Царапина на правой ножке",
                       6, 1000, 7000),
            RentalLine(lamp, "Лампа настольная зелёная", "000002",
                       "Банкирская лампа с зелёным плафоном", "", 2, 1000, 15000),
            RentalLine(table, "Стол письменный дубовый", "000003",
                       "Дуб, две тумбы, вторая половина XX века", "Скол на краске у ножки",
                       1, 1500, 40000),
        ]))
    rentals_service.add_items(rental, [
        RentalLine(lamp, "Лампа настольная зелёная", "000002",
                   "Банкирская лампа с зелёным плафоном", "Добор для второй смены", 3, 1000, 15000)])
    billing_service.return_item(billing_service.items(rental)[0]["id"], 2, "возврат двух стульев")

    document = documents_service.create_document(rental)
    pdf_path = Path(documents_service.get(document)["abs_path"])

    out_png.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-r", "130", "-f", "1", "-l", "1",
                    str(pdf_path), str(out_png.with_suffix(""))], check=True)
    produced = out_png.with_name(out_png.stem + "-1.png")
    if produced.exists():
        produced.replace(out_png)
    return pdf_path, out_png


if __name__ == "__main__":
    pdf, png = build(ROOT / "docs" / "screens" / "14_document.png")
    print("PDF:", pdf)
    print("PNG:", png)
