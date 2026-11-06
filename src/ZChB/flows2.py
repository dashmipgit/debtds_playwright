import re
import os
from patchright.async_api import Page
from loguru import logger


async def click_beneficiaries(page: Page) -> bool:
    try:
        # 0) Desktop layout and remove cookie overlay
        await page.set_viewport_size({"width": 1400, "height": 900})
        try:
            await page.locator('#cookie-accept-button').click(timeout=1000)
        except Exception:
            pass  # banner might not be there

        # 1) Scroll the section into view and produce real scroll events
        header = page.locator("text=Бенефициары (Выгодоприобретатели)")
        # If header not yet attached, wheel-scroll down until it appears
        for _ in range(20):
            if await header.count() > 0:
                break
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(100)

        if await header.count() == 0:
            # As a fallback, go near bottom to force more lazy loads
            for _ in range(10):
                await page.mouse.wheel(0, 1200)
                await page.wait_for_timeout(100)

        # Ensure it’s within viewport
        if await header.count():
            await header.first.scroll_into_view_if_needed(timeout=3000)

        # 2) Wait a beat for their scroll-handler to issue Ajax
        await page.wait_for_timeout(300)

        # 3) Try to find the link; allow wording variations
        section = page.locator('#benefic_tree, .ajax-content[data-content*="/ajax/benefic-tree"]')
        link = section.locator("a:has-text('Показать всех')")
        if await link.count() == 0:
            link = section.locator("a").filter(has_text=re.compile(r"Показать\s+все(х)?", re.I))

        # Give the lazy loader a few more scroll nudges if needed
        for _ in range(6):
            if await link.count() > 0:
                break
            await page.mouse.wheel(0, 600)
            await page.evaluate("window.dispatchEvent(new Event('scroll'))")
            await page.wait_for_timeout(150)

        # 4) If still not there, force-inject via fetch (awaited)
        if await link.count() == 0:
            await page.evaluate("""
                (async () => {
                  const el = document.querySelector('#benefic_tree, .ajax-content[data-content*="/ajax/benefic-tree"]');
                  if (!el) return;
                  const url = el.getAttribute('data-content');
                  if (!url) return;
                  const resp = await fetch(url, { credentials: 'include' });
                  const html = await resp.text();
                  el.innerHTML = html;
                })();
            """)
            await page.wait_for_selector("#benefic_tree a:has-text('Показать всех'), .ajax-content[data-content*='/ajax/benefic-tree'] a:has-text('Показать всех')", timeout=8000)

            # Refresh link locator after injection
            link = section.locator("a:has-text('Показать всех')")

        if await link.count() == 0:
            return False  # not loaded

        # 5) Click link (JS click fallback in case something overlays)
        try:
            await link.first.click(timeout=5000)
        except TimeoutError:
            await link.first.evaluate("el => el.click()")

        # 6) Wait for modal (either normal or premium)
        await page.wait_for_selector("#modal-template .modal-title", timeout=5000)

        if await page.locator("#modal-template .modal-title:has-text('Бенефициары')").count() > 0:
            return True
        if await page.locator("#modal-template .modal-title:has-text('доступны в тарифах')").count() > 0:
            return False  # premium-locked

        return True

    except TimeoutError:
        return False
    except Exception:
        return False


async def click_ceos(page: Page) -> bool:
    """
    Debug version:
    - clicks CEO history link
    - waits for modal
    - logs row counts before/after extra wait
    - saves screenshot and modal html snapshot
    """
    link_locator = page.locator('a[data-title*="История изменений руководителей"]')

    logger.info("=== DEBUG CEO CLICK START ===")
    logger.info(f"CEO link count: {await link_locator.count()}")

    if await link_locator.count() == 0:
        logger.warning("The 'CEO History' link was not found on the page.")
        return False

    try:
        try:
            link_text = await link_locator.first.text_content()
        except Exception:
            link_text = "<unavailable>"
        logger.info(f"CEO link text: {link_text}")

        try:
            link_title = await link_locator.first.get_attribute("data-title")
        except Exception:
            link_title = "<unavailable>"
        logger.info(f"CEO link data-title: {link_title}")

        logger.info("Clicking the 'CEO History' link...")
        await link_locator.first.click()

        modal_locator = page.locator(
            "div.modal-content:has(div.modal-title:has-text('История изменений руководителей'))"
        )
        await modal_locator.wait_for(state="visible", timeout=5000)

        logger.success("CEO history modal became visible.")

        # Immediate diagnostics
        rows_now = modal_locator.locator("tr:has(td)")
        tbodies_now = modal_locator.locator("table tbody")
        founder_chunks_now = modal_locator.locator("tbody[id^='history-founder-chunk-']")

        logger.info(f"[CEO DEBUG] rows immediately after open: {await rows_now.count()}")
        logger.info(f"[CEO DEBUG] all tbody immediately after open: {await tbodies_now.count()}")
        logger.info(f"[CEO DEBUG] founder-style chunks immediately after open: {await founder_chunks_now.count()}")

        # Extra wait for ajax/lazy render
        await page.wait_for_timeout(2500)

        rows_later = modal_locator.locator("tr:has(td)")
        tbodies_later = modal_locator.locator("table tbody")
        founder_chunks_later = modal_locator.locator("tbody[id^='history-founder-chunk-']")

        logger.info(f"[CEO DEBUG] rows after 2.5s: {await rows_later.count()}")
        logger.info(f"[CEO DEBUG] all tbody after 2.5s: {await tbodies_later.count()}")
        logger.info(f"[CEO DEBUG] founder-style chunks after 2.5s: {await founder_chunks_later.count()}")

        # Save debug artifacts
        os.makedirs("data/output", exist_ok=True)
        os.makedirs("data/screenshots", exist_ok=True)

        try:
            modal_html = await modal_locator.inner_html()
            with open("data/output/ceo_modal_debug_after_click.html", "w", encoding="utf-8") as f:
                f.write(modal_html)
            logger.info("Saved modal HTML to data/output/ceo_modal_debug_after_click.html")

            logger.info(f"[CEO DEBUG] contains 'Хиле': {'Хиле' in modal_html}")
            logger.info(f"[CEO DEBUG] contains 'Басенцян': {'Басенцян' in modal_html}")
            logger.info(f"[CEO DEBUG] contains 'Показать всех': {'Показать всех' in modal_html}")
        except Exception as e:
            logger.error(f"Failed to save CEO modal HTML after click: {e}")

        try:
            await page.screenshot(path="data/screenshots/ceo_modal_debug_after_click.png", full_page=True)
            logger.info("Saved screenshot to data/screenshots/ceo_modal_debug_after_click.png")
        except Exception as e:
            logger.error(f"Failed to save CEO screenshot after click: {e}")

        logger.info("=== DEBUG CEO CLICK END ===")
        return True

    except TimeoutError:
        logger.error("Timed out waiting for the CEO history modal to appear after clicking the link.")
        return False
    except Exception as e:
        logger.error(f"An error occurred while trying to open the CEO history modal: {e}")
        return False


async def click_founders(page: Page) -> bool:
    """
    Finds and clicks the link to open the 'История изменений учредителей' modal.

    It waits for the modal to become visible after the click.

    Args:
        page: The Playwright page object.

    Returns:
        True if the link was clicked and the modal appeared, False otherwise.
    """
    # Be flexible about the company name and quotes; just match the invariant part.
    link_locator = page.locator('a[data-title*="История изменений учредителей"]')

    if await link_locator.count() == 0:
        logger.warning("The 'Founders History' link was not found on the page.")
        return False

    try:
        logger.info("Clicking the 'Founders History' link...")
        await link_locator.first.click()

        # Wait for the modal title that contains the invariant text.
        modal_title_locator = page.locator(
            "div.modal-title:has-text('История изменений учредителей')"
        )
        await modal_title_locator.wait_for(state="visible", timeout=5000)

        logger.success("Successfully clicked the link and the Founders history modal is visible.")
        return True
    except TimeoutError:
        logger.error("Timed out waiting for the Founders history modal to appear after clicking the link.")
        return False
    except Exception as e:
        logger.error(f"An error occurred while trying to open the Founders history modal: {e}")
        return False


async def extract_beneficiaries(page: Page) -> dict:
    """
    Finds the 'Бенефициары' (Beneficiaries) modal on the page and extracts data from its table.

    This function specifically targets the modal by its title, making the selector robust.
    If the modal or table is not found, it returns an empty dictionary.

    Args:
        page: The Playwright page object.

    Returns:
        A dictionary of beneficiaries, keyed by their row number.
        Example: {"1": {"фио": "Иванов Иван", "связь": "Прямая", "инн": "123...", "доля": "100%"}}
    """
    beneficiaries = {}

    # Locate the modal by finding the header that contains the exact text "Бенефициары"
    # and then navigate up to the main modal content container.
    modal_locator = page.locator("div.modal-content:has(div.modal-title:text-is('Бенефициары'))")

    if await modal_locator.count() == 0:
        logger.info("Beneficiaries modal not found on the page.")
        return {}

    # Find all data rows (tr) in the table, skipping the header row (th)
    rows = await modal_locator.locator("table.founders-table tbody tr:has(td)").all()

    if not rows:
        logger.warning("Beneficiaries table found, but it contains no data rows.")
        return {}

    for row in rows:
        cells = await row.locator("td").all()
        if len(cells) >= 5:
            try:
                row_num = (await cells[0].text_content() or "").strip()
                fio = (await cells[1].locator("a").first.text_content() or "").strip()
                svyaz = (await cells[2].text_content() or "").strip()
                inn = (await cells[3].text_content() or "").strip()
                dolya = (await cells[4].text_content() or "").strip()

                if row_num:
                    beneficiaries[row_num] = {
                        "фио": fio,
                        "связь": svyaz,
                        "инн": inn,
                        "доля": dolya
                    }
            except Exception as e:
                logger.error(f"Could not parse a beneficiary row. HTML: {await row.inner_html()}. Error: {e}")

    logger.info(f"Extracted {len(beneficiaries)} beneficiaries.")
    return beneficiaries


async def extract_ceos(page: Page) -> dict:
    """
    Debug version:
    - inspects modal html/text
    - compares narrow selector vs broad selector
    - logs every parsed row
    - logs skipped rows
    """
    ceos_by_date = {}

    logger.info("=== DEBUG CEO EXTRACT START ===")

    modal_locator = page.locator(
        "div.modal-content:has(div.modal-title:has-text('История изменений руководителей'))"
    )
    try:
        await modal_locator.wait_for(state="visible", timeout=5000)
        await page.wait_for_timeout(2000)
    except Exception:
        logger.info("CEO history modal not found on the page.")
        return {}

    os.makedirs("data/output", exist_ok=True)

    # Save raw HTML/text for offline inspection
    try:
        modal_html = await modal_locator.inner_html()
        with open("data/output/ceo_modal_debug_extract.html", "w", encoding="utf-8") as f:
            f.write(modal_html)
        logger.info("Saved modal HTML to data/output/ceo_modal_debug_extract.html")
        logger.info(f"[CEO DEBUG] html contains 'Хиле': {'Хиле' in modal_html}")
        logger.info(f"[CEO DEBUG] html contains 'Басенцян': {'Басенцян' in modal_html}")
    except Exception as e:
        logger.error(f"Failed to save CEO modal HTML during extract: {e}")
        modal_html = ""

    try:
        modal_text = " ".join(((await modal_locator.text_content()) or "").split())
        logger.info(f"[CEO DEBUG] text contains 'Хиле': {'Хиле' in modal_text}")
        logger.info(f"[CEO DEBUG] text contains 'Басенцян': {'Басенцян' in modal_text}")
        logger.info(f"[CEO DEBUG] modal text preview: {modal_text[:3000]}")
    except Exception as e:
        logger.error(f"Failed to read CEO modal text: {e}")

    # Compare selectors
    narrow_chunks = modal_locator.locator("tbody[id^='history-founder-chunk-']")
    all_tbodies = modal_locator.locator("table tbody")
    all_rows = modal_locator.locator("table tr:has(td)")
    attr_dates = modal_locator.locator("td.attr-date")

    logger.info(f"[CEO DEBUG] narrow founder-style chunks count: {await narrow_chunks.count()}")
    logger.info(f"[CEO DEBUG] all tbody count: {await all_tbodies.count()}")
    logger.info(f"[CEO DEBUG] all table rows with td count: {await all_rows.count()}")
    logger.info(f"[CEO DEBUG] td.attr-date count: {await attr_dates.count()}")

    # First pass: old narrow logic, but with detailed logs
    narrow_count = await narrow_chunks.count()
    logger.info(f"[CEO DEBUG] starting narrow pass over {narrow_count} chunks")

    for i in range(narrow_count):
        chunk = narrow_chunks.nth(i)

        try:
            chunk_html = await chunk.inner_html()
            logger.info(f"[CEO DEBUG][NARROW] chunk {i} html preview: {chunk_html[:2000]}")
        except Exception as e:
            logger.error(f"[CEO DEBUG][NARROW] failed to get chunk {i} html: {e}")

        date_cell = chunk.locator("td.attr-date")
        logger.info(f"[CEO DEBUG][NARROW] chunk {i} attr-date count: {await date_cell.count()}")

        if await date_cell.count() == 0:
            logger.warning(f"[CEO DEBUG][NARROW] chunk {i} skipped: no td.attr-date")
            continue

        date_anchor_locator = date_cell.locator("a[href*='/ordering?date=']")
        logger.info(f"[CEO DEBUG][NARROW] chunk {i} date anchors: {await date_anchor_locator.count()}")

        if await date_anchor_locator.count() == 0:
            logger.warning(f"[CEO DEBUG][NARROW] chunk {i} skipped: no date anchor")
            continue

        date_str = (await date_anchor_locator.first.text_content() or "").strip()
        logger.info(f"[CEO DEBUG][NARROW] chunk {i} date: {date_str}")

        if not date_str:
            logger.warning(f"[CEO DEBUG][NARROW] chunk {i} skipped: empty date text")
            continue

        if date_str not in ceos_by_date:
            ceos_by_date[date_str] = []

        data_rows = chunk.locator("tr:has(td[data-th])")
        data_rows_count = await data_rows.count()
        logger.info(f"[CEO DEBUG][NARROW] chunk {i} data_rows count: {data_rows_count}")

        for j in range(data_rows_count):
            row = data_rows.nth(j)
            tds = row.locator("td")
            td_count = await tds.count()

            try:
                raw_cells = []
                for k in range(td_count):
                    raw_cells.append(" ".join(((await tds.nth(k).text_content()) or "").split()))
                logger.info(f"[CEO DEBUG][NARROW] chunk {i} row {j} td_count={td_count} raw={raw_cells}")
            except Exception as e:
                logger.error(f"[CEO DEBUG][NARROW] failed reading raw cells for chunk {i} row {j}: {e}")

            if td_count < 4:
                logger.warning(f"[CEO DEBUG][NARROW] chunk {i} row {j} skipped: td_count < 4")
                continue

            try:
                position = (await tds.nth(1).text_content() or "").strip()
                name = (await tds.nth(2).text_content() or "").strip()
                inn = (await tds.nth(3).text_content() or "").strip()

                position = " ".join(position.split())
                name = " ".join(name.split())
                inn = " ".join(inn.split())

                entry = {
                    "должность": position,
                    "руководитель": name,
                    "инн": inn
                }

                ceos_by_date[date_str].append(entry)
                logger.info(f"[CEO DEBUG][NARROW] parsed entry for {date_str}: {entry}")
            except Exception as e:
                try:
                    html_snippet = await row.inner_html()
                except Exception:
                    html_snippet = "<unavailable>"
                logger.error(f"[CEO DEBUG][NARROW] Could not parse row. HTML: {html_snippet}. Error: {e}")

    # Broad fallback diagnostics: not changing result, only logging what broad selector sees
    broad_tbodies_count = await all_tbodies.count()
    logger.info(f"[CEO DEBUG] starting broad diagnostics over {broad_tbodies_count} tbodies")

    for i in range(broad_tbodies_count):
        tbody = all_tbodies.nth(i)

        try:
            tbody_html = await tbody.inner_html()
            logger.info(f"[CEO DEBUG][BROAD] tbody {i} html preview: {tbody_html[:2000]}")
            logger.info(f"[CEO DEBUG][BROAD] tbody {i} contains 'Хиле': {'Хиле' in tbody_html}")
            logger.info(f"[CEO DEBUG][BROAD] tbody {i} contains 'Басенцян': {'Басенцян' in tbody_html}")
        except Exception as e:
            logger.error(f"[CEO DEBUG][BROAD] failed to get tbody {i} html: {e}")

        rows = tbody.locator("tr:has(td)")
        row_count = await rows.count()
        logger.info(f"[CEO DEBUG][BROAD] tbody {i} row count: {row_count}")

        for j in range(row_count):
            row = rows.nth(j)
            tds = row.locator("td")
            td_count = await tds.count()

            try:
                raw_cells = []
                for k in range(td_count):
                    raw_cells.append(" ".join(((await tds.nth(k).text_content()) or "").split()))
                logger.info(f"[CEO DEBUG][BROAD] tbody {i} row {j} td_count={td_count} raw={raw_cells}")
            except Exception as e:
                logger.error(f"[CEO DEBUG][BROAD] failed reading raw cells for tbody {i} row {j}: {e}")

    # Final summary
    logger.info(f"[CEO DEBUG] extracted dates: {list(ceos_by_date.keys())}")
    for dt, entries in ceos_by_date.items():
        logger.info(f"[CEO DEBUG] date={dt}, entries={entries}")

    logger.info(f"Extracted CEO history for {len(ceos_by_date)} dates.")
    logger.info("=== DEBUG CEO EXTRACT END ===")
    return ceos_by_date


async def extract_founders(page: Page) -> dict:
    """
    Extracts the 'История изменений учредителей' table grouped by date.

    Returns:
        {
          "12.05.2014": [
            {"учредитель": "...", "инн": "...", "доля": "...", "доля_руб": "..."},
            ...
          ],
          ...
        }
    """
    founders_by_date: dict[str, list[dict]] = {}

    # Scope to the specific modal by its title to avoid mixing with other modals.
    modal_locator = page.locator(
        "div.modal-content:has(div.modal-title:has-text('История изменений учредителей'))"
    )
    try:
        await modal_locator.wait_for(state="visible", timeout=5000)
    except Exception:
        logger.info("Founders history modal not found on the page.")
        return {}

    # Chunks are grouped per date; ids look like history-founder-chunk-DD-MM-YYYY
    date_chunks = modal_locator.locator("tbody[id^='history-founder-chunk-']")
    if await date_chunks.count() == 0:
        logger.warning("Founders history modal found, but no date chunks were located.")
        return {}

    for i in range(await date_chunks.count()):
        chunk = date_chunks.nth(i)

        # The date lives in the row with class 'attr-date' and contains an <a href="/.../ordering?date=DD.MM.YYYY">DD.MM.YYYY</a>
        date_cell = chunk.locator("td.attr-date")
        if await date_cell.count() == 0:
            continue

        date_anchor = date_cell.locator("a[href*='/ordering?date=']")
        if await date_anchor.count() == 0:
            continue

        date_str = (await date_anchor.first.text_content() or "").strip()
        if not date_str:
            continue

        founders_by_date.setdefault(date_str, [])

        # Data rows have td[data-th]; the date row does not.
        data_rows = chunk.locator("tr:has(td[data-th])")
        for j in range(await data_rows.count()):
            row = data_rows.nth(j)

            # Expected columns: # | Учредитель | ИНН | Доля | Доля (руб.)
            tds = row.locator("td")
            if await tds.count() < 5:
                # Some variants might omit a column; try to be defensive.
                try:
                    html_snippet = await row.inner_html()
                except Exception:
                    html_snippet = "<unavailable>"
                logger.warning(f"Unexpected founders row shape, skipping. HTML: {html_snippet}")
                continue

            try:
                founder = (await tds.nth(1).text_content() or "").strip()
                inn = (await tds.nth(2).text_content() or "").strip()
                share = (await tds.nth(3).text_content() or "").strip()
                share_rub = (await tds.nth(4).text_content() or "").strip()

                # Normalize whitespace/newlines
                def norm(s: str) -> str:
                    return " ".join(s.split())

                entry = {
                    "учредитель": norm(founder),
                    "инн": norm(inn),
                    "доля": norm(share),
                    "доля_руб": norm(share_rub),
                }
                founders_by_date[date_str].append(entry)
            except Exception as e:
                try:
                    html_snippet = await row.inner_html()
                except Exception:
                    html_snippet = "<unavailable>"
                logger.error(f"Could not parse a founders row. HTML: {html_snippet}. Error: {e}")

    logger.info(f"Extracted founders history for {len(founders_by_date)} dates.")
    return founders_by_date


def format_founders_data(founders_by_date: dict) -> dict:
    from datetime import datetime

    # Sort dates ascending
    dates = sorted(founders_by_date.keys(), key=lambda d: datetime.strptime(d, "%d.%m.%Y"))

    def short_name(full: str) -> str:
        if not full:
            return ""
        parts = full.split()
        if len(parts) >= 2:
            return f"{parts[0]} {parts[1][0]}."
        return full

    result = {}
    prev = {}

    for date in dates:
        current = {f["инн"]: f for f in founders_by_date[date]}
        lines = []

        # Detect leavers
        for inn, f in prev.items():
            if inn not in current:
                lines.append(f"{short_name(f['учредитель']) or inn} вышел ({f['доля']})")

        # Detect entrants + share changes
        for inn, f in current.items():
            name = short_name(f["учредитель"]) or inn
            if inn not in prev:
                lines.append(f"{name} вошёл ↑{f['доля']}")
            else:
                old_share = prev[inn]["доля"]
                if old_share != f["доля"]:
                    lines.append(f"{name} изменил долю: {old_share} → {f['доля']}")

        if lines:
            result[date] = lines

        prev = current

    return result


async def extract_employees_by_year(page) -> dict:
    """
    Extracts employee counts year by year from the div#sshr-collapse.

    Returns:
        dict like: {"2018": 14, "2019": 13, "2020": 11, "2021": 9, "2022": 8}
    """
    employees_by_year = {}

    try:
        collapse = page.locator("div#sshr-collapse")
        await collapse.wait_for(state="attached", timeout=5000)
    except Exception:
        logger.warning("Employee collapse div (#sshr-collapse) not found.")
        return {}

    year_rows = await collapse.locator("div").all()
    if not year_rows:
        logger.warning("No year rows found under #sshr-collapse.")
        return {}

    for row in year_rows:
        try:
            year_span = row.locator("span.text-gray").first
            year_text = (await year_span.text_content() or "").strip()
            year = year_text if year_text.isdigit() else None
            if not year:
                continue

            # Extract all text and filter out year & +/- info
            row_text = " ".join((await row.text_content() or "").split())
            # Example row_text: "2022 8 -1 чел."
            parts = row_text.split()
            # first part = year, second part = employee count
            count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

            if count is not None:
                employees_by_year[year] = count
        except Exception as e:
            logger.error(f"Failed to parse employee row: {await row.inner_html()}. Error: {e}")

    # Sort dict by year (ascending = oldest first)
    employees_by_year = {
        k: employees_by_year[k]
        for k in sorted(employees_by_year.keys(), key=int)
    }

    logger.info(f"Extracted employees by year: {employees_by_year}")
    return employees_by_year