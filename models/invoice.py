from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VerificationStatus(str, Enum):
    PENDING = "待查验"
    VERIFIED = "已查验"
    FAILED = "查验失败"
    MANUAL_REQUIRED = "需人工处理"


class VerificationResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    success: bool = False
    status: VerificationStatus = VerificationStatus.PENDING
    error_message: str | None = None
    pdf_path: str | None = None
    screenshot_path: str | None = None
    verified_at: str | None = None


class Invoice(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    file_path: str
    file_name: str
    file_type: str
    relative_path: str = ""
    category: str = "unknown"
    invoice_type: str = "unknown"
    document_type: str = "invoice"
    support_type: str | None = None

    invoice_code: str | None = None
    invoice_number: str | None = None
    issue_date: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    amount: float | None = None
    tax_amount: float | None = None
    total_amount: float | None = None

    passenger_name: str | None = None
    traveller_id_masked: str | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    verification_pdf_path: str | None = None

    departure_date: str | None = None
    departure_place: str | None = None
    arrival_date: str | None = None
    arrival_place: str | None = None
    transport_tool: str | None = None
    vehicle_no: str | None = None

    hotel_guest: str | None = None
    hotel_city: str | None = None
    checkin_date: str | None = None
    checkout_date: str | None = None
    nights: int | None = None
    rooms: int | None = None
    unit_price: float | None = None

    confidence: float = 0.8
    remarks: list[str] = Field(default_factory=list)
    raw_text: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def reimbursement_amount(self) -> float:
        if self.document_type == "support_doc":
            return 0.0
        return float(self.total_amount if self.total_amount is not None else (self.amount or 0))

    @property
    def display_name(self) -> str:
        return self.passenger_name or self.hotel_guest or ""

    @property
    def verification_status_label(self) -> str:
        status = self.verification_status
        return status.value if isinstance(status, VerificationStatus) else str(status)

    def add_remark(self, message: str) -> None:
        if message and message not in self.remarks:
            self.remarks.append(message)

    def mark_verified(self, pdf_path: str | Path) -> None:
        self.verification_status = VerificationStatus.VERIFIED
        self.verification_pdf_path = str(pdf_path)

    def mark_manual(self, message: str) -> None:
        self.verification_status = VerificationStatus.MANUAL_REQUIRED
        self.add_remark(message)

    def as_table_row(self) -> dict[str, Any]:
        return {
            "是否选择": True,
            "文件性质": "辅助资料" if self.document_type == "support_doc" else "发票",
            "是否计入报销": "否" if self.document_type == "support_doc" else "是",
            "文件名": self.file_name,
            "发票类型": self.invoice_type,
            "姓名": self.display_name,
            "出发日期": self.departure_date or "",
            "出发地": self.departure_place or "",
            "到达日期": self.arrival_date or "",
            "到达地": self.arrival_place or "",
            "交通工具": self.transport_tool or "",
            "住宿地点": self.hotel_city or "",
            "天数": self.nights or "",
            "间数": self.rooms or "",
            "金额": self.reimbursement_amount,
            "发票号码": self.invoice_number or "",
            "查验状态": "无需验真" if self.document_type == "support_doc" else self.verification_status_label,
            "备注": "；".join(self.remarks),
        }

    def to_json_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["reimbursement_amount"] = self.reimbursement_amount
        return data


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
