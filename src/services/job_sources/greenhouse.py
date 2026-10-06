from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from pydantic import AnyHttpUrl, ValidationError

from src.models import ContractType
from src.services.job_sources.base import (
    JobSourceDescriptor,
    JobSourceError,
    SourceListing,
    clean_html_to_text,
    detect_country_code,
    detect_relocation,
    extract_explicit_email,
)

GREENHOUSE_API_ROOT = "https://boards-api.greenhouse.io/v1/boards"
GREENHOUSE_TIMEOUT_SECONDS = 15

GREENHOUSE_DESCRIPTOR = JobSourceDescriptor(
    source_id="greenhouse",
    display_name="Greenhouse Job Board",
    access_method="official_api",
    documentation_url="https://docs.greenhouse.io/job-board.html",
    enabled=True,
)


def extract_greenhouse_board_token(value: str) -> str:
    """Extract a Greenhouse board token from a raw string or full URL."""
    cleaned = value.strip()
    if cleaned.startswith(("http://", "https://")):
        parsed = urlsplit(cleaned)
        query_params = parse_qs(parsed.query)
        if "for" in query_params and query_params["for"]:
            return query_params["for"][0].strip()
        parts = [segment for segment in parsed.path.split("/") if segment]
        if parts:
            if parts[0] in {"embed", "v1"} and len(parts) > 1:
                return parts[1].strip()
            return parts[0].strip()
    return cleaned


def _validate_board_token(board_token: str) -> str:
    cleaned = extract_greenhouse_board_token(board_token)
    if not cleaned or not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", cleaned):
        raise JobSourceError(
            "L'identifiant du tableau Greenhouse doit comporter entre 2 et 100 caractères "
            "alphanuériques (lettres, chiffres, tirets ou tirets bas)."
        )
    return cleaned


def _detect_contract_type(title: str, text: str) -> ContractType | None:
    combined = f"{title}\n{text}".casefold()
    if re.search(r"\b(?:stage|internship|intern)\b", combined):
        return "Stage"
    if re.search(r"\b(?:alternance|apprenticeship|apprentice)\b", combined):
        return "Alternance"
    if re.search(r"\b(?:freelance|contractor|indépendant)\b", combined):
        return "Freelance"
    if re.search(r"\b(?:cdd|fixed[\s-]term)\b", combined):
        return "CDD"
    if re.search(r"\b(?:cdi|permanent|full[\s-]time)\b", combined):
        return "CDI"
    return None


def fetch_greenhouse_listings(
    board_token: str,
    *,
    company_name: str = "",
    timeout: float = GREENHOUSE_TIMEOUT_SECONDS,
) -> list[SourceListing]:
    """Fetch published jobs from a company's Greenhouse Job Board."""
    token = _validate_board_token(board_token)
    url = f"{GREENHOUSE_API_ROOT}/{token}/jobs?content=true"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "assistant-candidatures/0.1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 404:
            raise JobSourceError(
                f"Le tableau d'affichage « {token} » est introuvable sur Greenhouse (HTTP 404). "
                "Vérifie le nom ou slug du tableau."
            ) from error
        raise JobSourceError(
            f"Greenhouse a refusé la requête (HTTP {error.code})."
        ) from error
    except (URLError, TimeoutError, ConnectionResetError) as error:
        raise JobSourceError(
            "Impossible de joindre l'API Greenhouse. Vérifie la connexion réseau."
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JobSourceError("Greenhouse a renvoyé une réponse JSON invalide.") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise JobSourceError(
            "La réponse Greenhouse n'a pas le format attendu (clé 'jobs' manquante)."
        )

    jobs_data: list[object] = payload["jobs"]
    resolved_company = company_name.strip() or token.replace("-", " ").replace("_", " ").title()

    listings: list[SourceListing] = []
    for item in jobs_data:
        listing = _map_greenhouse_job(item, resolved_company)
        if listing is not None:
            listings.append(listing)
    return listings


def _map_greenhouse_job(item: object, default_company: str) -> SourceListing | None:
    if not isinstance(item, dict):
        return None
    raw_id = item.get("id")
    title = item.get("title")
    absolute_url = item.get("absolute_url")
    if raw_id is None or not isinstance(title, str) or not isinstance(absolute_url, str):
        return None

    raw_location = item.get("location")
    location_name = ""
    if isinstance(raw_location, dict) and isinstance(raw_location.get("name"), str):
        location_name = raw_location["name"].strip()

    raw_content = item.get("content")
    clean_description = clean_html_to_text(str(raw_content)) if raw_content else ""

    country_code = detect_country_code(location_name)
    contract_type = _detect_contract_type(title, clean_description)
    reloc_signal, reloc_evidence = detect_relocation(clean_description)
    contact_email = extract_explicit_email(clean_description)

    try:
        url_obj = AnyHttpUrl(absolute_url.strip())
        return SourceListing(
            source_id=f"greenhouse-{raw_id}",
            source_name="Greenhouse",
            title=title.strip(),
            company=default_company,
            location=location_name,
            country_code=country_code,
            contract_type=contract_type,
            original_url=url_obj,
            description=clean_description,
            public_contact_email=contact_email,
            contact_source_url=url_obj if contact_email else None,
            relocation_signal=reloc_signal,
            relocation_evidence=reloc_evidence,
        )
    except ValidationError:
        return None
