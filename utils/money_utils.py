from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def parse_money(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return round_money(float(value))
    text = str(value).replace(",", "").replace("￥", "").replace("¥", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return round_money(float(Decimal(match.group(0))))
    except (InvalidOperation, ValueError):
        return None


def round_money(value: float | int | Decimal | None) -> float | None:
    if value is None:
        return None
    quantized = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(quantized)


def money_or_zero(value: float | int | Decimal | None) -> float:
    parsed = parse_money(value)
    return float(parsed or 0)
