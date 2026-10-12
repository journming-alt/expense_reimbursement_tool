from __future__ import annotations

import platform
import subprocess
from pathlib import Path

import pandas as pd
import streamlit as st

from models.invoice import VerificationStatus
from storage.db import ReimbursementDB
from tax_verify.browser_verifier import BrowserTaxVerifier, cleanup_leftover_testing_browsers
from workflow import generate_excel, import_trip_source, parse_existing_verification_pdfs, scan_trip, table_rows_to_invoices


APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
WORK_DIR = DATA_DIR / "work"
TEMPLATE_PATH = DATA_DIR / "templates" / "default_差旅报销明细表.xlsx"
DB_PATH = DATA_DIR / "history.sqlite3"

DATA_DIR.mkdir(exist_ok=True)
WORK_DIR.mkdir(exist_ok=True)


def main() -> None:
    st.set_page_config(page_title="自动差旅报销工具", layout="wide")
    if "page" not in st.session_state:
        st.session_state.page = "首页"

    st.sidebar.title("自动差旅报销工具")
    page = st.sidebar.radio(
        "页面",
        ["首页", "新建报销", "发票识别结果", "验真", "导出", "历史报销"],
        index=["首页", "新建报销", "发票识别结果", "验真", "导出", "历史报销"].index(st.session_state.page),
    )
    st.session_state.page = page

    if page == "首页":
        render_home()
    elif page == "新建报销":
        render_new_trip()
    elif page == "发票识别结果":
        render_results()
    elif page == "验真":
        render_verification()
    elif page == "导出":
        render_export()
    else:
        render_history()


def render_home() -> None:
    st.title("自动差旅报销工具")
    col1, col2 = st.columns(2)
    if col1.button("新建报销", use_container_width=True):
        st.session_state.page = "新建报销"
        st.rerun()
    if col2.button("打开历史报销", use_container_width=True):
        st.session_state.page = "历史报销"
        st.rerun()


def render_new_trip() -> None:
    st.title("新建报销")
    selected_folder = st.session_state.get("selected_trip_folder", "")
    cols = st.columns([1, 3])
    if cols[0].button("选择出差文件夹", use_container_width=True):
        folder = _choose_local_folder()
        if folder:
            st.session_state.selected_trip_folder = folder
            st.rerun()
        else:
            st.warning("没有选择文件夹。")
    cols[1].text_input("已选择文件夹", value=selected_folder, disabled=True)
    examiner = st.text_input("考试员姓名")
    site = st.text_input("考点，例如 吉林01")

    if st.button("开始扫描", type="primary"):
        source_text = st.session_state.get("selected_trip_folder", "")
        if not source_text:
            st.error("请选择一个出差文件夹。")
            return

        source = Path(source_text).expanduser()
        if not source.exists() or not source.is_dir():
            st.error(f"文件夹不存在或不可访问：{source}")
            return

        fallback = TEMPLATE_PATH if TEMPLATE_PATH.exists() else None
        progress_bar = st.progress(0)
        progress_text = st.empty()

        def update_scan_progress(current: int, total: int, path: Path) -> None:
            ratio = current / total if total else 1
            progress_bar.progress(ratio, text=f"正在识别第 {current}/{total} 个文件：{path.name}")
            progress_text.info(f"正在识别：{path.name}。首次 OCR 加载模型会比较慢，请稍等。")

        with st.spinner("正在识别发票。首次 OCR 会加载模型，可能需要几十秒，请稍等..."):
            trip_root = import_trip_source(source, WORK_DIR)
            trip = scan_trip(
                trip_root,
                examiner=examiner,
                site=site or trip_root.name,
                fallback_template=fallback,
                progress_callback=update_scan_progress,
            )
        progress_bar.progress(1.0, text="识别完成")
        progress_text.empty()
        st.session_state.trip = trip
        _bump_invoice_editor_key()
        st.success(f"扫描完成：识别 {len(trip.invoices)} 张发票。")
        st.session_state.page = "发票识别结果"
        st.rerun()


def render_results() -> None:
    st.title("发票识别结果")
    trip = st.session_state.get("trip")
    if not trip:
        st.info("请先新建报销并扫描发票。")
        return

    col1, col2 = st.columns(2)
    trip.examiner = col1.text_input("考试员", value=trip.examiner)
    trip.site = col2.text_input("考点", value=trip.site)

    df = pd.DataFrame([invoice.as_table_row() for invoice in trip.invoices])
    edited = st.data_editor(df, use_container_width=True, num_rows="fixed", key=_invoice_editor_key())

    col_a, col_b, col_c, col_d = st.columns(4)
    if col_a.button("重新识别", use_container_width=True):
        progress_bar = st.progress(0)
        progress_text = st.empty()

        def update_rescan_progress(current: int, total: int, path: Path) -> None:
            ratio = current / total if total else 1
            progress_bar.progress(ratio, text=f"正在重新识别第 {current}/{total} 个文件：{path.name}")
            progress_text.info(f"正在重新识别：{path.name}")

        with st.spinner("正在重新识别发票，请稍等..."):
            refreshed = scan_trip(
                trip.root_path,
                examiner=trip.examiner,
                site=trip.site,
                fallback_template=trip.template_path,
                progress_callback=update_rescan_progress,
            )
        progress_bar.progress(1.0, text="重新识别完成")
        progress_text.empty()
        st.session_state.trip = refreshed
        _bump_invoice_editor_key()
        st.rerun()
    if col_b.button("保存修改", use_container_width=True):
        st.session_state.trip = table_rows_to_invoices(trip, edited.to_dict("records"))
        st.success("修改已保存。")
    if col_c.button("开始验真", use_container_width=True):
        st.session_state.trip = table_rows_to_invoices(trip, edited.to_dict("records"))
        st.session_state.page = "验真"
        st.rerun()
    if col_d.button("生成报销表", type="primary", use_container_width=True):
        trip = table_rows_to_invoices(trip, edited.to_dict("records"))
        generated = generate_excel(trip, trip.root)
        trip.generated_excel_path = str(generated)
        trip.exported_zip_path = None
        trip.output_dir = str(trip.root)
        ReimbursementDB(DB_PATH).save_trip(trip)
        st.session_state.trip = trip
        st.session_state.page = "导出"
        st.rerun()


def render_verification() -> None:
    st.title("发票验真")
    trip = st.session_state.get("trip")
    if not trip:
        st.info("请先扫描发票。")
        return

    st.warning("遇到验证码、滑块或人机验证时，请在打开的浏览器里手动完成。工具不会破解或绕过校验。验真结果请手动保存为 PDF 到“发票验真结果”文件夹。")
    cleanup_cols = st.columns(2)
    if cleanup_cols[0].button("清理残留查验浏览器", use_container_width=True):
        closed = cleanup_leftover_testing_browsers(force=False)
        if closed:
            st.success(f"已请求关闭 {closed} 个查验浏览器相关进程。Dock 图标通常会在几秒内消失。")
        else:
            st.info("没有发现本工具启动的残留查验浏览器进程。")
    if cleanup_cols[1].button("强制清理残留浏览器", use_container_width=True):
        closed = cleanup_leftover_testing_browsers(force=True)
        if closed:
            st.success(f"已强制关闭 {closed} 个查验浏览器相关进程。")
        else:
            st.info("没有发现本工具启动的残留查验浏览器进程。")

    verify_dir = trip.root / "发票验真结果"
    verify_dir.mkdir(exist_ok=True)
    verifier = BrowserTaxVerifier(APP_DIR / "config" / "tax_platform_selectors.yaml", headless=False, output_dir=verify_dir)

    active = st.session_state.get("active_tax_session")
    if active:
        active_index = active.get("invoice_index")
        active_invoice = trip.invoices[active_index] if active_index is not None and active_index < len(trip.invoices) else None
        session = active["session"]
        if session.browser_window_closed():
            session.close()
            st.session_state.pop("active_tax_session", None)
            st.info("检测到查验浏览器窗口已手动关闭，已清理后台会话。")
            active = None

    if active:
        active_index = active.get("invoice_index")
        active_invoice = trip.invoices[active_index] if active_index is not None and active_index < len(trip.invoices) else None
        with st.container(border=True):
            st.info("查验浏览器已打开。请在浏览器中完成验证码/人机验证和查验，然后手动把结果页面保存为 PDF 到“发票验真结果”文件夹。")
            if active_invoice:
                st.write(f"当前发票：{active_invoice.file_name}")
                st.write(f"发票号码：{active_invoice.invoice_number or '需人工补充'}")
                st.write(f"开票日期：{active_invoice.issue_date or '需人工补充'}")
                st.write(f"金额/价税合计：{active_invoice.total_amount if active_invoice.total_amount is not None else '需人工补充'}")
            cols = st.columns(2)
            if cols[0].button("我已手动保存 PDF，标记已查验并关闭浏览器", type="primary", use_container_width=True):
                session = active["session"]
                session.close()
                if active_invoice:
                    _mark_manual_verification_saved(trip, active_invoice)
                st.session_state.pop("active_tax_session", None)
                if active_invoice and active_invoice.verification_pdf_path:
                    st.success(f"已匹配到手动保存的验真 PDF：{active_invoice.verification_pdf_path}")
                else:
                    st.warning("已标记为已查验，但没有在“发票验真结果”文件夹里匹配到该发票号码的 PDF。")
                st.rerun()
            if cols[1].button("只关闭浏览器", use_container_width=True):
                active["session"].close()
                st.session_state.pop("active_tax_session", None)
                st.rerun()
            st.divider()

    for idx, invoice in enumerate(trip.invoices):
        with st.container(border=True):
            cols = st.columns([3, 2, 2, 2])
            cols[0].write(f"**{invoice.file_name}**")
            cols[1].write(invoice.invoice_number or "缺少发票号码")
            cols[2].write(invoice.verification_status_label)
            st.caption(
                f"开票日期：{invoice.issue_date or '需人工补充'} ｜ "
                f"金额/价税合计：{invoice.total_amount if invoice.total_amount is not None else '需人工补充'}"
            )
            if invoice.verification_pdf_path:
                cols[3].write(invoice.verification_pdf_path)
            elif invoice.document_type == "support_doc":
                cols[3].write("辅助资料无需验真")
            else:
                if cols[3].button("标记已查验", key=f"mark_{idx}"):
                    _mark_manual_verification_saved(trip, invoice)
                    st.rerun()

            if invoice.document_type == "invoice" and invoice.verification_status_label != VerificationStatus.VERIFIED.value:
                if st.button("打开查验（半自动）", key=f"verify_{idx}"):
                    if st.session_state.get("active_tax_session"):
                        st.warning("已经有一个查验浏览器打开，请先保存或关闭当前查验。")
                    else:
                        try:
                            session = verifier.open_invoice(invoice)
                            st.session_state.active_tax_session = {"invoice_index": idx, "session": session}
                            st.success("查验浏览器已打开。请在浏览器里完成查验并手动保存 PDF 后，回到本页面点击“我已手动保存 PDF”。")
                            st.rerun()
                        except Exception as exc:
                            invoice.verification_status = VerificationStatus.FAILED
                            invoice.add_remark(f"打开查验平台失败：{exc}")
                            st.error(f"打开查验平台失败：{exc}")

    if st.button("返回识别结果"):
        st.session_state.page = "发票识别结果"
        st.rerun()


def render_export() -> None:
    st.title("导出")
    trip = st.session_state.get("trip")
    if not trip:
        st.info("还没有生成报销结果。")
        return

    if trip.generated_excel_path:
        st.success("报销表已生成")
        st.code(trip.generated_excel_path)
    if trip.exported_zip_path:
        st.success("报销包已生成")
        st.code(trip.exported_zip_path)
    if trip.output_dir:
        st.write("输出文件夹")
        st.code(trip.output_dir)


def render_history() -> None:
    st.title("历史报销")
    rows = ReimbursementDB(DB_PATH).list_trips()
    if not rows:
        st.info("暂无历史记录。")
        return
    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _invoice_editor_key(state=None) -> str:
    state = st.session_state if state is None else state
    return f"invoice_editor_{state.get('invoice_editor_version', 0)}"


def _bump_invoice_editor_key(state=None) -> None:
    state = st.session_state if state is None else state
    state["invoice_editor_version"] = state.get("invoice_editor_version", 0) + 1


def _choose_local_folder() -> str:
    if platform.system() == "Darwin":
        return _choose_local_folder_macos()
    return _choose_local_folder_tk()


def _choose_local_folder_macos() -> str:
    script = (
        'set selectedFolder to choose folder with prompt "请选择出差报销文件夹"\n'
        "POSIX path of selectedFolder"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return _choose_local_folder_tk()
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _choose_local_folder_tk() -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(title="请选择出差报销文件夹")
        root.destroy()
        return folder or ""
    except Exception:
        return ""


def _mark_manual_verification_saved(trip, invoice) -> None:
    verification_map = parse_existing_verification_pdfs(trip.root)
    if invoice.invoice_number and invoice.invoice_number in verification_map:
        invoice.mark_verified(verification_map[invoice.invoice_number])
        return
    invoice.verification_status = VerificationStatus.VERIFIED
    invoice.verification_pdf_path = None
    invoice.add_remark("已由用户手动标记验真，未自动匹配到验真 PDF")


if __name__ == "__main__":
    main()
