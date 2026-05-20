import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cma.database import get_db
from cma.models import CMAReport, ClientBrief
from cma.pipeline.orchestrator import CMAOrchestrator

router = APIRouter(tags=["cma"])


class BriefCreate(BaseModel):
    city: str
    neighborhood: str
    property_type: str
    offer_type: str
    min_price: float | None = None
    max_price: float | None = None
    bedrooms: int | None = None
    category: str | None = None
    amenities: list[str] | None = None
    notes: str | None = None


class ReportResponse(BaseModel):
    id: int
    brief_id: int
    status: str
    phase: int
    listings_found: int
    listings_matched: int
    kpis: dict | None = None
    error_message: str | None = None

    model_config = {"from_attributes": True}


# ─────────────────────────────────────────────────────────────────────────────
# Background tasks
# ─────────────────────────────────────────────────────────────────────────────

async def _run_phase1(report_id: int):
    from cma.database import async_session

    async with async_session() as db:
        orchestrator = CMAOrchestrator(db)
        await orchestrator.run_phase1(report_id)


async def _run_phase2(report_id: int):
    from cma.database import async_session

    async with async_session() as db:
        orchestrator = CMAOrchestrator(db)
        await orchestrator.run_phase2(report_id)


async def _run_pipeline(report_id: int):
    """Legacy: run both phases sequentially."""
    from cma.database import async_session

    async with async_session() as db:
        orchestrator = CMAOrchestrator(db)
        await orchestrator.run(report_id)


# ─────────────────────────────────────────────────────────────────────────────
# CRUD endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/cma", response_model=ReportResponse)
async def create_cma(
    data: BriefCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    brief = ClientBrief(
        city=data.city,
        neighborhood=data.neighborhood,
        property_type=data.property_type,
        offer_type=data.offer_type,
        min_price=data.min_price,
        max_price=data.max_price,
        bedrooms=data.bedrooms,
        category=data.category,
        amenities=data.amenities,
        notes=data.notes,
    )
    db.add(brief)
    await db.flush()

    report = CMAReport(brief_id=brief.id, status="pending", phase=1)
    db.add(report)
    await db.commit()
    await db.refresh(report)

    background_tasks.add_task(_run_phase1, report.id)

    return report


@router.get("/cma", response_model=list[ReportResponse])
async def list_cmas(
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CMAReport).order_by(CMAReport.created_at.desc()).offset(skip).limit(limit)
    )
    return result.scalars().all()


@router.get("/cma/{report_id}", response_model=ReportResponse)
async def get_cma(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(CMAReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="التقرير غير موجود")
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Phase-2 trigger
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/cma/{report_id}/analyze")
async def start_phase2(
    report_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Trigger phase 2 (matching + analysis + PDF) for a scraped report."""
    report = await db.get(CMAReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="التقرير غير موجود")
    if report.status not in ("scraped", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"لا يمكن بدء المرحلة الثانية — الحالة الحالية: {report.status}",
        )
    # Reset to allow re-run
    report.status = "pending_phase2"
    await db.commit()

    background_tasks.add_task(_run_phase2, report_id)
    return {"message": "تم بدء التحليل", "report_id": report_id}


# ─────────────────────────────────────────────────────────────────────────────
# Download endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/cma/{report_id}/html")
async def get_cma_html(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(CMAReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="التقرير غير موجود")
    if not report.report_html:
        raise HTTPException(status_code=404, detail="التقرير لم يُنشأ بعد")
    from fastapi.responses import HTMLResponse

    return HTMLResponse(content=report.report_html)


@router.get("/cma/{report_id}/pdf")
async def get_cma_pdf(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(CMAReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="التقرير غير موجود")
    if not report.report_pdf_path:
        raise HTTPException(status_code=404, detail="ملف PDF غير متوفر")
    return FileResponse(
        report.report_pdf_path,
        media_type="application/pdf",
        filename=f"cma_report_{report_id}.pdf",
    )


@router.get("/cma/{report_id}/excel")
async def get_cma_excel(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(CMAReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="التقرير غير موجود")
    if not report.report_excel_path:
        raise HTTPException(status_code=404, detail="ملف Excel غير متوفر بعد")
    return FileResponse(
        report.report_excel_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"cma_data_{report_id}.xlsx",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Delete
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/cma/{report_id}")
async def delete_cma(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(CMAReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="التقرير غير موجود")
    await db.delete(report)
    await db.commit()
    return {"message": "تم حذف التقرير"}
