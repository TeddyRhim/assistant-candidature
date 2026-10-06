from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
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

LEVER_API_ROOT = "https://api.lever.co/v0/postings"
LEVER_EU_API_ROOT = "https://api.eu.lever.co/v0/postings"
LEVER_TIMEOUT_SECONDS = 15

LEVER_DESCRIPTOR = JobSourceDescriptor(
    source_id="lever",
    display_name="Lever Postings API",
    access_method="official_api",
    documentation_url="https://github.com/Lever/postings-api",
    enabled=True,
)


def extract_lever_site_slug(value: str) -> str:
    """Extract a Lever site slug from a raw string or full URL."""
    cleaned = value.strip()
    if cleaned.startswith(("http://", "https://")):
        parsed = urlsplit(cleaned)
        parts = [segment for segment in parsed.path.split("/") if segment]
        if parts:
            return parts[0].strip()
    return cleaned


def _validate_site_slug(site: str) -> str:
    cleaned = extract_lever_site_slug(site)
    if not cleaned or not re.fullmatch(r"[A-Za-z0-9_-]{2,100}", cleaned):
        raise JobSourceError(
            "L'identifiant du site Lever doit comporter entre 2 et 100 caractères "
            "alphanuériques (lettres, chiffres, tirets ou tirets bas)."
        )
    return cleaned


def _map_commitment_to_contract_type(commitment: str, full_text: str) -> ContractType | None:
    cleaned = commitment.casefold().strip()
    if "full" in cleaned or "permanent" in cleaned or "cdi" in cleaned:
        return "CDI"
    if "intern" in cleaned or "stage" in cleaned:
        return "Stage"
    if "apprentice" in cleaned or "alternan" in cleaned:
        return "Alternance"
    if "contract" in cleaned or "freelance" in cleaned or "temp" in cleaned:
        return "Freelance"
    if "part" in cleaned:
        return "Autre"

    # Fallback to text detection
    combined = full_text.casefold()
    if re.search(r"\b(?:stage|internship)\b", combined):
        return "Stage"
    if re.search(r"\b(?:alternance|apprenticeship)\b", combined):
        return "Alternance"
    if re.search(r"\b(?:cdi|permanent)\b", combined):
        return "CDI"
    if re.search(r"\b(?:cdd)\b", combined):
        return "CDD"
    return None


def fetch_lever_listings(
    site: str,
    *,
    company_name: str = "",
    use_eu_endpoint: bool = False,
    timeout: float = LEVER_TIMEOUT_SECONDS,
) -> list[SourceListing]:
    """Fetch published jobs from a company's Lever Postings feed."""
    slug = _validate_site_slug(site)
    is_eu = use_eu_endpoint or ("eu.lever.co" in site.casefold())
    base_root = LEVER_EU_API_ROOT if is_eu else LEVER_API_ROOT
    url = f"{base_root}/{slug}?mode=json"

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
                f"Le site d'offres « {slug} » est introuvable sur Lever (HTTP 404). "
                "Vérifie le nom ou identifiant de l'entreprise."
            ) from error
        raise JobSourceError(
            f"Lever a refusé la requête (HTTP {error.code})."
        ) from error
    except (URLError, TimeoutError, ConnectionResetError) as error:
        raise JobSourceError(
            "Impossible de joindre l'API Lever. Vérifie la connexion réseau."
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JobSourceError("Lever a renvoyé une réponse JSON invalide.") from error

    if not isinstance(payload, list):
        raise JobSourceError(
            "La réponse Lever n'a pas le format attendu (liste de postes attendue)."
        )

    resolved_company = company_name.strip() or slug.replace("-", " ").replace("_", " ").title()

    listings: list[SourceListing] = []
    for item in payload:
        listing = _map_lever_posting(item, resolved_company)
        if listing is not None:
            listings.append(listing)
    return listings


def _map_lever_posting(item: object, default_company: str) -> SourceListing | None:
    if not isinstance(item, dict):
        return None
    raw_id = item.get("id")
    title = item.get("text")
    hosted_url = item.get("hostedUrl") or item.get("applyUrl")
    if not raw_id or not isinstance(title, str) or not isinstance(hosted_url, str):
        return None

    categories = item.get("categories", {})
    location = ""
    commitment = ""
    if isinstance(categories, dict):
        location = str(categories.get("location") or "").strip()
        workplace_type = str(categories.get("workplaceType") or "").strip()
        if workplace_type and workplace_type.casefold() not in location.casefold():
            location = (
                f"{location} ({workplace_type.capitalize()})"
                if location
                else workplace_type.capitalize()
            )
        commitment = str(categories.get("commitment") or "").strip()

    # Build comprehensive description text
    parts: list[str] = []
    desc_plain = item.get("descriptionPlain")
    if isinstance(desc_plain, str) and desc_plain.strip():
        parts.append(desc_plain.strip())
    elif item.get("description"):
        parts.append(clean_html_to_text(str(item["description"])))

    lists = item.get("lists")
    if isinstance(lists, list):
        for section in lists:
            if isinstance(section, dict):
                heading = str(section.get("text") or "").strip()
                content = clean_html_to_text(str(section.get("content") or ""))
                if heading and content:
                    parts.append(f"{heading}:\n{content}")
                elif content:
                    parts.append(content)

    add_plain = item.get("additionalPlain")
    if isinstance(add_plain, str) and add_plain.strip():
        parts.append(add_plain.strip())
    elif item.get("additional"):
        parts.append(clean_html_to_text(str(item["additional"])))

    full_description = "\n\n".join(part for part in parts if part).strip()

    country_code = detect_country_code(location)
    contract_type = _map_commitment_to_contract_type(commitment, f"{title}\n{full_description}")
    reloc_signal, reloc_evidence = detect_relocation(full_description)
    contact_email = extract_explicit_email(full_description)

    try:
        url_obj = AnyHttpUrl(hosted_url.strip())
        return SourceListing(
            source_id=f"lever-{raw_id}",
            source_name="Lever",
            title=title.strip(),
            company=default_company,
            location=location,
            country_code=country_code,
            contract_type=contract_type,
            original_url=url_obj,
            description=full_description,
            public_contact_email=contact_email,
            contact_source_url=url_obj if contact_email else None,
            relocation_signal=reloc_signal,
            relocation_evidence=reloc_evidence,
        )
    except ValidationError:
        return None
