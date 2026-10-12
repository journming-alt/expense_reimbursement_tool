from __future__ import annotations

from datetime import datetime
from pathlib import Path


def save_result_pdf(page, pdf_path: str | Path, screenshot_path: str | Path | None = None) -> tuple[Path | None, Path | None]:
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_file = Path(screenshot_path) if screenshot_path else pdf_path.with_suffix(".png")

    try:
        page.pdf(path=str(pdf_path), format="A4", print_background=True)
        return pdf_path, None
    except Exception:
        try:
            page.screenshot(path=str(screenshot_file), full_page=True)
            _image_to_pdf(screenshot_file, pdf_path)
            return pdf_path, screenshot_file
        except Exception:
            return None, screenshot_file if screenshot_file.exists() else None


def build_result_filename(invoice_number: str | None, amount: float | None, issue_date: str | None) -> str:
    number = invoice_number or "未知发票号码"
    amount_text = str(amount or "未知金额").replace(".", "_")
    date_text = issue_date or datetime.now().strftime("%Y%m%d")
    return f"验真结果_{number}_{amount_text}_{date_text}.pdf"


def _image_to_pdf(image_path: Path, pdf_path: Path) -> None:
    from PIL import Image

    image = Image.open(image_path).convert("RGB")
    image.save(pdf_path, "PDF", resolution=100.0)
