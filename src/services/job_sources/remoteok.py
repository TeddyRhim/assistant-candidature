from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote

from pydantic import AnyHttpUrl, ValidationError

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
    is_open_to_france,
)

REMOTEOK_API_URL = "https://remoteok.com/api"
REMOTEOK_SOURCE_NAME = "Remote OK"

REMOTEOK_DESCRIPTOR = JobSourceDescriptor(
    source_id="remoteok",
    display_name="Remote OK (télétravail)",
    access_method="official_api",
    documentation_url="https://remoteok.com/api",
    enabled=True,
)


def map_remoteok_job(item: object) -> SourceListing | None:
    """Transforme une offre Remote OK ; ignore le message légal et les offres hors zone.

    L'adresse conservée est la page Remote OK de l'offre : leurs conditions demandent de renvoyer
    vers leur site et de citer la source.
    """
    if not isinstance(item, dict) or "legal" in item:
        return None
    position = item.get("position")
    url = item.get("url")
    raw_id = item.get("id")
    if not isinstance(position, str) or not isinstance(url, str) or raw_id is None:
        return None
    if not is_open_to_france(str(item.get("location") or "")):
        return None

    body = clean_html_to_text(str(item.get("description") or ""))
    tags = item.get("tags")
    technologies = (
        f"Technologies : {', '.join(str(tag) for tag in tags)}.\n"
        if isinstance(tags, list) and tags
        else ""
    )
    description = f"{FULL_REMOTE_LABEL}.\n{technologies}{body}".strip()
    relocation_signal, relocation_evidence = detect_relocation(description)
    try:
        return SourceListing(
            source_id=f"remoteok-{raw_id}"[:100],
            source_name=REMOTEOK_SOURCE_NAME,
            title=position.strip()[:250],
            company=str(item.get("company") or "").strip()[:200],
            location=FULL_REMOTE_LABEL,
            original_url=AnyHttpUrl(url.strip()),
            description=description,
            relocation_signal=relocation_signal,
            relocation_evidence=relocation_evidence,
        )
    except ValidationError:
        return None


def search_remoteok(
    tags: Sequence[str],
    *,
    pause_seconds: float = REMOTE_PAUSE_SECONDS,
    fetch: Callable[..., Any] = fetch_json,
    sleep: Callable[[float], None] = time.sleep,
) -> RemoteBoardResult:
    """Une requête par étiquette technologique, espacées ; s'arrête sur un 429."""
    result = RemoteBoardResult()
    seen: set[str] = set()
    for index, tag in enumerate(tags):
        if index:
            sleep(pause_seconds)
        url = f"{REMOTEOK_API_URL}?tag={quote(tag.strip().casefold())}"
        try:
            payload = fetch(url, source=REMOTEOK_SOURCE_NAME)
        except RemoteBoardRateLimited as error:
            result.errors.append(str(error))
            result.rate_limited = True
            return result
        except JobSourceError as error:
            result.errors.append(f"{REMOTEOK_SOURCE_NAME} ({tag}) : {error}")
            continue
        if not isinstance(payload, list):
            result.errors.append(f"{REMOTEOK_SOURCE_NAME} ({tag}) : réponse inattendue.")
            continue
        for item in payload:
            listing = map_remoteok_job(item)
            if listing is not None and listing.source_id not in seen:
                seen.add(listing.source_id)
                result.listings.append(listing)
    return result
