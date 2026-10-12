from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app import _bump_invoice_editor_key, _invoice_editor_key  # noqa: E402


class AppStateTest(unittest.TestCase):
    def test_invoice_editor_key_changes_after_rescan(self) -> None:
        state = {}

        first = _invoice_editor_key(state)
        _bump_invoice_editor_key(state)
        second = _invoice_editor_key(state)

        self.assertNotEqual(first, second)
        self.assertEqual(second, "invoice_editor_1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
