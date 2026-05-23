"""
Bayut.sa scraper — Saudi Arabia real estate listings.

Correct URL pattern (verified live):
  Search: https://www.bayut.sa/en/to-rent/apartments/riyadh/
  Detail: https://www.bayut.sa/en/property/details-{ID}.html
"""
import asyncio
import logging
import re
from dataclasses import dataclass

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from cma.config import settings
from cma.scraper.stealth import (
    apply_stealth,
    get_random_proxy,
    human_scroll,
    random_delay,
    random_user_agent,
    random_viewport,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bayut.sa"

OFFER_TYPE_MAP = {
    "إيجار": "to-rent",
    "بيع": "for-sale",
}

PROPERTY_TYPE_MAP = {
    "شقة": "apartments",
    "فيلا": "villas",
    "دور": "floors",
    "أرض": "land",
    "عمارة": "buildings",
    "استراحة": "chalets",
    "محل": "shops",
    "مكتب": "offices",
}

CITY_MAP = {
    "الرياض": "riyadh",
    "جدة": "jeddah",
    "الدمام": "dammam",
    "الخبر": "khobar",
    "مكة": "makkah",
    "المدينة": "madinah",
}

# e.g. /en/property/details-87750301.html
LISTING_URL_RE = re.compile(r"/en/property/details-\d+\.html")


@dataclass
class ScrapedListing:
    url: str
    raw_html: str
    title: str
    price_text: str
    location_text: str


class BayutScraper:
    def __init__(self):
        self.base_url = BASE_URL
        self.headless = settings.scraper_headless
        self.max_listings = settings.max_listings_per_search

    def _build_search_url(
        self,
        city: str,
        property_type: str,
        offer_type: str,
        neighborhood: str | None = None,
    ) -> str:
        # Verified format: https://www.bayut.sa/en/to-rent/apartments/riyadh/
        offer_slug = OFFER_TYPE_MAP.get(offer_type, "to-rent")
        prop_slug = PROPERTY_TYPE_MAP.get(property_type, "apartments")
        city_slug = CITY_MAP.get(city, city.lower())
        url = f"{self.base_url}/en/{offer_slug}/{prop_slug}/{city_slug}/"
        return url

    def _is_listing_url(self, href: str) -> bool:
        """Matches /en/property/details-XXXXXXXX.html"""
        return bool(LISTING_URL_RE.search(href))

    async def scrape(
        self,
        city: str,
        neighborhood: str,
        property_type: str,
        offer_type: str,
        min_price: float | None = None,
        max_price: float | None = None,
        bedrooms: int | None = None,
        category: str | None = None,
    ) -> list[ScrapedListing]:
        results: list[ScrapedListing] = []
        search_url = self._build_search_url(city, property_type, offer_type, neighborhood)

        logger.info(f"[Bayut] Starting scrape: {search_url}")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            viewport = random_viewport()
            context_kwargs = {
                "user_agent": random_user_agent(),
                "viewport": viewport,
                "locale": "en-US",
            }
            proxy_settings = get_random_proxy()
            if proxy_settings:
                context_kwargs["proxy"] = proxy_settings
                logger.info(f"[Bayut] Using proxy: {proxy_settings['server']}")

            context = await browser.new_context(**context_kwargs)
            await apply_stealth(context)
            page = await context.new_page()

            try:
                page_num = 1
                seen_urls: set[str] = set()

                while len(results) < self.max_listings:
                    # Bayut uses /page/N/ suffix for pagination
                    paginated_url = (
                        search_url
                        if page_num == 1
                        else f"{search_url}page-{page_num}/"
                    )
                    logger.info(f"[Bayut] Scraping page {page_num}: {paginated_url}")

                    async for attempt in AsyncRetrying(
                        stop=stop_after_attempt(settings.scraper_max_retries),
                        wait=wait_exponential(multiplier=1, min=2, max=10),
                        retry=retry_if_exception_type(PlaywrightTimeoutError),
                        reraise=True,
                    ):
                        with attempt:
                            await page.goto(paginated_url, wait_until="domcontentloaded", timeout=30000)
                            title = await page.title()
                            if "Just a moment" in title or "Cloudflare" in title:
                                logger.warning("[Bayut] Cloudflare challenge detected, waiting...")
                                await asyncio.sleep(5)
                                title = await page.title()
                                if "Just a moment" in title or "Cloudflare" in title:
                                    raise PlaywrightTimeoutError("[Bayut] Blocked by Cloudflare")

                    await random_delay(2, 4)
                    await human_scroll(page, scrolls=4)
                    await random_delay(1, 2)

                    all_anchors = await page.query_selector_all("a[href]")
                    listing_hrefs: list[str] = []

                    for anchor in all_anchors:
                        href = await anchor.get_attribute("href")
                        if not href:
                            continue
                        if not href.startswith("http"):
                            href = f"{self.base_url}{href}"
                        if self._is_listing_url(href) and href not in seen_urls:
                            listing_hrefs.append(href)
                            seen_urls.add(href)

                    if not listing_hrefs:
                        logger.info("[Bayut] No new listing links found — stopping.")
                        break

                    logger.info(f"[Bayut] Found {len(listing_hrefs)} new listings on page {page_num}")

                    for href in listing_hrefs:
                        if len(results) >= self.max_listings:
                            break
                        try:
                            detail = await self._scrape_detail(context, href)
                            if detail:
                                results.append(detail)
                                logger.info(f"[Bayut] Scraped: {href}")
                        except Exception as e:
                            logger.warning(f"[Bayut] Failed to scrape {href}: {e}")

                    page_num += 1

            except Exception as e:
                logger.error(f"[Bayut] Scraping failed: {e}")
            finally:
                await browser.close()

        logger.info(f"[Bayut] Done — {len(results)} listings scraped.")
        return results

    async def _scrape_detail(self, context, url: str) -> ScrapedListing | None:
        page = await context.new_page()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(settings.scraper_max_retries),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception_type(PlaywrightTimeoutError),
                reraise=True,
            ):
                with attempt:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)

            await random_delay(1, 2)
            raw_html = await page.content()

            price_text = ""
            price_el = await page.query_selector(
                "[aria-label*='price'], [class*='price'], [class*='Price'], "
                "[data-testid='property-price'], span[class*='StyledPrice']"
            )
            if price_el:
                price_text = (await price_el.inner_text()).strip()

            location_text = ""
            location_el = await page.query_selector(
                "nav[aria-label='breadcrumb'], [class*='breadcrumb'], [class*='Breadcrumb'], "
                "[class*='address'], [data-testid='property-location']"
            )
            if location_el:
                location_text = (await location_el.inner_text()).strip()

            title = await page.title()
            return ScrapedListing(
                url=url,
                raw_html=raw_html,
                title=title,
                price_text=price_text,
                location_text=location_text,
            )
        except Exception as e:
            logger.warning(f"[Bayut] Detail scrape failed for {url}: {e}")
            return None
        finally:
            await page.close()
