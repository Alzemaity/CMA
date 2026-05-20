import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cma.ai.extractor import ListingExtractor
from cma.ai.narrator import ReportNarrator
from cma.models import CMAReport, ClientBrief, Listing, ReportListing
from cma.pipeline.analyzer import MarketAnalyzer
from cma.pipeline.cleaner import DataCleaner
from cma.pipeline.matcher import PropertyMatcher
from cma.report.excel_exporter import ExcelExporter
from cma.report.generator import ReportGenerator
from cma.scraper.aqar import AqarScraper

logger = logging.getLogger(__name__)


class CMAOrchestrator:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.scraper = AqarScraper()
        self.extractor = ListingExtractor()
        self.cleaner = DataCleaner()
        self.matcher = PropertyMatcher()
        self.analyzer = MarketAnalyzer()
        self.narrator = ReportNarrator()
        self.report_gen = ReportGenerator()
        self.excel_exporter = ExcelExporter()

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1: Scrape, Extract, Clean → Excel export
    # ─────────────────────────────────────────────────────────────────────────

    async def run_phase1(self, report_id: int) -> CMAReport:
        report = await self.db.get(CMAReport, report_id)
        if not report:
            raise ValueError(f"Report {report_id} not found")

        brief = await self.db.get(ClientBrief, report.brief_id)
        if not brief:
            raise ValueError(f"Brief {report.brief_id} not found")

        try:
            # Step 1 – Scraping
            await self._update_status(report, "scraping")
            scraped = await self.scraper.scrape(
                city=brief.city,
                neighborhood=brief.neighborhood,
                property_type=brief.property_type,
                offer_type=brief.offer_type,
                min_price=brief.min_price,
                max_price=brief.max_price,
                bedrooms=brief.bedrooms,
                category=brief.category,
            )
            report.listings_found = len(scraped)
            await self.db.commit()

            if not scraped:
                await self._update_status(report, "failed", "لم يتم العثور على إعلانات مطابقة")
                return report

            # Step 2 – Extract structured data via AI
            await self._update_status(report, "cleaning")
            extraction_input = [(item.raw_html, item.url) for item in scraped]
            structured = await self.extractor.extract_batch(extraction_input)

            if not structured:
                await self._update_status(report, "failed", "فشل استخراج البيانات من الإعلانات")
                return report

            # Step 3 – Clean & deduplicate
            cleaned = self.cleaner.clean(structured)

            # Persist listings to DB
            for item in cleaned:
                url = item.get("رابط_الإعلان", "")
                existing = await self.db.execute(
                    select(Listing).where(Listing.source_url == url)
                )
                listing = existing.scalar_one_or_none()
                if not listing:
                    listing = Listing(
                        source_url=url,
                        structured_data=item,
                        trust_score=item.get("trust_score", 0),
                    )
                    self.db.add(listing)
                else:
                    listing.structured_data = item
                    listing.trust_score = item.get("trust_score", 0)
            await self.db.commit()

            # Step 4 – Export Excel (raw data, no rating)
            excel_path = self.excel_exporter.export(
                listings=cleaned,
                city=brief.city,
                neighborhood=brief.neighborhood,
                report_id=report.id,
            )

            # Cache cleaned data for phase 2 and mark phase complete
            report.scraped_data = cleaned
            report.report_excel_path = excel_path
            report.phase = 1
            await self._update_status(report, "scraped")  # new terminal state for phase 1

            logger.info(
                f"Phase 1 complete: report={report.id}, "
                f"found={report.listings_found}, cleaned={len(cleaned)}"
            )
            return report

        except Exception as e:
            logger.exception(f"Phase 1 failed for report {report_id}")
            await self._update_status(report, "failed", str(e))
            return report

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2: Match, Analyse, Narrate → PDF report
    # ─────────────────────────────────────────────────────────────────────────

    async def run_phase2(self, report_id: int) -> CMAReport:
        report = await self.db.get(CMAReport, report_id)
        if not report:
            raise ValueError(f"Report {report_id} not found")

        brief = await self.db.get(ClientBrief, report.brief_id)
        if not brief:
            raise ValueError(f"Brief {report.brief_id} not found")

        # Load cached cleaned data from phase 1
        cleaned = report.scraped_data
        if not cleaned:
            await self._update_status(report, "failed", "لا توجد بيانات من المرحلة الأولى")
            return report

        try:
            # Step 1 – Matching
            await self._update_status(report, "matching")
            matched = self.matcher.match(
                city=brief.city,
                neighborhood=brief.neighborhood,
                property_type=brief.property_type,
                offer_type=brief.offer_type,
                listings=cleaned,
                min_price=brief.min_price,
                max_price=brief.max_price,
                bedrooms=brief.bedrooms,
                category=brief.category,
                amenities=brief.amenities if isinstance(brief.amenities, list) else [],
            )
            report.listings_matched = len(matched)
            await self.db.commit()

            if not matched:
                await self._update_status(
                    report, "failed", "لم يتم العثور على عقارات مطابقة للمعايير"
                )
                return report

            # Step 2 – Analyze KPIs
            await self._update_status(report, "analyzing")
            kpis = self.analyzer.analyze(matched, cleaned, brief.neighborhood)

            # Step 3 – Narrate (AI summary)
            await self._update_status(report, "generating")
            narrative = await self.narrator.narrate(
                kpis=kpis,
                top_listings=matched[:5],
                city=brief.city,
                neighborhood=brief.neighborhood,
                offer_type=brief.offer_type,
                property_type=brief.property_type,
            )

            # Step 4 – Generate HTML + PDF report
            html, pdf_path = self.report_gen.generate(
                kpis=kpis,
                matched_listings=matched,
                narrative=narrative,
                city=brief.city,
                neighborhood=brief.neighborhood,
                property_type=brief.property_type,
                offer_type=brief.offer_type,
            )

            report.report_html = html
            report.report_pdf_path = pdf_path
            report.kpis = kpis
            report.phase = 2

            # Link report listings
            for item in matched:
                url = item.get("رابط_الإعلان", "")
                listing_row = await self.db.execute(
                    select(Listing).where(Listing.source_url == url)
                )
                listing = listing_row.scalar_one_or_none()
                if listing:
                    rl = ReportListing(
                        report_id=report.id,
                        listing_id=listing.id,
                        match_score=item.get("_match_score", 0),
                        recommendation_rank=item.get("ترتيب_التوصية", 0),
                    )
                    self.db.add(rl)

            await self._update_status(report, "done")
            report.completed_at = datetime.datetime.now(datetime.UTC)
            await self.db.commit()

            logger.info(
                f"Phase 2 complete: report={report.id}, "
                f"matched={report.listings_matched}"
            )
            return report

        except Exception as e:
            logger.exception(f"Phase 2 failed for report {report_id}")
            await self._update_status(report, "failed", str(e))
            return report

    # ─────────────────────────────────────────────────────────────────────────
    # Legacy: run both phases in sequence (kept for API compatibility)
    # ─────────────────────────────────────────────────────────────────────────

    async def run(self, report_id: int) -> CMAReport:
        report = await self.run_phase1(report_id)
        if report.status == "scraped":
            report = await self.run_phase2(report_id)
        return report

    # ─────────────────────────────────────────────────────────────────────────

    async def _update_status(
        self, report: CMAReport, status: str, error: str | None = None
    ):
        report.status = status
        if error:
            report.error_message = error
        await self.db.commit()
        logger.info(f"Report {report.id} status → {status}")
