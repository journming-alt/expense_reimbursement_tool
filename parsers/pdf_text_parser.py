from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from models.invoice import Invoice
from parsers.base import BaseInvoiceParser
from parsers.classifier import classify_invoice
from parsers.normalizer import compact_text, normalize_invoice
from utils.date_utils import day_diff, parse_date
from utils.money_utils import parse_money, round_money


class PDFTextInvoiceParser(BaseInvoiceParser):
    def parse(self, path: str | Path, category_hint: str = "unknown") -> Invoice:
        path = Path(path)
        text = self.extract_text(path)
        return self.parse_text(text, path, category_hint)

    def parse_text(self, text: str, path: str | Path, category_hint: str = "unknown") -> Invoice:
        path = Path(path)
        parse_text = _normalize_extracted_invoice_text(text)
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
            confidence=0.92 if text else 0.2,
        )
        self._parse_common(invoice, parse_text)

        if invoice.support_type == "taxi_trip_detail":
            self._parse_taxi_trip_detail(invoice, parse_text)
        elif invoice.invoice_type == "铁路电子客票":
            self._parse_railway(invoice, parse_text)
        elif invoice.invoice_type == "航空运输电子客票行程单":
            self._parse_air_transport(invoice, parse_text)
        elif invoice.invoice_type == "机票电子发票":
            self._parse_air_receipt(invoice, parse_text)
        elif invoice.category == "hotel" or "住宿" in parse_text:
            self._parse_hotel(invoice, parse_text)
        elif invoice.category in {"meal", "local_transport"}:
            self._parse_amount_only(invoice, parse_text)
        else:
            self._parse_amount_only(invoice, parse_text)

        if not text.strip():
            invoice.mark_manual("PDF 未提取到文本，建议使用 OCR 兜底识别")
        return normalize_invoice(invoice)

    @staticmethod
    def extract_text(path: str | Path) -> str:
        path = Path(path)
        text = ""
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            text = ""

        if len(text.strip()) < 20:
            try:
                import pdfplumber

                with pdfplumber.open(str(path)) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            except Exception:
                pass

        if len(text.strip()) < 20:
            try:
                import fitz

                doc = fitz.open(str(path))
                text = "\n".join(page.get_text() for page in doc)
            except Exception:
                pass
        return text or ""

    def parse_verification_pdf(self, path: str | Path) -> tuple[str | None, str]:
        text = self.extract_text(path)
        number = self._invoice_number_from_text(text)
        return number, text

    def _parse_common(self, invoice: Invoice, text: str) -> None:
        invoice.invoice_number = self._invoice_number_from_text(text)
        invoice.issue_date = _extract_issue_date(text, invoice.invoice_number)

        if "中国航空运输协会" in text or "中 国 航 空" in text:
            invoice.buyer_name = "中国航空运输协会"
        tax_ids = re.findall(r"(?<![A-Z0-9])([0-9A-Z]{18})(?![A-Z0-9])", text)
        if tax_ids:
            invoice.buyer_tax_id = "51100000500019891R" if "51100000500019891R" in tax_ids else tax_ids[0]
            sellers = [tax for tax in tax_ids if tax != invoice.buyer_tax_id]
            if sellers:
                invoice.seller_tax_id = sellers[0]

    def _parse_railway(self, invoice: Invoice, text: str) -> None:
        invoice.category = "transport"
        invoice.invoice_type = "铁路电子客票"
        invoice.total_amount = _extract_railway_price(text)
        invoice.amount = invoice.total_amount

        departure_time = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*\d{1,2}:\d{2}\s*开", text)
        all_dates = re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
        if departure_time:
            invoice.departure_date = _ymd(*departure_time.groups())
        elif all_dates:
            invoice.departure_date = _ymd(*all_dates[0])
            invoice.arrival_date = invoice.departure_date
        if invoice.departure_date:
            invoice.arrival_date = invoice.departure_date

        invoice.vehicle_no = _first_match(text, r"\b([DGCKZT]\d{1,5})\b")
        if invoice.vehicle_no and invoice.vehicle_no[0] in {"G", "D", "C"}:
            invoice.transport_tool = "高铁"
        elif invoice.vehicle_no:
            invoice.transport_tool = "火车"

        name = _first_match(text, r"(?:\d|X|x|\*){8,}\s*([\u4e00-\u9fff]{2,6})")
        if not name:
            name = _first_match(text, r"姓名\s*([\u4e00-\u9fff]{2,6})")
        invoice.passenger_name = name

        station_pair = re.search(r"出发站\s*([\u4e00-\u9fff]{2,8})\s*到达站\s*([\u4e00-\u9fff]{2,8})", compact_text(text))
        if station_pair:
            invoice.departure_place, invoice.arrival_place = station_pair.groups()
        else:
            header_pair = re.search(
                r"([\u4e00-\u9fff]{2,8})\s+[DGCKZT]\d{1,5}\s+([\u4e00-\u9fff]{2,8})\s*\n\s*站\s+站",
                text,
            )
            if header_pair:
                invoice.departure_place, invoice.arrival_place = header_pair.groups()
            else:
                station_matches = re.findall(r"([\u4e00-\u9fff]{2,8})\s*站\s*([\u4e00-\u9fff]{2,8})\s*站", text)
                if station_matches:
                    first, second = station_matches[-1]
                    invoice.departure_place = second
                    invoice.arrival_place = first

        if not invoice.departure_place or not invoice.arrival_place:
            invoice.add_remark("铁路出发/抵达站需人工核对")

    def _parse_air_transport(self, invoice: Invoice, text: str) -> None:
        invoice.category = "transport"
        invoice.invoice_type = "航空运输电子客票行程单"
        invoice.transport_tool = "飞机"

        invoice.passenger_name = _extract_air_passenger(text)
        invoice.vehicle_no = _extract_air_flight_no(text)
        invoice.issue_date = _extract_air_issue_date(text) or invoice.issue_date
        invoice.departure_date = _extract_air_departure_date(text, invoice.vehicle_no)
        invoice.arrival_date = invoice.departure_date

        places = _extract_air_places(text)
        if not places:
            places = _extract_air_places_from_filename(Path(invoice.file_path).name)
        if places:
            invoice.departure_place, invoice.arrival_place = places

        invoice.amount, invoice.tax_amount, invoice.total_amount = _extract_air_amounts(text)

    def _parse_air_receipt(self, invoice: Invoice, text: str) -> None:
        invoice.category = "transport"
        invoice.invoice_type = "机票电子发票"
        invoice.transport_tool = "飞机"
        invoice.extra["no_itinerary"] = True
        invoice.amount, invoice.tax_amount, invoice.total_amount = _extract_amount_tax_total(text)

    def _parse_hotel(self, invoice: Invoice, text: str) -> None:
        invoice.category = "hotel"
        invoice.invoice_type = "住宿电子发票"

        invoice.amount, invoice.tax_amount, invoice.total_amount = _extract_amount_tax_total(text)

        guest = _extract_hotel_guest(text)
        invoice.hotel_guest = guest
        invoice.passenger_name = guest

        default_year = int((invoice.issue_date or "")[:4]) if invoice.issue_date else None
        checkin, checkout, nights = _extract_stay_dates(text, default_year=default_year)
        invoice.checkin_date = checkin
        invoice.checkout_date = checkout
        if nights:
            invoice.nights = nights

        if invoice.nights is None:
            explicit_nights = _first_match(text, r"(\d+)\s*晚")
            invoice.nights = int(explicit_nights) if explicit_nights else None

        invoice.rooms = 1
        if invoice.total_amount is not None and invoice.nights:
            invoice.unit_price = round_money(invoice.total_amount / invoice.nights / invoice.rooms)

        seller = self._seller_name_from_hotel_text(text, invoice)
        invoice.seller_name = seller
        city = _hotel_city_from_seller(seller)
        invoice.hotel_city = city or _first_match(text, r"(长春|沈阳|成都|杭州|南昌|吉林|太原|武汉|北京|上海|广州|深圳|青岛|南京)")

    def _parse_amount_only(self, invoice: Invoice, text: str) -> None:
        amount, tax_amount, total_amount = _extract_amount_tax_total(text)
        invoice.amount = amount
        invoice.tax_amount = tax_amount
        invoice.total_amount = total_amount

    def _parse_taxi_trip_detail(self, invoice: Invoice, text: str) -> None:
        invoice.document_type = "support_doc"
        invoice.support_type = "taxi_trip_detail"
        invoice.category = "local_transport"
        invoice.invoice_type = "打车行程辅助资料"
        invoice.transport_tool = "网约车"
        invoice.total_amount = parse_money(_first_match(text, r"合计\s*([0-9]+(?:\.[0-9]+)?)\s*元"))
        invoice.amount = invoice.total_amount

        trip_time = re.search(
            r"行程时间\s*[:：]?\s*(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s+(\d{1,2}:\d{2})",
            text,
        )
        if trip_time:
            invoice.departure_date = _ymd(*trip_time.groups()[:3])
            invoice.arrival_date = invoice.departure_date

        row = _extract_taxi_row_by_words(Path(invoice.file_path), text)
        if row:
            invoice.seller_name = row.get("provider") or invoice.seller_name
            invoice.departure_date = row.get("date") or invoice.departure_date
            invoice.arrival_date = invoice.departure_date
            invoice.departure_place = row.get("start") or invoice.departure_place
            invoice.arrival_place = row.get("end") or invoice.arrival_place
            invoice.total_amount = row.get("amount") or invoice.total_amount
            invoice.amount = invoice.total_amount
            invoice.extra.update(row)

        if invoice.departure_place and invoice.arrival_place:
            invoice.add_remark(f"行程单：{invoice.departure_place} -> {invoice.arrival_place}")

    def _invoice_number_from_text(self, text: str) -> str | None:
        match = re.search(r"发票号码\s*[:：]?\s*([0-9]{8,30})", compact_text(text))
        if match:
            return match.group(1)
        long_numbers = re.findall(r"(?<!\d)(\d{20})(?!\d)", text)
        return long_numbers[0] if long_numbers else None

    def _seller_name_from_hotel_text(self, text: str, invoice: Invoice) -> str | None:
        if invoice.buyer_name and invoice.buyer_tax_id and invoice.seller_tax_id:
            pattern = re.escape(invoice.buyer_name) + r"\s*" + re.escape(invoice.buyer_tax_id) + r"\s*([^\n]+?)\s*" + re.escape(invoice.seller_tax_id)
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        match = re.search(r"名称[：:]\s*([\u4e00-\u9fffA-Za-z0-9（）()]+)\s*统一社会信用代码", compact_text(text))
        return match.group(1) if match else None


def _extract_issue_date(text: str, invoice_number: str | None = None) -> str | None:
    adjacent = re.search(
        r"开票日期\s*[:：]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        text,
    )
    if adjacent:
        return _ymd(*adjacent.groups())

    if invoice_number:
        after_number = re.search(
            re.escape(invoice_number) + r"[\s\S]{0,40}?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
            text,
        )
        if after_number:
            return _ymd(*after_number.groups())

    label_window = re.search(
        r"开票日期[\s\S]{0,260}?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        text,
    )
    if label_window:
        return _ymd(*label_window.groups())

    before_buyer = re.split(r"购买方|购\s*买\s*方|买\s*方\s*信\s*息", text, maxsplit=1)[0]
    header_dates = re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", before_buyer)
    if header_dates:
        return _ymd(*header_dates[-1])
    return None


def _extract_air_passenger(text: str) -> str | None:
    match = re.search(r"([\u4e00-\u9fff]{2,8})\s+\d{6}[\dXx*]{0,12}\*[\dXx*]{0,12}", text)
    return match.group(1) if match else None


def _extract_air_flight_no(text: str) -> str | None:
    matches = re.findall(r"(?<![A-Z0-9])([A-Z0-9]{2}\d{3,5})(?![A-Z0-9])", text)
    for match in matches:
        if any(char.isalpha() for char in match):
            return match
    return matches[0] if matches else None


def _extract_air_departure_date(text: str, flight_no: str | None) -> str | None:
    if flight_no:
        match = re.search(
            re.escape(flight_no) + r"[\s\S]{0,80}?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
            text,
        )
        if match:
            return _ymd(*match.groups())
    dates = re.findall(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    return _ymd(*dates[0]) if dates else None


def _extract_air_issue_date(text: str) -> str | None:
    before_buyer = re.search(
        r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[\s\S]{0,80}?中国航空运输协会",
        text,
    )
    if before_buyer:
        return _ymd(*before_buyer.groups())

    after_issuer = re.search(
        r"(?:填\s*开\s*单\s*位|销售\s*网点\s*代号)[\s\S]{0,220}?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        text,
    )
    if after_issuer:
        return _ymd(*after_issuer.groups())
    return None


def _extract_air_places(text: str) -> tuple[str, str] | None:
    compact = compact_text(text)
    direct = re.search(
        r"自\s*[:：]\s*(?:[A-Z]\s*){0,3}\s*([\u4e00-\u9fff]{2,8})[\s\S]{0,80}?"
        r"至\s*[:：]\s*(?:[A-Z]\s*){0,3}\s*([\u4e00-\u9fff]{2,8})",
        compact,
    )
    if direct:
        return _clean_air_place(direct.group(1)), _clean_air_place(direct.group(2))

    flight_no = _extract_air_flight_no(text)
    if flight_no:
        agency = re.search(
            r"([\u4e00-\u9fff]{2,8})\s+[\u4e00-\u9fff]{2,8}\s+[\u4e00-\u9fff]{1,4}\s+"
            + re.escape(flight_no)
            + r"[\s\S]{0,120}?\n\s*([\u4e00-\u9fff]{2,8})\s+[\u4e00-\u9fff]{2,8}",
            text,
        )
        if agency:
            return _clean_air_place(agency.group(1)), _clean_air_place(agency.group(2))
    return None


def _extract_air_places_from_filename(file_name: str) -> tuple[str, str] | None:
    match = re.search(r"([\u4e00-\u9fff]{2,8})\s*[-—至]\s*([\u4e00-\u9fff]{2,8})", file_name)
    if not match:
        return None
    return _clean_air_place(match.group(1)), _clean_air_place(match.group(2))


def _extract_railway_price(text: str) -> float | None:
    after_label = _first_match(text, r"票价\s*[:：]?\s*[￥¥]\s*([0-9]+(?:\.[0-9]+)?)")
    if after_label:
        return parse_money(after_label)

    before_label = re.search(r"[￥¥]\s*([0-9]+(?:\.[0-9]+)?)\s*\n\s*票价", text)
    if before_label:
        return parse_money(before_label.group(1))

    values = [
        parse_money(value)
        for value in re.findall(r"[￥¥]\s*([0-9]+(?:\.[0-9]+)?)", text)
    ]
    values = [value for value in values if value is not None]
    return values[0] if values else None


def _clean_air_place(value: str) -> str:
    value = re.sub(r"\s+", "", value or "")
    for suffix in ("机场", "航站楼", "国际", "国内"):
        value = value.replace(suffix, "")
    return value


def _extract_cny_total(text: str) -> float | None:
    window = text
    start = re.search(r"票\s*价", text)
    if start:
        window = text[start.start() :]
    end = re.search(r"电子\s*客票\s*号码|销售\s*网点\s*代号|自\s*[:：]", window)
    if end:
        window = window[: end.start()]
    values = [parse_money(value) for value in re.findall(r"CNY\s*([0-9]+(?:\.[0-9]+)?)", window)]
    values = [value for value in values if value is not None]
    if not values:
        values = [parse_money(value) for value in re.findall(r"CNY\s*([0-9]+(?:\.[0-9]+)?)", text)]
        values = [value for value in values if value is not None]
    non_zero = [value for value in values if value > 0]
    return max(non_zero) if non_zero else None


def _extract_air_amounts(text: str) -> tuple[float | None, float | None, float | None]:
    values = [parse_money(value) for value in re.findall(r"CNY\s*([0-9]+(?:\.[0-9]+)?)", text)]
    values = [value for value in values if value is not None]
    if not values:
        return None, None, None

    total = max(value for value in values if value > 0)
    candidates = [value for value in values if value > 0 and value != total]
    tax = None
    amount = None
    for idx, value in enumerate(values):
        if idx > 0 and "9%" in text:
            tax = value
    if len(candidates) >= 2:
        amount = round_money(candidates[0] + candidates[1])
    elif candidates:
        amount = candidates[0]
    return amount, tax, total


def _extract_amount_tax_total(text: str) -> tuple[float | None, float | None, float | None]:
    """Return (amount_without_tax, tax_amount, total_tax_included).

    The important rule is: reimbursement should use `价税合计（小写）`, not the first
    currency value after the printed label. PDF extraction often places the printed label
    before the `合计` row, so the first ¥ value can be the pre-tax amount.
    """
    money_values = [parse_money(x) for x in re.findall(r"[￥¥]\s*([0-9]+(?:\.[0-9]+)?)", text)]
    money_values = [v for v in money_values if v is not None]

    amount = None
    tax_amount = None
    total = None

    # 1) Direct signal: 小写 and ¥ amount are adjacent. Keep the window short,
    #    otherwise text extracted from PDFs may jump to the 合计 row and capture pre-tax amount.
    direct_matches = re.findall(
        r"价税合计[\s\S]{0,40}?小写[）\)]?\s*[￥¥]\s*([0-9]+(?:\.[0-9]+)?)",
        text,
    )
    if direct_matches:
        total = parse_money(direct_matches[-1])

    # 2) Strong signal: Chinese uppercase amount immediately followed by a ¥ 小写 amount.
    #    Used when the PDF puts the 价税合计 label before the 合计 row.
    if total is None:
        upper_total_patterns = [
            r"[零壹贰叁肆伍陆柒捌玖拾佰仟万亿整圆元角分]+\s*[￥¥]\s*([0-9]+(?:\.[0-9]+)?)",
            r"[一二三四五六七八九十百千万亿零壹贰叁肆伍陆柒捌玖拾佰仟万亿整圆元角分]+\s*[￥¥]\s*([0-9]+(?:\.[0-9]+)?)",
        ]
        for pattern in upper_total_patterns:
            matches = re.findall(pattern, text)
            if matches:
                total = parse_money(matches[-1])
                break

    # 3) Fallback: common extracted order is [amount_without_tax, tax_amount, total].
    #    Choose a value that equals the sum of two previous values, otherwise choose the largest.
    if total is None and len(money_values) >= 3:
        for i in range(2, len(money_values)):
            if abs((money_values[i - 2] + money_values[i - 1]) - money_values[i]) < 0.03:
                amount, tax_amount, total = money_values[i - 2], money_values[i - 1], money_values[i]
        if total is None:
            total = max(money_values)

    if len(money_values) >= 2:
        if total is not None:
            # Select the nearest pair amount + tax = total.
            candidates = []
            for a in money_values:
                for t in money_values:
                    if a >= t and abs((a + t) - total) < 0.03:
                        candidates.append((a, t))
            if candidates:
                amount, tax_amount = candidates[0]
            else:
                lower_values = [v for v in money_values if v < total]
                if lower_values:
                    amount = max(lower_values)
                    tax_candidates = [v for v in lower_values if abs((amount + v) - total) < 0.03]
                    tax_amount = tax_candidates[0] if tax_candidates else None
        else:
            amount, tax_amount = money_values[0], money_values[1]
            total = round_money(amount + tax_amount)
    elif len(money_values) == 1:
        total = money_values[0]

    return round_money(amount) if amount is not None else None, round_money(tax_amount) if tax_amount is not None else None, round_money(total) if total is not None else None


def _extract_stay_dates(text: str, default_year: int | None = None) -> tuple[str | None, str | None, int | None]:
    patterns = [
        # 入住时间：2026.7.25-2026.7.27
        r"入住时间\s*[；;:：]?\s*(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})日?\s*[-至~—]+\s*(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})日?",
        # 朱艳明 2026.5.12入住-2026.5.14离店
        r"(\d{4})[.\-/年]\s*(\d{1,2})[.\-/月]\s*(\d{1,2})日?\s*入住\s*[-至~—，,、；;。 ]*\s*(\d{4})[.\-/年]\s*(\d{1,2})[.\-/月]\s*(\d{1,2})日?\s*离店",
        # 入离时间:2026年5月21日至2026年5月24日
        r"入离时间\s*[；;:：]?\s*(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})日?\s*[-至~—]+\s*(\d{4})[.\-/年](\d{1,2})[.\-/月](\d{1,2})日?",
        # 入离时间/入离日期：5月7日-5月9日
        r"入离(?:时间|日期)\s*[；;:：]?\s*(\d{1,2})月(\d{1,2})日?\s*[-至~—]+\s*(\d{1,2})月(\d{1,2})日?",
        # 5.12入住5.14离店 / 入住时间；5月9号 离店时间；5月12号
        r"(?:入住时间\s*[；;:：]?\s*)?(\d{1,2})[.月](\d{1,2})[日号]?\s*入住[\s\S]{0,20}?(?:离店时间\s*[；;:：]?\s*)?(\d{1,2})[.月](\d{1,2})[日号]?\s*离店",
        r"入住时间\s*[；;:：]?\s*(\d{1,2})月(\d{1,2})号?[\s\S]{0,20}?(?:离店|退房)时间\s*[；;:：]?\s*(\d{1,2})月(\d{1,2})号?",
    ]
    for idx, pattern in enumerate(patterns):
        m = re.search(pattern, text)
        if not m:
            continue
        g = m.groups()
        if len(g) == 6:
            y1, m1, d1, y2, m2, d2 = map(int, g)
        else:
            if not default_year:
                continue
            y1 = y2 = int(default_year)
            m1, d1, m2, d2 = map(int, g)
        checkin = f"{y1:04d}-{m1:02d}-{d1:02d}"
        checkout = f"{y2:04d}-{m2:02d}-{d2:02d}"
        try:
            nights = (datetime.strptime(checkout, "%Y-%m-%d") - datetime.strptime(checkin, "%Y-%m-%d")).days
        except Exception:
            nights = None
        return checkin, checkout, nights
    return None, None, None


def _extract_hotel_guest(text: str) -> str | None:
    explicit = _first_match(text, r"入住\s*人(?:姓名)?\s*[:：]\s*([\u4e00-\u9fff]{2,8})")
    if explicit:
        return explicit
    explicit = _first_match(text, r"([\u4e00-\u9fff]{2,8})\s*入离(?:时间|日期)")
    if explicit:
        return explicit
    # Name before a full-date stay note: 朱艳明 2026.5.12入住-2026.5.14离店
    m = re.search(r"([\u4e00-\u9fff]{2,8})\s+\d{4}[.\-/年]\d{1,2}[.\-/月]\d{1,2}日?\s*入住", text)
    if m:
        return m.group(1)
    # Name after a compact stay note: 5.12入住5.14离店 2晚 武如通
    m = re.search(r"\d{1,2}[.月]\d{1,2}[日号]?\s*入住[\s\S]{0,25}?离店\s*\d*\s*晚?\s*([\u4e00-\u9fff]{2,8})", text)
    if m:
        return m.group(1)
    # Name immediately before 入住时间 line.
    m = re.search(r"([\u4e00-\u9fff]{2,8})\s*\n\s*入住时间", text)
    if m:
        return m.group(1)
    return None


def _hotel_city_from_seller(seller: str | None) -> str | None:
    if not seller:
        return None
    known = ("南京", "长春", "沈阳", "成都", "杭州", "南昌", "吉林", "太原", "武汉", "北京", "上海", "广州", "深圳", "青岛")
    for city in known:
        if city in seller:
            return city
    return _first_match(seller, r"([\u4e00-\u9fff]{2,4})市")


def _extract_taxi_row_by_words(path: Path, text: str) -> dict[str, object] | None:
    try:
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            words = pdf.pages[0].extract_words() if pdf.pages else []
    except Exception:
        words = []

    if words:
        data_words = [w for w in words if 330 <= float(w.get("top", 0)) <= 365]
        provider = _join_words(data_words, 65, 115)
        date_time = _join_words(data_words, 190, 275)
        start = _join_words(data_words, 345, 425)
        end = _join_words(data_words, 425, 515)
        amount_text = _join_words(data_words, 510, 560)
        amount = parse_money(_first_match(amount_text, r"([0-9]+(?:\.[0-9]+)?)"))
        date_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_time)
        result = {
            "provider": provider,
            "start": start,
            "end": end,
            "amount": amount,
            "date": _ymd(*date_match.groups()) if date_match else None,
        }
        if result["start"] and result["end"]:
            return result

    compact = compact_text(text)
    match = re.search(
        r"(?P<date>\d{4}-\d{1,2}-\d{1,2})\s+\d{1,2}:\d{2}\s+(?P<city>[\u4e00-\u9fff]{2,8}市)\s+(?P<body>.+?)\s+(?P<amount>[0-9]+(?:\.[0-9]+)?)元",
        compact,
    )
    if not match:
        return None
    body = match.group("body").strip()
    start, end = _split_taxi_places(body)
    return {
        "provider": "",
        "start": start,
        "end": end,
        "amount": parse_money(match.group("amount")),
        "date": parse_date(match.group("date")),
    }


def _join_words(words: list[dict], min_x: float, max_x: float) -> str:
    selected = sorted(
        [w for w in words if min_x <= float(w.get("x0", 0)) < max_x],
        key=lambda w: (float(w.get("top", 0)), float(w.get("x0", 0))),
    )
    return "".join(str(w.get("text", "")).strip() for w in selected).strip()


def _split_taxi_places(text: str) -> tuple[str, str]:
    for marker in ("机场", "车站", "酒店", "宾馆", "基地", "考点"):
        pos = text.find(marker)
        if pos > 0:
            split_at = pos + len(marker)
            return text[:split_at].strip(), text[split_at:].strip()
    parts = text.split()
    if len(parts) >= 2:
        return parts[0], "".join(parts[1:])
    return text, ""


def _first_match(text: str, pattern: str, flags: int = 0) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def _ymd(year: str, month: str, day: str) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _normalize_extracted_invoice_text(text: str) -> str:
    text = text or ""

    def join_digits(match: re.Match[str]) -> str:
        return re.sub(r"\s+", "", match.group(0))

    text = "\n".join(re.sub(r"(?<![A-Z0-9])\d[\d \t]{18,}\d(?![A-Z0-9])", join_digits, line) for line in text.splitlines())
    text = re.sub(r"(?<=\d)\s+\.\s*(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s+(?=\d\s*\.)", "", text)
    text = re.sub(r"(?<=\.\d)\s+(?=\d)", "", text)
    text = re.sub(r"(\d{4}\.)\s+(\d{1,2}\.)", r"\1\2", text)
    text = re.sub(r"(\d{4}\.\d{1,2}\.)\s+(\d{1,2})", r"\1\2", text)
    text = re.sub(r"入住\s+人", "入住人", text)
    return text
