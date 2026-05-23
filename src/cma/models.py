import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ClientBrief(Base):
    __tablename__ = "client_briefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(100))
    neighborhood: Mapped[str] = mapped_column(String(100))
    property_type: Mapped[str] = mapped_column(String(50))
    offer_type: Mapped[str] = mapped_column(String(20))
    min_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    amenities: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    reports: Mapped[list["CMAReport"]] = relationship(back_populates="brief")


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(String(500), unique=True)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    structured_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    trust_score: Mapped[float] = mapped_column(Float, default=0.0)
    scraped_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    report_listings: Mapped[list["ReportListing"]] = relationship(back_populates="listing")


class CMAReport(Base):
    __tablename__ = "cma_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("client_briefs.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    phase: Mapped[int] = mapped_column(Integer, default=1)  # 1=scraped, 2=analyzed
    listings_found: Mapped[int] = mapped_column(Integer, default=0)
    listings_matched: Mapped[int] = mapped_column(Integer, default=0)
    report_html: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    report_pdf_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    report_excel_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    scraped_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # cleaned listings cache
    kpis: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True
    )

    brief: Mapped["ClientBrief"] = relationship(back_populates="reports")
    report_listings: Mapped[list["ReportListing"]] = relationship(back_populates="report")


class ReportListing(Base):
    __tablename__ = "report_listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("cma_reports.id"))
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    recommendation_rank: Mapped[int] = mapped_column(Integer, default=0)

    report: Mapped["CMAReport"] = relationship(back_populates="report_listings")
    listing: Mapped["Listing"] = relationship(back_populates="report_listings")


class ScrapeJob(Base):
    __tablename__ = "scrape_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|scraping|extracting|saving|done|failed
    source: Mapped[str] = mapped_column(String(50))
    source_label: Mapped[str] = mapped_column(String(50), default="")
    city: Mapped[str] = mapped_column(String(100))
    neighborhood: Mapped[str] = mapped_column(String(100))
    property_type: Mapped[str] = mapped_column(String(50))
    offer_type: Mapped[str] = mapped_column(String(20))
    limit: Mapped[int] = mapped_column(Integer, default=20)
    listings_scraped: Mapped[int] = mapped_column(Integer, default=0)
    listings_saved: Mapped[int] = mapped_column(Integer, default=0)
    excel_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime, nullable=True
    )
