from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from models.invoice import Invoice  # noqa: E402
from tax_verify.browser_verifier import BrowserTaxVerifier, _field_selectors, _try_fill, _verification_values  # noqa: E402


class FakeElement:
    def __init__(self, accepts_fill: bool = True):
        self.accepts_fill = accepts_fill
        self.value = ""
        self.fill_calls = 0

    def fill(self, value: str, timeout: int = 1000) -> None:
        self.fill_calls += 1
        if self.accepts_fill:
            self.value = value

    def evaluate(self, script: str, value: str | None = None):
        if value is None:
            return self.value
        if self.accepts_fill:
            self.value = value
        return self.value

    def blur(self, timeout: int = 500) -> None:
        return None


class FakeLocator:
    def __init__(self, elements: list[FakeElement]):
        self.elements = elements

    @property
    def first(self) -> "FakeLocator":
        return FakeLocator(self.elements[:1])

    def nth(self, index: int) -> "FakeLocator":
        return FakeLocator([self.elements[index]])

    def count(self) -> int:
        return len(self.elements)

    def fill(self, value: str, timeout: int = 1000) -> None:
        self.elements[0].fill(value, timeout=timeout)

    def evaluate(self, script: str, value: str | None = None):
        return self.elements[0].evaluate(script, value)

    def blur(self, timeout: int = 500) -> None:
        self.elements[0].blur(timeout=timeout)


class FakePage:
    def __init__(self, elements: list[FakeElement]):
        self.elements = elements

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.elements)


class DelayedFakePage:
    def __init__(self):
        self.element = FakeElement()
        self.available = False
        self.frames = []

    def locator(self, selector: str) -> FakeLocator:
        if selector == "input.delayed" and self.available:
            return FakeLocator([self.element])
        return FakeLocator([])

    def wait_for_timeout(self, timeout: int) -> None:
        self.available = True


class TaxVerifierTest(unittest.TestCase):
    def test_verification_values_use_tax_platform_formats(self) -> None:
        invoice = Invoice(
            file_path="air.pdf",
            file_name="air.pdf",
            file_type="pdf",
            invoice_number="26448784110006563088",
            issue_date="2026-07-21",
            amount=550.45,
            total_amount=650.0,
        )

        values = _verification_values(invoice)

        self.assertEqual(values["invoice_number"], "26448784110006563088")
        self.assertEqual(values["issue_date"], "20260721")
        self.assertEqual(values["total_amount"], "650.00")

    def test_field_selectors_include_label_based_fallbacks(self) -> None:
        selectors = _field_selectors({"common": {}}, "invoice_number")

        self.assertTrue(any("发票号码" in selector and "following::input" in selector for selector in selectors))

    def test_field_selectors_include_current_tax_platform_form_item_classes(self) -> None:
        self.assertIn(".t-form-item__fphm input", _field_selectors({"common": {}}, "invoice_number"))
        self.assertIn(".t-form-item__kprq input", _field_selectors({"common": {}}, "issue_date"))
        self.assertIn(".t-form-item__kpje input", _field_selectors({"common": {}}, "total_amount"))

    def test_try_fill_checks_value_and_continues_to_next_match(self) -> None:
        first = FakeElement(accepts_fill=False)
        second = FakeElement(accepts_fill=True)

        filled = _try_fill(FakePage([first, second]), "input", "26448784110006563088")

        self.assertTrue(filled)
        self.assertEqual(first.value, "")
        self.assertEqual(second.value, "26448784110006563088")

    def test_fill_known_fields_retries_until_spa_form_is_rendered(self) -> None:
        verifier = BrowserTaxVerifier()
        verifier.config = {
            "fill_timeout_ms": 1000,
            "fill_retry_interval_ms": 100,
            "common": {"invoice_number": ["input.delayed"]},
        }
        invoice = Invoice(
            file_path="air.pdf",
            file_name="air.pdf",
            file_type="pdf",
            invoice_number="26448784110006563088",
        )
        page = DelayedFakePage()

        verifier._fill_known_fields(page, invoice)

        self.assertEqual(page.element.value, "26448784110006563088")


if __name__ == "__main__":
    unittest.main(verbosity=2)
