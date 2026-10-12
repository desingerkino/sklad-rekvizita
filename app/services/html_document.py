# -*- coding: utf-8 -*-
"""HTML-версия документа: предпросмотр и печать через Qt (QTextDocument)."""
from __future__ import annotations

from ..utils import fmt_date, money_plain


def build_html(snapshot: dict) -> str:
    wh = snapshot.get("warehouse", {})
    r = snapshot.get("rental", {})
    c = snapshot.get("client", {})
    p = snapshot.get("production", {})
    pr = snapshot.get("project", {})

    period_days = snapshot.get("period_days", 14)
    rows = []
    for it in snapshot.get("items", []):
        note = f"<br><i>{it['note']}</i>" if it.get("note") else ""
        period = "—"
        if it.get("period_start") and it.get("period_end"):
            period = f"{fmt_date(it['period_start'])}–{fmt_date(it['period_end'])}"
        rows.append(
            f"<tr><td>{it['n']}</td><td>{it.get('name','')}</td><td>{it.get('inv_number','')}</td>"
            f"<td>{it.get('description','')}{note}</td>"
            f"<td>{('выдано ' + fmt_date(it['date_start'])) if it.get('date_start') else '—'}"
            f"<br>{period}</td>"
            f"<td align=right>{it.get('quantity',0)}</td>"
            f"<td align=right>{money_plain(it.get('price',0))}</td>"
            f"<td align=right>{money_plain(it.get('amount',0))}</td>"
            f"<td align=right>{money_plain(it.get('valuation',0))}</td></tr>")

    ext_block = ""
    if snapshot.get("doc_type") == "extension" and snapshot.get("extension"):
        e = snapshot["extension"]
        ext_block = (f"<p>К договору аренды № {r.get('number','')}. Срок продлён с "
                     f"<b>{fmt_date(e.get('old_date_end')) if e.get('old_date_end') else 'бессрочно'}</b> по "
                     f"<b>{fmt_date(e.get('new_date_end'))}</b>.</p>")

    return f"""
<html><body style="font-family:'Segoe UI',Arial;font-size:10pt;color:#111;">
<table width="100%"><tr><td>
  <b style="font-size:13pt">{wh.get('name') or 'СКЛАД РЕКВИЗИТА'}</b><br>
  <span style="font-size:8pt">{wh.get('address','')} · {wh.get('phone','')} · {wh.get('email','')}<br>
  ИНН {wh.get('inn','')} · ОГРН {wh.get('ogrn','')}</span>
</td></tr></table><hr>
<h2 align="center">{snapshot.get('doc_title','')} № {r.get('number','')}</h2>
<p align="center">от {snapshot.get('created_date_ui','')} {snapshot.get('created_time_ui','')}</p>
{ext_block}
<table width="100%"><tr valign="top">
<td width="50%"><b>Проект:</b> {pr.get('name','—')}<br>
<b>Продакшен:</b> {p.get('legal_form','')} {p.get('name','—')}<br>
<b>ИНН/ОГРН:</b> {p.get('inn','—')} / {p.get('ogrn','—')}<br>
<b>Адрес:</b> {p.get('legal_address','—')}</td>
<td width="50%"><b>Физическое лицо:</b> {c.get('full_name','—')}<br>
<b>Паспорт:</b> {c.get('passport','—')}<br>
<b>Телефон:</b> {c.get('phone','—')}<br>
<b>E-mail:</b> {c.get('email','—')}</td>
</tr></table>
<p><b>Срок аренды:</b> с {fmt_date(r.get('date_start'))} по
{fmt_date(r.get('date_end')) if r.get('date_end') else 'срок не определён'} &nbsp;
<b>Оплата:</b> {r.get('payment_label','')}</p>
<table width="100%" border="1" cellspacing="0" cellpadding="4" style="font-size:8.5pt">
<tr bgcolor="#eceff1"><th>№</th><th>Наименование</th><th>Инв. №</th><th>Описание / примечание</th>
<th>Период аренды</th><th>Кол-во</th><th>Цена за {period_days} дн.</th><th>Сумма</th><th>Оценка</th></tr>
{''.join(rows)}
</table>
<table width="100%" style="margin-top:10px"><tr><td align="right">
Начислено всего (все периоды): <b>{money_plain(r.get('total_before_discount',0))} ₽</b><br>
Скидка {money_plain(r.get('discount_percent',0))} %: −{money_plain(r.get('discount_amount',0))} ₽<br>
<span style="font-size:11pt">ИТОГО АРЕНДА: <b>{money_plain(r.get('total_final',0))} ₽</b></span><br>
ЗАЛОГ: <b>{money_plain(r.get('deposit',0))} ₽</b><br>
Оценочная стоимость: {money_plain(r.get('total_valuation',0))} ₽
</td></tr></table>
<p style="font-size:8pt">Цена — стоимость аренды позиции на срок до {period_days} календарных дней.
При досрочном возврате стоимость начатого периода не пересчитывается; если позиция не возвращена
по окончании периода, начисляется следующий период по полной стоимости.</p>
<table width="100%"><tr>
<td>Представитель склада: _____________ / _____________<br><br>Дата: «___» _________ 20__ г.</td>
<td>Клиент: _____________ / _____________<br><br>Дата: «___» _________ 20__ г.</td>
</tr></table>
</body></html>"""
