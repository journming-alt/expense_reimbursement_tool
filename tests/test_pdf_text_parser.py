from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from parsers.pdf_text_parser import PDFTextInvoiceParser  # noqa: E402


class PDFTextParserTest(unittest.TestCase):
    def test_hotel_invoice_handles_split_digits_and_dates(self) -> None:
        text = "\n".join(
            [
                "电子发票（普通发票） 发票号码：",
                "开票日期：",
                "26132000002017538 521",
                "2026年06月26日",
                "中国航空运输协会",
                "51100000500019891R",
                "石家庄市西美金五洲 酒 店有限公司高新 区分公司",
                "91130101MA0CJR806C",
                "¥ 658 . 49 ¥ 39 . 51",
                "陆佰玖拾捌圆整 ¥ 69 8 . 00",
                "* 生产生活服 务* 住 宿费 6%天 658 . 49 39 . 51329 . 24528 3018 8 6792",
                "入住 人：朱艳明。两天。2026. 6. 24入住 ，2026. 6. 26离店。",
            ]
        )

        parser = PDFTextInvoiceParser()
        invoice = parser.parse_text(text, Path("hotel.pdf"), "hotel")

        self.assertEqual(invoice.invoice_number, "26132000002017538521")
        self.assertEqual(invoice.issue_date, "2026-06-26")
        self.assertEqual(invoice.hotel_guest, "朱艳明")
        self.assertEqual(invoice.checkin_date, "2026-06-24")
        self.assertEqual(invoice.checkout_date, "2026-06-26")
        self.assertEqual(invoice.nights, 2)
        self.assertEqual(invoice.total_amount, 698.0)
        self.assertEqual(invoice.unit_price, 349.0)

    def test_railway_invoice_extracts_price_before_ticket_label_and_stations(self) -> None:
        text = (
            "电子发票（铁路电子客票）\n"
            "发票号码:26219153481000581010 辽宁省税务局 开票日期:2026年07月21日\n"
            "长春西 G116 沈阳北\n"
            "站 站\n"
            "Changchunxi Shenyangbei\n"
            "2026年07月09日 15:03开 04车03D号 二等座\n"
            "￥137.00\n"
            "票价:\n"
            "2101241994****1839 朱艳明\n"
            "电子客票号:5348181086071092233292026 始发改签\n"
            "购买方名称:中国航空运输协会 统一社会信用代码:51100000500019891R\n"
        )

        invoice = PDFTextInvoiceParser().parse_text(text, Path("rail.pdf"), "transport")

        self.assertEqual(invoice.invoice_number, "26219153481000581010")
        self.assertEqual(invoice.issue_date, "2026-07-21")
        self.assertEqual(invoice.total_amount, 137.0)
        self.assertEqual(invoice.departure_date, "2026-07-09")
        self.assertEqual(invoice.departure_place, "长春西")
        self.assertEqual(invoice.arrival_place, "沈阳北")
        self.assertEqual(invoice.transport_tool, "高铁")

    def test_hotel_invoice_extracts_guest_city_and_nights_from_new_kpls_notes(self) -> None:
        text = (
            "电子发票（普通发票） 发票号码：\n"
            "开票日期：\n"
            "26422000002697131431\n"
            "2026年07月27日\n"
            "中国航空运输协会\n"
            "51100000500019891R\n"
            "武汉齐耀酒店管理有限公司\n"
            "91420100MA49BB5T1X\n"
            "*生产生活服务*住宿服务 晚 2 320.7547169811321 641.51 6% 38.49\n"
            "陆佰捌拾圆整 ¥680.00\n"
            "641.51¥ 38.49¥\n"
            "入住人：朱艳明\n"
            "入住时间：2026.7.25-2026.7.27\n"
            "一间两晚\n"
        )

        invoice = PDFTextInvoiceParser().parse_text(text, Path("hotel.pdf"), "hotel")

        self.assertEqual(invoice.hotel_guest, "朱艳明")
        self.assertEqual(invoice.hotel_city, "武汉")
        self.assertEqual(invoice.checkin_date, "2026-07-25")
        self.assertEqual(invoice.checkout_date, "2026-07-27")
        self.assertEqual(invoice.nights, 2)
        self.assertEqual(invoice.total_amount, 680.0)
        self.assertEqual(invoice.unit_price, 340.0)

    def test_hotel_invoice_extracts_nights_from_checkin_and_checkout_time_labels(self) -> None:
        text = (
            "电子发票（普通发票） 发票号码：\n"
            "26332000005838650656\n"
            "2026年07月07日\n"
            "中国航空运输协会\n"
            "杭州亚豪酒店投资管理有限公司\n"
            "*生产生活服务*住宿服务 晚 2 358.4905660377359 716.98 6% 43.02\n"
            "柒佰陆拾圆整 ¥760.00\n"
            "716.98¥ 43.02¥\n"
            "入住人：朱艳明\n"
            "房间数量：1\n"
            "入住时间：7月5号  退房时间：7月7号\n"
        )

        invoice = PDFTextInvoiceParser().parse_text(text, Path("hotel.pdf"), "hotel")

        self.assertEqual(invoice.hotel_city, "杭州")
        self.assertEqual(invoice.checkin_date, "2026-07-05")
        self.assertEqual(invoice.checkout_date, "2026-07-07")
        self.assertEqual(invoice.nights, 2)
        self.assertEqual(invoice.total_amount, 760.0)
        self.assertEqual(invoice.unit_price, 380.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
