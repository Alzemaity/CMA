import datetime
import logging
import pathlib

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"
REPORTS_DIR = pathlib.Path("reports")


class ReportGenerator:
    def __init__(self):
        self.env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )

    def generate(
        self,
        kpis: dict,
        matched_listings: list[dict],
        narrative: str,
        city: str,
        neighborhood: str,
        property_type: str,
        offer_type: str,
    ) -> tuple[str, str | None]:
        template = self.env.get_template("cma_report.html")
        html = template.render(
            kpis=kpis,
            listings=matched_listings,
            narrative=narrative,
            city=city,
            neighborhood=neighborhood,
            property_type=property_type,
            offer_type=offer_type,
            date=datetime.date.today().isoformat(),
        )

        pdf_path = self._generate_pdf(html, city, neighborhood)

        return html, pdf_path

    def _generate_pdf(self, html: str, city: str, neighborhood: str) -> str | None:
        try:
            from weasyprint import HTML
            import re

            # Remove Google Fonts imports which cause Arabic rendering issues in WeasyPrint
            html = re.sub(
                r"@import\s+url\(['\"]https://fonts\.googleapis\.com/[^'\"]+['\"]\);\s*",
                "",
                html
            )

            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"cma_{city}_{neighborhood}_{timestamp}.pdf"
            pdf_path = REPORTS_DIR / filename

            HTML(string=html).write_pdf(str(pdf_path))
            logger.info(f"PDF generated: {pdf_path}")
            return str(pdf_path)
        except ImportError:
            logger.warning("WeasyPrint not available, skipping PDF generation")
            return None
        except Exception as e:
            logger.error(f"PDF generation failed: {e}")
            return None
