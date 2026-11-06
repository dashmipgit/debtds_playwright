from patchright.async_api import Page
from loguru import logger
import json
import os
import datetime
import asyncio
import tempfile
import pandas as pd

from .flows import (
    click_ceos,
    extract_ceos,
    click_beneficiaries,
    extract_beneficiaries,
    click_founders,
    extract_founders,
    format_founders_data,
)
from .login import login
from ..utils import process_inn
from ..browser import Browser
from dotenv import load_dotenv

# =========================================================
# CONFIG - CHANGE THESE VALUES
# =========================================================

INPUT_EXCEL_PATH = "/Users/darabon/Desktop/april/malcev1.xlsx"
INN_COLUMN_NAME = "ИНН Должника:"
OUTPUT_COLUMN_NAME = "ceos_founders_beneficiaries"
OUTPUT_FILE_BASE = "malcev_ceos_founders_beneficiaries1"

HEADLESS = True
BROWSER_DATADIR = "datadir"

# save after each processed unique INN
SAVE_EVERY_N = 1
# =========================================================
load_dotenv()
# =========================================================


async def close_modal(page: Page):
    """
    Closes the currently active modal by forcing a JavaScript click on its
    close button and waits for it to become hidden.
    """
    try:
        modal_container = page.locator("#modal-template .modal-content:visible")

        if await modal_container.count() == 0:
            logger.warning("close_modal was called, but no visible modal was found. Skipping.")
            return

        close_button = modal_container.locator('button[data-dismiss="modal"]')

        if await close_button.count() > 0:
            logger.info("Modal close button found. Forcing a JavaScript click to close modal.")
            await close_button.first.evaluate("el => el.click()")
            await modal_container.wait_for(state="hidden", timeout=5000)
            logger.info("Modal has been successfully closed.")
        else:
            logger.warning("Modal close button not found, falling back to pressing 'Escape'.")
            await page.keyboard.press("Escape")
            await modal_container.wait_for(state="hidden", timeout=5000)

    except Exception as e:
        logger.error(f"An error occurred while closing the modal: {e}. Attempting to press 'Escape' as a final fallback.")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass


async def wait_for_modal_content(
    page: Page,
    section_name: str,
    selectors: list[str],
    no_data_selectors: list[str] | None = None,
    timeout_ms: int = 10000,
    stable_rounds_needed: int = 3,
    poll_ms: int = 250,
) -> None:
    """
    Waits not only for the modal to become visible, but also for its content to
    settle. This is designed to reduce flaky partial reads when the modal body
    is rendered in several async steps.
    """
    modal = page.locator("#modal-template .modal-content:visible").first
    await modal.wait_for(state="visible", timeout=timeout_ms)

    if no_data_selectors is None:
        no_data_selectors = []

    start = asyncio.get_running_loop().time()
    prev_signature = None
    stable_rounds = 0

    async def get_signature() -> tuple:
        counts: list[int] = []
        for selector in selectors:
            try:
                counts.append(await modal.locator(selector).count())
            except Exception:
                counts.append(0)

        no_data_counts: list[int] = []
        for selector in no_data_selectors:
            try:
                no_data_counts.append(await modal.locator(selector).count())
            except Exception:
                no_data_counts.append(0)

        try:
            raw_text = (await modal.inner_text(timeout=2000)) or ""
        except Exception:
            raw_text = ""

        compact_text = " ".join(raw_text.split())
        text_len = len(compact_text)
        text_preview = compact_text[:300]

        has_data_signal = any(c > 0 for c in counts) or text_len > 80
        has_no_data_signal = any(c > 0 for c in no_data_counts)

        return (
            tuple(counts),
            tuple(no_data_counts),
            text_len,
            text_preview,
            has_data_signal,
            has_no_data_signal,
        )

    logger.info(f"Waiting for {section_name} modal content to stabilize...")

    while True:
        now = asyncio.get_running_loop().time()
        elapsed_ms = int((now - start) * 1000)
        if elapsed_ms > timeout_ms:
            logger.warning(
                f"Timed out while waiting for {section_name} modal stabilization. "
                "Proceeding with the best available DOM state."
            )
            return

        signature = await get_signature()
        counts, no_data_counts, text_len, text_preview, has_data_signal, has_no_data_signal = signature

        logger.debug(
            f"[{section_name}] modal wait poll | counts={counts} | no_data={no_data_counts} | "
            f"text_len={text_len} | stable_rounds={stable_rounds} | preview={text_preview!r}"
        )

        if has_no_data_signal:
            logger.info(f"{section_name} modal shows an explicit no-data state. Proceeding.")
            return

        if signature == prev_signature and has_data_signal:
            stable_rounds += 1
        else:
            stable_rounds = 0
            prev_signature = signature

        if has_data_signal and stable_rounds >= stable_rounds_needed:
            logger.info(
                f"{section_name} modal content stabilized: counts={counts}, text_len={text_len}."
            )
            return

        await page.wait_for_timeout(poll_ms)


async def wait_for_ceos_modal(page: Page) -> None:
    await wait_for_modal_content(
        page=page,
        section_name="CEO",
        selectors=[
            'tbody[id^="history-founder-chunk-"]',
            'td.attr-date',
            'tr td[data-th="Руководитель: "]',
            'tr td[data-th="ИНН: "]',
        ],
        no_data_selectors=[
            "text=/нет данных|не найдено|отсутств/i",
        ],
    )


async def wait_for_founders_modal(page: Page) -> None:
    await wait_for_modal_content(
        page=page,
        section_name="Founders",
        selectors=[
            'tbody[id^="history-founder-chunk-"]',
            'td.attr-date',
            'tr td[data-th*="Учред"]',
            'tr td[data-th*="ИНН"]',
        ],
        no_data_selectors=[
            "text=/нет данных|не найдено|отсутств/i",
        ],
    )


async def wait_for_beneficiaries_modal(page: Page) -> None:
    await wait_for_modal_content(
        page=page,
        section_name="Beneficiaries",
        selectors=[
            'tr td[data-th*="Бенеф"]',
            'tr td[data-th*="ФИО"]',
            'tr td[data-th*="ИНН"]',
            'tr td[data-th*="Доля"]',
            "table tbody tr",
        ],
        no_data_selectors=[
            "text=/нет данных|не найдено|отсутств/i",
        ],
    )


async def login_once(browser: Browser) -> None:
    """
    Performs login once per full dataset run.
    Session should persist in the browser context.
    """
    logger.info("Starting one-time login before processing the dataset...")
    page = await browser.context.new_page()

    try:
        ok = await login(page)
        if not ok:
            raise RuntimeError("Login failed before dataset processing.")
        logger.success("One-time login completed successfully.")
    finally:
        try:
            await page.close()
        except Exception as e:
            logger.warning(f"Failed to close login page: {e}")


async def extract_company_data(page: Page, inn: str) -> dict:
    """
    Extracts only:
      - ceos
      - founders
      - beneficiaries
    for a single INN.
    """
    inn = process_inn(inn)
    logger.info(f"Starting company extraction for INN: {inn}")

    result = {
        "inn": inn,
        "ceos": {},
        "founders": {},
        "beneficiaries": {}
    }

    await page.goto(
        f"https://zachestnyibiznes.ru/search?query={inn}",
        wait_until="domcontentloaded",
        timeout=60000
    )

    company_links_locator = page.locator('a[href*="/company/ul/"]:visible')

    if await company_links_locator.count() == 0:
        logger.warning(f"No visible company link found for INN: {inn}")
        return {
            "inn": inn,
            "message": "Не найдено данных на ЗЧБ. Нет такой компании."
        }

    logger.info(f"Company link found for INN: {inn}, navigating to company page.")
    await company_links_locator.first.click()
    await page.wait_for_load_state("domcontentloaded")
    logger.success(f"Successfully navigated to the company page for INN: {inn}")

    if await click_ceos(page):
        await wait_for_ceos_modal(page)
        result["ceos"] = await extract_ceos(page)
        await close_modal(page)
        logger.info(f"CEO modal processed and closed for INN: {inn}")
    else:
        logger.warning(f"Could not open or find the CEO modal for INN: {inn}")

    if await click_founders(page):
        await wait_for_founders_modal(page)
        founders = await extract_founders(page)
        result["founders"] = founders
        result["founders"]["formatted"] = format_founders_data(founders)
        await close_modal(page)
        logger.info(f"Founders modal processed and closed for INN: {inn}")
    else:
        logger.warning(f"Could not open or find the founders modal for INN: {inn}")

    if await click_beneficiaries(page):
        await wait_for_beneficiaries_modal(page)
        result["beneficiaries"] = await extract_beneficiaries(page)
        await close_modal(page)
        logger.info(f"Beneficiaries modal processed and closed for INN: {inn}")
    else:
        logger.warning(f"Could not open or find the beneficiaries modal for INN: {inn}")

    return result


async def run_single_inn(browser: Browser, inn: str) -> dict:
    """
    Opens a fresh page for one INN and extracts data.
    """
    page = await browser.context.new_page()
    logger.debug(f"New page created for INN: {inn}")

    try:
        return await extract_company_data(page, inn)
    except asyncio.CancelledError:
        logger.warning(f"Processing was cancelled for INN {inn}")
        raise
    except KeyboardInterrupt:
        logger.warning(f"KeyboardInterrupt during processing for INN {inn}")
        raise
    except Exception as e:
        logger.exception(f"An error occurred during processing for INN {inn}: {e}")
        os.makedirs("data/screenshots", exist_ok=True)
        try:
            await page.screenshot(
                path=f"data/screenshots/error_{inn}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            )
        except Exception:
            pass

        return {
            "inn": str(inn),
            "error": str(e)
        }
    finally:
        try:
            await page.close()
        except Exception as e:
            logger.warning(f"Failed to close page for INN {inn}: {e}")
        logger.debug(f"Page closed for INN: {inn}")


def build_output_paths() -> tuple[str, str]:
    """
    Returns:
      excel_output_path, json_output_path
    """
    os.makedirs("data/output", exist_ok=True)

    excel_name = f"{OUTPUT_FILE_BASE}_ceos_founders.xlsx"
    json_name = f"{OUTPUT_FILE_BASE}_ceos_founders.json"

    excel_output_path = os.path.join("data/output", excel_name)
    json_output_path = os.path.join("data/output", json_name)

    return excel_output_path, json_output_path


def normalize_inn(value) -> str:
    """
    Normalizes INN value to a clean string key.
    """
    if value is None:
        return ""

    s = str(value).strip()

    if not s or s.lower() == "nan":
        return ""

    try:
        s = process_inn(s)
    except Exception:
        s = s.strip()

    return str(s).strip()


def ordered_unique_inns(values: list) -> list[str]:
    """
    Returns unique normalized INNs preserving original order.
    """
    seen = set()
    result = []

    for value in values:
        inn = normalize_inn(value)
        if not inn:
            continue
        if inn in seen:
            continue
        seen.add(inn)
        result.append(inn)

    return result


def load_existing_results(json_output_path: str) -> list[dict]:
    """
    Loads previously saved results from JSON if present.
    If file is absent or broken, returns empty list.
    """
    if not os.path.exists(json_output_path):
        return []

    try:
        with open(json_output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            logger.info(f"Loaded {len(data)} existing results from JSON cache.")
            return data

        logger.warning("Existing JSON cache is not a list. Starting with empty cache.")
        return []
    except Exception as e:
        logger.warning(f"Could not load existing JSON cache: {e}. Starting with empty cache.")
        return []


def merge_results_by_inn(results: list[dict]) -> list[dict]:
    """
    Deduplicates results by INN preserving last value for each INN
    and preserving insertion order of the first appearance.
    """
    order = []
    data_by_inn = {}

    for item in results:
        if not isinstance(item, dict):
            continue

        inn = normalize_inn(item.get("inn", ""))
        if not inn:
            continue

        item = dict(item)
        item["inn"] = inn

        if inn not in data_by_inn:
            order.append(inn)

        data_by_inn[inn] = item

    return [data_by_inn[inn] for inn in order]


def build_results_map(all_results: list[dict]) -> dict[str, dict]:
    """
    Returns dict[inn] = result
    """
    result_map = {}

    for item in all_results:
        if not isinstance(item, dict):
            continue

        inn = normalize_inn(item.get("inn", ""))
        if not inn:
            continue

        normalized_item = dict(item)
        normalized_item["inn"] = inn
        result_map[inn] = normalized_item

    return result_map


def save_json_atomic(data, path: str) -> None:
    """
    Writes JSON atomically:
    1) write to temp file in the same directory
    2) flush + fsync
    3) os.replace(temp, final)

    If script crashes during write, the old JSON stays intact.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)

    fd, temp_path = tempfile.mkstemp(
        dir=os.path.dirname(path),
        prefix=".tmp_",
        suffix=".json",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def save_excel_atomic(df: pd.DataFrame, path: str) -> None:
    """
    Writes Excel through temp file + replace.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + ".tmp.xlsx"

    try:
        df.to_excel(temp_path, index=False)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def attach_results_to_dataframe(df: pd.DataFrame, all_results: list[dict]) -> pd.DataFrame:
    """
    Builds a dataframe copy with JSON serialized results in OUTPUT_COLUMN_NAME.
    Repeated INNs in source Excel will receive the same cached result.
    """
    results_by_inn = build_results_map(all_results)
    df_copy = df.copy()

    def serialize_result(inn_value):
        key = normalize_inn(inn_value)
        data = results_by_inn.get(key, {})
        return json.dumps(data, ensure_ascii=False)

    df_copy[OUTPUT_COLUMN_NAME] = df_copy[INN_COLUMN_NAME].apply(serialize_result)
    return df_copy


def save_progress(df: pd.DataFrame, all_results: list[dict]) -> tuple[str, str, list[dict]]:
    """
    Deduplicates results by INN and saves both JSON and Excel safely.
    """
    normalized_results = merge_results_by_inn(all_results)
    excel_output_path, json_output_path = build_output_paths()

    save_json_atomic(normalized_results, json_output_path)

    df_with_results = attach_results_to_dataframe(df, normalized_results)
    save_excel_atomic(df_with_results, excel_output_path)

    return excel_output_path, json_output_path, normalized_results


async def main():
    log_file = f"data/logs/main_enhanced_{datetime.datetime.now().strftime('%Y-%m-%d')}.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation="1 day", level="INFO")

    logger.info("=== Starting enhanced batch processing ===")
    logger.info(f"Input file: {INPUT_EXCEL_PATH}")
    logger.info(f"INN column: {INN_COLUMN_NAME}")
    logger.info(f"Output column: {OUTPUT_COLUMN_NAME}")

    if not os.path.exists(INPUT_EXCEL_PATH):
        raise FileNotFoundError(f"Input Excel file not found: {INPUT_EXCEL_PATH}")

    df = pd.read_excel(INPUT_EXCEL_PATH)

    if INN_COLUMN_NAME not in df.columns:
        raise ValueError(
            f"Column '{INN_COLUMN_NAME}' not found in Excel. Available columns: {list(df.columns)}"
        )

    raw_inns = (
        df[INN_COLUMN_NAME]
        .dropna()
        .tolist()
    )

    unique_inns = ordered_unique_inns(raw_inns)

    if not unique_inns:
        raise ValueError(f"No INN values found in column '{INN_COLUMN_NAME}'.")

    logger.info(f"Loaded {len(raw_inns)} raw INN values from Excel.")
    logger.info(f"Prepared {len(unique_inns)} unique normalized INN values for processing.")

    excel_output_path, json_output_path = build_output_paths()

    all_results = load_existing_results(json_output_path)
    all_results = merge_results_by_inn(all_results)

    processed_inns = set(build_results_map(all_results).keys())
    inns_to_process = [inn for inn in unique_inns if inn not in processed_inns]

    logger.info(f"Already processed unique INNs from cache: {len(processed_inns)}")
    logger.info(f"Remaining unique INNs to process: {len(inns_to_process)}")

    if not inns_to_process:
        logger.info("Nothing left to process. Rebuilding output files from existing cache.")
        excel_output_path, json_output_path, all_results = save_progress(df, all_results)
        print(f"Excel output: {excel_output_path}")
        print(f"JSON output: {json_output_path}")
        return

    browser_manager = Browser(headless=HEADLESS, datadir=BROWSER_DATADIR)

    try:
        await browser_manager.launch()
        await login_once(browser_manager)

        for idx, inn in enumerate(inns_to_process, start=1):
            logger.info(f"--- Processing remaining {idx}/{len(inns_to_process)} | INN: {inn} ---")

            result = await run_single_inn(browser_manager, inn)
            result["inn"] = normalize_inn(result.get("inn", inn))
            all_results.append(result)

            if idx % SAVE_EVERY_N == 0:
                try:
                    excel_output_path, json_output_path, all_results = save_progress(df, all_results)
                    logger.info(
                        f"Progress saved after {idx}/{len(inns_to_process)} remaining items | "
                        f"Excel: {excel_output_path} | JSON: {json_output_path}"
                    )
                except Exception as save_error:
                    logger.exception(f"Failed to save progress after INN {inn}: {save_error}")

        excel_output_path, json_output_path, all_results = save_progress(df, all_results)

        logger.success(f"Excel output saved to: {excel_output_path}")
        logger.success(f"JSON output saved to: {json_output_path}")

        print(f"Excel output: {excel_output_path}")
        print(f"JSON output: {json_output_path}")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user (KeyboardInterrupt).")
        raise

    except asyncio.CancelledError:
        logger.warning("Main task was cancelled.")
        raise

    finally:
        try:
            if all_results:
                excel_output_path, json_output_path, all_results = save_progress(df, all_results)
                logger.info(
                    f"Final progress save completed | "
                    f"Excel: {excel_output_path} | JSON: {json_output_path}"
                )
        except Exception as final_save_error:
            logger.exception(f"Final save failed: {final_save_error}")

        try:
            await browser_manager.close()
            logger.info("Browser manager closed.")
        except Exception as close_error:
            logger.exception(f"Browser manager close failed: {close_error}")


if __name__ == "__main__":
    asyncio.run(main())