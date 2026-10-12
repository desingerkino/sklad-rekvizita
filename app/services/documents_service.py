# -*- coding: utf-8 -*-
"""
Документы аренды и пролонгации.

Ключевой принцип: в момент создания документа сохраняется исторический snapshot —
JSON со всеми данными (склад, клиент, паспорт, продакшен, банковские реквизиты,
проект, позиции, суммы). Документ всегда рисуется из snapshot, поэтому изменение
карточки клиента в 2027 году не меняет документ, выписанный в 2026-м.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .. import paths
from ..db.database import get_db
from ..errors import NotFoundError
from ..utils import fmt_date, fmt_dt, now, parse_date, rental_days
from ..version import RENTAL_PERIOD_DAYS
from .clients_service import clients_service
from .crypto_service import crypto_service
from .productions_service import productions_service
from .projects_service import projects_service
from .rentals_service import PAYMENT_LABELS, rentals_service
from .settings_service import settings_service

log = logging.getLogger(__name__)

DOC_TYPES = {"rental": "ДОГОВОР-АКТ АРЕНДЫ РЕКВИЗИТА", "extension": "ПРОЛОНГАЦИЯ АРЕНДЫ"}


class DocumentsService:
    # ------------------------------------------------------------- snapshot
    def build_snapshot(self, rental_id: int, doc_type: str = "rental",
                       extension_id: Optional[int] = None) -> dict:
        rental = rentals_service.get(rental_id)
        if not rental:
            raise NotFoundError("Аренда не найдена.")
        client = clients_service.get(rental["client_id"]) if rental["client_id"] else {}
        production = productions_service.get(rental["production_id"]) if rental["production_id"] else {}
        invoice_prod = (productions_service.get(rental["invoice_production_id"])
                        if rental["invoice_production_id"] else production)
        project = projects_service.get(rental["project_id"]) if rental["project_id"] else {}

        created = now()
        snapshot = {
            "doc_type": doc_type,
            "doc_title": DOC_TYPES.get(doc_type, "ДОКУМЕНТ"),
            "created_at": created.strftime("%Y-%m-%d %H:%M:%S"),
            "created_date_ui": created.strftime("%d.%m.%Y"),
            "created_time_ui": created.strftime("%H:%M"),
            "warehouse": settings_service.warehouse(),
            "rental": {
                "id": rental["id"],
                "number": rental["number"],
                "date_start": rental["date_start"],
                "date_end": rental["date_end"],
                "days": rental_days(parse_date(rental["date_start"]),
                                    parse_date(rental["date_end"])),
                "payment_method": rental["payment_method"],
                "payment_label": PAYMENT_LABELS.get(rental["payment_method"], ""),
                "discount_percent": rental["discount_percent"],
                "discount_amount": rental["discount_amount"],
                "total_before_discount": rental["total_before_discount"],
                "total_final": rental["total_final"],
                "total_valuation": rental["total_valuation"],
                "deposit": rental["deposit"],
                "note": rental["note"],
            },
            "client": dict(client or {}),
            "production": dict(production or {}),
            "invoice_production": dict(invoice_prod or {}),
            "project": dict(project or {}),
            "period_days": RENTAL_PERIOD_DAYS,
            "items": [
                {
                    "n": i + 1,
                    "name": it["name_snap"],
                    "inv_number": it["inv_number_snap"],
                    "description": it["description_snap"],
                    "note": it["note"],
                    "quantity": it["quantity"],
                    "price": it["price"],
                    "amount": it["amount"],
                    "valuation": it["valuation"],
                    # версия 2.0: собственный арендный цикл позиции
                    "date_start": it.get("date_start"),
                    "period_number": it.get("period_number", 1),
                    "period_start": it.get("period_start"),
                    "period_end": it.get("period_end"),
                    "next_prolongation": it.get("next_prolongation"),
                    "prolongations": it.get("prolongations", 0),
                    "returned_qty": it.get("returned_qty", 0),
                    "date_returned": it.get("date_returned"),
                    "status": it.get("status", "rented"),
                    "total_amount": it.get("total_amount", it["amount"]),
                }
                for i, it in enumerate(rental["items"])
            ],
            "charges": rental.get("charges", []),
            "summary": rental.get("summary", {}),
        }
        if extension_id:
            ext = get_db().query_one("SELECT * FROM extensions WHERE id=?", (extension_id,))
            if ext:
                snapshot["extension"] = dict(ext)
        return snapshot

    # ------------------------------------------------------------- создание
    def create_document(self, rental_id: int, doc_type: str = "rental",
                        extension_id: Optional[int] = None) -> int:
        from .auth_service import auth_service
        auth_service.require_login()
        snapshot = self.build_snapshot(rental_id, doc_type, extension_id)
        rental = rentals_service.get(rental_id)
        number = rental["number"] if doc_type == "rental" else f"{rental['number']}-П{extension_id}"

        db = get_db()
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO documents(doc_type, number, rental_id, extension_id) VALUES (?,?,?,?)",
                (doc_type, number, rental_id, extension_id))
            doc_id = int(cur.lastrowid)
            # снимок содержит паспорт и контакты — храним зашифрованным
            conn.execute(
                "INSERT INTO document_snapshots(document_id, payload) VALUES (?,?)",
                (doc_id, crypto_service.encrypt(json.dumps(snapshot, ensure_ascii=False))))
            conn.execute(
                "INSERT INTO history_log(entity, entity_id, action, details) "
                "VALUES ('rental',?,'Создан документ',?)", (rental_id, number))

        # PDF формируем сразу — документ должен существовать как файл
        try:
            pdf_path = self.render_pdf(doc_id)
            with db.transaction() as conn:
                conn.execute("UPDATE documents SET file_path=? WHERE id=?",
                             (str(Path(pdf_path).relative_to(paths.DOCS_DIR)), doc_id))
        except Exception:
            log.exception("Не удалось сформировать PDF для документа %s", doc_id)
        return doc_id

    def get_snapshot(self, document_id: int) -> dict:
        row = get_db().query_one(
            "SELECT payload FROM document_snapshots WHERE document_id=? ORDER BY id LIMIT 1",
            (document_id,))
        if not row:
            raise NotFoundError("Снимок документа не найден.")
        payload = crypto_service.decrypt(row["payload"])
        if not payload:
            raise NotFoundError("Не удалось прочитать снимок документа: он зашифрован "
                                "ключом другой установки программы.")
        return json.loads(payload)

    def get(self, document_id: int) -> Optional[dict]:
        row = get_db().query_one("SELECT * FROM documents WHERE id=?", (document_id,))
        if not row:
            return None
        doc = dict(row)
        doc["abs_path"] = str(paths.DOCS_DIR / doc["file_path"]) if doc["file_path"] else None
        return doc

    def list_for_rental(self, rental_id: int) -> list[dict]:
        rows = get_db().query(
            "SELECT * FROM documents WHERE rental_id=? ORDER BY created_at", (rental_id,))
        out = []
        for r in rows:
            d = dict(r)
            d["abs_path"] = str(paths.DOCS_DIR / d["file_path"]) if d["file_path"] else None
            out.append(d)
        return out

    def list_all(self, limit: int = 500) -> list[dict]:
        rows = get_db().query(
            "SELECT d.*, r.number rental_number, c.full_name client_name, p.name project_name "
            "FROM documents d LEFT JOIN rentals r ON r.id=d.rental_id "
            "LEFT JOIN clients c ON c.id=r.client_id LEFT JOIN projects p ON p.id=r.project_id "
            "ORDER BY d.created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- вывод
    def render_pdf(self, document_id: int, target: Optional[Path] = None) -> str:
        from .pdf_service import build_pdf
        snapshot = self.get_snapshot(document_id)
        doc = self.get(document_id)
        if target is None:
            paths.ensure_dirs()
            safe = doc["number"].replace("/", "-")
            year_dir = paths.DOCS_DIR / str(now().year)
            year_dir.mkdir(parents=True, exist_ok=True)
            target = year_dir / f"{safe}.pdf"
        build_pdf(snapshot, Path(target))
        return str(target)

    def render_html(self, document_id: int) -> str:
        """HTML-версия того же документа — для предпросмотра и печати средствами Qt."""
        from .html_document import build_html
        return build_html(self.get_snapshot(document_id))


documents_service = DocumentsService()
