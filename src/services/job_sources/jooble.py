from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import AnyHttpUrl, ValidationError

from src.config import get_data_dir
from src.models import ContractType
from src.services.job_sources.base import (
    JobSourceDescriptor,
    JobSourceError,
    SourceListing,
    clean_html_to_text,
)
from src.services.job_sources.remote_common import RemoteBoardRateLimited

# La clé est liée au site français : sur jooble.org elle répond 403, sur fr.jooble.org elle passe.
JOOBLE_API_ROOT = "https://fr.jooble.org/api"
JOOBLE_SOURCE_NAME = "Jooble"
JOOBLE_TIMEOUT_SECONDS = 20
JOOBLE_PAUSE_SECONDS = 1.0

# Quota de la clé gratuite : 500 requêtes. On garde une réserve et on plafonne chaque passage.
JOOBLE_REQUEST_LIMIT = 500
JOOBLE_RESERVE = 25
JOOBLE_MAX_REQUESTS_PER_RUN = 10
JOOBLE_TERM_LIMIT = 3

JOOBLE_DESCRIPTOR = JobSourceDescriptor(
    source_id="jooble",
    display_name="Jooble",
    access_method="official_api",
    documentation_url="https://jooble.org/api/about",
    enabled=True,
)


@dataclass
class JoobleUsage:
    """Requêtes déjà envoyées avec la clé (compteur local, jamais remis à zéro tout seul)."""

    used: int = 0
    limit: int = JOOBLE_REQUEST_LIMIT
    reserve: int = JOOBLE_RESERVE

    @property
    def remaining(self) -> int:
        """Requêtes encore utilisables par la veille, réserve déduite."""
        return max(0, self.limit - self.reserve - self.used)


def get_usage_path() -> Path:
    return get_data_dir() / "jooble_usage.json"


def load_usage() -> JoobleUsage:
    try:
        raw = json.loads(get_usage_path().read_text(encoding="utf-8"))
        return JoobleUsage(
            used=max(0, int(raw.get("used", 0))),
            limit=int(raw.get("limit", JOOBLE_REQUEST_LIMIT)),
            reserve=int(raw.get("reserve", JOOBLE_RESERVE)),
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return JoobleUsage()


def save_usage(usage: JoobleUsage) -> None:
    path = get_usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(usage)), encoding="utf-8")


def record_request() -> JoobleUsage:
    """Compte une requête AVANT de l'envoyer : un plantage ne fait jamais sous-estimer l'usage."""
    usage = load_usage()
    usage.used += 1
    save_usage(usage)
    return usage


def reset_usage() -> None:
    """À utiliser seulement si Jooble remet le quota à zéro."""
    save_usage(JoobleUsage())


@dataclass
class JoobleResult:
    listings: list[SourceListing] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requests_made: int = 0
    rate_limited: bool = False
    budget_exhausted: bool = False


def post_json(url: str, body: dict[str, Any], *, timeout: float = JOOBLE_TIMEOUT_SECONDS) -> Any:
    """POST JSON. L'adresse contient la clé : elle n'apparaît jamais dans les messages d'erreur."""
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "assistant-candidatures/0.1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 429:
            raise RemoteBoardRateLimited(
                "Jooble : limite de requêtes atteinte (HTTP 429)."
            ) from error
        if error.code in (401, 403):
            raise JobSourceError(f"Jooble a refusé la clé (HTTP {error.code}).") from error
        raise JobSourceError(f"Jooble a refusé la requête (HTTP {error.code}).") from error
    except (URLError, TimeoutError, ConnectionResetError) as error:
        raise JobSourceError("Impossible de joindre Jooble. Vérifie la connexion.") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JobSourceError("Jooble a renvoyé une réponse JSON invalide.") from error


def _contract_type(raw_type: object) -> ContractType | None:
    text = str(raw_type or "").casefold()
    if "intern" in text or "stage" in text:
        return "Stage"
    if "apprent" in text or "alternan" in text:
        return "Alternance"
    if "contract" in text or "freelance" in text:
        return "Freelance"
    if "temporar" in text or "cdd" in text:
        return "CDD"
    return None


def map_jooble_job(item: object) -> SourceListing | None:
    if not isinstance(item, dict):
        return None
    title = clean_html_to_text(str(item.get("title") or "")).strip()
    link = item.get("link")
    raw_id = item.get("id")
    if not title or not isinstance(link, str) or not link.strip():
        return None
    snippet = clean_html_to_text(str(item.get("snippet") or "")).strip()
    salary = str(item.get("salary") or "").strip()
    description = snippet + (f"\nSalaire indiqué : {salary}." if salary else "")
    origin = str(item.get("source") or "").strip()
    source_name = f"{JOOBLE_SOURCE_NAME} ({origin})" if origin else JOOBLE_SOURCE_NAME
    try:
        return SourceListing(
            source_id=f"jooble-{raw_id or link}"[:100],
            source_name=source_name[:200],
            title=title[:250],
            company=str(item.get("company") or "").strip()[:200],
            location=str(item.get("location") or "").strip()[:200],
            contract_type=_contract_type(item.get("type")),
            original_url=AnyHttpUrl(link.strip()),
            description=description.strip(),
        )
    except ValidationError:
        return None


def search_jooble(
    api_key: str,
    terms: Sequence[str],
    locations: Sequence[str],
    *,
    max_requests: int = JOOBLE_MAX_REQUESTS_PER_RUN,
    pause_seconds: float = JOOBLE_PAUSE_SECONDS,
    post: Callable[..., Any] = post_json,
    sleep: Callable[[float], None] = time.sleep,
) -> JoobleResult:
    """Une requête par couple (technologie, zone), dans la limite du passage et du quota.

    Les technologies passent en premier : chacune est cherchée dans toutes les zones avant de
    passer à la suivante. Chaque requête est comptée avant l'envoi.
    """
    result = JoobleResult()
    if not api_key.strip():
        return result
    seen: set[str] = set()
    pairs = [(term, zone) for term in terms for zone in (locations or [""])]
    url = f"{JOOBLE_API_ROOT}/{api_key.strip()}"
    for term, zone in pairs:
        usage = load_usage()
        if usage.remaining <= 0:
            result.budget_exhausted = True
            result.errors.append(
                f"Jooble : quota presque épuisé ({usage.used}/{usage.limit} requêtes utilisées)."
            )
            break
        if result.requests_made >= max_requests:
            break
        if result.requests_made:
            sleep(pause_seconds)
        record_request()
        result.requests_made += 1
        body = {"keywords": term, "location": zone}
        try:
            payload = post(url, body)
        except RemoteBoardRateLimited as error:
            result.errors.append(str(error))
            result.rate_limited = True
            break
        except JobSourceError as error:
            result.errors.append(f"Jooble ({term}, {zone or 'France'}) : {error}")
            continue
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            continue
        for item in jobs:
            listing = map_jooble_job(item)
            if listing is not None and listing.source_id not in seen:
                seen.add(listing.source_id)
                result.listings.append(listing)
    return result
