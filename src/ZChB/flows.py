import re
import random
from patchright.async_api import Page, TimeoutError
from loguru import logger


# =========================================================
# PAUSE SETTINGS
# =========================================================
# Все паузы и "человечность" скрипта настраиваются здесь.

PAUSE_SETTINGS = {
    # Обычная короткая пауза почти после каждого действия
    "short_pause_ms": (180, 650),

    # Иногда чуть более заметная "человеческая" пауза
    "micro_pause_chance": 0.16,
    "micro_pause_ms": (700, 1600),

    # После случайного числа значимых действий — большая пауза
    "long_pause_every_actions": (5, 10),
    "long_pause_ms": (2000, 5000),

    # Иногда перед кликом человек как будто "целится"
    "pre_click_pause_ms": (220, 520),

    # Иногда после загрузки/открытия модалки есть небольшое ожидание
    "post_open_pause_ms": (320, 760),
}


# Внутреннее состояние ритма
_ACTIONS_SINCE_LONG_PAUSE = 0
_NEXT_LONG_PAUSE_AFTER = random.randint(*PAUSE_SETTINGS["long_pause_every_actions"])


def _reset_long_pause_cycle() -> None:
    global _ACTIONS_SINCE_LONG_PAUSE, _NEXT_LONG_PAUSE_AFTER
    _ACTIONS_SINCE_LONG_PAUSE = 0
    _NEXT_LONG_PAUSE_AFTER = random.randint(*PAUSE_SETTINGS["long_pause_every_actions"])


async def _wait_random(page: Page, ms_range: tuple[int, int]) -> None:
    await page.wait_for_timeout(random.randint(*ms_range))


async def human_pause(page: Page, min_ms: int = 180, max_ms: int = 650) -> None:
    """
    Базовая короткая пауза.
    Если диапазон не передан — берётся из глобальных настроек.
    """
    if min_ms == 180 and max_ms == 650:
        await _wait_random(page, PAUSE_SETTINGS["short_pause_ms"])
    else:
        await page.wait_for_timeout(random.randint(min_ms, max_ms))


async def maybe_micro_pause(page: Page) -> None:
    """
    Иногда добавляет дополнительную небольшую паузу,
    чтобы поведение не было слишком ровным.
    """
    if random.random() < PAUSE_SETTINGS["micro_pause_chance"]:
        await _wait_random(page, PAUSE_SETTINGS["micro_pause_ms"])


async def human_action_pause(page: Page, actions: int = 1) -> None:
    """
    Вызывать после значимого действия:
    - открытия карточки,
    - клика по ссылке,
    - загрузки модалки,
    - запроса / перехода / ajax-подгрузки.

    Делает:
    1) короткую паузу,
    2) иногда микропаузу,
    3) после 5-10 действий — длинную паузу 2-5 сек.
    """
    global _ACTIONS_SINCE_LONG_PAUSE, _NEXT_LONG_PAUSE_AFTER

    _ACTIONS_SINCE_LONG_PAUSE += actions

    await _wait_random(page, PAUSE_SETTINGS["short_pause_ms"])
    await maybe_micro_pause(page)

    if _ACTIONS_SINCE_LONG_PAUSE >= _NEXT_LONG_PAUSE_AFTER:
        logger.info(
            f"Taking a longer human-like pause after {_ACTIONS_SINCE_LONG_PAUSE} actions..."
        )
        await _wait_random(page, PAUSE_SETTINGS["long_pause_ms"])
        _reset_long_pause_cycle()


async def pre_click_pause(page: Page) -> None:
    await _wait_random(page, PAUSE_SETTINGS["pre_click_pause_ms"])


async def post_open_pause(page: Page) -> None:
    await _wait_random(page, PAUSE_SETTINGS["post_open_pause_ms"])


async def click_beneficiaries(page: Page) -> bool:
    try:
        # 0) Desktop layout and remove cookie overlay
        await page.set_viewport_size({"width": 1400, "height": 900})
        await human_pause(page, 120, 280)
        try:
            await page.locator('#cookie-accept-button').click(timeout=1000)
            await human_action_pause(page)
        except Exception:
            pass  # banner might not be there

        # 1) Scroll the section into view and produce real scroll events
        header = page.locator("text=Бенефициары (Выгодоприобретатели)")

        # If header not yet attached, wheel-scroll down until it appears
        for _ in range(20):
            if await header.count() > 0:
                break
            await page.mouse.wheel(0, random.randint(650, 950))
            await page.wait_for_timeout(random.randint(90, 180))

        if await header.count() == 0:
            # As a fallback, go near bottom to force more lazy loads
            for _ in range(10):
                await page.mouse.wheel(0, random.randint(950, 1350))
                await page.wait_for_timeout(random.randint(90, 180))

        # Ensure it’s within viewport
        if await header.count():
            await header.first.scroll_into_view_if_needed(timeout=3000)
            await human_pause(page, 220, 520)

        # 2) Wait a beat for their scroll-handler to issue Ajax
        await human_pause(page, 280, 620)

        # 3) Try to find the link; allow wording variations
        section = page.locator('#benefic_tree, .ajax-content[data-content*="/ajax/benefic-tree"]')
        link = section.locator("a:has-text('Показать всех')")
        if await link.count() == 0:
            link = section.locator("a").filter(has_text=re.compile(r"Показать\s+все(х)?", re.I))

        # Give the lazy loader a few more scroll nudges if needed
        for _ in range(6):
            if await link.count() > 0:
                break
            await page.mouse.wheel(0, random.randint(450, 750))
            await page.evaluate("window.dispatchEvent(new Event('scroll'))")
            await page.wait_for_timeout(random.randint(120, 240))

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
            await page.wait_for_selector(
                "#benefic_tree a:has-text('Показать всех'), "
                ".ajax-content[data-content*='/ajax/benefic-tree'] a:has-text('Показать всех')",
                timeout=8000
            )
            await human_action_pause(page)

            # Refresh link locator after injection
            link = section.locator("a:has-text('Показать всех')")

        if await link.count() == 0:
            return False  # not loaded

        # 5) Click link (JS click fallback in case something overlays)
        try:
            await pre_click_pause(page)
            await link.first.click(timeout=5000)
        except TimeoutError:
            await link.first.evaluate("el => el.click()")

        await human_action_pause(page)

        # 6) Wait for modal (either normal or premium)
        await page.wait_for_selector("#modal-template .modal-title", timeout=5000)
        await post_open_pause(page)

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
    Finds and clicks the link to open the CEO history modal.

    It waits for the modal to become visible after the click.

    Args:
        page: The Playwright page object.

    Returns:
        True if the link was clicked and the modal appeared, False otherwise.
    """
    link_locator = page.locator('a[data-title*="История изменений руководителей"]')

    if await link_locator.count() == 0:
        logger.warning("The 'CEO History' link was not found on the page.")
        return False

    try:
        logger.info("Clicking the 'CEO History' link...")
        await pre_click_pause(page)
        await link_locator.first.click()
        await human_action_pause(page)

        modal_title_locator = page.locator("div.modal-title:has-text('История изменений руководителей')")
        await modal_title_locator.wait_for(state="visible", timeout=5000)
        await post_open_pause(page)

        logger.success("Successfully clicked the link and the CEO history modal is visible.")
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
    link_locator = page.locator('a[data-title*="История изменений учредителей"]')

    if await link_locator.count() == 0:
        logger.warning("The 'Founders History' link was not found on the page.")
        return False

    try:
        logger.info("Clicking the 'Founders History' link...")
        await pre_click_pause(page)
        await link_locator.first.click()
        await human_action_pause(page)

        modal_title_locator = page.locator(
            "div.modal-title:has-text('История изменений учредителей')"
        )
        await modal_title_locator.wait_for(state="visible", timeout=5000)
        await post_open_pause(page)

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
    Finds the 'Бенефициары' modal on the page and extracts data from its table.
    """
    beneficiaries = {}

    modal_locator = page.locator("div.modal-content:has(div.modal-title:text-is('Бенефициары'))")

    if await modal_locator.count() == 0:
        logger.info("Beneficiaries modal not found on the page.")
        return {}

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
    Finds the modal for 'История изменений руководителей' and extracts the data by date.
    Returns: {"12.05.2014": [{"должность": "...", "руководитель": "...", "инн": "..."}], ...}
    """
    ceos_by_date = {}

    modal_locator = page.locator(
        "div.modal-content:has(div.modal-title:has-text('История изменений руководителей'))"
    )
    try:
        await modal_locator.wait_for(state="visible", timeout=5000)
    except Exception:
        logger.info("CEO history modal not found on the page.")
        return {}

    date_chunks = modal_locator.locator("tbody[id^='history-founder-chunk-']")
    if await date_chunks.count() == 0:
        logger.warning("CEO history modal found, but no date chunks were located.")
        return {}

    for i in range(await date_chunks.count()):
        chunk = date_chunks.nth(i)

        date_cell = chunk.locator("td.attr-date")
        if await date_cell.count() == 0:
            continue

        date_anchor_locator = date_cell.locator("a[href*='/ordering?date=']")
        if await date_anchor_locator.count() == 0:
            continue

        date_str = (await date_anchor_locator.first.text_content() or "").strip()
        if not date_str:
            continue

        if date_str not in ceos_by_date:
            ceos_by_date[date_str] = []

        data_rows = chunk.locator("tr:has(td[data-th])")
        for j in range(await data_rows.count()):
            row = data_rows.nth(j)

            tds = row.locator("td")
            if await tds.count() < 4:
                continue

            try:
                position = (await tds.nth(1).text_content() or "").strip()
                name = (await tds.nth(2).text_content() or "").strip()
                inn = (await tds.nth(3).text_content() or "").strip()

                position = " ".join(position.split())
                name = " ".join(name.split())
                inn = " ".join(inn.split())

                ceos_by_date[date_str].append({
                    "должность": position,
                    "руководитель": name,
                    "инн": inn
                })
            except Exception as e:
                try:
                    html_snippet = await row.inner_html()
                except Exception:
                    html_snippet = "<unavailable>"
                logger.error(f"Could not parse a CEO row. HTML: {html_snippet}. Error: {e}")

    logger.info(f"Extracted CEO history for {len(ceos_by_date)} dates.")
    return ceos_by_date


async def extract_founders(page: Page) -> dict:
    """
    Extracts the 'История изменений учредителей' table grouped by date.
    """
    founders_by_date: dict[str, list[dict]] = {}

    modal_locator = page.locator(
        "div.modal-content:has(div.modal-title:has-text('История изменений учредителей'))"
    )
    try:
        await modal_locator.wait_for(state="visible", timeout=5000)
    except Exception:
        logger.info("Founders history modal not found on the page.")
        return {}

    date_chunks = modal_locator.locator("tbody[id^='history-founder-chunk-']")
    if await date_chunks.count() == 0:
        logger.warning("Founders history modal found, but no date chunks were located.")
        return {}

    for i in range(await date_chunks.count()):
        chunk = date_chunks.nth(i)

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

        data_rows = chunk.locator("tr:has(td[data-th])")
        for j in range(await data_rows.count()):
            row = data_rows.nth(j)

            tds = row.locator("td")
            if await tds.count() < 5:
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

        for inn, f in prev.items():
            if inn not in current:
                lines.append(f"{short_name(f['учредитель']) or inn} вышел ({f['доля']})")

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

            row_text = " ".join((await row.text_content() or "").split())
            parts = row_text.split()
            count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None

            if count is not None:
                employees_by_year[year] = count
        except Exception as e:
            logger.error(f"Failed to parse employee row: {await row.inner_html()}. Error: {e}")

    employees_by_year = {
        k: employees_by_year[k]
        for k in sorted(employees_by_year.keys(), key=int)
    }

    logger.info(f"Extracted employees by year: {employees_by_year}")
    return employees_by_year