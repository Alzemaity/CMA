from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from cma.database import get_db
from cma.models import CMAReport, ClientBrief
import pathlib
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

    report = CMAReport(brief_id=brief.id, status="pending", phase=1)
    db.add(report)
    await db.commit()
    await db.refresh(report)

    from cma.api.routes import _run_phase1
    background_tasks.add_task(_run_phase1, report.id)

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
