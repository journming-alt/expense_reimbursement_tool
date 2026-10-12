from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from models.invoice import Invoice  # noqa: E402
from workflow import scan_trip  # noqa: E402


class WorkflowProgressTest(unittest.TestCase):
    def test_scan_trip_reports_each_file_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "交通" / "first.pdf"
            second = root / "住宿" / "second.pdf"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"%PDF-1.4\n")
            second.write_bytes(b"%PDF-1.4\n")
            events: list[tuple[int, int, str]] = []

            def fake_parse(path: Path, category_hint: str) -> Invoice:
                return Invoice(
                    file_path=str(path),
                    file_name=path.name,
                    file_type=path.suffix.lower().lstrip("."),
                    category=category_hint,
                    invoice_type="测试发票",
                    invoice_number=f"NO-{path.stem}",
                )

            with (
                patch("workflow.collect_invoice_files", return_value=[(first, "transport"), (second, "hotel")]),
                patch("workflow.parse_invoice_file", side_effect=fake_parse),
                patch("workflow.parse_existing_verification_pdfs", return_value={}),
            ):
                scan_trip(root, progress_callback=lambda current, total, path: events.append((current, total, path.name)))

            self.assertEqual(events, [(1, 2, "first.pdf"), (2, 2, "second.pdf")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
