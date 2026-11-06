from loguru import logger
from patchright.async_api import (
    Browser as PlaywrightBrowser,
)
from patchright.async_api import (
    BrowserContext,
    Error,
    Playwright,
    TimeoutError,
    async_playwright,
)

from src.proxy_manager import ProxyManager


class Browser:
    """Manages a persistent Playwright browser instance and context."""

    def __init__(self, headless: bool = True, datadir: str | None = None):
        self._headless = headless
        self._datadir = datadir
        self._playwright_context_manager = None
        self._playwright: Playwright | None = None
        self._browser: PlaywrightBrowser | None = None
        self.default_context: BrowserContext | None = None
        self.proxy_manager = ProxyManager.from_file("proxies.txt")
        self._using_proxy = False

    @property
    def context(self) -> BrowserContext | None:
        """Returns the active persistent context (with or without proxy)."""
        return self.default_context

    async def launch(self):
        """
        Launches a persistent browser context using the provided datadir.
        """
        if self.is_connected():
            logger.info("Browser context is already launched and connected.")
            return

        logger.info("Starting Playwright driver...")
        self._playwright_context_manager = async_playwright()
        self._playwright = await self._playwright_context_manager.start()

        logger.info(
            f"Launching persistent browser context with user data dir: '{self._datadir}'..."
        )
        self.default_context = (
            await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self._datadir,
                headless=self._headless,
                channel="chrome",
            )
        )

        logger.success("Persistent browser context is launched and ready.")

    async def goto_with_retry(self, url: str, **kwargs):
        """
        Tries to navigate to a URL using the current context.
        On timeout/connection errors, rotates through proxies until one works.
        Once proxy is enabled, all subsequent requests use proxy.
        """
        page = None
        try:
            page = await self.default_context.new_page()
            await page.goto(url, **kwargs)
            return page
        except (TimeoutError, Error) as e:
            error_msg = str(e).lower()
            is_timeout_error = any(
                keyword in error_msg
                for keyword in [
                    "timeout",
                    "err_connection_timed_out",
                    "err_timed_out",
                    "err_connection_refused",
                    "err_connection_reset",
                ]
            )

            if not is_timeout_error:
                if page:
                    try:
                        await page.close()
                    except Exception as close_error:
                        logger.warning(f"Failed to close failed page: {close_error}")
                raise

            logger.warning(
                f"Connection/timeout error for URL: {url}. Switching to proxy..."
            )

            if page:
                try:
                    await page.close()
                except Exception as close_error:
                    logger.warning(f"Failed to close failed page: {close_error}")
                page = None

            max_proxy_attempts = 5
            for attempt in range(max_proxy_attempts):
                new_proxy = self.proxy_manager.get_next_proxy()
                if not new_proxy:
                    logger.error("No proxies available to retry.")
                    raise

                await self._switch_to_proxy(new_proxy)

                try:
                    page = await self.default_context.new_page()
                    await page.goto(url, **kwargs)
                    logger.success(
                        f"Successfully loaded URL with proxy after {attempt + 1} attempt(s)"
                    )
                    return page
                except (TimeoutError, Error) as proxy_error:
                    if page:
                        try:
                            await page.close()
                        except Exception as close_error:
                            logger.warning(f"Failed to close failed proxy page: {close_error}")
                        page = None

                    proxy_error_msg = str(proxy_error).lower()
                    is_proxy_timeout = any(
                        keyword in proxy_error_msg
                        for keyword in [
                            "timeout",
                            "err_connection_timed_out",
                            "err_timed_out",
                            "err_connection_refused",
                            "err_connection_reset",
                        ]
                    )

                    if not is_proxy_timeout:
                        raise proxy_error

                    if attempt < max_proxy_attempts - 1:
                        logger.warning(
                            f"Proxy attempt {attempt + 1} failed, trying next proxy..."
                        )
                    else:
                        logger.error(f"All {max_proxy_attempts} proxy attempts failed")
                        raise proxy_error

    async def _switch_to_proxy(self, proxy_config: dict):
        """
        Recreates the persistent context with proxy configuration.
        Maintains the same datadir for saved passwords/cookies.
        """
        if self._using_proxy:
            logger.info("Already using proxy, updating to new proxy...")
        else:
            logger.info("Switching to proxy mode for all subsequent requests...")

        if self.default_context:
            try:
                await self.default_context.close()
            except Exception as e:
                logger.warning(f"Failed to close old context before proxy switch: {e}")
            finally:
                self.default_context = None

        logger.info(
            f"Launching persistent context with proxy: {proxy_config['server']}"
        )
        self.default_context = (
            await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self._datadir,
                headless=self._headless,
                channel="chrome",
                proxy=proxy_config,
            )
        )

        self._using_proxy = True
        logger.success(
            "Persistent context recreated with proxy. All future requests will use this proxy."
        )

    async def close(self):
        """Close the persistent context and stop Playwright safely."""
        if self.default_context:
            try:
                await self.default_context.close()
                logger.info("Persistent browser context closed.")
            except Exception as e:
                logger.warning(f"Failed to close persistent browser context: {e}")
            finally:
                self.default_context = None

        if self._playwright:
            try:
                await self._playwright.stop()
                logger.info("Playwright driver stopped.")
            except Exception as e:
                logger.warning(f"Failed to stop Playwright driver: {e}")
            finally:
                self._playwright = None

        self._playwright_context_manager = None
        self._using_proxy = False

    def is_connected(self) -> bool:
        """
        Checks if the browser context is initialized.
        With launch_persistent_context, the context is the primary object.
        If it exists, we consider it 'connected'.
        """
        return self.default_context is not None