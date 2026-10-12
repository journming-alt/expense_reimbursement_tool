from __future__ import annotations

import io
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from models.invoice import Invoice
from parsers.base import BaseInvoiceParser
from parsers.classifier import classify_invoice
from parsers.normalizer import normalize_invoice
from parsers.pdf_text_parser import _extract_amount_tax_total, _extract_issue_date
from utils.date_utils import parse_date
from utils.money_utils import parse_money


class OFDInvoiceParser(BaseInvoiceParser):
    def parse(self, path: str | Path, category_hint: str = "unknown") -> Invoice:
        path = Path(path)
        fields, raw_text = self._read_xml_fields(path)
        classified = classify_invoice(path, raw_text, category_hint)
        invoice = Invoice(
            file_path=str(path),
            file_name=path.name,
            file_type="ofd",
            category=classified["category"],
            invoice_type=classified["invoice_type"],
            transport_tool=classified["transport_tool"] or "飞机",
            raw_text=raw_text,
            confidence=0.98 if fields else 0.3,
        )

        if _has_air_transport_fields(fields):
            self._parse_air_transport(invoice, fields)
        else:
            ocr_invoice = self._parse_embedded_image_with_ocr(path, category_hint)
            if ocr_invoice:
                return ocr_invoice
            invoice.mark_manual("OFD 未找到可解析的 XML/XBRL 字段，需人工确认")
        return normalize_invoice(invoice)

    def _read_xml_fields(self, path: Path) -> tuple[dict[str, str], str]:
        fields: dict[str, str] = {}
        raw_chunks: list[str] = []
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if not name.lower().endswith((".xml", ".xbrl")):
                    continue
                try:
                    content = zf.read(name).decode("utf-8")
                except UnicodeDecodeError:
                    content = zf.read(name).decode("gb18030", errors="ignore")
                raw_chunks.append(content)
                try:
                    root = ET.fromstring(content)
                except ET.ParseError:
                    continue
                for elem in root.iter():
                    if elem.text and elem.text.strip():
                        tag = _local_name(elem.tag)
                        value = elem.text.strip()
                        fields.setdefault(tag, value)
        raw_text = "\n".join(f"{key}: {value}" for key, value in fields.items())
        if not raw_text:
            raw_text = "\n".join(raw_chunks)
        return fields, raw_text

    def _parse_embedded_image_with_ocr(self, path: Path, category_hint: str) -> Invoice | None:
        image_data = self._render_page_image(path)
        if not image_data:
            return None

        try:
            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                tmp.write(image_data)
                tmp.flush()
                from parsers.ocr_parser import OCRInvoiceParser

                invoice = OCRInvoiceParser().parse(Path(tmp.name), category_hint)
        except Exception:
            return None

        invoice.file_path = str(path)
        invoice.file_name = path.name
        invoice.file_type = "ofd"
        invoice.extra["ofd_ocr_source"] = "rendered_page"
        self._parse_ocr_air_invoice_text(invoice)
        invoice = normalize_invoice(invoice)
        return invoice

    def _render_page_image(self, path: Path) -> bytes | None:
        try:
            from PIL import Image, ImageDraw
        except Exception:
            return None

        with zipfile.ZipFile(path) as zf:
            content_name = next((name for name in zf.namelist() if name.endswith("Pages/Page_0/Content.xml")), None)
            if not content_name:
                return None
            content = _decode_zip_text(zf, content_name)
            resources = _read_resources(zf)
            width, height = _page_size(content)
            scale = 4
            image = Image.new("RGB", (max(1, int(width * scale)), max(1, int(height * scale))), "white")
            draw = ImageDraw.Draw(image)

            try:
                root = ET.fromstring(content)
            except ET.ParseError:
                return None

            for elem in root.iter():
                tag = _local_name(elem.tag)
                if tag == "PathObject":
                    color = resources["colors"].get(elem.attrib.get("DrawParam"), (0, 0, 0))
                    for commands in _split_paths(_text_child(elem, "AbbreviatedData")):
                        points = _path_points(commands, scale)
                        if len(points) >= 2:
                            if elem.attrib.get("Fill", "false").lower() == "true":
                                draw.polygon(points, fill=color)
                            if elem.attrib.get("Stroke", "false").lower() == "true":
                                draw.line(points + [points[0]], fill=color, width=max(1, scale))
                elif tag == "ImageObject":
                    media_path = resources["media"].get(elem.attrib.get("ResourceID", ""))
                    if media_path:
                        _paste_image_object(zf, image, media_path, elem.attrib.get("CTM", ""), scale)

            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()

    def _parse_air_transport(self, invoice: Invoice, fields: dict[str, str]) -> None:
        invoice.category = "transport"
        invoice.invoice_type = "航空运输电子客票行程单"
        invoice.transport_tool = "飞机"

        invoice.invoice_number = fields.get("ElectronicInvoiceAirTransportReceiptNumber")
        invoice.passenger_name = fields.get("PassengerName")
        invoice.traveller_id_masked = fields.get("ValidIdNumber")
        invoice.departure_place = fields.get("DepartureStation")
        invoice.arrival_place = fields.get("DestinationStation")
        invoice.vehicle_no = fields.get("Flight")
        invoice.departure_date = fields.get("CarrierDate")
        invoice.arrival_date = fields.get("CarrierDate")
        invoice.issue_date = fields.get("IssueDate")
        invoice.amount = parse_money(fields.get("Fare"))
        invoice.tax_amount = parse_money(fields.get("VatTaxAmount"))
        invoice.total_amount = parse_money(fields.get("TotalAmount"))
        invoice.buyer_name = fields.get("NameOfPurchaser")
        invoice.buyer_tax_id = fields.get("UnifiedSocialCreditCodeOfPurchaser")
        invoice.seller_name = fields.get("NameOfSeller") or fields.get("IssueParty")
        invoice.extra.update(
            {
                "carrier": fields.get("Carrier"),
                "departure_time": fields.get("DepartureTime"),
                "fuel_surcharge": parse_money(fields.get("FuelSurcharge")),
                "civil_aviation_development_fund": parse_money(fields.get("CivilAviationDevelopmentFund")),
                "e_ticket_number": fields.get("ETicketNumber"),
                "verification_code": fields.get("VerificationCode"),
            }
        )

        if not invoice.invoice_number:
            qr = fields.get("QrCode") or ""
            match = re.search(r"(26\d{18})", qr)
            if match:
                invoice.invoice_number = match.group(1)

    def _parse_ocr_air_invoice_text(self, invoice: Invoice) -> None:
        text = invoice.raw_text or ""
        invoice.category = "transport"
        invoice.transport_tool = invoice.transport_tool or "飞机"
        if invoice.invoice_type == "unknown":
            invoice.invoice_type = "航空客票代理服务发票"

        if not invoice.invoice_number:
            invoice.invoice_number = _invoice_number_from_ocr_text(text)
        if not invoice.issue_date:
            invoice.issue_date = _extract_issue_date(text, invoice.invoice_number)

        amount, tax_amount, total_amount = _extract_amount_tax_total(text)
        invoice.amount = invoice.amount if invoice.amount is not None else amount
        invoice.tax_amount = invoice.tax_amount if invoice.tax_amount is not None else tax_amount
        invoice.total_amount = invoice.total_amount if invoice.total_amount is not None else total_amount

        trip = _air_trip_from_remarks(text)
        if trip:
            invoice.passenger_name = invoice.passenger_name or trip["passenger_name"]
            invoice.departure_date = invoice.departure_date or trip["departure_date"]
            invoice.arrival_date = invoice.arrival_date or trip["departure_date"]
            invoice.departure_place = invoice.departure_place or trip["departure_place"]
            invoice.arrival_place = invoice.arrival_place or trip["arrival_place"]
            invoice.vehicle_no = invoice.vehicle_no or trip["vehicle_no"]
        _remove_resolved_remarks(invoice)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _has_air_transport_fields(fields: dict[str, str]) -> bool:
    return any(
        fields.get(name)
        for name in (
            "ElectronicInvoiceAirTransportReceiptNumber",
            "PassengerName",
            "DepartureStation",
            "DestinationStation",
            "CarrierDate",
            "TotalAmount",
            "QrCode",
        )
    )


def _invoice_number_from_ocr_text(text: str) -> str | None:
    match = re.search(r"发票号码\s*[:：]?\s*([0-9]{8,30})", text)
    if match:
        return match.group(1)
    numbers = re.findall(r"(?<!\d)(\d{20})(?!\d)", text)
    return numbers[0] if numbers else None


def _air_trip_from_remarks(text: str) -> dict[str, str] | None:
    match = re.search(
        r"([\u4e00-\u9fff]{2,8})\s*[（(]\s*\d+\s*[）)]\s*"
        r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s+"
        r"([\u4e00-\u9fff]{2,8})\s*[-—至]\s*([\u4e00-\u9fff]{2,8})\s+"
        r"([A-Z0-9]{2}\d{3,5})",
        text,
    )
    if not match:
        return None
    passenger_name, year, month, day, departure_place, arrival_place, vehicle_no = match.groups()
    return {
        "passenger_name": passenger_name,
        "departure_date": parse_date(f"{year}-{month}-{day}") or "",
        "departure_place": departure_place,
        "arrival_place": arrival_place,
        "vehicle_no": vehicle_no,
    }


def _remove_resolved_remarks(invoice: Invoice) -> None:
    invoice.remarks = [
        remark
        for remark in invoice.remarks
        if not (
            remark.endswith(" 需人工补充")
            and getattr(invoice, remark.removesuffix(" 需人工补充"), None) not in (None, "")
        )
    ]


def _decode_zip_text(zf: zipfile.ZipFile, name: str) -> str:
    try:
        return zf.read(name).decode("utf-8")
    except UnicodeDecodeError:
        return zf.read(name).decode("gb18030", errors="ignore")


def _read_resources(zf: zipfile.ZipFile) -> dict[str, dict]:
    resources: dict[str, dict] = {"colors": {}, "media": {}}
    for name in zf.namelist():
        if not name.endswith("DocumentRes.xml"):
            continue
        try:
            root = ET.fromstring(_decode_zip_text(zf, name))
        except ET.ParseError:
            continue
        for elem in root.iter():
            tag = _local_name(elem.tag)
            if tag == "DrawParam":
                color = _color_from_draw_param(elem)
                if color:
                    resources["colors"][elem.attrib.get("ID", "")] = color
            elif tag == "MultiMedia":
                media_id = elem.attrib.get("ID", "")
                media_file = _text_child(elem, "MediaFile")
                if media_id and media_file:
                    resources["media"][media_id] = media_file.lstrip("/")
    return resources


def _color_from_draw_param(elem: ET.Element) -> tuple[int, int, int] | None:
    for child in elem:
        if _local_name(child.tag) in {"FillColor", "StrokeColor"}:
            value = child.attrib.get("Value", "")
            parts = [int(float(part)) for part in value.split()[:3]]
            if len(parts) == 3:
                return tuple(parts)
    return None


def _page_size(content: str) -> tuple[float, float]:
    match = re.search(r"<[^>]*PhysicalBox[^>]*>\s*([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", content)
    if match:
        _x, _y, width, height = match.groups()
        return float(width), float(height)
    return 600.0, 400.0


def _text_child(elem: ET.Element, child_name: str) -> str:
    for child in elem:
        if _local_name(child.tag) == child_name and child.text:
            return child.text.strip()
    return ""


def _split_paths(data: str) -> list[str]:
    return [part.strip() for part in re.split(r"\bC\b", data or "") if part.strip()]


def _path_points(data: str, scale: int) -> list[tuple[int, int]]:
    tokens = re.findall(r"[MLQ]|-?\d+(?:\.\d+)?", data)
    points: list[tuple[int, int]] = []
    idx = 0
    command = ""
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"M", "L", "Q"}:
            command = token
            idx += 1
            continue
        if command == "Q" and idx + 3 < len(tokens):
            idx += 2
        if idx + 1 < len(tokens) and _is_number(tokens[idx]) and _is_number(tokens[idx + 1]):
            points.append((int(float(tokens[idx]) * scale), int(float(tokens[idx + 1]) * scale)))
            idx += 2
        else:
            idx += 1
    return points


def _is_number(value: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", value))


def _paste_image_object(zf: zipfile.ZipFile, page_image, media_path: str, ctm: str, scale: int) -> None:
    try:
        from PIL import Image

        data = zf.read(media_path)
        embedded = Image.open(io.BytesIO(data)).convert("RGBA")
        values = [float(part) for part in ctm.split()]
        if len(values) == 6:
            width, height, x, y = values[0], values[3], values[4], values[5]
            embedded = embedded.resize((max(1, int(width * scale)), max(1, int(height * scale))))
            page_image.paste(embedded, (int(x * scale), int(y * scale)), embedded)
    except Exception:
        return
