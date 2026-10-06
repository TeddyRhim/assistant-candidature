from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Engine

from src.models import Company, CompanyCandidateData, CompanyData, CompanyDepartment
from src.services.companies import create_company
from src.services.job_sources.base import JobSourceDescriptor, JobSourceError
from src.services.job_sources.france_travail import _clear_token_cache, get_access_token

BONNE_BOITE_API_ROOT = "https://api.francetravail.io/partenaire/labonneboite/v2"
# L'API exige les scopes de recherche et de consultation en plus du scope de l'API.
BONNE_BOITE_SCOPES = ("api_labonneboitev2 search office", "api_labonneboitev2 search")
BONNE_BOITE_TIMEOUT_SECONDS = 30
BONNE_BOITE_PAGE_SIZE = 100
BONNE_BOITE_MAX_PAGES = 5
BONNE_BOITE_MIN_INTERVAL_SECONDS = 0.55  # quota publié : 2 appels par seconde
ANNUAIRE_URL = "https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}"
SUPPORTED_DEPARTMENTS: tuple[CompanyDepartment, ...] = ("06", "75", "83")
DEFAULT_DEPARTMENTS: tuple[CompanyDepartment, ...] = ("06", "83")
ROME_CODES = {
    "M1805": "Développeur / Développeuse informatique",
    "M1806": "Consultant fonctionnel / Consultante fonctionnelle des systèmes d'information",
    "M1810": "Technicien / Technicienne d'exploitation informatique",
}
DEFAULT_ROME_CODES = ("M1805",)

BONNE_BOITE_DESCRIPTOR = JobSourceDescriptor(
    source_id="bonne-boite",
    display_name="La Bonne Boîte — potentiel d'embauche",
    access_method="official_api",
    documentation_url="https://francetravail.io/produits-partages/catalogue/bonne-boite-v2",
    enabled=True,
)

_THROTTLE_LOCK = threading.Lock()
_last_call_at = 0.0


class BonneBoiteCompany(BaseModel):
    """Entreprise à potentiel d'embauche pour un métier, sans offre publiée."""

    model_config = ConfigDict(frozen=True)

    siret: str
    siren: str
    name: str
    office_name: str = ""
    city: str
    department: CompanyDepartment
    postcode: str = ""
    naf: str
    naf_label: str = ""
    headcount_min: int = 0
    headcount_max: int = 0
    hiring_potential: float
    is_high_potential: bool = False
    has_known_email: bool | None = None
    rome: str
    api_url: str

    @property
    def source_url(self) -> str:
        return ANNUAIRE_URL.format(siren=self.siren)

    @property
    def employee_range(self) -> str | None:
        if not self.headcount_max:
            return None
        return f"{self.headcount_min} à {self.headcount_max} salariés"

    @property
    def is_software_activity(self) -> bool:
        return self.naf.startswith("62.")

    def to_candidate(self) -> CompanyCandidateData:
        return CompanyCandidateData(
            siren=self.siren,
            name=self.name,
            location=self.city,
            department=self.department,
            activity_code=self.naf,
            employee_range=self.employee_range,
            source_url=self.source_url,
            activity_api_url=self.api_url,
        )


@dataclass(frozen=True)
class BonneBoiteSearchResult:
    companies: tuple[BonneBoiteCompany, ...]
    total_hits: int
    truncated: bool


def _throttle() -> None:
    """Espace les appels pour respecter le quota de 2 requêtes par seconde."""
    global _last_call_at
    with _THROTTLE_LOCK:
        wait = BONNE_BOITE_MIN_INTERVAL_SECONDS - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def _fetch_page(url: str, token: str) -> dict[str, object]:
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "assistant-candidatures/0.1",
        },
    )
    _throttle()
    try:
        with urlopen(request, timeout=BONNE_BOITE_TIMEOUT_SECONDS) as response:
            raw = response.read()
    except HTTPError as error:
        if error.code in {401, 403}:
            _clear_token_cache()
            raise JobSourceError(
                f"La Bonne Boîte a refusé la recherche (HTTP {error.code}) : vérifie que "
                "l'application est abonnée à l'API « La Bonne Boîte »."
            ) from error
        if error.code == 429:
            raise JobSourceError(
                "La Bonne Boîte limite le débit des requêtes (HTTP 429). Réessaie plus tard."
            ) from error
        raise JobSourceError(
            f"La Bonne Boîte a refusé la recherche (HTTP {error.code})."
        ) from error
    except (URLError, TimeoutError, ConnectionResetError) as error:
        raise JobSourceError(
            "Impossible de joindre La Bonne Boîte. Vérifie la connexion."
        ) from error
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JobSourceError("La Bonne Boîte a renvoyé une réponse JSON invalide.") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("hits"), int):
        raise JobSourceError("La réponse La Bonne Boîte n'a pas le format attendu.")
    return payload


def search_bonne_boite(
    client_id: str,
    client_secret: str,
    *,
    rome_codes: tuple[str, ...] = DEFAULT_ROME_CODES,
    departments: tuple[str, ...] = DEFAULT_DEPARTMENTS,
    max_pages: int = BONNE_BOITE_MAX_PAGES,
) -> BonneBoiteSearchResult:
    """Entreprises à fort potentiel d'embauche pour des métiers, par départements entiers."""
    if not rome_codes or any(code not in ROME_CODES for code in rome_codes):
        raise JobSourceError("Choisis au moins un code métier pris en charge.")
    if not departments or any(code not in SUPPORTED_DEPARTMENTS for code in departments):
        raise JobSourceError("Choisis au moins un département parmi 06, 75 et 83.")
    if not 1 <= max_pages <= 20:
        raise JobSourceError("Le nombre de pages doit être compris entre 1 et 20.")

    token = get_access_token(
        client_id,
        client_secret,
        scopes=BONNE_BOITE_SCOPES,
        api_label="La Bonne Boîte",
    )
    params: list[tuple[str, object]] = [
        *(("rome", code) for code in rome_codes),
        *(("department_number", int(code)) for code in departments),
        ("page_size", BONNE_BOITE_PAGE_SIZE),
    ]
    companies_by_siret: dict[str, BonneBoiteCompany] = {}
    total_hits = 0
    pages_fetched = 0
    for page in range(1, max_pages + 1):
        api_url = f"{BONNE_BOITE_API_ROOT}/recherche?{urlencode([*params, ('page', page)])}"
        payload = _fetch_page(api_url, token)
        pages_fetched += 1
        total_hits = int(payload["hits"])  # type: ignore[call-overload]
        items = payload.get("items")
        for item in items if isinstance(items, list) else []:
            company = _map_company(item, api_url.split("&page=")[0])
            if company is None:
                continue
            known = companies_by_siret.get(company.siret)
            if known is None or company.hiring_potential > known.hiring_potential:
                companies_by_siret[company.siret] = company
        if not isinstance(items, list) or not items or page * BONNE_BOITE_PAGE_SIZE >= total_hits:
            break

    companies = tuple(
        sorted(companies_by_siret.values(), key=lambda item: item.hiring_potential, reverse=True)
    )
    return BonneBoiteSearchResult(
        companies=companies,
        total_hits=total_hits,
        truncated=pages_fetched * BONNE_BOITE_PAGE_SIZE < total_hits,
    )


def _normalize_naf(naf: str) -> str:
    match = re.fullmatch(r"(\d{2})(\d{2})([A-Z])", naf.strip().upper())
    return f"{match.group(1)}.{match.group(2)}{match.group(3)}" if match else naf.strip()


def _map_company(item: object, api_url: str) -> BonneBoiteCompany | None:
    if not isinstance(item, dict):
        return None
    siret = item.get("siret")
    name = item.get("company_name")
    city = item.get("city")
    naf = item.get("naf")
    potential = item.get("hiring_potential")
    department = str(item.get("department_number", "")).zfill(2)
    if (
        not isinstance(siret, str)
        or not re.fullmatch(r"\d{14}", siret)
        or not isinstance(name, str)
        or not name.strip()
        or not isinstance(city, str)
        or not city.strip()
        or not isinstance(naf, str)
        or not isinstance(potential, (int, float))
        or department not in SUPPORTED_DEPARTMENTS
    ):
        return None
    email = item.get("email")
    try:
        return BonneBoiteCompany(
            siret=siret,
            siren=siret[:9],
            name=name.strip(),
            office_name=str(item.get("office_name") or "").strip(),
            city=city.strip().title(),
            department=department,  # type: ignore[arg-type]
            postcode=str(item.get("postcode") or ""),
            naf=_normalize_naf(naf),
            naf_label=str(item.get("naf_label") or "").strip(),
            headcount_min=int(item.get("headcount_min") or 0),
            headcount_max=int(item.get("headcount_max") or 0),
            hiring_potential=float(potential),
            is_high_potential=bool(item.get("is_high_potential")),
            has_known_email=(
                {"yes": True, "no": False}.get(email) if isinstance(email, str) else None
            ),
            rome=str(item.get("rome") or ""),
            api_url=api_url,
        )
    except (ValidationError, ValueError, TypeError):
        return None


def promote_bonne_boite_company(engine: Engine, company: BonneBoiteCompany) -> Company:
    """Crée une fiche « Entreprise à prospecter » sans inventer de contact ni de recrutement."""
    email_note = {
        True: "La Bonne Boîte indique connaître une adresse de contact (non communiquée par l'API)",
        False: "La Bonne Boîte ne connaît pas d'adresse de contact",
        None: "Aucune information de contact fournie par l'API",
    }[company.has_known_email]
    job_label = ROME_CODES.get(company.rome, company.rome or "le métier recherché")
    company_data = CompanyData(
        name=company.name,
        location=company.city,
        source_url=company.source_url,
        development_evidence=(
            f"La Bonne Boîte (France Travail) estime un potentiel d'embauche de "
            f"{company.hiring_potential:.0f}/100 pour le métier « {job_label} » ; activité "
            f"déclarée {company.naf} ({company.naf_label or 'libellé non fourni'}). "
            "Il s'agit d'une estimation statistique : elle ne prouve ni une offre ouverte ni "
            "une équipe de développement."
        ),
        notes=(
            f"Piste issue de La Bonne Boîte (candidature spontanée). {email_note}. "
            "Trouver le contact sur le site de l'entreprise avant toute candidature."
        ),
    )
    return create_company(engine, company_data)
