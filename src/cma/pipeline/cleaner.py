import logging
import statistics
from collections import defaultdict

logger = logging.getLogger(__name__)


class DataCleaner:
    def clean(self, listings: list[dict]) -> list[dict]:
        listings = self._deduplicate(listings)
        listings = self._compute_derived_fields(listings)
        neighborhood_medians = self._compute_neighborhood_medians(listings)
        listings = self._detect_outliers(listings, neighborhood_medians)
        listings = self._assign_trust_scores(listings)
        logger.info(f"Cleaning complete: {len(listings)} listings after dedup and validation")
        return listings

    def _deduplicate(self, listings: list[dict]) -> list[dict]:
        seen_urls: set[str] = set()
        seen_signatures: set[str] = set()
        unique: list[dict] = []

        for item in listings:
            url = item.get("رابط_الإعلان", "")
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)

            sig = self._listing_signature(item)
            if sig and sig in seen_signatures:
                continue
            if sig:
                seen_signatures.add(sig)

            unique.append(item)

        removed = len(listings) - len(unique)
        if removed:
            logger.info(f"Removed {removed} duplicate listings")
        return unique

    def _listing_signature(self, item: dict) -> str | None:
        neighborhood = item.get("الحي")
        price = item.get("السعر")
        bedrooms = item.get("غرف_النوم")
        area = item.get("المساحة_متر")
        if neighborhood and price:
            return f"{neighborhood}|{price}|{bedrooms}|{area}"
        return None

    def _compute_derived_fields(self, listings: list[dict]) -> list[dict]:
        REGION_MAP = {
            "الرياض": "منطقة الرياض",
            "جدة": "منطقة مكة المكرمة",
            "مكة": "منطقة مكة المكرمة",
            "الدمام": "المنطقة الشرقية",
            "الخبر": "المنطقة الشرقية",
            "المدينة": "منطقة المدينة المنورة",
        }

        for item in listings:
            price = item.get("السعر")
            area = item.get("المساحة_متر")
            if price and area and area > 0 and not item.get("السعر_للمتر"):
                item["السعر_للمتر"] = round(price / area, 2)

            if not item.get("المنطقة"):
                city = item.get("المدينة", "")
                item["المنطقة"] = REGION_MAP.get(city)

        return listings

    def _compute_neighborhood_medians(self, listings: list[dict]) -> dict[str, float]:
        by_neighborhood: dict[str, list[float]] = defaultdict(list)
        for item in listings:
            neighborhood = item.get("الحي")
            ppm = item.get("السعر_للمتر")
            if neighborhood and ppm and ppm > 0:
                by_neighborhood[neighborhood].append(ppm)

        medians = {}
        for neighborhood, prices in by_neighborhood.items():
            if len(prices) >= 3:
                medians[neighborhood] = statistics.median(prices)
        return medians

    def _detect_outliers(
        self, listings: list[dict], medians: dict[str, float]
    ) -> list[dict]:
        by_neighborhood: dict[str, list[float]] = defaultdict(list)
        for item in listings:
            neighborhood = item.get("الحي")
            ppm = item.get("السعر_للمتر")
            if neighborhood and ppm and neighborhood in medians:
                by_neighborhood[neighborhood].append(ppm)

        stdevs: dict[str, float] = {}
        for neighborhood, prices in by_neighborhood.items():
            if len(prices) >= 3:
                stdevs[neighborhood] = statistics.stdev(prices)

        for item in listings:
            item["_is_outlier"] = False
            neighborhood = item.get("الحي")
            ppm = item.get("السعر_للمتر")
            if neighborhood and ppm and neighborhood in medians and neighborhood in stdevs:
                median = medians[neighborhood]
                stdev = stdevs[neighborhood]
                if stdev > 0 and abs(ppm - median) > 2 * stdev:
                    item["_is_outlier"] = True
                    logger.debug(
                        f"Outlier detected: {ppm} SAR/sqm in {neighborhood} "
                        f"(median: {median}, stdev: {stdev})"
                    )
        return listings

    def _assign_trust_scores(self, listings: list[dict]) -> list[dict]:
        for item in listings:
            score = 0.0

            if item.get("السعر") and item.get("السعر_للمتر"):
                score += 20
            elif item.get("السعر"):
                score += 10

            if item.get("الشارع") and item.get("الحي"):
                score += 20
            elif item.get("الحي"):
                score += 10

            specs = ["غرف_النوم", "المساحة_متر", "الدور"]
            filled = sum(1 for s in specs if item.get(s) is not None)
            score += (filled / len(specs)) * 15

            amenities = item.get("المميزات", [])
            if amenities and len(amenities) > 0:
                score += 15

            if item.get("تاريخ_الإعلان"):
                score += 15

            if item.get("رقم_التواصل"):
                score += 15

            if item.get("_is_outlier"):
                score -= 20

            item["trust_score"] = max(0, min(100, score))

        return listings
