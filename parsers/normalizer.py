from __future__ import annotations

import re

from models.invoice import Invoice, VerificationStatus
from utils.date_utils import parse_date
from utils.money_utils import round_money


def compact_text(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text or "").strip()


def normalize_invoice(invoice: Invoice) -> Invoice:
    for field in ("issue_date", "departure_date", "arrival_date", "checkin_date", "checkout_date"):
        value = getattr(invoice, field)
        parsed = parse_date(value)
        if parsed:
            setattr(invoice, field, parsed)

    for field in ("amount", "tax_amount", "total_amount", "unit_price"):
        value = getattr(invoice, field)
        rounded = round_money(value) if value is not None else None
        setattr(invoice, field, rounded)

    if invoice.total_amount is None and invoice.amount is not None:
        invoice.total_amount = invoice.amount
    if invoice.verification_pdf_path and invoice.verification_status == VerificationStatus.PENDING:
        invoice.verification_status = VerificationStatus.VERIFIED

    transport_required = ["transport_tool", "total_amount"]
    if not invoice.extra.get("no_itinerary"):
        transport_required = ["departure_date", "departure_place", "arrival_place", *transport_required]

    required_by_category = {
        "transport": transport_required,
        "hotel": ["hotel_city", "nights", "rooms", "unit_price", "total_amount"],
        "meal": ["total_amount"],
        "local_transport": ["total_amount"],
    }
    for field in required_by_category.get(invoice.category, []):
        if getattr(invoice, field) in (None, ""):
            invoice.add_remark(f"{field} 需人工补充")
    return invoice
