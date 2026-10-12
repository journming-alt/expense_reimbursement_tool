from __future__ import annotations

import csv
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

from models.invoice import Invoice
from models.trip import TripContext

INVOICE_SUFFIXES = {".pdf", ".ofd", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}


def safe_extract_zip(zip_path: str | Path, dest_dir: str | Path) -> Path:
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = _decode_zip_name(info)
            if _should_skip_zip_member(name):
                continue
            target = dest_dir / name
            resolved = target.resolve()
            if not str(resolved).startswith(str(dest_dir.resolve())):
                raise ValueError(f"Unsafe zip member path: {name}")
            if info.is_dir():
                resolved.mkdir(parents=True, exist_ok=True)
                continue
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, resolved.open("wb") as out:
                shutil.copyfileobj(source, out)

    roots = find_trip_roots(dest_dir)
    return roots[0] if len(roots) == 1 else dest_dir


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for encoding in ("utf-8", "gb18030", "gbk", "cp936", "big5"):
        try:
            decoded = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\ufffd" not in decoded:
            return decoded
    return name


def _should_skip_zip_member(name: str) -> bool:
    parts = Path(name).parts
    if not parts:
        return True
    if parts[0] == "__MACOSX":
        return True
    base = Path(name).name
    return base in {".DS_Store"} or base.startswith("._")


def find_trip_roots(root: str | Path) -> list[Path]:
    root = Path(root)
    if _looks_like_trip_root(root):
        return [root]
    results = [p for p in root.rglob("*") if p.is_dir() and _looks_like_trip_root(p)]
    return sorted(results, key=lambda p: (len(p.parts), str(p)))


def _looks_like_trip_root(path: Path) -> bool:
    if not path.is_dir():
        return False
    child_names = {p.name for p in path.iterdir()}
    has_invoice_dir = any(any(key in name for key in ("交通", "住宿", "餐票", "市内", "验真")) for name in child_names)
    has_template = any(p.suffix.lower() in EXCEL_SUFFIXES and "差旅报销明细表" in p.name for p in path.iterdir() if p.is_file())
    return has_invoice_dir or has_template


def find_template_excel(root: str | Path) -> Path | None:
    root = Path(root)
    candidates = [
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in EXCEL_SUFFIXES
        and "差旅报销明细表" in p.name
        and "自动生成" not in p.name
        and not p.name.startswith("~$")
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: (len(p.parts), str(p)))[0]


def collect_invoice_files(root: str | Path) -> list[tuple[Path, str]]:
    root = Path(root)
    files: list[tuple[Path, str]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name.startswith("._"):
            continue
        if path.suffix.lower() not in INVOICE_SUFFIXES:
            continue
        category = category_from_path(path.relative_to(root))
        if category == "verification":
            continue
        files.append((path, category))
    return sorted(files, key=lambda item: str(item[0]))


def find_verification_pdfs(root: str | Path) -> list[Path]:
    root = Path(root)
    results = []
    for path in root.rglob("*.pdf"):
        rel = path.relative_to(root)
        if category_from_path(rel) == "verification":
            results.append(path)
    return sorted(results)


def category_from_path(path: str | Path) -> str:
    text = "/".join(Path(path).parts)
    if "验真" in text or "查验" in text:
        return "verification"
    if "交通" in text and "市内" not in text:
        return "transport"
    if "住宿" in text:
        return "hotel"
    if "餐票" in text or "餐费" in text or "餐饮" in text:
        return "meal"
    if "市内" in text or "出租" in text or "网约" in text:
        return "local_transport"
    return "unknown"


def default_output_dir(base_dir: str | Path, site: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_site = site or "未命名考点"
    output_dir = Path(base_dir) / "data" / "outputs" / f"{safe_site}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def save_recognition_json(invoices: Iterable[Invoice], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = [invoice.to_json_dict() for invoice in invoices]
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def save_recognition_csv(invoices: Iterable[Invoice], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [invoice.as_table_row() for invoice in invoices]
    fieldnames = list(rows[0].keys()) if rows else [
        "是否选择",
        "文件名",
        "发票类型",
        "姓名",
        "金额",
        "发票号码",
        "查验状态",
        "备注",
    ]
    with output_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def write_processing_log(trip: TripContext, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    totals = trip.totals()
    lines = [
        f"处理时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"考试员：{trip.examiner}",
        f"考点：{trip.site}",
        f"发票数量：{len(trip.invoices)}",
        f"交通费小计：{totals['transport']}",
        f"住宿费小计：{totals['hotel']}",
        f"餐费小计：{totals['meal']}",
        f"市内交通费小计：{totals['local_transport']}",
        f"合计：{totals['total']}",
        "",
        "需人工关注：",
    ]
    pending = False
    for invoice in trip.invoices:
        if invoice.remarks or invoice.verification_status_label != "已查验":
            pending = True
            lines.append(
                f"- {invoice.file_name}：{invoice.verification_status_label}；"
                f"{'；'.join(invoice.remarks) or '请核对'}"
            )
    if not pending:
        lines.append("- 无")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def export_reimbursement_package(trip: TripContext, output_dir: str | Path) -> Path:
    output_dir = Path(output_dir)
    package_dir = output_dir / f"{trip.site or trip.root.name}_报销包"
    package_dir.mkdir(parents=True, exist_ok=True)

    for folder_name in ("交通发票", "住宿发票", "餐票", "市内交通费", "发票验真结果"):
        source = _find_child_dir(trip.root, folder_name)
        target = package_dir / folder_name
        if source and source.exists():
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".DS_Store", "._*"))
        else:
            target.mkdir(parents=True, exist_ok=True)

    if trip.generated_excel_path:
        shutil.copy2(trip.generated_excel_path, package_dir / Path(trip.generated_excel_path).name)

    save_recognition_json(trip.invoices, package_dir / "识别明细.json")
    save_recognition_csv(trip.invoices, package_dir / "发票识别明细.csv")
    write_processing_log(trip, package_dir / "处理日志.txt")

    zip_path = output_dir / f"{trip.site or trip.root.name}_报销包.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in package_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(package_dir))
    return zip_path


def _find_child_dir(root: Path, preferred_name: str) -> Path | None:
    direct = root / preferred_name
    if direct.exists():
        return direct
    keys = {
        "交通发票": ("交通",),
        "住宿发票": ("住宿",),
        "餐票": ("餐",),
        "市内交通费": ("市内", "出租", "网约"),
        "发票验真结果": ("验真", "查验"),
    }[preferred_name]
    for child in root.iterdir():
        if child.is_dir() and any(key in child.name for key in keys):
            return child
    return None
