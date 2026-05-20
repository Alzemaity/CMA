import logging
import re
from dataclasses import dataclass

from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type

from cma.config import settings
from cma.scraper.stealth import (
    apply_stealth,
    human_scroll,
    random_delay,
    random_user_agent,
    random_viewport,
)

logger = logging.getLogger(__name__)

# Aqar.fm uses Arabic slugs for both property type and offer type.
# Format: /{property_type_slug}-{offer_type_slug}/{city_slug}/...
OFFER_TYPE_MAP = {
    "إيجار": "للإيجار",
    "بيع": "للبيع",
}

PROPERTY_TYPE_MAP = {
    "شقة": "شقق",
    "فيلا": "فلل",
    "دور": "دور",
    "أرض": "أراضي",
    "عمارة": "عمائر",
    "استراحة": "استراحة",
    "محل": "محلات",
    "مكتب": "مكتب-تجاري",
    "غرفة": "غرف",
    "مستودع": "مستودع",
}

CITY_MAP = {
    "الرياض": "الرياض",
    "riyadh": "الرياض",
    "جدة": "جدة",
    "jeddah": "جدة",
    "الدمام": "الدمام",
    "dammam": "الدمام",
    "مكة": "مكة-المكرمة",
    "المدينة": "المدينة-المنورة",
}

# Regex to detect a listing URL (ends with a numeric ID)
LISTING_ID_RE = re.compile(r"-(\d{5,})$")


@dataclass
class ScrapedListing:
    url: str
    raw_html: str
    title: str
    price_text: str
    location_text: str


class AqarScraper:
    def __init__(self):
        self.base_url = settings.aqar_base_url
        self.headless = settings.scraper_headless
        self.max_listings = settings.max_listings_per_search

    def _build_search_url(
        self,
        city: str,
        property_type: str,
        offer_type: str,
        neighborhood: str | None = None,
    ) -> str:
        prop_slug = PROPERTY_TYPE_MAP.get(property_type, "شقق")
        offer_slug = OFFER_TYPE_MAP.get(offer_type, "للإيجار")
        city_slug = CITY_MAP.get(city, city)

        # e.g. https://sa.aqar.fm/شقق-للإيجار/الرياض
        url = f"{self.base_url}/{prop_slug}-{offer_slug}/{city_slug}"
        return url

    def _is_listing_url(self, href: str) -> bool:
        """Return True if the href looks like an individual listing page."""
        return bool(LISTING_ID_RE.search(href))

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

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self.headless)
            viewport = random_viewport()
            context_kwargs = {
                "user_agent": random_user_agent(),
                "viewport": viewport,
                "locale": "ar-SA",
            }
            if settings.proxy_server:
                proxy_settings = {"server": settings.proxy_server}
                if settings.proxy_username and settings.proxy_password:
                    proxy_settings["username"] = settings.proxy_username
                    proxy_settings["password"] = settings.proxy_password
                context_kwargs["proxy"] = proxy_settings
                logger.info(f"Using proxy: {settings.proxy_server}")

            context = await browser.new_context(**context_kwargs)
            await apply_stealth(context)
            page = await context.new_page()

            try:
                logger.info(f"Navigating to {search_url}")
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(settings.scraper_max_retries),
                    wait=wait_exponential(multiplier=1, min=2, max=10),
                    retry=retry_if_exception_type(PlaywrightTimeoutError),
                    reraise=True
                ):
                    with attempt:
                        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                
                await random_delay(2, 4)

                page_num = 0
                seen_urls: set[str] = set()

                while len(results) < self.max_listings:
                    page_num += 1
                    logger.info(f"Scraping page {page_num}, {len(results)} listings so far")

                    await human_scroll(page, scrolls=4)
                    await random_delay(1, 3)

                    # Collect all anchor tags whose href matches a listing pattern
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
                        logger.warning("No listing links found on page — stopping.")
                        break

                    logger.info(f"Found {len(listing_hrefs)} new listing links on page {page_num}")

                    for href in listing_hrefs:
                        if len(results) >= self.max_listings:
                            break
                        try:
                            detail = await self._scrape_detail(context, href)
                            if detail:
                                results.append(detail)
                                logger.info(f"Scraped listing: {href}")
                        except Exception as e:
                            logger.warning(f"Failed to process listing {href}: {e}")

                    # Try to go to the next page
                    next_btn = await page.query_selector(
                        "a[rel='next'], "
                        "a[aria-label='Next'], button[aria-label='Next'], "
                        "[class*='pagination'] a:last-child, [class*='next']"
                    )
                    if next_btn:
                        await next_btn.click()
                        await random_delay(2, 5)
                        await page.wait_for_load_state("domcontentloaded")
                    else:
                        logger.info("No next page button found — stopping.")
                        break

            except Exception as e:
                logger.error(f"Scraping failed: {e}")
            finally:
                await browser.close()

        logger.info(f"Scraping complete: {len(results)} listings found")
        return results

    async def _scrape_detail(
        self, context, url: str
    ) -> ScrapedListing | None:
        page = await context.new_page()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(settings.scraper_max_retries),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception_type(PlaywrightTimeoutError),
                reraise=True
            ):
                with attempt:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    
            await random_delay(1, 2)

            raw_html = await page.content()

            price_text = ""
            # aqar.fm shows price in a bold element with class text-brand font-bold
            price_el = await page.query_selector(
                ".text-brand, [class*='price'], [class*='Price'], [data-testid*='price']"
            )
            if price_el:
                price_text = (await price_el.inner_text()).strip()

            location_text = ""
            location_el = await page.query_selector(
                "[class*='location'], [class*='Location'], [class*='address'], "
                "[class*='breadcrumb'], nav[aria-label='breadcrumb']"
            )
            if location_el:
                location_text = (await location_el.inner_text()).strip()

            # Fallback: get title from page
            title = await page.title()

            return ScrapedListing(
                url=url,
                raw_html=raw_html,
                title=title,
                price_text=price_text,
                location_text=location_text,
            )

        except Exception as e:
            logger.warning(f"Failed to scrape detail {url}: {e}")
            return None
        finally:
            await page.close()
