from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(PROJECT_ROOT))

from models.invoice import Invoice  # noqa: E402
from parsers.ofd_parser import OFDInvoiceParser  # noqa: E402


_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA\xe2{\xd7\x00\x00\x00\x00IEND\xaeB`\x82"
)


class OFDParserTest(unittest.TestCase):
    def test_image_only_ofd_falls_back_to_ocr_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ofd_path = Path(tmp) / "image_only.ofd"
            with zipfile.ZipFile(ofd_path, "w") as zf:
                zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016' />")
                zf.writestr(
                    "Doc_0/Pages/Page_0/Content.xml",
                    (
                        "<ofd:Page xmlns:ofd='http://www.ofdspec.org/2016'>"
                        "<ofd:Area><ofd:PhysicalBox>0 0 100 80</ofd:PhysicalBox></ofd:Area>"
                        "<ofd:PathObject Fill='true' Stroke='false' DrawParam='9'>"
                        "<ofd:AbbreviatedData>M 10 10 L 40 10 L 40 40 L 10 40 C</ofd:AbbreviatedData>"
                        "</ofd:PathObject>"
                        "<ofd:ImageObject ResourceID='1' CTM='10 0 0 10 50 20' />"
                        "</ofd:Page>"
                    ),
                )
                zf.writestr(
                    "Doc_0/DocumentRes.xml",
                    (
                        "<ofd:Res xmlns:ofd='http://www.ofdspec.org/2016'>"
                        "<ofd:DrawParams><ofd:DrawParam ID='9'><ofd:FillColor Value='0 0 0' />"
                        "<ofd:StrokeColor Value='0 0 0' /></ofd:DrawParam></ofd:DrawParams>"
                        "<ofd:MultiMedias><ofd:MultiMedia Type='Image' Format='PNG' ID='1'>"
                        "<ofd:MediaFile>/Doc_0/Res/page.png</ofd:MediaFile>"
                        "</ofd:MultiMedia></ofd:MultiMedias>"
                        "</ofd:Res>"
                    ),
                )
                zf.writestr("Doc_0/Res/page.png", _PNG_BYTES)

            ocr_invoice = Invoice(
                file_path="temporary.png",
                file_name="temporary.png",
                file_type="png",
                category="transport",
                invoice_type="航空运输电子客票行程单",
                invoice_number="26132000002017538521",
                total_amount=699.0,
                raw_text="OCR text",
                confidence=0.86,
            )

            with patch("parsers.ocr_parser.OCRInvoiceParser") as parser_cls:
                parser_cls.return_value.parse.return_value = ocr_invoice

                invoice = OFDInvoiceParser().parse(ofd_path, "transport")

            parser_cls.return_value.parse.assert_called_once()
            ocr_path = Path(parser_cls.return_value.parse.call_args.args[0])
            self.assertEqual(ocr_path.suffix, ".png")
            self.assertEqual(invoice.file_path, str(ofd_path))
            self.assertEqual(invoice.file_name, ofd_path.name)
            self.assertEqual(invoice.file_type, "ofd")
            self.assertEqual(invoice.invoice_number, "26132000002017538521")
            self.assertEqual(invoice.total_amount, 699.0)

    def test_ofd_ocr_text_extracts_air_trip_from_invoice_remarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ofd_path = Path(tmp) / "image_only.ofd"
            with zipfile.ZipFile(ofd_path, "w") as zf:
                zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016' />")
                zf.writestr(
                    "Doc_0/Pages/Page_0/Content.xml",
                    (
                        "<ofd:Page xmlns:ofd='http://www.ofdspec.org/2016'>"
                        "<ofd:Area><ofd:PhysicalBox>0 0 100 80</ofd:PhysicalBox></ofd:Area>"
                        "<ofd:PathObject Fill='true' Stroke='false' DrawParam='9'>"
                        "<ofd:AbbreviatedData>M 10 10 L 40 10 L 40 40 L 10 40 C</ofd:AbbreviatedData>"
                        "</ofd:PathObject>"
                        "</ofd:Page>"
                    ),
                )

            ocr_invoice = Invoice(
                file_path="temporary.png",
                file_name="temporary.png",
                file_type="png",
                category="transport",
                invoice_type="unknown",
                raw_text=(
                    "电子发票（普通发票）\n"
                    "发票号码：26317000002405003690\n"
                    "开票日期：2026年07月01日\n"
                    "金额 526.42 税额 31.58 （小写）￥558.00\n"
                    "朱艳明（1）2026/06/24 长春-石家庄 9C8662 经济舱R"
                ),
                confidence=0.86,
            )

            with patch("parsers.ocr_parser.OCRInvoiceParser") as parser_cls:
                parser_cls.return_value.parse.return_value = ocr_invoice

                invoice = OFDInvoiceParser().parse(ofd_path, "transport")

            self.assertEqual(invoice.invoice_number, "26317000002405003690")
            self.assertEqual(invoice.issue_date, "2026-07-01")
            self.assertEqual(invoice.passenger_name, "朱艳明")
            self.assertEqual(invoice.departure_date, "2026-06-24")
            self.assertEqual(invoice.departure_place, "长春")
            self.assertEqual(invoice.arrival_place, "石家庄")
            self.assertEqual(invoice.vehicle_no, "9C8662")
            self.assertEqual(invoice.transport_tool, "飞机")
            self.assertEqual(invoice.total_amount, 558.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
