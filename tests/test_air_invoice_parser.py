from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from parsers.pdf_text_parser import PDFTextInvoiceParser  # noqa: E402


class AirInvoiceParserTest(unittest.TestCase):
    def test_feizhu_southern_airlines_pdf_extracts_air_trip_fields(self) -> None:
        text = (
            "电子发票（航空运输电子客票行程单）\n"
            "发票号码：\n"
            "26448784110006563088\n"
            "朱艳明 210124******301839 Q /BUD EQIANZHUAN 不得签转\n"
            "南航 CZ5817 T 2026 年 07 月 05 日 TRE0WNNP 2026 年 07 月 05 日 20K自： SHE  沈阳\n"
            "至： HGH  杭州\n"
            "票价 燃油附加费 增值税税率 增值税税额 民航发展基金 其他税费 合计\n"
            "CNY 412.84 CNY 137.61 9% CNY 49.55 CNY 50.00 CNY 650.00\n"
            "电子客票号码： 验证码： 提示信息： 保险费：\n"
            "销售网点代号： 填开单位： 填开日期：\n"
            "7842193408256 3088 0.00\n"
            "CAN011/00001704 中国南方航空股份有限公司 2026 年 07 月 21 日\n"
            "中国航空运输协会 51100000500019891R\n"
        )

        invoice = PDFTextInvoiceParser().parse_text(text, Path("feizhu.pdf"), "transport")

        self.assertEqual(invoice.passenger_name, "朱艳明")
        self.assertEqual(invoice.departure_date, "2026-07-05")
        self.assertEqual(invoice.departure_place, "沈阳")
        self.assertEqual(invoice.arrival_place, "杭州")
        self.assertEqual(invoice.transport_tool, "飞机")
        self.assertEqual(invoice.vehicle_no, "CZ5817")
        self.assertEqual(invoice.amount, 550.45)
        self.assertEqual(invoice.total_amount, 650.0)
        self.assertEqual(invoice.invoice_number, "26448784110006563088")
        self.assertEqual(invoice.issue_date, "2026-07-21")
        self.assertEqual(invoice.remarks, [])

    def test_feizhu_agency_pdf_extracts_places_after_labels(self) -> None:
        text = (
            "电子发票（航空运输电子客票行程单）\n"
            "发 票 号 码 ：\n"
            "26378324111054381597\n"
            "朱艳明 210124********1839 不得签转改期退票收费\n"
            "票 价 燃 油 附 加 费 增 值 税 税 率 增 值 税 税 额 民 航 发 展 基 金 其 他 税 费 合 计\n"
            "CNY 697.25 CNY 91.74 9% CNY 71.01 CNY 50.00 CNY 0.00 CNY 910.00\n"
            "3242162081430 1597 0.00\n"
            "SDH999/08612351 阿斯兰航空服务（上海）有限公司 2026 年 07 月 23 日\n"
            "中国航空运输协会 51100000500019891R\n"
            "自 :\n"
            "至 :\n"
            "太原  武宿 山航 SC2267 S 2026 年 07 月 22 日 12:20 S 20K\n"
            "长春  龙嘉\n"
        )

        invoice = PDFTextInvoiceParser().parse_text(text, Path("feizhu_agency.pdf"), "transport")

        self.assertEqual(invoice.passenger_name, "朱艳明")
        self.assertEqual(invoice.departure_date, "2026-07-22")
        self.assertEqual(invoice.departure_place, "太原")
        self.assertEqual(invoice.arrival_place, "长春")
        self.assertEqual(invoice.transport_tool, "飞机")
        self.assertEqual(invoice.vehicle_no, "SC2267")
        self.assertEqual(invoice.amount, 788.99)
        self.assertEqual(invoice.total_amount, 910.0)
        self.assertEqual(invoice.invoice_number, "26378324111054381597")
        self.assertEqual(invoice.issue_date, "2026-07-23")
        self.assertEqual(invoice.remarks, [])

    def test_feizhu_receipt_without_itinerary_extracts_invoice_only_fields(self) -> None:
        text = (
            "电子发票（普通发票）\n"
            "发票号码：26317000002630435371\n"
            "开票日期：2026年07月21日\n"
            "名称：中国航空运输协会\n"
            "名称：阿斯兰航空服务（上海）有限公司\n"
            "*生产生活服务*代订机票费 个 1 584.91 584.91 6% 35.09\n"
            "合计 ¥584.91 ¥35.09\n"
            "价税合计（大写）陆佰贰拾圆整 （小写）¥620.00\n"
        )

        invoice = PDFTextInvoiceParser().parse_text(
            text,
            Path("【飞猪】沈阳-太原  订单9897171790332-机票款凭证 报销凭证.pdf"),
            "transport",
        )

        self.assertEqual(invoice.invoice_number, "26317000002630435371")
        self.assertEqual(invoice.issue_date, "2026-07-21")
        self.assertEqual(invoice.amount, 584.91)
        self.assertEqual(invoice.tax_amount, 35.09)
        self.assertEqual(invoice.total_amount, 620.0)
        self.assertEqual(invoice.transport_tool, "飞机")
        self.assertIsNone(invoice.departure_place)
        self.assertIsNone(invoice.arrival_place)
        self.assertNotIn("departure_date 需人工补充", invoice.remarks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
