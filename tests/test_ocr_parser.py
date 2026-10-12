from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from parsers.ocr_parser import OCRInvoiceParser  # noqa: E402


class OCRParserTest(unittest.TestCase):
    def test_pdf_is_rendered_to_image_and_ocr_text_is_parsed(self) -> None:
        seen_paths: list[str] = []
        ocr_text = "\n".join(
            [
                "电子发票（航空运输电子客票行程单）",
                "发票号码：26448784110006563088",
                "朱艳明 210124******301839",
                "南航 CZ5817 T 2026 年 07 月 05 日 20K自： SHE 沈阳",
                "至： HGH 杭州",
                "CNY 412.84 CNY 137.61 9% CNY 49.55 CNY 50.00 CNY 650.00",
            ]
        )

        class FakePaddleOCR:
            def __init__(self, *args, **kwargs) -> None:
                pass

            def ocr(self, path: str, cls: bool = True):
                seen_paths.append(path)
                return [[(None, (line, 0.96)) for line in ocr_text.splitlines()]]

        fake_module = types.SimpleNamespace(PaddleOCR=FakePaddleOCR)

        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {"paddleocr": fake_module}):
            pdf_path = Path(tmp) / "air.pdf"
            from pypdf import PdfWriter

            writer = PdfWriter()
            writer.add_blank_page(width=300, height=200)
            with pdf_path.open("wb") as file:
                writer.write(file)

            invoice = OCRInvoiceParser().parse(pdf_path, "transport")

        self.assertEqual(Path(seen_paths[0]).suffix, ".png")
        self.assertEqual(invoice.file_path, str(pdf_path))
        self.assertEqual(invoice.file_type, "pdf")
        self.assertEqual(invoice.passenger_name, "朱艳明")
        self.assertEqual(invoice.departure_date, "2026-07-05")
        self.assertEqual(invoice.departure_place, "沈阳")
        self.assertEqual(invoice.arrival_place, "杭州")
        self.assertEqual(invoice.vehicle_no, "CZ5817")
        self.assertEqual(invoice.total_amount, 650.0)
        self.assertEqual(invoice.invoice_number, "26448784110006563088")

    def test_paddleocr_v3_predict_result_is_supported(self) -> None:
        ocr_text = [
            "电子发票（普通发票）",
            "发票号码：26317000002630435371",
            "开票日期：2026年07月21日",
            "*生产生活服务*代订机票费 个 1 584.91 584.91 6% 35.09",
            "价税合计（大写）陆佰贰拾圆整 （小写）¥620.00",
        ]

        class FakePaddleOCRV3:
            def __init__(self, *args, **kwargs) -> None:
                if "show_log" in kwargs or "use_angle_cls" in kwargs:
                    raise ValueError("old PaddleOCR arguments should not be used")

            def predict(self, path: str):
                return [{"rec_texts": ocr_text, "rec_scores": [0.96] * len(ocr_text)}]

        fake_module = types.SimpleNamespace(PaddleOCR=FakePaddleOCRV3)

        with patch.dict(sys.modules, {"paddleocr": fake_module}):
            text, confidence = OCRInvoiceParser()._run_paddleocr(Path("invoice.png"))

        self.assertIn("26317000002630435371", text)
        self.assertEqual(confidence, 0.96)

    def test_paddleocr_instance_is_reused_between_ocr_calls(self) -> None:
        created = 0

        class CachedFakePaddleOCR:
            def __init__(self, *args, **kwargs) -> None:
                nonlocal created
                created += 1

            def predict(self, path: str):
                return [{"rec_texts": ["发票号码：26317000002630435371"], "rec_scores": [0.96]}]

        fake_module = types.SimpleNamespace(PaddleOCR=CachedFakePaddleOCR)

        with patch.dict(sys.modules, {"paddleocr": fake_module}):
            OCRInvoiceParser()._run_paddleocr(Path("invoice1.png"))
            OCRInvoiceParser()._run_paddleocr(Path("invoice2.png"))

        self.assertEqual(created, 1)

    def test_ocr_unavailable_keeps_feizhu_receipt_as_no_itinerary_air_invoice(self) -> None:
        def blocked_import(name, *args, **kwargs):
            if name == "paddleocr":
                raise ImportError("No module named 'paddleocr'")
            return original_import(name, *args, **kwargs)

        original_import = __import__

        with patch("builtins.__import__", side_effect=blocked_import):
            invoice = OCRInvoiceParser().parse(
                Path("【飞猪】沈阳-太原  订单9897171790332-机票款凭证 报销凭证.pdf"),
                "transport",
            )

        self.assertEqual(invoice.invoice_type, "机票电子发票")
        self.assertEqual(invoice.transport_tool, "飞机")
        self.assertNotIn("departure_date 需人工补充", invoice.remarks)
        self.assertIn("total_amount 需人工补充", invoice.remarks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
