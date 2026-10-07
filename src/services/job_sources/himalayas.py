from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlencode

from pydantic import AnyHttpUrl, ValidationError

from src.models import ContractType
from src.services.job_sources.base import (
    JobSourceDescriptor,
    JobSourceError,
    SourceListing,
    clean_html_to_text,
    detect_relocation,
)
from src.services.job_sources.remote_common import (
    FULL_REMOTE_LABEL,
    REMOTE_PAUSE_SECONDS,
    RemoteBoardRateLimited,
    RemoteBoardResult,
    fetch_json,
)

HIMALAYAS_SEARCH_URL = "https://himalayas.app/jobs/api/search"
HIMALAYAS_SOURCE_NAME = "Himalayas"
HIMALAYAS_MAX_PAGES = 2

HIMALAYAS_DESCRIPTOR = JobSourceDescriptor(
    source_id="himalayas",
    display_name="Himalayas (télétravail)",
    access_method="official_api",
    documentation_url="https://himalayas.app/docs/remote-jobs-api",
    enabled=True,
)

_CONTRACTS: dict[str, ContractType] = {
    "contractor": "Freelance",
    "intern": "Stage",
    "internship": "Stage",
    "temporary": "CDD",
}


def _contract_type(employment_type: object) -> ContractType | None:
    return _CONTRACTS.get(str(employment_type or "").strip().casefold())


def map_himalayas_job(item: object) -> SourceListing | None:
    """Transforme une offre Himalayas ; ignore celles qui excluent la France."""
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    guid = item.get("guid")
    link = item.get("applicationLink") or guid
    if not isinstance(title, str) or not isinstance(guid, str) or not isinstance(link, str):
        return None

    restrictions = item.get("locationRestrictions")
    if isinstance(restrictions, list) and restrictions and "France" not in restrictions:
        return None

    raw_description = item.get("description") or item.get("excerpt") or ""
    body = clean_html_to_text(str(raw_description))
    seniority = item.get("seniority")
    level = (
        f"Niveau indiqué : {', '.join(str(s) for s in seniority)}.\n"
        if isinstance(seniority, list) and seniority
        else ""
    )
    description = f"{FULL_REMOTE_LABEL}.\n{level}{body}".strip()
    relocation_signal, relocation_evidence = detect_relocation(description)
    try:
        return SourceListing(
            source_id=f"himalayas-{guid}"[:100],
            source_name=HIMALAYAS_SOURCE_NAME,
            title=title.strip()[:250],
            company=str(item.get("companyName") or "").strip()[:200],
            location=FULL_REMOTE_LABEL,
            contract_type=_contract_type(item.get("employmentType")),
            original_url=AnyHttpUrl(link.strip()),
            description=description,
            relocation_signal=relocation_signal,
            relocation_evidence=relocation_evidence,
        )
    except ValidationError:
        return None


def search_himalayas(
    terms: Sequence[str],
    *,
    country: str = "FR",
    pause_seconds: float = REMOTE_PAUSE_SECONDS,
    max_pages: int = HIMALAYAS_MAX_PAGES,
    fetch: Callable[..., Any] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> RemoteBoardResult:
    """Une requête par mot-clé : offres ouvertes à la France, puis offres mondiales.

    Une requête à la fois, espacées ; sur un 429 on s'arrête et on garde ce qui est déjà lu.
    """
    result = RemoteBoardResult()
    seen: set[str] = set()
    first_request = True
    for term in terms:
        for scope in ({"country": country}, {"worldwide": "true"}):
            for page in range(1, max_pages + 1):
                if not first_request:
                    sleep(pause_seconds)
                first_request = False
                query = urlencode({"q": term, **scope, "page": page})
                try:
                    payload = fetch(f"{HIMALAYAS_SEARCH_URL}?{query}", source=HIMALAYAS_SOURCE_NAME)
                except RemoteBoardRateLimited as error:
                    result.errors.append(str(error))
                    result.rate_limited = True
                    return result
                except JobSourceError as error:
                    result.errors.append(f"{HIMALAYAS_SOURCE_NAME} ({term}) : {error}")
                    break
                jobs = payload.get("jobs") if isinstance(payload, dict) else None
                if not isinstance(jobs, list) or not jobs:
                    break
                for item in jobs:
                    listing = map_himalayas_job(item)
                    if listing is not None and listing.source_id not in seen:
                        seen.add(listing.source_id)
                        result.listings.append(listing)
    return result
