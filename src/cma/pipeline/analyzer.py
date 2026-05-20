import logging
import statistics
from collections import Counter

logger = logging.getLogger(__name__)


class MarketAnalyzer:
    def analyze(
        self,
        matched_listings: list[dict],
        all_listings: list[dict],
        neighborhood: str,
    ) -> dict:
        prices = [x["السعر"] for x in matched_listings if x.get("السعر")]
        ppms = [x["السعر_للمتر"] for x in matched_listings if x.get("السعر_للمتر")]

        kpis: dict = {
            "total_scraped": len(all_listings),
            "total_matched": len(matched_listings),
            "match_rate": (
                round(len(matched_listings) / len(all_listings) * 100, 1)
                if all_listings
                else 0
            ),
            "neighborhood": neighborhood,
        }

        if prices:
            kpis["price"] = {
                "min": min(prices),
                "max": max(prices),
                "median": statistics.median(prices),
                "mean": round(statistics.mean(prices), 2),
            }
        else:
            kpis["price"] = {"min": 0, "max": 0, "median": 0, "mean": 0}

        if ppms:
            kpis["price_per_sqm"] = {
                "min": round(min(ppms), 2),
                "max": round(max(ppms), 2),
                "median": round(statistics.median(ppms), 2),
            }
        else:
            kpis["price_per_sqm"] = {"min": 0, "max": 0, "median": 0}

        value_counts = Counter(
            x.get("تقييم_القيمة", "غير مصنف") for x in matched_listings
        )
        kpis["value_distribution"] = dict(value_counts)

        all_amenities: list[str] = []
        for item in matched_listings:
            all_amenities.extend(item.get("المميزات", []))
        amenity_counts = Counter(all_amenities)
        total = len(matched_listings) or 1
        kpis["amenity_availability"] = {
            k: round(v / total * 100, 1)
            for k, v in amenity_counts.most_common(10)
        }

        category_counts = Counter(
            x.get("الفئة", "غير محدد") for x in matched_listings
        )
        kpis["category_breakdown"] = dict(category_counts)

        if matched_listings:
            top = matched_listings[0]
            kpis["top_recommendation"] = {
                "url": top.get("رابط_الإعلان"),
                "price": top.get("السعر"),
                "neighborhood": top.get("الحي"),
                "bedrooms": top.get("غرف_النوم"),
                "area": top.get("المساحة_متر"),
                "price_per_sqm": top.get("السعر_للمتر"),
                "score": top.get("_match_score"),
                "rating": top.get("تقييم_القيمة"),
            }

        trust_scores = [x.get("trust_score", 0) for x in matched_listings]
        if trust_scores:
            kpis["avg_trust_score"] = round(statistics.mean(trust_scores), 1)
        else:
            kpis["avg_trust_score"] = 0

        logger.info(f"Analysis complete: {len(matched_listings)} matched listings analyzed")
        return kpis
