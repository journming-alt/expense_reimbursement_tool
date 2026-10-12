from __future__ import annotations

import tempfile
import os
from pathlib import Path

from models.invoice import Invoice
from parsers.base import BaseInvoiceParser
from parsers.classifier import classify_invoice
from parsers.normalizer import normalize_invoice

_PADDLE_OCR_CACHE: dict[int, object] = {}


class OCRInvoiceParser(BaseInvoiceParser):
    def parse(self, path: str | Path, category_hint: str = "unknown") -> Invoice:
        path = Path(path)
        text = ""
        confidence = 0.0
        try:
            text, confidence = self._extract_text(path)
        except Exception as exc:
            classified = classify_invoice(path, "", category_hint)
            invoice = Invoice(
                file_path=str(path),
                file_name=path.name,
                file_type=path.suffix.lower().lstrip("."),
                category=classified["category"],
                invoice_type=classified["invoice_type"],
                document_type=classified.get("document_type", "invoice"),
                support_type=classified.get("support_type") or None,
                transport_tool=classified["transport_tool"] or None,
                raw_text="",
                confidence=0.0,
            )
            if classified["invoice_type"] == "机票电子发票":
                invoice.extra["no_itinerary"] = True
            invoice.mark_manual(f"OCR 不可用或识别失败：{exc}")
            return normalize_invoice(invoice)

        if text.strip():
            from parsers.pdf_text_parser import PDFTextInvoiceParser

            invoice = PDFTextInvoiceParser().parse_text(text, path, category_hint)
            invoice.confidence = confidence
            if confidence < 0.75:
                invoice.mark_manual("OCR 置信度低，需人工确认")
            return normalize_invoice(invoice)

        classified = classify_invoice(path, text, category_hint)
        invoice = Invoice(
            file_path=str(path),
            file_name=path.name,
            file_type=path.suffix.lower().lstrip("."),
            category=classified["category"],
            invoice_type=classified["invoice_type"],
            document_type=classified.get("document_type", "invoice"),
            support_type=classified.get("support_type") or None,
            transport_tool=classified["transport_tool"] or None,
            raw_text=text,
            confidence=confidence,
        )
        if confidence < 0.75:
            invoice.mark_manual("OCR 置信度低，需人工确认")
        return normalize_invoice(invoice)

    def _extract_text(self, path: Path) -> tuple[str, float]:
        if path.suffix.lower() == ".pdf":
            with tempfile.NamedTemporaryFile(suffix=".png") as image_file:
                self._render_pdf_first_page(path, Path(image_file.name))
                return self._run_paddleocr(Path(image_file.name))
        return self._run_paddleocr(path)

    def _run_paddleocr(self, path: Path) -> tuple[str, float]:
        os.environ.setdefault(
            "PADDLE_PDX_CACHE_HOME",
            str(Path(__file__).resolve().parents[1] / ".paddlex_cache"),
        )
        from paddleocr import PaddleOCR

        ocr = _get_paddleocr(PaddleOCR)
        if hasattr(ocr, "predict"):
            results = ocr.predict(str(path))
        else:
            results = ocr.ocr(str(path), cls=True)
        chunks, scores = _collect_ocr_text(results)
        text = "\n".join(chunks)
        confidence = sum(scores) / len(scores) if scores else 0.0
        return text, confidence

    def _render_pdf_first_page(self, pdf_path: Path, image_path: Path) -> None:
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(pdf_path))
            page = pdf[0]
            bitmap = page.render(scale=2)
            bitmap.to_pil().save(str(image_path))
            page.close()
            pdf.close()
            return
        except Exception:
            pass

        import fitz

        doc = fitz.open(str(pdf_path))
        page = doc[0]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        pixmap.save(str(image_path))


def _create_paddleocr(paddleocr_class):
    try:
        return paddleocr_class(
            lang="ch",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,
        )
    except (TypeError, ValueError):
        return paddleocr_class(use_angle_cls=True, lang="ch")


def _get_paddleocr(paddleocr_class):
    cache_key = id(paddleocr_class)
    if cache_key not in _PADDLE_OCR_CACHE:
        _PADDLE_OCR_CACHE[cache_key] = _create_paddleocr(paddleocr_class)
    return _PADDLE_OCR_CACHE[cache_key]


def _collect_ocr_text(results) -> tuple[list[str], list[float]]:
    chunks: list[str] = []
    scores: list[float] = []
    _collect_ocr_node(results, chunks, scores)
    return chunks, scores


def _collect_ocr_node(node, chunks: list[str], scores: list[float]) -> None:
    if node is None:
        return
    if isinstance(node, dict):
        texts = node.get("rec_texts") or node.get("texts")
        node_scores = node.get("rec_scores") or node.get("scores")
        if texts:
            for index, text in enumerate(texts):
                chunks.append(str(text))
                if node_scores and index < len(node_scores):
                    scores.append(float(node_scores[index]))
            return
        for value in node.values():
            _collect_ocr_node(value, chunks, scores)
        return
    if hasattr(node, "json"):
        _collect_ocr_node(getattr(node, "json"), chunks, scores)
        return
    if hasattr(node, "res"):
        _collect_ocr_node(getattr(node, "res"), chunks, scores)
        return
    if isinstance(node, (list, tuple)):
        if (
            len(node) >= 2
            and isinstance(node[1], (list, tuple))
            and len(node[1]) >= 2
            and isinstance(node[1][0], str)
        ):
            chunks.append(node[1][0])
            scores.append(float(node[1][1]))
            return
        for item in node:
            _collect_ocr_node(item, chunks, scores)
