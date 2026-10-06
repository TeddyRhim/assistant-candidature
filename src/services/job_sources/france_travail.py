from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import AnyHttpUrl, ValidationError

from src.models import ContractType, ProfileData
from src.services.job_sources.adzuna import (
    ProfileSearchResult,
    ordered_profile_terms,
    profile_search_keywords,
)
from src.services.job_sources.base import (
    JobSourceDescriptor,
    JobSourceError,
    SourceListing,
    clean_html_to_text,
    detect_relocation,
    extract_explicit_email,
)

FRANCE_TRAVAIL_TOKEN_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
)
FRANCE_TRAVAIL_API_ROOT = "https://api.francetravail.io/partenaire/offresdemploi/v2"
FRANCE_TRAVAIL_SCOPE = "api_offresdemploiv2 o2dsoffre"
FRANCE_TRAVAIL_FALLBACK_SCOPE = "api_offresdemploiv2"
FRANCE_TRAVAIL_OFFER_URL = "https://candidat.francetravail.fr/offres/recherche/detail/{offer_id}"
FRANCE_TRAVAIL_TIMEOUT_SECONDS = 20
FRANCE_TRAVAIL_PAGE_SIZE = 50
FRANCE_TRAVAIL_MAX_PAGE_SIZE = 150
FRANCE_TRAVAIL_WORKERS = 4
PUBLISHED_SINCE_CHOICES = (1, 3, 7, 14, 31)
DEFAULT_DEPARTMENTS = ("06", "83", "13", "75")
DEPARTMENT_NAMES = {
    "06": "Alpes-Maritimes",
    "83": "Var",
    "13": "Bouches-du-Rhône",
    "75": "Paris",
    "84": "Vaucluse",
    "34": "Hérault",
    "69": "Rhône",
    "31": "Haute-Garonne",
}
CONTRACT_CODES: dict[ContractType, str] = {"CDI": "CDI", "CDD": "CDD", "Freelance": "LIB"}

FRANCE_TRAVAIL_DESCRIPTOR = JobSourceDescriptor(
    source_id="france-travail",
    display_name="France Travail — Offres d'emploi",
    access_method="official_api",
    documentation_url="https://francetravail.io/data/api/offres-emploi",
    enabled=True,
)

_TOKEN_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


def _post_token_request(client_id: str, client_secret: str, scope: str) -> dict[str, object]:
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }
    ).encode("ascii")
    request = Request(
        FRANCE_TRAVAIL_TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "assistant-candidatures/0.1",
        },
    )
    with urlopen(request, timeout=FRANCE_TRAVAIL_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("réponse inattendue")
    return payload


def get_access_token(
    client_id: str,
    client_secret: str,
    *,
    scopes: tuple[str, ...] = (FRANCE_TRAVAIL_SCOPE, FRANCE_TRAVAIL_FALLBACK_SCOPE),
    api_label: str = "Offres d'emploi",
) -> str:
    """Retourne un jeton OAuth2 (flux client_credentials), mis en cache jusqu'à expiration.

    `scopes` liste les scopes à essayer dans l'ordre (le suivant sert de repli) ; `api_label`
    nomme l'API dans les messages d'erreur.
    """
    if not client_id.strip() or not client_secret.strip():
        raise JobSourceError(
            "Les identifiants France Travail (client id et secret) doivent être configurés "
            "localement."
        )
    cache_key = f"{client_id.strip()}|{scopes[0]}"
    with _TOKEN_LOCK:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        payload = _request_token_with_fallback(
            client_id.strip(), client_secret.strip(), scopes, api_label
        )
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise JobSourceError("France Travail n'a pas renvoyé de jeton d'accès valide.")
        expires_in = payload.get("expires_in")
        lifetime = float(expires_in) if isinstance(expires_in, (int, float)) else 1200.0
        _TOKEN_CACHE[cache_key] = (token, time.monotonic() + max(lifetime - 60, 30))
        return token


def _request_token_with_fallback(
    client_id: str,
    client_secret: str,
    scopes: tuple[str, ...],
    api_label: str,
) -> dict[str, object]:
    for index, scope in enumerate(scopes):
        try:
            return _post_token_request(client_id, client_secret, scope)
        except HTTPError as error:
            if error.code == 400 and index < len(scopes) - 1:
                continue  # scope refusé : on retente avec le scope suivant
            oauth_error = _oauth_error_code(error)
            if oauth_error == "invalid_scope":
                raise JobSourceError(
                    f"L'application France Travail n'est pas abonnée à l'API « {api_label} » "
                    "(scope refusé). Sur francetravail.io, ajoute cette API à ton application."
                ) from error
            if error.code in {400, 401, 403}:
                raise JobSourceError(
                    f"France Travail a refusé l'authentification (HTTP {error.code}) : "
                    "vérifie le client id et le secret de l'application."
                ) from error
            raise JobSourceError(
                f"France Travail a refusé la demande de jeton (HTTP {error.code})."
            ) from error
        except (URLError, TimeoutError, ConnectionResetError) as error:
            raise JobSourceError(
                "Impossible de joindre France Travail. Vérifie la connexion."
            ) from error
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise JobSourceError(
                "France Travail a renvoyé une réponse de jeton invalide."
            ) from error
    raise JobSourceError("France Travail a refusé l'authentification.")


def _oauth_error_code(error: HTTPError) -> str:
    """Code d'erreur OAuth2 (`invalid_scope`, `invalid_client`…) du corps de la réponse."""
    try:
        payload = json.loads(error.read())
    except (OSError, ValueError):
        return ""
    return str(payload.get("error", "")) if isinstance(payload, dict) else ""


def _clear_token_cache() -> None:
    with _TOKEN_LOCK:
        _TOKEN_CACHE.clear()


def _clean_keywords(keywords: list[str]) -> str:
    joined = " ".join(keywords)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s+#.-]", " ", joined)).strip()


def search_france_travail(
    keywords: list[str],
    client_id: str,
    client_secret: str,
    *,
    department: str | None = None,
    contract_codes: tuple[str, ...] = (),
    published_since_days: int | None = None,
    limit: int = FRANCE_TRAVAIL_PAGE_SIZE,
    access_token: str | None = None,
) -> list[SourceListing]:
    if not 1 <= limit <= FRANCE_TRAVAIL_MAX_PAGE_SIZE:
        raise JobSourceError(
            f"Le nombre de résultats doit être compris entre 1 et {FRANCE_TRAVAIL_MAX_PAGE_SIZE}."
        )
    if department is not None and not re.fullmatch(r"\d{2,3}|2[AB]", department):
        raise JobSourceError("Le département doit être un code comme 06, 83 ou 2A.")
    if published_since_days is not None and published_since_days not in PUBLISHED_SINCE_CHOICES:
        raise JobSourceError("La période de publication doit être de 1, 3, 7, 14 ou 31 jours.")

    params: dict[str, object] = {"range": f"0-{limit - 1}"}
    cleaned_keywords = _clean_keywords(keywords)
    if cleaned_keywords:
        params["motsCles"] = cleaned_keywords
    if department:
        params["departement"] = department
    if contract_codes:
        params["typeContrat"] = ",".join(contract_codes)
    if published_since_days:
        params["publieeDepuis"] = published_since_days

    token = access_token or get_access_token(client_id, client_secret)
    request = Request(
        f"{FRANCE_TRAVAIL_API_ROOT}/offres/search?{urlencode(params)}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "assistant-candidatures/0.1",
        },
    )
    try:
        with urlopen(request, timeout=FRANCE_TRAVAIL_TIMEOUT_SECONDS) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
    except HTTPError as error:
        if error.code in {401, 403}:
            _clear_token_cache()
            raise JobSourceError(
                f"France Travail a refusé la recherche (HTTP {error.code}) : vérifie "
                "l'abonnement de l'application à l'API « Offres d'emploi »."
            ) from error
        if error.code == 429:
            raise JobSourceError(
                "France Travail limite le débit des requêtes (HTTP 429). Réessaie plus tard."
            ) from error
        raise JobSourceError(
            f"France Travail a refusé la recherche (HTTP {error.code})."
        ) from error
    except (URLError, TimeoutError, ConnectionResetError) as error:
        raise JobSourceError(
            "Impossible de joindre France Travail. Vérifie la connexion."
        ) from error

    if status == 204 or not raw.strip():
        return []  # 204 : aucune offre pour ces critères
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JobSourceError("France Travail a renvoyé une réponse JSON invalide.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("resultats"), list):
        raise JobSourceError("La réponse France Travail n'a pas le format attendu.")

    listings = []
    for item in payload["resultats"]:
        listing = _map_offer(item)
        if listing is not None:
            listings.append(listing)
    return listings


def search_france_travail_for_profile(
    profile: ProfileData,
    client_id: str,
    client_secret: str,
    *,
    departments: tuple[str, ...] = DEFAULT_DEPARTMENTS,
    contract_codes: tuple[str, ...] = (),
    published_since_days: int | None = 14,
) -> ProfileSearchResult:
    """Une requête par compétence prioritaire (et le poste visé) et par département."""
    terms = ordered_profile_terms(profile)
    if not terms:
        raise JobSourceError("Renseigne le poste visé ou au moins une compétence dans ton profil.")
    if not departments:
        raise JobSourceError("Sélectionne au moins un département.")

    token = get_access_token(client_id, client_secret)
    listings_by_id: dict[str, SourceListing] = {}
    failures: list[tuple[str, str]] = []
    keywords = profile_search_keywords(profile, "fr")
    with ThreadPoolExecutor(max_workers=FRANCE_TRAVAIL_WORKERS) as executor:
        futures = {
            executor.submit(
                search_france_travail,
                [keyword],
                client_id,
                client_secret,
                department=department,
                contract_codes=contract_codes,
                published_since_days=published_since_days,
                access_token=token,
            ): (department, keyword)
            for department in departments
            for keyword in keywords
        }
        for future in as_completed(futures):
            department, keyword = futures[future]
            try:
                found = future.result()
            except JobSourceError as error:
                label = DEPARTMENT_NAMES.get(department, department)
                failures.append((f"{label} — « {keyword} »", str(error)))
                continue
            for listing in found:
                listings_by_id.setdefault(listing.source_id, listing)

    return ProfileSearchResult(
        listings=tuple(listings_by_id.values()),
        failed_targets=tuple(failures),
        searched_targets=tuple(DEPARTMENT_NAMES.get(code, code) for code in departments),
        search_terms=terms,
    )


def _map_contract(item: dict[str, object]) -> ContractType | None:
    nature = str(item.get("natureContrat") or "").casefold()
    if "apprentissage" in nature or "professionnalisation" in nature:
        return "Alternance"
    code = str(item.get("typeContrat") or "").upper()
    return {"CDI": "CDI", "CDD": "CDD", "LIB": "Freelance"}.get(code, "Autre" if code else None)


def _clean_location(label: str) -> str:
    match = re.fullmatch(r"(\d{2,3}|2[AB])\s*-\s*(.+)", label.strip())
    if not match:
        return label.strip()
    return f"{match.group(2).strip().title()} ({match.group(1)})"


def _build_description(item: dict[str, object]) -> str:
    sections = [clean_html_to_text(str(item.get("description") or ""))]
    salary = item.get("salaire")
    if isinstance(salary, dict) and isinstance(salary.get("libelle"), str):
        sections.append(f"Salaire : {salary['libelle'].strip()}")
    if isinstance(item.get("experienceLibelle"), str):
        sections.append(f"Expérience : {item['experienceLibelle'].strip()}")
    skills = item.get("competences")
    if isinstance(skills, list):
        names = [
            skill["libelle"].strip()
            for skill in skills
            if isinstance(skill, dict) and isinstance(skill.get("libelle"), str)
        ]
        if names:
            sections.append("Compétences demandées : " + ", ".join(names))
    return "\n\n".join(section for section in sections if section)


def _map_offer(item: object) -> SourceListing | None:
    if not isinstance(item, dict):
        return None
    offer_id = item.get("id")
    title = item.get("intitule")
    if not isinstance(offer_id, str) or not offer_id or not isinstance(title, str):
        return None

    origin = item.get("origineOffre")
    origin_url = origin.get("urlOrigine") if isinstance(origin, dict) else None
    url = (
        origin_url.strip()
        if isinstance(origin_url, str) and origin_url.strip().startswith(("http://", "https://"))
        else FRANCE_TRAVAIL_OFFER_URL.format(offer_id=offer_id)
    )

    company = item.get("entreprise")
    workplace = item.get("lieuTravail")
    description = _build_description(item)
    contact = item.get("contact")
    contact_email = None
    if isinstance(contact, dict) and isinstance(contact.get("courriel"), str):
        contact_email = extract_explicit_email(contact["courriel"])
    relocation_signal, relocation_evidence = detect_relocation(description)

    try:
        listing_url = AnyHttpUrl(url)
        return SourceListing(
            source_id=f"francetravail-{offer_id}",
            source_name="France Travail",
            title=title.strip(),
            company=str(company.get("nom") or "").strip() if isinstance(company, dict) else "",
            location=(
                _clean_location(str(workplace.get("libelle") or ""))
                if isinstance(workplace, dict)
                else ""
            ),
            country_code="FR",
            contract_type=_map_contract(item),
            original_url=listing_url,
            description=description,
            public_contact_email=contact_email,
            contact_source_url=listing_url if contact_email else None,
            relocation_signal=relocation_signal,
            relocation_evidence=relocation_evidence,
        )
    except ValidationError:
        return None
