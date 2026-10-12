from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .invoice import Invoice


class TripContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    examiner: str = ""
    site: str = ""
    root_path: str
    template_path: str | None = None
    invoices: list[Invoice] = Field(default_factory=list)
    generated_excel_path: str | None = None
    exported_zip_path: str | None = None
    output_dir: str | None = None

    @property
    def root(self) -> Path:
        return Path(self.root_path)

    def infer_examiner(self) -> str:
        names = [i.display_name for i in self.invoices if i.display_name]
        if not names:
            return self.examiner
        return Counter(names).most_common(1)[0][0]

    def totals(self) -> dict[str, float]:
        result = {"transport": 0.0, "hotel": 0.0, "meal": 0.0, "local_transport": 0.0}
        for invoice in self.invoices:
            if invoice.category in result:
                result[invoice.category] += invoice.reimbursement_amount
        result["total"] = sum(result.values())
        return {k: round(v, 2) for k, v in result.items()}
