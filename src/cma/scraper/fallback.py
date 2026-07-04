import asyncio
import logging
import urllib.parse
from dataclasses import dataclass
from playwright.async_api import async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception_type

from cma.config import settings
from cma.scraper.stealth import (
    apply_stealth,
    get_random_proxy,
    random_delay,
    random_user_agent,
    random_viewport,
)

logger = logging.getLogger(__name__)


@dataclass
class ScrapedListing:
    url: str
    raw_html: str
    title: str
    price_text: str
    location_text: str


class FallbackScraper:
    def __init__(self, domain: str):
        self.domain = domain
        self.headless = settings.scraper_headless
        self.max_listings = settings.max_listings_per_search

    def _is_listing_url(self, url: str) -> bool:
        url_lower = url.lower()
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.strip("/")
        parts = path.split("/")

        if "wasalt.com" in self.domain or "wasalt.sa" in self.domain:
            return "/property/" in url_lower or "property" in parts
        elif "sakan.co" in self.domain:
            # sakan.co/sa/ar/rent/riyadh/villa/title-slug-id
            return len(parts) >= 5
        elif "zaheb.com" in self.domain:
            return "details" in parts or any(char.isdigit() for char in path)
        elif "shobbak.com" in self.domain:
            return "ad" in parts or "post" in parts or any(char.isdigit() for char in path)
        elif "mstaml.com" in self.domain:
            return "ad" in parts or "item" in parts or "post" in parts
        elif "haraj.com.sa" in self.domain:
            if parts and parts[0].isdigit() and len(parts[0]) >= 6:
                return True
            return False
        
        # General fallback
        return len(parts) >= 3 or any(char.isdigit() for char in path)

    def _extract_real_url(self, href: str) -> str | None:
        if not href:
            return None
        if "/l/?uddg=" in href or "uddg=" in href:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs:
                return urllib.parse.unquote(qs["uddg"][0])
        return href

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
        
        # Build search query for DuckDuckGo or route directly if it's Haraj
        if "haraj.com.sa" in self.domain:
            prop_plural = "شقق" if "شقة" in property_type or "شقق" in property_type else (
                "فلل" if "فيلا" in property_type or "فلل" in property_type else property_type
            )
            offer_slug = "للايجار" if "إيجار" in offer_type or "ايجار" in offer_type else "للبيع"
            nb = neighborhood
            if not nb.startswith("حي "):
                nb = f"حي {nb}"
            tag = f"{prop_plural} {offer_slug} في {nb} في {city}"
            encoded_tag = urllib.parse.quote(tag)
            search_url = f"https://haraj.com.sa/tags/{encoded_tag}"
            logger.info(f"[FallbackScraper] Routing directly to Haraj tag search URL: {search_url}")
        else:
            query_parts = [f"site:{self.domain}", city, neighborhood, property_type, offer_type]
            search_query = " ".join(query_parts)
            encoded_query = urllib.parse.quote_plus(search_query)
            search_url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            logger.info(f"[FallbackScraper] Searching DuckDuckGo for: {search_query}")
        
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
                logger.info(f"[FallbackScraper] Using proxy: {proxy_settings['server']}")

            context = await browser.new_context(**context_kwargs)
            await apply_stealth(context)
            page = await context.new_page()

            try:
                # 1. Fetch search/tag page
                async for attempt in AsyncRetrying(
                    stop=stop_after_attempt(settings.scraper_max_retries),
                    wait=wait_exponential(multiplier=1, min=2, max=10),
                    retry=retry_if_exception_type(PlaywrightTimeoutError),
                    reraise=True
                ):
                    with attempt:
                        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                
                await random_delay(2, 4)
                
                all_anchors = await page.query_selector_all("a[href]")
                seen_urls = set()
                listing_urls = []

                for anchor in all_anchors:
                    href = await anchor.get_attribute("href")
                    if not href:
                        continue
                    
                    real_url = self._extract_real_url(href)
                    if not real_url:
                        continue
                    
                    # Convert to absolute URL if relative
                    if real_url.startswith("/"):
                        real_url = f"https://{self.domain}{real_url}"
                        
                    if self.domain in real_url.lower() and real_url not in seen_urls:
                        if self._is_listing_url(real_url):
                            listing_urls.append(real_url)
                            seen_urls.add(real_url)

                logger.info(f"[FallbackScraper] Found {len(listing_urls)} candidate listing URLs.")

                # If we didn't find enough, try a broader query (only for non-Haraj sites using DuckDuckGo)
                if not listing_urls and "haraj.com.sa" not in self.domain:
                    logger.warning("[FallbackScraper] No URLs found. Trying broader search query...")
                    broad_query = f"site:{self.domain} {city} {property_type} {offer_type} {neighborhood}"
                    broad_search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote_plus(broad_query)}"
                    
                    await page.goto(broad_search_url, wait_until="domcontentloaded", timeout=30000)
                    await random_delay(2, 3)
                    all_anchors = await page.query_selector_all("a[href]")
                    for anchor in all_anchors:
                        href = await anchor.get_attribute("href")
                        real_url = self._extract_real_url(href)
                        if real_url:
                            if real_url.startswith("/"):
                                real_url = f"https://{self.domain}{real_url}"
                            if self.domain in real_url.lower() and real_url not in seen_urls:
                                if self._is_listing_url(real_url):
                                    listing_urls.append(real_url)
                                    seen_urls.add(real_url)
                    logger.info(f"[FallbackScraper] Broad search found {len(listing_urls)} candidate URLs.")

                # Limit the URLs to max_listings
                listing_urls = listing_urls[:self.max_listings]

                # 2. Visit each listing URL and fetch details
                for url in listing_urls:
                    if len(results) >= self.max_listings:
                        break
                    try:
                        detail = await self._scrape_detail(context, url)
                        if detail:
                            results.append(detail)
                            logger.info(f"[FallbackScraper] Scraped listing: {url}")
                    except Exception as e:
                        logger.warning(f"[FallbackScraper] Failed to process listing {url}: {e}")

            except Exception as e:
                logger.error(f"[FallbackScraper] Scraping failed: {e}")
            finally:
                await browser.close()

        logger.info(f"[FallbackScraper] Scraping complete: {len(results)} listings found")
        return results

    async def _scrape_detail(self, context, url: str) -> ScrapedListing | None:
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
            title = await page.title()

            return ScrapedListing(
                url=url,
                raw_html=raw_html,
                title=title,
                price_text="",
                location_text="",
            )
        except Exception as e:
            logger.warning(f"[FallbackScraper] Failed to scrape detail {url}: {e}")
            return None
        finally:
            await page.close()
