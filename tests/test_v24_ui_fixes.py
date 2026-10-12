# -*- coding: utf-8 -*-
"""
Проверки правок интерфейса: фотографии, подсказки поиска, HEIC.

Тесты бьют по сервисам и по исходникам экранов — PySide6 в этой среде не
запускается, но убедиться, что механика на месте, можно и так.

Запуск:  python -m tests.test_v24_ui_fixes
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="sklad_uifix_"))
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


def make_photo(path: Path, color) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (300, 220), color).save(path)
    return path


def main() -> int:
    from app.db.database import Database, set_db
    from app.db.seed import seed_if_empty
    from app.errors import ValidationError
    from app.services.auth_service import auth_service
    from app.services.categories_service import categories_service
    from app.services.clients_service import clients_service
    from app.services.inventory_service import inventory_service
    from app.services.photo_service import PHOTO_KINDS, photo_service

    db = Database()
    db.initialize()
    set_db(db)
    seed_if_empty(db)
    auth_service.create_first_admin("Иванов Иван", "admin", "1234")
    auth_service.pop_recovery_code()
    auth_service.login("admin", "1234")
    cat = categories_service.tree()[0]["id"]

    section("Ракурсы фотографий")
    chair = inventory_service.create({"name": "Стул венский", "category_id": cat,
                                      "quantity_total": 1})
    first = photo_service.add_photo(chair, make_photo(_TMP / "a.jpg", (200, 60, 40)),
                                    make_primary=True)
    second = photo_service.add_photo(chair, make_photo(_TMP / "b.jpg", (40, 90, 200)))
    third = photo_service.add_photo(chair, make_photo(_TMP / "c.jpg", (40, 200, 90)))
    photos = photo_service.photos_of(chair)
    check(len(photos) == 3, f"Загружены три фотографии (получено {len(photos)})")
    check(sum(1 for p in photos if p["is_primary"]) == 1, "Основная ровно одна")

    check(set(PHOTO_KINDS) >= {"main", "front", "side", "detail", "damage"},
          "Список ракурсов включает все кнопки карточки")
    photo_service.set_kind(second, "damage")
    photo_service.set_kind(third, "detail")
    kinds = {p["id"]: p["kind"] for p in photo_service.photos_of(chair)}
    check(kinds[second] == "damage" and kinds[third] == "detail",
          "Ракурс у загруженной фотографии меняется")
    try:
        photo_service.set_kind(second, "выдуманный")
        bad_kind_ok = False
    except ValidationError:
        bad_kind_ok = True
    check(bad_kind_ok, "Несуществующий ракурс отклоняется")

    photo_service.set_primary(second)
    photos = photo_service.photos_of(chair)
    check(sum(1 for p in photos if p["is_primary"]) == 1,
          "После смены основной она по-прежнему одна")
    check(next(p for p in photos if p["id"] == second)["is_primary"],
          "Основной стала выбранная фотография")

    photo_service.delete_photo(third)
    check(len(photo_service.photos_of(chair)) == 2, "Фотография удаляется")
    check(inventory_service.get(chair) is not None, "Предмет при этом остаётся")

    section("Подсказки поиска")
    inventory_service.create({"name": "Стул венский тёмный", "category_id": cat,
                              "quantity_total": 2})
    inventory_service.create({"name": "Лампа настольная", "category_id": cat,
                              "quantity_total": 3})
    found = inventory_service.search("стул", limit=8)
    check(len(found) == 2, f"Поиск по части слова находит оба стула ({len(found)})")
    check(all("available_now" in i and "quantity_total" in i for i in found),
          "В подсказке есть остатки — оператор видит, что доступно")

    section("Подбор физического лица")
    clients_service.create({"full_name": "Петров Пётр Петрович", "phone": "+70000000001"})
    clients_service.create({"full_name": "Иванов Пётр Сергеевич", "phone": "+70000000002"})
    clients_service.create({"full_name": "Сидорова Анна", "phone": "+70000000003"})
    clients_service.create({"full_name": "Абрамов Иван Петров", "phone": "+70000000004"})

    by_middle = clients_service.suggest("Петров", 10)
    check(len(by_middle) == 2,
          f"Фамилия в середине строки тоже находится (получено {len(by_middle)})")
    check(by_middle[0]["full_name"].startswith("Петров"),
          "Совпадение с начала строки идёт первым, хотя по алфавиту оно второе")
    check(by_middle[1]["full_name"] == "Абрамов Иван Петров",
          "Совпадение в середине идёт следом")
    check(len(clients_service.suggest("Сидор", 10)) == 1, "Поиск по началу работает")
    check(len(clients_service.suggest("сидор", 10)) == 1,
          "Регистр не важен — кириллица тоже приводится к нижнему")
    upper = {c["full_name"] for c in clients_service.suggest("АН", 10)}
    lower = {c["full_name"] for c in clients_service.suggest("ан", 10)}
    check(upper == lower and "Сидорова Анна" in upper,
          f"Заглавные и строчные дают один результат: {sorted(upper)}")
    check(len(clients_service.search("петров")) == 2,
          "В общем поиске по клиентам регистр тоже не важен")
    check(all("phone" in c for c in by_middle),
          "В подсказке есть телефон — однофамильцев можно различить")
    check(clients_service.suggest("", 10) == [], "Пустой запрос ничего не предлагает")

    section("HEIC")
    from app.services import photo_service as photo_module

    check(hasattr(photo_module, "enable_heic"), "Поддержка HEIC подключается явно")
    heic = _TMP / "снимок.heic"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64)
    try:
        photo_service.add_photo(chair, heic)
        heic_result = "загружен"
    except Exception as exc:                                       # noqa: BLE001
        heic_result = str(exc)
    if photo_module.enable_heic():
        check(heic_result == "загружен" or "не читается" not in heic_result,
              "HEIC обрабатывается библиотекой")
    else:
        check("HEIC" in heic_result and "pillow-heif" in heic_result,
              f"Без библиотеки объяснение понятное: {heic_result[:70]}")

    requirements = (Path(__file__).resolve().parent.parent
                    / "requirements.txt").read_text(encoding="utf-8")
    check("pillow-heif" in requirements, "pillow-heif объявлен в зависимостях")
    spec = (Path(__file__).resolve().parent.parent
            / "build" / "sklad.spec").read_text(encoding="utf-8")
    check("pillow_heif" in spec, "И включён в сборку .exe")

    section("Интерфейс")
    ui = Path(__file__).resolve().parent.parent / "app" / "ui"
    card = (ui / "item_card.py").read_text(encoding="utf-8")
    rental = (ui / "rental_new_view.py").read_text(encoding="utf-8")
    window = (ui / "main_window.py").read_text(encoding="utf-8")
    common = (ui / "widgets" / "common.py").read_text(encoding="utf-8")
    suggest = (ui / "widgets" / "suggest.py").read_text(encoding="utf-8")

    check("clicked = Signal()" in common,
          "Миниатюра фотографии кликается сигналом, а не подменой метода")
    check("mousePressEvent" not in card,
          "В карточке не осталось присваивания обработчика экземпляру")
    check("_step_photo" in card and "prev_photo_btn" in card,
          "В карточке есть перелистывание фотографий")
    check("PhotoViewer" in card and (ui / "photo_viewer.py").exists(),
          "Снимок открывается во весь экран")
    check("button.setEnabled(True)" in card and "нажмите, чтобы добавить" in card,
          "Кнопки ракурсов не гаснут, а предлагают добавить фотографию")
    check("_make_primary" in card and "_delete_photo" in card,
          "Основную можно сменить, лишнюю удалить")

    check("class SuggestList" in suggest, "Есть общий виджет подсказок")
    check("TYPING_PAUSE_MS" in suggest, "Запрос идёт с паузой, а не на каждую букву")
    check("Qt.Key_Down" in suggest and "Qt.Key_Return" in suggest,
          "Подсказками можно управлять с клавиатуры")

    check("SuggestList" in window and "self.global_search.clear()" in window,
          "Верхний поиск показывает подсказки и очищается после поиска")
    check("right_layout.addWidget(suggest_wrap)" in window,
          "Блок подсказок стоит в раскладке главного окна, а не поверх неё")
    check("SuggestList" in rental and "_preview_item" in rental,
          "В аренде есть подсказки и предпросмотр")
    check("root.addWidget(self.item_suggest)" in rental
          and "party_form.addRow(\"\", self.client_suggest)" in rental,
          "Оба списка подсказок в аренде встроены в раскладку")
    check("_add_previewed" in rental,
          "Позиция добавляется отдельной кнопкой, а не по клику в списке")
    check("FIELD_HEIGHT" in rental and "FORM_BOX_WIDTH" in rental,
          "Поля формы аренды имеют заданные размеры")
    check("import QCompleter" not in rental and "QCompleter(" not in rental,
          "Старое узкое автодополнение убрано")

    check("setWindowFlags" not in suggest,
          "Подсказки не отдельное окно: оно перехватывало фокус и уходило под "
          "соседние виджеты")
    check("layout.addWidget(self.list)" in suggest and "self.hide()" in suggest,
          "Подсказки встроены в раскладку и скрыты, пока их нет")
    check("setFocusPolicy(Qt.NoFocus)" in suggest,
          "Клик по подсказке не выдёргивает курсор из поля")
    check("def close_list" in suggest and "Qt.Key_Escape" in suggest,
          "Список закрывается по Esc и после выбора")
    check("self.field.setFocus()" not in suggest,
          "Список не дёргает фокус сам — из-за этого он и не показывался")

    picker = (ui / "client_picker.py")
    check(picker.exists(), "Есть список физических лиц для выбора")
    if picker.exists():
        picker_src = picker.read_text(encoding="utf-8")
        check("clients_service.search" in picker_src, "Список ищет по всей базе")
        check("Аренд сейчас" in picker_src,
              "Видно, у кого есть незакрытые аренды")
    check("ClientPickerDialog" in rental and "Выбрать…" in rental,
          "Из аренды можно открыть список физических лиц")

    print()
    if failures:
        print(f"НЕ ПРОЙДЕНО: {len(failures)}")
        for item in failures:
            print(f"  — {item}")
        return 1
    print("Все проверки правок интерфейса пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
