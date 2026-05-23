"""
DealApp.sa scraper — Saudi Arabia real estate marketplace.

Correct URL pattern (verified live):
  Search: https://dealapp.sa/ar/السوق/الاعلانات/الرياض/شقق_وغرف/للإيجار?propertyTypeName=شقة
  Detail: https://dealapp.sa/ar/ad-details/{ID}?purpose=RENT&...
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

BASE_URL = "https://dealapp.sa"

OFFER_TYPE_MAP = {
    "إيجار": "للإيجار",
    "بيع": "للبيع",
}

PROPERTY_CATEGORY_MAP = {
    "شقة": "شقق_وغرف",
    "فيلا": "فلل_وقصور",
    "دور": "أدوار_وشقق_شاملة",
    "أرض": "أراضي",
    "عمارة": "عمائر",
    "استراحة": "استراحات",
    "محل": "تجاري",
    "مكتب": "مكاتب",
}

PROPERTY_TYPE_MAP = {
    "شقة": "شقة",
    "فيلا": "فيلا",
    "دور": "دور",
    "أرض": "أرض",
    "عمارة": "عمارة",
    "استراحة": "استراحة",
    "محل": "محل",
    "مكتب": "مكتب",
}

CITY_MAP = {
    "الرياض": "الرياض",
    "جدة": "جدة",
    "الدمام": "الدمام",
    "الخبر": "الخبر",
    "مكة": "مكة_المكرمة",
    "المدينة": "المدينة_المنورة",
}

# e.g. /ar/ad-details/502454
LISTING_URL_RE = re.compile(r"/ar/ad-details/\d+")


@dataclass
class ScrapedListing:
    url: str
    raw_html: str
    title: str
    price_text: str
    location_text: str


class DealAppScraper:
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
        # Verified format: /ar/السوق/الاعلانات/{مدينة}/{تصنيف}/{نوع_العرض}
        offer_slug = OFFER_TYPE_MAP.get(offer_type, "للإيجار")
        category_slug = PROPERTY_CATEGORY_MAP.get(property_type, "شقق_وغرف")
        prop_name = PROPERTY_TYPE_MAP.get(property_type, "شقة")
        city_slug = CITY_MAP.get(city, city)

        url = (
            f"{self.base_url}/ar/السوق/الاعلانات"
            f"/{city_slug}/{category_slug}/{offer_slug}"
            f"?propertyTypeName={prop_name}"
        )
        return url

    def _is_listing_url(self, href: str) -> bool:
        """Matches /ar/ad-details/{numeric_id}"""
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

        logger.info(f"[DealApp] Starting scrape: {search_url}")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            viewport = random_viewport()
            context_kwargs = {
                "user_agent": random_user_agent(),
                "viewport": viewport,
                "locale": "ar-SA",
            }
            proxy_settings = get_random_proxy()
            if proxy_settings:
                context_kwargs["proxy"] = proxy_settings
                logger.info(f"[DealApp] Using proxy: {proxy_settings['server']}")

            context = await browser.new_context(**context_kwargs)
            await apply_stealth(context)
            page = await context.new_page()

            try:
                page_num = 1
                seen_urls: set[str] = set()

                while len(results) < self.max_listings:
                    paginated_url = (
                        search_url if page_num == 1 else f"{search_url}&page={page_num}"
                    )
                    logger.info(f"[DealApp] Scraping page {page_num}: {paginated_url}")

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
                                logger.warning("[DealApp] Cloudflare challenge, waiting...")
                                await asyncio.sleep(5)
                                title = await page.title()
                                if "Just a moment" in title or "Cloudflare" in title:
                                    raise PlaywrightTimeoutError("[DealApp] Blocked by Cloudflare")

                    # DealApp is a React SPA — wait for JS rendering
                    await random_delay(3, 5)
                    await human_scroll(page, scrolls=5)
                    await random_delay(2, 3)

                    # Try waiting for network idle after scrolling to ensure cards load
                    try:
                        await page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass

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
                        logger.info("[DealApp] No new listing links found — stopping.")
                        break

                    logger.info(f"[DealApp] Found {len(listing_hrefs)} new listings on page {page_num}")

                    for href in listing_hrefs:
                        if len(results) >= self.max_listings:
                            break
                        try:
                            detail = await self._scrape_detail(context, href)
                            if detail:
                                results.append(detail)
                                logger.info(f"[DealApp] Scraped: {href}")
                        except Exception as e:
                            logger.warning(f"[DealApp] Failed to scrape {href}: {e}")

                    page_num += 1

            except Exception as e:
                logger.error(f"[DealApp] Scraping failed: {e}")
            finally:
                await browser.close()

        logger.info(f"[DealApp] Done — {len(results)} listings scraped.")
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

            # Wait for React SPA to render content
            await random_delay(2, 3)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass

            raw_html = await page.content()

            price_text = ""
            price_el = await page.query_selector(
                "[class*='price'], [class*='Price'], [class*='amount'], "
                "[class*='Amount'], [data-testid*='price']"
            )
            if price_el:
                price_text = (await price_el.inner_text()).strip()

            location_text = ""
            location_el = await page.query_selector(
                "[class*='location'], [class*='Location'], [class*='address'], "
                "[class*='neighborhood'], [class*='district'], [class*='city']"
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
            logger.warning(f"[DealApp] Detail scrape failed for {url}: {e}")
            return None
        finally:
            await page.close()
