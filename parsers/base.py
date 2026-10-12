from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from models.invoice import Invoice


class BaseInvoiceParser(ABC):
    @abstractmethod
    def parse(self, path: str | Path, category_hint: str = "unknown") -> Invoice:
        raise NotImplementedError
