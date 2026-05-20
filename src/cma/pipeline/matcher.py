import logging
import statistics

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "price_fit": 0.25,
    "location": 0.20,
    "size_rooms": 0.15,
    "price_per_sqm": 0.15,
    "trust_score": 0.10,
    "amenities": 0.10,
    "freshness": 0.05,
}

VALUE_RATINGS = [
    (85, "ممتاز"),
    (70, "جيد جداً"),
    (55, "جيد"),
    (40, "مقبول"),
    (0, "ضعيف"),
]


class PropertyMatcher:
    def __init__(self, weights: dict | None = None):
        self.weights = weights or DEFAULT_WEIGHTS

    def match(
        self,
        city: str,
        neighborhood: str,
        property_type: str,
        offer_type: str,
        listings: list[dict],
        min_price: float | None = None,
        max_price: float | None = None,
        bedrooms: int | None = None,
        category: str | None = None,
        amenities: list[str] | None = None,
        top_n: int = 25,
    ) -> list[dict]:
        filtered = self._hard_filter(
            listings, city, property_type, offer_type, category
        )
        if not filtered:
            logger.warning("No listings passed hard filters")
            return []

        prices_per_sqm = [
            item["السعر_للمتر"]
            for item in filtered
            if item.get("السعر_للمتر") and item["السعر_للمتر"] > 0
        ]
        median_ppm = statistics.median(prices_per_sqm) if prices_per_sqm else None

        scored = []
        for item in filtered:
            score = self._compute_score(
                item,
                neighborhood=neighborhood,
                min_price=min_price,
                max_price=max_price,
                bedrooms=bedrooms,
                amenities=amenities or [],
                median_ppm=median_ppm,
            )
            item["_match_score"] = round(score, 2)
            item["تقييم_القيمة"] = self._value_rating(score)
            scored.append(item)

        scored.sort(key=lambda x: x["_match_score"], reverse=True)

        for rank, item in enumerate(scored[:top_n], 1):
            item["ترتيب_التوصية"] = rank

        logger.info(
            f"Matching complete: {len(filtered)} passed filters, "
            f"returning top {min(top_n, len(scored))}"
        )
        return scored[:top_n]

    def _hard_filter(
        self,
        listings: list[dict],
        city: str,
        property_type: str,
        offer_type: str,
        category: str | None,
    ) -> list[dict]:
        results = []
        for item in listings:
            if item.get("المدينة") and item["المدينة"] != city:
                continue
            if item.get("نوع_الوحدة") and item["نوع_الوحدة"] != property_type:
                continue
            if item.get("نوع_العرض") and item["نوع_العرض"] != offer_type:
                continue
            if category and item.get("الفئة") and item["الفئة"] != category:
                continue
            results.append(item)
        return results

    def _compute_score(
        self,
        item: dict,
        neighborhood: str,
        min_price: float | None,
        max_price: float | None,
        bedrooms: int | None,
        amenities: list[str],
        median_ppm: float | None,
    ) -> float:
        scores = {}

        scores["price_fit"] = self._score_price_fit(
            item.get("السعر"), min_price, max_price
        )
        scores["location"] = self._score_location(item.get("الحي"), neighborhood)
        scores["size_rooms"] = self._score_rooms(item.get("غرف_النوم"), bedrooms)
        scores["price_per_sqm"] = self._score_ppm(item.get("السعر_للمتر"), median_ppm)
        scores["trust_score"] = item.get("trust_score", 0)
        scores["amenities"] = self._score_amenities(item.get("المميزات", []), amenities)
        scores["freshness"] = 70.0  # default for V1 without date parsing

        total = sum(scores[k] * self.weights[k] for k in self.weights)
        return total

    def _score_price_fit(
        self, price: float | None, min_p: float | None, max_p: float | None
    ) -> float:
        if not price:
            return 30.0
        if min_p and max_p:
            if min_p <= price <= max_p:
                return 100.0
            if price < min_p:
                ratio = price / min_p
                return max(0, 100 * ratio)
            ratio = max_p / price
            return max(0, 100 * ratio)
        if max_p:
            return 100.0 if price <= max_p else max(0, 100 * (max_p / price))
        if min_p:
            return 100.0 if price >= min_p else max(0, 100 * (price / min_p))
        return 50.0

    def _score_location(self, listing_neighborhood: str | None, target: str) -> float:
        if not listing_neighborhood:
            return 30.0
        if listing_neighborhood == target:
            return 100.0
        if target in listing_neighborhood or listing_neighborhood in target:
            return 75.0
        return 50.0

    def _score_rooms(self, listing_rooms: int | None, target: int | None) -> float:
        if not target or listing_rooms is None:
            return 50.0
        diff = abs(listing_rooms - target)
        if diff == 0:
            return 100.0
        return max(0, 100 - diff * 25)

    def _score_ppm(self, ppm: float | None, median: float | None) -> float:
        if not ppm or not median or median == 0:
            return 50.0
        ratio = ppm / median
        if ratio <= 1.0:
            return 100.0
        return max(0, 100 * (1 / ratio))

    def _score_amenities(
        self, listing_amenities: list[str], requested: list[str]
    ) -> float:
        if not requested:
            return 70.0
        if not listing_amenities:
            return 0.0
        matched = sum(1 for a in requested if a in listing_amenities)
        return (matched / len(requested)) * 100

    def _value_rating(self, score: float) -> str:
        for threshold, label in VALUE_RATINGS:
            if score >= threshold:
                return label
        return "ضعيف"
