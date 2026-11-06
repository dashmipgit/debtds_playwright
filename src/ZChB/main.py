from patchright.async_api import Page
from loguru import logger
import json
import os
import datetime
import asyncio

from .flows import (
    click_ceos,
    extract_ceos,
    click_beneficiaries,
    extract_beneficiaries,
    extract_employees_by_year,
    click_founders,
    extract_founders,
    format_founders_data
)
from .login import login
from .court_debts import extract_defendant_in_progress
from ..utils import process_inn
from ..browser import Browser


async def close_modal(page: Page):
    """
    Closes the currently active modal by forcing a JavaScript click on its
    close button and waits for it to become hidden. This is the most direct
    and reliable method for this specific site.
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


async def run_test(browser: Browser, inn: str) -> dict:
    """
    Tests the CEO and Beneficiary extraction flow on zachestnyibiznes.ru.

    Args:
        browser: Browser wrapper instance.
        inn: The company's INN.

    Returns:
        A dictionary containing the extracted data.
    """
    inn = process_inn(inn)
    logger.info(f"Starting test run for INN: {inn}")

    page = await browser.context.new_page()
    logger.debug("New page created.")

    results = {
        "ceos": {},
        "beneficiaries": {},
        "defendant_in_progress": {}
    }

    try:
        if not await login(page):
            return {}

        logger.info(f"Navigating to search page for INN: {inn}")
        await page.goto(
            f"https://zachestnyibiznes.ru/search?query={inn}",
            wait_until="domcontentloaded"
        )

        company_links_locator = page.locator('a[href*="/company/ul/"]:visible')

        if await company_links_locator.count() == 0:
            logger.warning("No visible company link found on the search results page.")
            return {"message": "Не найдено данных на ЗЧБ. Нет такой компании."}

        logger.info("Company link found, navigating to company page.")
        await company_links_locator.first.click()
        await page.wait_for_load_state("domcontentloaded")
        logger.success("Successfully navigated to the company page.")

        if await click_ceos(page):
            results["ceos"] = await extract_ceos(page)
            await close_modal(page)
            logger.info("CEO modal processed and closed.")
        else:
            logger.warning("Could not open or find the CEO modal.")

        if await click_founders(page):
            results["founders"] = await extract_founders(page)
            results["founders"]["formatted"] = format_founders_data(results["founders"])
            await close_modal(page)
            logger.info("Founders modal processed and closed.")
        else:
            logger.warning("Could not open or find the founders modal.")

        if await click_beneficiaries(page):
            results["beneficiaries"] = await extract_beneficiaries(page)
            await close_modal(page)
            logger.info("Beneficiaries modal processed and closed.")
        else:
            logger.warning("Could not open or find the beneficiaries modal.")

        results["employees_by_year"] = await extract_employees_by_year(page)
        results["defendant_in_progress"] = await extract_defendant_in_progress(page)

        return results

    except Exception as e:
        logger.exception(f"An error occurred during the test run for INN {inn}: {e}")
        os.makedirs("data/screenshots", exist_ok=True)
        await page.screenshot(
            path=f"data/screenshots/error_{inn}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        )
        return {"error": str(e)}

    finally:
        await page.close()
        logger.debug("Page closed.")


async def main():
    test_inn = "5047276074"

    log_file = f"data/logs/test_runs_{datetime.datetime.now().strftime('%Y-%m-%d')}.log"
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    logger.add(log_file, rotation="1 day", level="INFO")

    logger.info(f"--- Starting new test session for INN: {test_inn} ---")

    browser_manager = Browser(headless=True, datadir="datadir")

    try:
        await browser_manager.launch()
        final_data = await run_test(browser_manager, test_inn)

        output_filename = f"data/output/{test_inn}_test_data.json"
        os.makedirs(os.path.dirname(output_filename), exist_ok=True)
        with open(output_filename, "w", encoding="utf-8") as f:
            json.dump(final_data, f, ensure_ascii=False, indent=4)

        logger.success(f"Test run complete. Data saved to {output_filename}")
        print(json.dumps(final_data, ensure_ascii=False, indent=4))

    except Exception as e:
        logger.exception(f"A critical error occurred in the main execution block: {e}")
        raise

    finally:
        await browser_manager.close()


if __name__ == "__main__":
    asyncio.run(main())