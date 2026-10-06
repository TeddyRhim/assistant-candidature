"""Connectors and models for official and read-only job sources."""

from src.services.job_sources.base import (
    JobSearchQuery,
    JobSource,
    JobSourceDescriptor,
    JobSourceError,
    RelocationSignal,
    SourceListing,
    clean_html_to_text,
    detect_country_code,
    detect_relocation,
    extract_explicit_email,
)
from src.services.job_sources.greenhouse import (
    GREENHOUSE_DESCRIPTOR,
    extract_greenhouse_board_token,
    fetch_greenhouse_listings,
)
from src.services.job_sources.lever import (
    LEVER_DESCRIPTOR,
    extract_lever_site_slug,
    fetch_lever_listings,
)

__all__ = [
    "GREENHOUSE_DESCRIPTOR",
    "LEVER_DESCRIPTOR",
    "JobSearchQuery",
    "JobSource",
    "JobSourceDescriptor",
    "JobSourceError",
    "RelocationSignal",
    "SourceListing",
    "clean_html_to_text",
    "detect_country_code",
    "detect_relocation",
    "extract_explicit_email",
    "extract_greenhouse_board_token",
    "extract_lever_site_slug",
    "fetch_greenhouse_listings",
    "fetch_lever_listings",
]
