import asyncio
import os
import sys
import pathlib

# Add src/ to sys.path
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from cma.database import async_session, engine
from cma.models import CMAReport, ClientBrief
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from weasyprint import HTML
import re

async def main():
    print("Starting PDF regeneration...")
    async with async_session() as db:
        result = await db.execute(
            select(CMAReport)
            .options(selectinload(CMAReport.brief))
            .where(CMAReport.status == "done")
        )
        reports = result.scalars().all()
        print(f"Found {len(reports)} completed reports.")

        for r in reports:
            if not r.report_html:
                print(f"Report {r.id} has no HTML content. Skipping.")
                continue
            
            # Sanitize the HTML string to remove the Google Fonts import
            html = re.sub(
                r"@import\s+url\(['\"]https://fonts\.googleapis\.com/[^'\"]+['\"]\);\s*",
                "",
                r.report_html
            )
            
            # Get path
            pdf_path_str = r.report_pdf_path
            if not pdf_path_str:
                city = r.brief.city if r.brief else "city"
                neighborhood = r.brief.neighborhood if r.brief else "neighborhood"
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                pdf_path_str = f"reports/cma_{city}_{neighborhood}_{timestamp}.pdf"
                r.report_pdf_path = pdf_path_str
            
            pdf_path = pathlib.Path(pdf_path_str)
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            
            print(f"Regenerating PDF for Report {r.id} at: {pdf_path}")
            try:
                HTML(string=html).write_pdf(str(pdf_path))
                print(f"Successfully generated PDF for Report {r.id}")
            except Exception as e:
                print(f"Failed to generate PDF for Report {r.id}: {e}")
        
        await db.commit()
    print("Completed PDF regeneration!")

if __name__ == "__main__":
    asyncio.run(main())
