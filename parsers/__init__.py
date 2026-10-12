from __future__ import annotations

from pathlib import Path

from models.invoice import Invoice

from .ocr_parser import OCRInvoiceParser
from .ofd_parser import OFDInvoiceParser
from .pdf_text_parser import PDFTextInvoiceParser


def parse_invoice_file(path: str | Path, category_hint: str = "unknown") -> Invoice:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".ofd":
        return OFDInvoiceParser().parse(path, category_hint)
    if suffix == ".pdf":
        invoice = PDFTextInvoiceParser().parse(path, category_hint)
        if invoice.raw_text.strip():
            return invoice
    return OCRInvoiceParser().parse(path, category_hint)


__all__ = ["parse_invoice_file", "PDFTextInvoiceParser", "OFDInvoiceParser", "OCRInvoiceParser"]
