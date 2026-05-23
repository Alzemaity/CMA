from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cma.database import get_db
from cma.models import CMAReport, ClientBrief, Listing, ScrapeJob
import pathlib
import datetime
from fastapi.templating import Jinja2Templates

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CMAReport)
        .options(selectinload(CMAReport.brief))
        .order_by(CMAReport.created_at.desc())
        .limit(50)
    )
    reports = result.scalars().all()
    return templates.TemplateResponse(request, "index.html", {"reports": reports})


@router.get("/cma/new", response_class=HTMLResponse)
async def new_cma_form(request: Request):
    return templates.TemplateResponse(request, "brief.html")


@router.post("/cma/new")
async def create_cma(
    request: Request,
    background_tasks: BackgroundTasks,
    city: str = Form(...),
    neighborhood: str = Form(...),
    property_type: str = Form(...),
    offer_type: str = Form(...),
    category: str = Form(""),
    bedrooms: str = Form(""),
    min_price: str = Form(""),
    max_price: str = Form(""),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    amenities_list = form.getlist("amenities")

    brief = ClientBrief(
        city=city,
        neighborhood=neighborhood,
        property_type=property_type,
        offer_type=offer_type,
        category=category or None,
        bedrooms=int(bedrooms) if bedrooms else None,
        min_price=float(min_price) if min_price else None,
        max_price=float(max_price) if max_price else None,
        amenities=amenities_list if amenities_list else None,
        notes=notes or None,
    )
    db.add(brief)
    await db.flush()

    report = CMAReport(brief_id=brief.id, status="pending", phase=2)
    db.add(report)
    await db.commit()
    await db.refresh(report)

    from cma.api.routes import _run_phase2
    background_tasks.add_task(_run_phase2, report.id)

    return RedirectResponse(url=f"/cma/{report.id}/progress", status_code=303)


@router.get("/cma/{report_id}/progress", response_class=HTMLResponse)
async def progress_page(
    request: Request,
    report_id: int,
    db: AsyncSession = Depends(get_db),
):
    report = await db.get(CMAReport, report_id)
    if not report:
        return HTMLResponse("التقرير غير موجود", status_code=404)

    brief = await db.get(ClientBrief, report.brief_id)

    # Phase 1 done → show scraping results page
    if report.status == "scraped":
        return templates.TemplateResponse(request, "phase1_done.html", {
            "report": report,
            "brief": brief,
        })

    # Phase 2 done → redirect to report view
    if report.status == "done":
        return RedirectResponse(url=f"/cma/{report_id}", status_code=303)

    return templates.TemplateResponse(request, "progress.html", {
        "report_id": report_id,
        "brief": brief,
        "status": report.status,
        "phase": report.phase,
        "error": report.error_message,
        "listings_found": report.listings_found,
        "listings_matched": report.listings_matched,
    })


@router.get("/cma/{report_id}/status", response_class=HTMLResponse)
async def status_fragment(
    request: Request,
    report_id: int,
    db: AsyncSession = Depends(get_db),
):
    report = await db.get(CMAReport, report_id)
    if not report:
        return HTMLResponse("غير موجود", status_code=404)

    if report.status == "scraped":
        # Phase 1 complete — redirect to phase1_done page
        return HTMLResponse(
            f'<script>window.location.href="/cma/{report_id}/progress";</script>'
        )

    if report.status == "done":
        return HTMLResponse(
            f'<script>window.location.href="/cma/{report_id}";</script>'
        )

    brief = await db.get(ClientBrief, report.brief_id)
    return templates.TemplateResponse(request, "progress.html", {
        "report_id": report_id,
        "brief": brief,
        "status": report.status,
        "phase": report.phase,
        "error": report.error_message,
        "listings_found": report.listings_found,
        "listings_matched": report.listings_matched,
    })


@router.post("/cma/{report_id}/analyze")
async def start_phase2_web(
    request: Request,
    report_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Web form action: trigger phase 2 and redirect to progress."""
    report = await db.get(CMAReport, report_id)
    if not report:
        return HTMLResponse("التقرير غير موجود", status_code=404)

    report.status = "pending_phase2"
    report.phase = 2
    await db.commit()

    from cma.api.routes import _run_phase2
    background_tasks.add_task(_run_phase2, report_id)

    return RedirectResponse(url=f"/cma/{report_id}/progress", status_code=303)


@router.get("/cma/{report_id}", response_class=HTMLResponse)
async def view_report(
    request: Request,
    report_id: int,
    db: AsyncSession = Depends(get_db),
):
    report = await db.get(CMAReport, report_id)
    if not report:
        return HTMLResponse("التقرير غير موجود", status_code=404)

    brief = await db.get(ClientBrief, report.brief_id)

    if report.status == "scraped":
        return RedirectResponse(url=f"/cma/{report_id}/progress", status_code=303)

    if report.status not in ("done", "failed"):
        return RedirectResponse(url=f"/cma/{report_id}/progress", status_code=303)

    return templates.TemplateResponse(request, "report.html", {
        "report": report,
        "brief": brief,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Decoupled Scraping & Listings view routes
# ─────────────────────────────────────────────────────────────────────────────

async def _run_general_scrape_task(job_id: int):
    import logging
    _logger = logging.getLogger("cma.scrape_task")
    _logger.info(f"[BG Task] Starting scrape for job_id={job_id}")
    try:
        from cma.database import async_session
        from cma.pipeline.orchestrator import CMAOrchestrator

        async with async_session() as db:
            job = await db.get(ScrapeJob, job_id)
            if not job:
                _logger.error(f"[BG Task] ScrapeJob {job_id} not found")
                return

            orchestrator = CMAOrchestrator(db)
            count = await orchestrator.run_general_scrape(
                source=job.source,
                city=job.city,
                neighborhood=job.neighborhood,
                property_type=job.property_type,
                offer_type=job.offer_type,
                limit=job.limit,
                job_id=job_id,
            )
            _logger.info(f"[BG Task] Job {job_id} finished: {count} listings saved.")
    except Exception as exc:
        _logger.exception(f"[BG Task] Job {job_id} failed: {exc}")
        try:
            from cma.database import async_session
            async with async_session() as db:
                job = await db.get(ScrapeJob, job_id)
                if job:
                    job.status = "failed"
                    job.error_message = str(exc)
                    await db.commit()
        except Exception:
            pass


@router.get("/scraper", response_class=HTMLResponse)
async def scraper_form(request: Request):
    return templates.TemplateResponse(request, "scraper.html", {})


@router.post("/scraper")
async def start_scraper(
    request: Request,
    background_tasks: BackgroundTasks,
    source: str = Form(...),
    city: str = Form(...),
    neighborhood: str = Form(...),
    property_type: str = Form(...),
    offer_type: str = Form(...),
    limit: str = Form("20"),
    bedrooms: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    # Create a ScrapeJob record to track this run
    job = ScrapeJob(
        status="pending",
        source=source,
        city=city,
        neighborhood=neighborhood,
        property_type=property_type,
        offer_type=offer_type,
        limit=int(limit) if limit else 20,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    background_tasks.add_task(_run_general_scrape_task, job_id=job.id)
    return RedirectResponse(url=f"/scraper/{job.id}/progress", status_code=303)


@router.get("/scraper/{job_id}/progress", response_class=HTMLResponse)
async def scraper_progress(
    request: Request,
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(ScrapeJob, job_id)
    if not job:
        return HTMLResponse("المهمة غير موجودة", status_code=404)
    return templates.TemplateResponse(request, "scraper_progress.html", {"job": job})


@router.get("/scraper/{job_id}/status", response_class=HTMLResponse)
async def scraper_status(
    request: Request,
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(ScrapeJob, job_id)
    if not job:
        return HTMLResponse("المهمة غير موجودة", status_code=404)
    return templates.TemplateResponse(request, "scraper_status_partial.html", {"job": job})


@router.get("/scraper/{job_id}/excel")
async def download_scrape_excel(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(ScrapeJob, job_id)
    if not job or not job.excel_path:
        return HTMLResponse("لا يوجد ملف إكسيل لهذه المهمة", status_code=404)
    excel_file = pathlib.Path(job.excel_path)
    if not excel_file.exists():
        return HTMLResponse("ملف الإكسيل غير موجود على القرص", status_code=404)
    return FileResponse(
        path=str(excel_file),
        filename=excel_file.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.get("/listings", response_class=HTMLResponse)
async def view_listings_db(
    request: Request,
    source: str = "",
    city: str = "",
    neighborhood: str = "",
    property_type: str = "",
    offer_type: str = "",
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Listing)
    result = await db.execute(stmt)
    db_listings = result.scalars().all()
    
    # Filter listings in Python for cross-DB compatibility (SQLite & Postgres)
    filtered = []
    for db_listing in db_listings:
        if not db_listing.structured_data:
            continue
            
        data = db_listing.structured_data
        
        # Apply filters
        if source and data.get("المصدر") != source:
            continue
        if city and data.get("المدينة") != city:
            continue
        if neighborhood and neighborhood not in (data.get("الحي") or ""):
            continue
        if property_type and data.get("نوع_الوحدة") != property_type:
            continue
        if offer_type and data.get("نوع_العرض") != offer_type:
            continue
            
        filtered.append(db_listing)
        
    total_count = len(filtered)
    
    # Sort by scraped_at descending
    filtered.sort(key=lambda x: x.scraped_at or datetime.datetime.min, reverse=True)
    
    # Limit displayed items to 100 for web responsiveness
    display_listings = filtered[:100]
    
    return templates.TemplateResponse(request, "listings.html", {
        "listings": display_listings,
        "total_count": total_count,
        "filters": {
            "source": source,
            "city": city,
            "neighborhood": neighborhood,
            "property_type": property_type,
            "offer_type": offer_type,
        }
    })
