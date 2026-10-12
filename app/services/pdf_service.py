# -*- coding: utf-8 -*-
"""
Настоящий PDF (ReportLab): шапка с реквизитами склада и логотипом, таблица
позиций с переносом на следующие страницы, блок итогов, подписи, нумерация страниц.
Кириллица — за счёт встроенного шрифта DejaVu (лежит в assets/fonts).
"""
from __future__ import annotations

import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from .. import paths
from ..utils import fmt_date, money_plain
from ..version import DISPLAY_NAME, VERSION

log = logging.getLogger(__name__)

FONT, FONT_BOLD = "Doc", "Doc-Bold"
_fonts_ready = False


def _register_fonts() -> None:
    """Ищет шрифт с кириллицей: сначала встроенный, затем системный Windows."""
    global _fonts_ready
    if _fonts_ready:
        return
    candidates = [
        (paths.assets_dir() / "fonts" / "DejaVuSans.ttf",
         paths.assets_dir() / "fonts" / "DejaVuSans-Bold.ttf"),
        (Path("C:/Windows/Fonts/segoeui.ttf"), Path("C:/Windows/Fonts/segoeuib.ttf")),
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
    ]
    for regular, bold in candidates:
        if regular.exists():
            pdfmetrics.registerFont(TTFont(FONT, str(regular)))
            pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold if bold.exists() else regular)))
            _fonts_ready = True
            return
    raise RuntimeError("Не найден шрифт с поддержкой кириллицы для формирования PDF.")


def _styles() -> dict:
    base = getSampleStyleSheet()["Normal"]
    def st(name, size=9, bold=False, align=TA_LEFT, leading=None, space=0):
        return ParagraphStyle(name, parent=base, fontName=FONT_BOLD if bold else FONT,
                              fontSize=size, leading=leading or size + 2.5,
                              alignment=align, spaceAfter=space)
    return {
        "title": st("title", 14, True, TA_CENTER, space=4),
        "sub": st("sub", 9, False, TA_CENTER, space=6),
        "h": st("h", 10, True, space=3),
        "p": st("p", 9),
        "small": st("small", 7.5),
        "cell": st("cell", 8),
        "cell_b": st("cell_b", 8, True),
        "cell_r": st("cell_r", 8, align=TA_RIGHT),
        "wh": st("wh", 8.5),
        "wh_b": st("wh_b", 11, True),
    }


def _page_decor(canvas, doc):
    canvas.saveState()
    canvas.setFont(FONT, 7)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(15 * mm, 10 * mm, f"{DISPLAY_NAME} · версия {VERSION}")
    canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Страница {doc.page}")
    canvas.restoreState()


def build_pdf(snapshot: dict, target: Path) -> Path:
    _register_fonts()
    s = _styles()
    target.parent.mkdir(parents=True, exist_ok=True)

    wh = snapshot.get("warehouse", {})
    rental = snapshot.get("rental", {})
    client = snapshot.get("client", {})
    prod = snapshot.get("production", {})
    invoice = snapshot.get("invoice_production", {}) or prod
    project = snapshot.get("project", {})

    doc = SimpleDocTemplate(
        str(target), pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=13 * mm, bottomMargin=16 * mm,
        title=f"{snapshot.get('doc_title','Документ')} {rental.get('number','')}",
        author=wh.get("name", "Склад реквизита"))
    flow: list = []

    # ---------------------------------------------------------------- шапка
    wh_lines = [f"<b>{wh.get('name') or 'СКЛАД РЕКВИЗИТА'}</b>"]
    for label, key in (("Адрес: ", "address"), ("Тел.: ", "phone"), ("E-mail: ", "email"),
                       ("ИНН: ", "inn"), ("ОГРН: ", "ogrn")):
        if wh.get(key):
            wh_lines.append(f"{label}{wh[key]}")
    bank = ", ".join(filter(None, [wh.get("bank_name"), f"р/с {wh['bank_account']}" if wh.get("bank_account") else "",
                                   f"БИК {wh['bank_bik']}" if wh.get("bank_bik") else "",
                                   f"к/с {wh['bank_corr']}" if wh.get("bank_corr") else ""]))
    if bank:
        wh_lines.append(bank)
    header_text = Paragraph("<br/>".join(wh_lines), s["wh"])

    logo_path = wh.get("logo_path") or ""
    if logo_path and Path(logo_path).exists():
        try:
            logo = Image(logo_path, width=28 * mm, height=28 * mm, kind="proportional")
            header = Table([[logo, header_text]], colWidths=[30 * mm, None])
        except Exception:
            header = Table([[header_text]], colWidths=[None])
    else:
        header = Table([[header_text]], colWidths=[None])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.8, colors.HexColor("#333333")),
    ]))
    flow += [header, Spacer(1, 6 * mm)]

    # -------------------------------------------------------------- титул
    flow.append(Paragraph(f"{snapshot.get('doc_title')} № {rental.get('number','')}", s["title"]))
    flow.append(Paragraph(
        f"от {snapshot.get('created_date_ui','')} {snapshot.get('created_time_ui','')}", s["sub"]))

    if snapshot.get("doc_type") == "extension" and snapshot.get("extension"):
        ext = snapshot["extension"]
        flow.append(Paragraph(
            f"К договору аренды № {rental.get('number','')}. Срок аренды продлён с "
            f"<b>{fmt_date(ext.get('old_date_end')) if ext.get('old_date_end') else 'бессрочно'}</b> "
            f"по <b>{fmt_date(ext.get('new_date_end'))}</b>.", s["p"]))
        flow.append(Spacer(1, 4 * mm))

    # -------------------------------------------------- стороны и параметры
    def kv(rows: list[tuple[str, str]]) -> Table:
        data = [[Paragraph(k, s["cell_b"]), Paragraph(v or "—", s["cell"])] for k, v in rows]
        t = Table(data, colWidths=[38 * mm, None])
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ]))
        return t

    left = kv([
        ("Проект:", project.get("name", "")),
        ("Продакшен:", " ".join(filter(None, [prod.get("legal_form", ""), prod.get("name", "")]))),
        ("ИНН / ОГРН:", " / ".join(filter(None, [prod.get("inn", ""), prod.get("ogrn", "")]))),
        ("Адрес:", prod.get("legal_address") or prod.get("actual_address", "")),
        ("Контакт:", " ".join(filter(None, [prod.get("contact_person", ""), prod.get("phone", "")]))),
    ])
    right = kv([
        ("Получатель (физ. лицо):", client.get("full_name", "")),
        ("Паспорт:", client.get("passport", "")),
        ("Телефон:", client.get("phone", "")),
        ("E-mail:", client.get("email", "")),
        ("Адрес:", client.get("address", "")),
    ])
    parties = Table([[left, right]], colWidths=[doc.width / 2 - 3 * mm, doc.width / 2 - 3 * mm])
    parties.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    flow += [parties, Spacer(1, 3 * mm)]

    period = (f"с {fmt_date(rental.get('date_start'))} по "
              f"{fmt_date(rental.get('date_end')) if rental.get('date_end') else 'срок не определён'}")
    if rental.get("days"):
        period += f" ({rental['days']} дн.)"
    flow += [kv([("Срок аренды:", period),
                 ("Способ оплаты:", rental.get("payment_label", "")),
                 ("Счёт выставляется:", invoice.get("name", "") if rental.get("payment_method") == "bank" else "—")]),
             Spacer(1, 4 * mm)]

    # ------------------------------------------------------ таблица позиций
    period_days = snapshot.get("period_days", 14)
    # Ширины подобраны так, чтобы описание и примечание не сжимались в столбик
    # из двух букв: полезная ширина A4 при полях 15 мм — 180 мм.
    head = ["№", "Наименование", "Инв. №", "Описание / примечание", "Период аренды",
            "Кол-во", f"Цена за {period_days} дн., ₽", "Сумма, ₽", "Оценка, ₽"]
    data = [[Paragraph(h, s["cell_b"]) for h in head]]
    for it in snapshot.get("items", []):
        desc = it.get("description", "")
        if it.get("note"):
            desc = (desc + "<br/>" if desc else "") + f"<i>{it['note']}</i>"
        period = "—"
        if it.get("date_start"):
            period = f"выдано {fmt_date(it['date_start'])}"
        if it.get("period_start") and it.get("period_end"):
            # даты периода — на отдельных строках, чтобы дата не рвалась посередине
            period += f"<br/>{fmt_date(it['period_start'])} —<br/>{fmt_date(it['period_end'])}"
            if it.get("prolongations"):
                period += f"<br/>период №{it.get('period_number', 1)}"
        if it.get("date_returned"):
            period += f"<br/>возвращено {fmt_date(it['date_returned'])}"
        data.append([
            Paragraph(str(it["n"]), s["cell"]),
            Paragraph(it.get("name", ""), s["cell"]),
            Paragraph(it.get("inv_number", ""), s["cell"]),
            Paragraph(desc, s["cell"]),
            Paragraph(period, s["cell"]),
            Paragraph(str(it.get("quantity", 0)), s["cell_r"]),
            Paragraph(money_plain(it.get("price", 0)), s["cell_r"]),
            Paragraph(money_plain(it.get("amount", 0)), s["cell_r"]),
            Paragraph(money_plain(it.get("valuation", 0)), s["cell_r"]),
        ])
    table = Table(data, colWidths=[7 * mm, 31 * mm, 16 * mm, None, 24 * mm,
                                   10 * mm, 19 * mm, 19 * mm, 20 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9e9e9e")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eceff1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (5, 1), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f7")]),
    ]))
    flow += [table, Spacer(1, 4 * mm)]

    # ------------------------------------------------------------- итоги
    totals_rows = [
        ("Начислено всего (все периоды):", money_plain(rental.get("total_before_discount", 0)) + " ₽"),
        (f"Скидка {money_plain(rental.get('discount_percent', 0))} %:",
         "− " + money_plain(rental.get("discount_amount", 0)) + " ₽"),
        ("ИТОГО АРЕНДА:", money_plain(rental.get("total_final", 0)) + " ₽"),
        ("ЗАЛОГ:", money_plain(rental.get("deposit", 0)) + " ₽"),
        ("Способ оплаты:", rental.get("payment_label", "")),
        ("Общая оценочная стоимость реквизита:", money_plain(rental.get("total_valuation", 0)) + " ₽"),
    ]
    trows = []
    for i, (k, v) in enumerate(totals_rows):
        bold = k.startswith("ИТОГО") or k.startswith("ЗАЛОГ")
        trows.append([Paragraph(k, s["cell_b"] if bold else s["cell"]),
                      Paragraph(v, s["cell_b"] if bold else s["cell_r"])])
    totals = Table(trows, colWidths=[70 * mm, 35 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.HexColor("#333333")),
        ("LINEABOVE", (0, 3), (-1, 3), 0.4, colors.HexColor("#999999")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    flow.append(totals)

    charges = snapshot.get("charges", [])
    if len([c for c in charges if c.get("type") != "initial"]) > 0:
        flow += [Spacer(1, 5 * mm), Paragraph("История начислений", s["h"])]
        rows = [[Paragraph(h, s["cell_b"]) for h in ("Дата", "Начисление", "Позиций", "Сумма, ₽")]]
        for charge in charges:
            label = charge.get("type_label", "")
            if charge.get("type") == "prolongation":
                label += f" (период №{charge.get('period_number')})"
            rows.append([
                Paragraph(fmt_date(charge.get("date")), s["cell"]),
                Paragraph(label, s["cell"]),
                Paragraph(str(charge.get("quantity", 0)), s["cell_r"]),
                Paragraph(money_plain(charge.get("amount", 0)), s["cell_r"]),
            ])
        charges_table = Table(rows, colWidths=[24 * mm, None, 20 * mm, 28 * mm], repeatRows=1)
        charges_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9e9e9e")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eceff1")),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        flow.append(charges_table)

    if rental.get("note"):
        flow += [Spacer(1, 3 * mm), Paragraph(f"Примечание к аренде: {rental['note']}", s["p"])]

    # ------------------------------------------------------------ подписи
    sign_note = Paragraph(
        f"Указанная цена — стоимость аренды позиции на срок до {period_days} календарных дней. "
        f"При досрочном возврате стоимость начатого периода не пересчитывается. Если позиция не "
        f"возвращена по окончании периода, начисляется следующий {period_days}-дневный период по "
        "полной стоимости. Реквизит получен в исправном состоянии, претензий к комплектности "
        "и внешнему виду нет. Клиент несёт материальную ответственность в размере оценочной "
        "стоимости реквизита.", s["small"])
    signs = Table([[
        Paragraph("Представитель склада: ______________________ / _____________________<br/><br/>"
                  "Дата: «____» ______________ 20___ г.", s["p"]),
        Paragraph("Клиент: ______________________ / _____________________<br/><br/>"
                  "Дата: «____» ______________ 20___ г.", s["p"]),
    ]], colWidths=[doc.width / 2 - 3 * mm, doc.width / 2 - 3 * mm])
    signs.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 8)]))
    flow += [Spacer(1, 6 * mm), KeepTogether([sign_note, signs])]

    doc.build(flow, onFirstPage=_page_decor, onLaterPages=_page_decor)
    log.info("PDF сформирован: %s", target)
    return target
