from __future__ import annotations

import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from models.invoice import Invoice, VerificationResult, VerificationStatus
from tax_verify.result_pdf import build_result_filename, save_result_pdf

os.environ.setdefault(
    "PLAYWRIGHT_BROWSERS_PATH",
    str(Path(__file__).resolve().parents[1] / ".playwright-browsers"),
)

PLAYWRIGHT_BROWSER_MARKER = str(Path(__file__).resolve().parents[1] / ".playwright-browsers" / "chromium")


class BrowserTaxVerifier:
    def __init__(
        self,
        selectors_path: str | Path | None = None,
        headless: bool = False,
        output_dir: str | Path | None = None,
    ):
        self.headless = headless
        self.output_dir = Path(output_dir) if output_dir else Path("发票验真结果")
        self.config = _load_selectors(selectors_path)

    def open_invoice(self, invoice: Invoice) -> "TaxVerificationSession":
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError(f"Playwright 未安装或不可用：{exc}") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        pdf_name = build_result_filename(invoice.invoice_number, invoice.total_amount, invoice.issue_date)
        pdf_path = self.output_dir / pdf_name
        screenshot_path = pdf_path.with_suffix(".png")

        playwright = sync_playwright().start()
        browser = None
        context = None
        try:
            browser = self._launch_browser(playwright)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.goto(self.config.get("url", "https://inv-veri.chinatax.gov.cn"), wait_until="domcontentloaded")
            self._fill_known_fields(page, invoice)
            return TaxVerificationSession(
                verifier=self,
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
                invoice=invoice,
                pdf_path=pdf_path,
                screenshot_path=screenshot_path,
            )
        except Exception:
            if context:
                context.close()
            if browser:
                browser.close()
            playwright.stop()
            raise

    def verify_invoice(
        self,
        invoice: Invoice,
        pause_callback: Callable[[Invoice], None] | None = None,
        wait_for_user: bool = True,
    ) -> VerificationResult:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            return VerificationResult(
                success=False,
                status=VerificationStatus.MANUAL_REQUIRED,
                error_message=f"Playwright 未安装或不可用：{exc}",
            )

        session: TaxVerificationSession | None = None
        try:
            session = self.open_invoice(invoice)
            if pause_callback:
                pause_callback(invoice)
            elif wait_for_user:
                input("请在打开的浏览器中完成验证码/查验，查验结果出现后回到这里按 Enter 继续...")
            return session.finish(close_browser=True)
        except Exception as exc:
            if session:
                return session.fail(str(exc))
            return VerificationResult(
                success=False,
                status=VerificationStatus.FAILED,
                error_message=str(exc),
                verified_at=datetime.now().isoformat(timespec="seconds"),
            )

    def _fill_known_fields(self, page, invoice: Invoice) -> None:
        pending = {
            field: str(value)
            for field, value in _verification_values(invoice).items()
            if value not in (None, "")
        }
        if not pending:
            return

        timeout_ms = int(self.config.get("fill_timeout_ms", 10000))
        retry_interval_ms = int(self.config.get("fill_retry_interval_ms", 300))
        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        while pending:
            filled_fields = []
            for field, value in pending.items():
                for selector in _field_selectors(self.config, field):
                    if _try_fill_anywhere(page, selector, value):
                        filled_fields.append(field)
                        break
            for field in filled_fields:
                pending.pop(field, None)
            if not pending or time.monotonic() >= deadline:
                break
            try:
                page.wait_for_timeout(retry_interval_ms)
            except Exception:
                time.sleep(retry_interval_ms / 1000)

    def _looks_successful(self, text: str) -> bool:
        if not text:
            return False
        result_config = self.config.get("result", {})
        if any(keyword in text for keyword in result_config.get("failure_keywords", [])):
            return False
        return any(keyword in text for keyword in result_config.get("success_keywords", []))

    def _launch_browser(self, playwright):
        launch_options = {
            "headless": self.headless,
            "args": [
                "--no-proxy-server",
                "--proxy-server=direct://",
                "--proxy-bypass-list=*",
            ],
        }
        try:
            return playwright.chromium.launch(channel="chrome", **launch_options)
        except Exception as exc:
            try:
                return playwright.chromium.launch(**launch_options)
            except Exception as bundled_exc:
                raise RuntimeError(
                    "没有成功调用本机 Google Chrome，且 Playwright 自带浏览器也不可用。"
                    "请确认 Mac 已安装 Google Chrome；或在网络正常时运行 playwright install chromium。"
                ) from bundled_exc


class TaxVerificationSession:
    def __init__(
        self,
        verifier: BrowserTaxVerifier,
        playwright,
        browser,
        context,
        page,
        invoice: Invoice,
        pdf_path: Path,
        screenshot_path: Path,
    ):
        self.verifier = verifier
        self.playwright = playwright
        self.browser = browser
        self.context = context
        self.page = page
        self.invoice = invoice
        self.pdf_path = pdf_path
        self.screenshot_path = screenshot_path
        self.closed = False

    def finish(self, close_browser: bool = True) -> VerificationResult:
        try:
            result_text = ""
            try:
                self.page.wait_for_load_state("networkidle", timeout=5000)
                result_text = self.page.locator("body").inner_text(timeout=5000)
            except Exception:
                result_text = ""

            pdf, screenshot = save_result_pdf(self.page, self.pdf_path, self.screenshot_path)
            success = self.verifier._looks_successful(result_text)
            status = VerificationStatus.VERIFIED if success and pdf else VerificationStatus.MANUAL_REQUIRED
            return VerificationResult(
                success=bool(success and pdf),
                status=status,
                error_message=None if success else "已保存页面，但未能自动判断查验成功，请人工确认结果内容",
                pdf_path=str(pdf) if pdf else None,
                screenshot_path=str(screenshot) if screenshot else None,
                verified_at=datetime.now().isoformat(timespec="seconds"),
            )
        except Exception as exc:
            return self.fail(str(exc), close_browser=False)
        finally:
            if close_browser:
                self.close()

    def fail(self, message: str, close_browser: bool = True) -> VerificationResult:
        try:
            self.page.screenshot(path=str(self.screenshot_path), full_page=True)
        except Exception:
            pass
        if close_browser:
            self.close()
        return VerificationResult(
            success=False,
            status=VerificationStatus.FAILED,
            error_message=message,
            screenshot_path=str(self.screenshot_path) if self.screenshot_path.exists() else None,
            verified_at=datetime.now().isoformat(timespec="seconds"),
        )

    def close(self) -> None:
        if self.closed:
            return
        try:
            try:
                if self.context:
                    self.context.close()
            except Exception:
                pass
            try:
                if self.browser:
                    self.browser.close()
            except Exception:
                pass
        finally:
            try:
                self.playwright.stop()
            except Exception:
                pass
            self.closed = True

    def browser_window_closed(self) -> bool:
        if self.closed:
            return True
        try:
            return self.page.is_closed()
        except Exception:
            return True


def _try_fill(page, selector: str, value: str) -> bool:
    try:
        matches = page.locator(selector)
        count = matches.count()
        if count == 0:
            return False
        for index in range(count):
            locator = matches.nth(index)
            if _fill_locator(locator, value):
                return True
        return False
    except Exception:
        return False


def _fill_locator(locator, value: str) -> bool:
    try:
        locator.fill(value, timeout=1000)
    except Exception:
        pass
    if _locator_has_value(locator, value):
        _blur_locator(locator)
        return True

    try:
        result = locator.evaluate(
            """(element, fieldValue) => {
                element.removeAttribute('readonly');
                element.removeAttribute('disabled');
                element.focus();
                const prototype = Object.getPrototypeOf(element);
                const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
                if (descriptor && descriptor.set) {
                    descriptor.set.call(element, fieldValue);
                } else {
                    element.value = fieldValue;
                }
                element.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    inputType: 'insertText',
                    data: fieldValue,
                }));
                element.dispatchEvent(new Event('change', { bubbles: true }));
                element.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
                element.blur();
                return element.value;
            }""",
            value,
        )
        if str(result) == value or _locator_has_value(locator, value):
            _blur_locator(locator)
            return True
    except Exception:
        pass
    return False


def _locator_has_value(locator, value: str) -> bool:
    try:
        return str(locator.evaluate("(element) => element.value")) == value
    except Exception:
        return False


def _blur_locator(locator) -> None:
    try:
        locator.blur(timeout=500)
    except Exception:
        pass


def _try_fill_anywhere(page, selector: str, value: str) -> bool:
    if _try_fill(page, selector, value):
        return True
    main_frame = getattr(page, "main_frame", None)
    for frame in getattr(page, "frames", []):
        if main_frame is not None and frame == main_frame:
            continue
        if _try_fill(frame, selector, value):
            return True
    return False


def _verification_values(invoice: Invoice) -> dict[str, str | None]:
    return {
        "invoice_number": invoice.invoice_number,
        "issue_date": _format_issue_date(invoice.issue_date),
        "total_amount": _format_amount(_verification_amount(invoice)),
    }


def _verification_amount(invoice: Invoice) -> float | int | str | None:
    return invoice.total_amount if invoice.total_amount not in (None, "") else invoice.amount


def _format_amount(amount: float | int | str | None) -> str | None:
    if amount in (None, ""):
        return None
    try:
        return f"{float(amount):.2f}"
    except (TypeError, ValueError):
        return str(amount)


def _format_issue_date(issue_date: str | None) -> str | None:
    if not issue_date:
        return None
    digits = "".join(ch for ch in str(issue_date) if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else str(issue_date)


def _field_selectors(config: dict, field: str) -> list[str]:
    configured = list(config.get("common", {}).get(field, []))
    fallbacks = {
        "invoice_number": [
            ".t-form-item__fphm input",
            "#fphm",
            "input#fphm",
            "input[name='fphm']",
            "input[placeholder*='发票号码']",
            "xpath=//*[contains(normalize-space(.), '发票号码')]/following::input[1]",
        ],
        "issue_date": [
            ".t-form-item__kprq input",
            "#kprq",
            "input#kprq",
            "input[name='kprq']",
            "input[placeholder='YYYYMMDD']",
            "input[placeholder*='开票日期']",
            "xpath=//*[contains(normalize-space(.), '开票日期')]/following::input[1]",
        ],
        "total_amount": [
            ".t-form-item__kpje input",
            "#kjje",
            "input#kjje",
            "input[name='kjje']",
            "input[placeholder*='价税合计']",
            "input[placeholder*='金额']",
            "xpath=//*[contains(normalize-space(.), '开票金额')]/following::input[1]",
            "xpath=//*[contains(normalize-space(.), '开具金额')]/following::input[1]",
            "xpath=//*[contains(normalize-space(.), '金额') and contains(normalize-space(.), '不含税')]/following::input[1]",
            "xpath=//*[contains(normalize-space(.), '价税合计')]/following::input[1]",
        ],
    }
    selectors: list[str] = []
    for selector in configured + fallbacks.get(field, []):
        if selector not in selectors:
            selectors.append(selector)
    return selectors


def _load_selectors(selectors_path: str | Path | None) -> dict:
    default = {
        "url": "https://inv-veri.chinatax.gov.cn",
        "common": {
            "invoice_number": ["input[placeholder*='发票号码']", "#fphm"],
            "invoice_code": ["input[placeholder*='发票代码']", "#fpdm"],
            "issue_date": ["input[placeholder*='开票日期']", "#kprq"],
            "total_amount": ["input[placeholder*='价税合计']", "input[placeholder*='金额']", "#kjje"],
        },
        "result": {
            "success_keywords": ["查验次数", "发票号码", "价税合计", "电子发票"],
            "failure_keywords": ["查验失败", "不一致"],
        },
    }
    if not selectors_path:
        return default
    path = Path(selectors_path)
    if not path.exists():
        return default
    try:
        import yaml

        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return _deep_merge(default, loaded)
    except Exception:
        return default


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def cleanup_leftover_testing_browsers(force: bool = False) -> int:
    """Close Chrome for Testing instances launched from this project only."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", PLAYWRIGHT_BROWSER_MARKER],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return 0

    pids = []
    current_pid = os.getpid()
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != current_pid:
            pids.append(pid)

    signal_to_send = signal.SIGKILL if force else signal.SIGTERM
    closed = 0
    for pid in pids:
        try:
            os.kill(pid, signal_to_send)
            closed += 1
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
    return closed
