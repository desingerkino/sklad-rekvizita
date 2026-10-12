# -*- coding: utf-8 -*-
"""
Единая система статусов версии 2.3.1.

Цвет — вспомогательный признак, текст обязателен всегда:

* 🟢 зелёный — «В наличии»            — реквизит на складе, доступен;
* 🟠 оранжевый — «В аренде»           — сейчас у клиента;
* 🔴 красный — «Пролонгация»          — период продлён или требует продления;
* ⚪ серый — «Открыт набор / аренда»   — аренда формируется;
* ⚫ чёрный — «Аренда завершена»       — аренда закрыта.

Состояния берутся из существующей логики: остатки, позиции аренды, пролонгации.
"""
from __future__ import annotations

IN_STOCK = "in_stock"
RENTED = "rented"
PROLONGED = "prolonged"
OPEN_SET = "open_set"
FINISHED = "finished"
REPAIR = "repair"

LABELS = {
    IN_STOCK: "В наличии",
    RENTED: "В аренде",
    PROLONGED: "Пролонгация",
    OPEN_SET: "Открыт набор / аренда",
    FINISHED: "Аренда завершена",
    REPAIR: "В ремонте",
}

ORDER = (IN_STOCK, RENTED, PROLONGED, OPEN_SET, FINISHED, REPAIR)


def label(code: str) -> str:
    return LABELS.get(code, code)


def item_status(item: dict) -> tuple[str, str]:
    """Статус позиции каталога: свободна, частично выдана, вся в аренде, в ремонте."""
    if item.get("status") == "repair" or item.get("in_repair", 0) >= item.get("quantity_total", 0) > 0:
        return REPAIR, LABELS[REPAIR]
    available = item.get("available_now", 0)
    total = item.get("quantity_total", 0)
    if total and available <= 0:
        return RENTED, LABELS[RENTED]
    if available < total:
        return RENTED, f"{LABELS[RENTED]} · свободно {available} из {total}"
    return IN_STOCK, LABELS[IN_STOCK]


def rental_status(rental: dict, prolonged: bool = False) -> tuple[str, str]:
    """Статус аренды целиком."""
    if rental.get("status") in ("closed", "cancelled"):
        return FINISHED, LABELS[FINISHED]
    if prolonged or rental.get("prolonged"):
        return PROLONGED, LABELS[PROLONGED]
    if rental.get("status") == "draft":
        return OPEN_SET, LABELS[OPEN_SET]
    return RENTED, LABELS[RENTED]


def rental_item_status(item: dict) -> tuple[str, str]:
    """Статус позиции внутри аренды."""
    if item.get("status") == "returned":
        return FINISHED, "Возвращено"
    if item.get("prolongations", 0) > 0:
        return PROLONGED, LABELS[PROLONGED]
    return RENTED, LABELS[RENTED]
