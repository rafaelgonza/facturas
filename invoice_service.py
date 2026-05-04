"""Business logic for invoice generation: numbering, dates, file naming."""
import calendar
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from database import Settings, Contract, Invoice, STORAGE_PATH


@dataclass
class InvoicePreview:
    invoice_number: str
    invoice_date: str       # DD/MM/YYYY
    period_start: str       # D/MM/YYYY
    period_end: str         # D/MM/YYYY
    days: int
    daily_rate: float
    total: float
    contract_id: int
    contract_name: str
    seq_in_month: int


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _format_date(d: date) -> str:
    """Format like '30/04/2026' (day not zero-padded)."""
    return f"{d.day}/{d.month:02d}/{d.year}"


def next_seq_in_month(db: Session, year: int, month: int) -> int:
    """Return next sequential invoice number for a given month/year (1-based)."""
    max_seq = (
        db.query(func.max(Invoice.seq_in_month))
        .filter(and_(Invoice.year == year, Invoice.month == month))
        .scalar()
    )
    return (max_seq or 0) + 1


def build_invoice_number(initials: str, year: int, month: int, seq: int) -> str:
    """Build like '26/04/RGM/01'."""
    yy = year % 100
    return f"{yy:02d}/{month:02d}/{initials}/{seq:02d}"


def build_filename(
    settings: Settings, year: int, month: int, seq: int
) -> str:
    """Build PDF filename based on configurable patterns."""
    pattern = (
        settings.filename_pattern_first
        if seq == 1
        else settings.filename_pattern_extra
    )
    name = pattern.format(
        month=month,
        year=year,
        year_short=year % 100,
        seq=seq,
        initials=settings.issuer_initials,
    )
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def make_preview(
    db: Session,
    settings: Settings,
    year: int,
    month: int,
    days: int,
    contract_id: Optional[int] = None,
    invoice_date: Optional[date] = None,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> InvoicePreview:
    """Compute all derived values for an invoice (without persisting)."""
    if contract_id is None:
        contract_id = settings.active_contract_id
    contract = db.query(Contract).get(contract_id)
    if not contract:
        raise ValueError(f"Contrato no encontrado (id={contract_id})")

    seq = next_seq_in_month(db, year, month)
    inv_number = build_invoice_number(settings.issuer_initials, year, month, seq)

    last = _last_day_of_month(year, month)
    inv_date = invoice_date or date(year, month, last)
    p_start = period_start or date(year, month, 1)
    p_end = period_end or date(year, month, last)

    rate = float(contract.daily_rate)
    total = round(rate * days, 2)

    return InvoicePreview(
        invoice_number=inv_number,
        invoice_date=_format_date(inv_date),
        period_start=_format_date(p_start),
        period_end=_format_date(p_end),
        days=days,
        daily_rate=rate,
        total=total,
        contract_id=contract.id,
        contract_name=contract.name,
        seq_in_month=seq,
    )


def build_pdf_context(settings: Settings, contract: Contract, preview: InvoicePreview) -> dict:
    """Build the context dict to feed the PDF template."""
    services_lines = [
        line.strip() for line in contract.services_description.splitlines() if line.strip()
    ]
    return {
        "invoice_number": preview.invoice_number,
        "invoice_date": preview.invoice_date,
        "period_start": preview.period_start,
        "period_end": preview.period_end,
        "days": preview.days,
        "daily_rate": preview.daily_rate,
        "total": preview.total,
        "vat_percentage": settings.vat_percentage,
        "issuer": {
            "name": settings.issuer_name,
            "address_line1": settings.issuer_address_line1,
            "address_line2": settings.issuer_address_line2,
            "phone": settings.issuer_phone,
            "email": settings.issuer_email,
            "vat": settings.issuer_vat,
        },
        "client": {
            "name": settings.client_name,
            "address_line1": settings.client_address_line1,
            "address_line2": settings.client_address_line2,
            "vat": settings.client_vat,
        },
        "bank": {
            "name": settings.bank_name,
            "iban": settings.bank_iban,
            "swift": settings.bank_swift,
            "holder": settings.bank_holder,
        },
        "contract": {
            "name": contract.name,
        },
        "services_lines": services_lines,
        "vat_note": settings.vat_note,
    }


def save_invoice_record(
    db: Session,
    settings: Settings,
    contract: Contract,
    preview: InvoicePreview,
    pdf_filename: str,
    signed: bool,
) -> Invoice:
    """Persist an Invoice row with full snapshot."""
    snapshot = {
        "issuer": {
            "name": settings.issuer_name,
            "address_line1": settings.issuer_address_line1,
            "address_line2": settings.issuer_address_line2,
            "phone": settings.issuer_phone,
            "email": settings.issuer_email,
            "vat": settings.issuer_vat,
        },
        "client": {
            "name": settings.client_name,
            "address_line1": settings.client_address_line1,
            "address_line2": settings.client_address_line2,
            "vat": settings.client_vat,
        },
        "bank": {
            "name": settings.bank_name,
            "iban": settings.bank_iban,
            "swift": settings.bank_swift,
            "holder": settings.bank_holder,
        },
        "contract": {
            "id": contract.id,
            "name": contract.name,
            "daily_rate": contract.daily_rate,
            "services_description": contract.services_description,
        },
        "vat_note": settings.vat_note,
    }
    inv = Invoice(
        invoice_number=preview.invoice_number,
        invoice_date=preview.invoice_date,
        period_start=preview.period_start,
        period_end=preview.period_end,
        month=int(preview.invoice_date.split("/")[1]),
        year=int(preview.invoice_date.split("/")[2]),
        seq_in_month=preview.seq_in_month,
        days=preview.days,
        daily_rate=preview.daily_rate,
        total=preview.total,
        vat_percentage=settings.vat_percentage,
        pdf_filename=pdf_filename,
        signed=signed,
        snapshot=json.dumps(snapshot, ensure_ascii=False),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv
