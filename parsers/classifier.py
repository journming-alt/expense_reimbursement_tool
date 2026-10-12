from __future__ import annotations

from pathlib import Path


def classify_invoice(path: str | Path, text: str = "", category_hint: str = "unknown") -> dict[str, str]:
    path = Path(path)
    lower_name = path.name.lower()
    haystack = f"{path.name}\n{text}"

    category = category_hint if category_hint != "unknown" else "unknown"
    invoice_type = "unknown"
    transport_tool = ""
    document_type = "invoice"
    support_type = ""
    taxi_tokens = (
        "高德打车",
        "滴滴出行",
        "T3出行",
        "首汽约车",
        "曹操出行",
        "聚的出租车",
        "妥妥E行",
        "旅程约车",
        "携华出行",
        "及时用车",
        "网约车",
        "出租车",
        "客运服务",
    )

    is_invoice = "发票" in haystack or "铁路电子客票" in haystack or "航空运输电子客票" in haystack
    if not is_invoice and any(token in haystack for token in ("水单", "入住单", "酒店账单", "客账单")):
        category = "hotel"
        invoice_type = "住宿水单"
        document_type = "support_doc"
        support_type = "hotel_statement"
    elif not is_invoice and any(token in haystack for token in ("打车行程", "高德打车", "滴滴出行", "T3出行")) and "行程单" in haystack:
        category = "local_transport"
        invoice_type = "打车行程辅助资料"
        document_type = "support_doc"
        support_type = "taxi_trip_detail"
    elif "铁路电子客票" in haystack or "车次" in haystack or _contains_train_no(haystack):
        category = "transport"
        invoice_type = "铁路电子客票"
        transport_tool = "高铁" if any(token in haystack for token in ("G", "D", "C", "高铁", "动车")) else "火车"
    elif "航空运输电子客票行程单" in haystack or "航班号" in haystack or lower_name.endswith(".ofd"):
        category = "transport"
        invoice_type = "航空运输电子客票行程单"
        transport_tool = "飞机"
    elif ("飞猪" in haystack and "机票款凭证" in haystack) or "代订机票" in haystack:
        category = "transport"
        invoice_type = "机票电子发票"
        transport_tool = "飞机"
    elif any(token in haystack for token in taxi_tokens):
        category = "local_transport"
        invoice_type = "市内交通发票"
        transport_tool = "网约车"
    elif any(token in haystack for token in ("住宿服务", "住宿费", "入住人", "入离时间")):
        category = "hotel"
        invoice_type = "住宿电子发票"
    elif any(token in haystack for token in ("餐饮服务", "餐费")):
        category = "meal"
        invoice_type = "餐饮发票"
    elif any(token in haystack for token in ("出租车", "网约车", "客运服务")):
        category = "local_transport"
        invoice_type = "市内交通发票"

    if category == "transport" and invoice_type == "unknown":
        invoice_type = "交通发票"
    elif category == "hotel" and invoice_type == "unknown":
        invoice_type = "住宿发票"
    elif category == "meal" and invoice_type == "unknown":
        invoice_type = "餐费发票"
    elif category == "local_transport" and invoice_type == "unknown":
        invoice_type = "市内交通发票"

    return {
        "category": category,
        "invoice_type": invoice_type,
        "transport_tool": transport_tool,
        "document_type": document_type,
        "support_type": support_type,
    }


def _contains_train_no(text: str) -> bool:
    import re

    return bool(re.search(r"\b[DGCKZT]\d{1,5}\b", text))
