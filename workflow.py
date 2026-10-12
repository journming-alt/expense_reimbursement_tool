from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable

from excel.excel_writer import ExcelWriter
from models.invoice import Invoice, VerificationStatus
from models.trip import TripContext
from parsers import parse_invoice_file
from parsers.pdf_text_parser import PDFTextInvoiceParser
from utils.file_utils import (
    collect_invoice_files,
    export_reimbursement_package,
    find_template_excel,
    find_verification_pdfs,
    find_trip_roots,
    save_recognition_csv,
    save_recognition_json,
    safe_extract_zip,
    write_processing_log,
)


def import_trip_source(source: str | Path, work_dir: str | Path) -> Path:
    source = Path(source)
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".zip":
        extracted = safe_extract_zip(source, work_dir / source.stem)
        roots = find_trip_roots(extracted)
        return roots[0] if roots else extracted
    roots = find_trip_roots(source)
    return roots[0] if roots else source


def scan_trip(
    trip_root: str | Path,
    examiner: str = "",
    site: str = "",
    fallback_template: str | Path | None = None,
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> TripContext:
    trip_root = Path(trip_root)
    invoices: list[Invoice] = []
    invoice_files = collect_invoice_files(trip_root)
    total_files = len(invoice_files)
    for index, (path, category_hint) in enumerate(invoice_files, start=1):
        if progress_callback:
            progress_callback(index, total_files, path)
        invoice = parse_invoice_file(path, category_hint)
        try:
            invoice.relative_path = str(path.relative_to(trip_root))
        except ValueError:
            invoice.relative_path = path.name
        invoices.append(invoice)

    _apply_support_documents(invoices)

    verification_map = parse_existing_verification_pdfs(trip_root)
    for invoice in invoices:
        if invoice.document_type == "support_doc":
            invoice.verification_status = VerificationStatus.VERIFIED
            continue
        if invoice.invoice_number and invoice.invoice_number in verification_map:
            invoice.verification_status = VerificationStatus.VERIFIED
            invoice.verification_pdf_path = str(verification_map[invoice.invoice_number])
        elif invoice.invoice_number:
            invoice.verification_status = VerificationStatus.PENDING
        else:
            invoice.mark_manual("缺少发票号码，无法自动查验")

    template = find_template_excel(trip_root)
    if template is None and fallback_template:
        template = Path(fallback_template)

    inferred_site = site or trip_root.name
    inferred_examiner = examiner or infer_examiner(invoices)
    return TripContext(
        examiner=inferred_examiner,
        site=inferred_site,
        root_path=str(trip_root),
        template_path=str(template) if template else None,
        invoices=invoices,
    )


def parse_existing_verification_pdfs(trip_root: str | Path) -> dict[str, Path]:
    parser = PDFTextInvoiceParser()
    mapping: dict[str, Path] = {}
    for pdf in find_verification_pdfs(trip_root):
        invoice_number, _text = parser.parse_verification_pdf(pdf)
        if invoice_number:
            mapping[invoice_number] = pdf
    return mapping


def infer_examiner(invoices: list[Invoice]) -> str:
    names = [invoice.display_name for invoice in invoices if invoice.display_name]
    return Counter(names).most_common(1)[0][0] if names else ""


def _apply_support_documents(invoices: list[Invoice]) -> None:
    taxi_docs = [i for i in invoices if i.document_type == "support_doc" and i.support_type == "taxi_trip_detail"]
    taxi_invoices = [i for i in invoices if i.document_type != "support_doc" and i.category == "local_transport"]
    for doc in taxi_docs:
        target = _match_taxi_invoice(doc, taxi_invoices)
        if not target:
            doc.add_remark("未自动匹配到对应市内交通发票")
            continue
        if doc.departure_place and not target.departure_place:
            target.departure_place = doc.departure_place
        if doc.arrival_place and not target.arrival_place:
            target.arrival_place = doc.arrival_place
        if doc.departure_date and not target.departure_date:
            target.departure_date = doc.departure_date
            target.arrival_date = doc.departure_date
        target.extra["taxi_trip_detail_file"] = doc.file_name
        target.extra["taxi_trip_start"] = doc.departure_place
        target.extra["taxi_trip_end"] = doc.arrival_place
        target.add_remark(f"已关联行程单：{doc.file_name}")
        doc.add_remark(f"已关联发票：{target.file_name}")


def _match_taxi_invoice(doc: Invoice, candidates: list[Invoice]) -> Invoice | None:
    scored: list[tuple[int, Invoice]] = []
    for invoice in candidates:
        score = 0
        if doc.total_amount is not None and invoice.total_amount is not None and abs(doc.total_amount - invoice.total_amount) < 0.01:
            score += 10
        if doc.departure_date and invoice.issue_date == doc.departure_date:
            score += 2
        if doc.departure_date and invoice.departure_date == doc.departure_date:
            score += 3
        if doc.seller_name and invoice.seller_name and doc.seller_name in invoice.seller_name:
            score += 1
        if score:
            scored.append((score, invoice))
    if not scored:
        return None
    return sorted(scored, key=lambda item: item[0], reverse=True)[0][1]


def generate_excel(trip: TripContext, output_dir: str | Path | None = None) -> Path:
    output_dir = Path(output_dir) if output_dir else trip.root
    output_dir.mkdir(parents=True, exist_ok=True)
    if not trip.template_path:
        template_path = Path(__file__).parent / "data" / "templates" / "default_差旅报销明细表.xlsx"
        if not template_path.exists():
            raise FileNotFoundError("未找到差旅报销明细表模板，请先把正式报销表放到 data/templates/default_差旅报销明细表.xlsx")
        trip.template_path = str(template_path)
    output_path = output_dir / f"{trip.site}差旅报销明细表_自动生成.xlsx"
    writer = ExcelWriter(trip.template_path)
    result = writer.write(trip, output_path)
    trip.generated_excel_path = str(result)
    trip.output_dir = str(output_dir)
    return result


def generate_outputs(trip: TripContext, output_dir: str | Path | None = None) -> TripContext:
    output_dir = Path(output_dir) if output_dir else trip.root
    output_dir.mkdir(parents=True, exist_ok=True)
    generate_excel(trip, output_dir)
    save_recognition_json(trip.invoices, output_dir / "识别明细.json")
    save_recognition_csv(trip.invoices, output_dir / "发票识别明细.csv")
    write_processing_log(trip, output_dir / "处理日志.txt")
    trip.exported_zip_path = str(export_reimbursement_package(trip, output_dir))
    trip.output_dir = str(output_dir)
    return trip


def table_rows_to_invoices(trip: TripContext, rows: list[dict]) -> TripContext:
    by_file = {invoice.file_name: invoice for invoice in trip.invoices}
    selected: list[Invoice] = []
    for row in rows:
        if not row.get("是否选择", True):
            continue
        invoice = by_file.get(str(row.get("文件名", "")))
        if not invoice:
            continue
        _apply_row(invoice, row)
        selected.append(invoice)
    trip.invoices = selected
    if not trip.examiner:
        trip.examiner = infer_examiner(trip.invoices)
    return trip


def _apply_row(invoice: Invoice, row: dict) -> None:
    invoice.passenger_name = str(row.get("姓名") or invoice.passenger_name or "") or None
    invoice.hotel_guest = invoice.hotel_guest or invoice.passenger_name
    invoice.departure_date = str(row.get("出发日期") or invoice.departure_date or "") or None
    invoice.departure_place = str(row.get("出发地") or invoice.departure_place or "") or None
    invoice.arrival_date = str(row.get("到达日期") or invoice.arrival_date or "") or None
    invoice.arrival_place = str(row.get("到达地") or invoice.arrival_place or "") or None
    invoice.transport_tool = str(row.get("交通工具") or invoice.transport_tool or "") or None
    invoice.hotel_city = str(row.get("住宿地点") or invoice.hotel_city or "") or None
    invoice.total_amount = _to_float(row.get("金额"), invoice.total_amount)
    invoice.nights = _to_int(row.get("天数"), invoice.nights)
    invoice.rooms = _to_int(row.get("间数"), invoice.rooms)
    if invoice.category == "hotel" and invoice.total_amount and invoice.nights and invoice.rooms:
        invoice.unit_price = round(invoice.total_amount / invoice.nights / invoice.rooms, 2)


def _to_float(value: object, default: float | None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: object, default: int | None) -> int | None:
    if value in (None, ""):
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
