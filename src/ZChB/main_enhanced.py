from patchright.async_api import Page
from loguru import logger
import json
import os
import datetime
import asyncio
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


# =========================================================
# CONFIG - CHANGE THESE VALUES
# =========================================================

INPUT_EXCEL_PATH = "/Users/darabon/Desktop/9from311.xlsx"
INN_COLUMN_NAME = "debtor_inn"
OUTPUT_COLUMN_NAME = "ceos_founders_beneficiaries"
OUTPUT_FILE_BASE = "companies_result"   # final file => companies_result_ceos_founders.xlsx

HEADLESS = True
BROWSER_DATADIR = "datadir"

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
            logger.info("Modal close button found. Forcing JavaScript click to close modal.")
            await close_button.first.evaluate("el => el.click()")
            await modal_container.wait_for(state="hidden", timeout=5000)
            logger.info("Modal has been successfully closed.")
        else:
            logger.warning("Modal close button not found, falling back to pressing 'Escape'.")
            await page.keyboard.press("Escape")
            await modal_container.wait_for(state="hidden", timeout=5000)

    except Exception as e:
        logger.error(f"An error occurred while closing the modal: {e}. Attempting to press 'Escape' as a final fallback.")
        await page.keyboard.press("Escape")


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
        await page.close()


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
        result["ceos"] = await extract_ceos(page)
        await close_modal(page)
        logger.info(f"CEO modal processed and closed for INN: {inn}")
    else:
        logger.warning(f"Could not open or find the CEO modal for INN: {inn}")

    if await click_founders(page):
        founders = await extract_founders(page)
        result["founders"] = founders
        result["founders"]["formatted"] = format_founders_data(founders)
        await close_modal(page)
        logger.info(f"Founders modal processed and closed for INN: {inn}")
    else:
        logger.warning(f"Could not open or find the founders modal for INN: {inn}")

    if await click_beneficiaries(page):
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
        await page.close()
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

    inns = (
        df[INN_COLUMN_NAME]
        .dropna()
        .astype(str)
        .str.strip()
        .tolist()
    )

    if not inns:
        raise ValueError(f"No INN values found in column '{INN_COLUMN_NAME}'.")

    logger.info(f"Loaded {len(inns)} INN values from Excel.")

    browser_manager = Browser(headless=HEADLESS, datadir=BROWSER_DATADIR)
    all_results = []

    try:
        await browser_manager.launch()
        await login_once(browser_manager)

        for idx, inn in enumerate(inns, start=1):
            logger.info(f"--- Processing {idx}/{len(inns)} | INN: {inn} ---")
            result = await run_single_inn(browser_manager, inn)
            all_results.append(result)

        # Map results back to the dataframe by INN
        results_by_inn = {
            str(item.get("inn", "")).strip(): item
            for item in all_results
        }

        def serialize_result(inn_value):
            key = str(inn_value).strip()
            data = results_by_inn.get(key, {})
            return json.dumps(data, ensure_ascii=False)

        df[OUTPUT_COLUMN_NAME] = df[INN_COLUMN_NAME].apply(serialize_result)

        excel_output_path, json_output_path = build_output_paths()

        df.to_excel(excel_output_path, index=False)

        with open(json_output_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=4)

        logger.success(f"Excel output saved to: {excel_output_path}")
        logger.success(f"JSON output saved to: {json_output_path}")

        print(f"Excel output: {excel_output_path}")
        print(f"JSON output: {json_output_path}")

    finally:
        await browser_manager.close()
        logger.info("Browser manager closed.")


if __name__ == "__main__":
    asyncio.run(main())