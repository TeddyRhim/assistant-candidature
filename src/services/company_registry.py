from __future__ import annotations

import json
import os
import ssl
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import certifi
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models import (
    Company,
    CompanyCandidate,
    CompanyCandidateData,
    CompanyData,
    CompanyDepartment,
)
from src.services.companies import create_company

REGISTRY_API_ROOT = "https://recherche-entreprises.api.gouv.fr"
REGISTRY_TIMEOUT_SECONDS = 30
CUSTOM_CA_BUNDLE_ENV = "ASSISTANT_CANDIDATURES_CA_BUNDLE"
MAX_CANDIDATES_PER_SEARCH = 25
SOFTWARE_ACTIVITY_CODES = {
    "62.01Z": "Programmation informatique",
    "62.02A": "Conseil en systèmes et logiciels informatiques",
    "62.02B": "Tierce maintenance de systèmes et d'applications informatiques",
    "62.03Z": "Gestion d'installations informatiques",
    "62.09Z": "Autres activités informatiques",
}
COMPANY_DEPARTMENT_LABELS = {
    "06": "Alpes-Maritimes",
    "75": "Paris",
    "83": "Var",
}
REGISTRY_SEARCH_WORKERS = 3


class CompanyRegistryError(RuntimeError):
    """The public company registry could not return usable results."""


class DuplicateCompanyCandidate(ValueError):
    """The registry candidate is already saved locally."""


def search_company_registry(
    query: str,
    department: CompanyDepartment,
    activity_code: str | None = None,
    page: int = 1,
) -> list[CompanyCandidateData]:
    normalized_query = query.strip()
    if normalized_query and len(normalized_query) < 3:
        raise CompanyRegistryError("Saisis au moins trois caractères pour chercher.")
    if page < 1 or page > 20:
        raise CompanyRegistryError("Le numéro de page doit être compris entre 1 et 20.")
    if activity_code is not None and activity_code not in SOFTWARE_ACTIVITY_CODES:
        raise CompanyRegistryError("Le code d'activité sélectionné n'est pas pris en charge.")

    activity_codes = (
        [activity_code] if activity_code else list(SOFTWARE_ACTIVITY_CODES)
    )
    if normalized_query or activity_code:
        return _search_registry_page(
            normalized_query,
            department,
            activity_code,
            page,
        )

    with ThreadPoolExecutor(max_workers=REGISTRY_SEARCH_WORKERS) as executor:
        batches = executor.map(
            lambda code: _search_registry_page("", department, code, page),
            activity_codes,
        )
        candidates_by_siren = {
            candidate.siren: candidate
            for batch in batches
            for candidate in batch
        }
    return list(candidates_by_siren.values())


def _search_registry_page(
    query: str,
    department: CompanyDepartment,
    activity_code: str | None,
    page: int,
) -> list[CompanyCandidateData]:
    params = {
        "departement": department,
        "etat_administratif": "A",
        "per_page": MAX_CANDIDATES_PER_SEARCH,
        "page": page,
    }
    if query:
        params["q"] = query
    if activity_code:
        params["activite_principale"] = activity_code
    api_url = f"{REGISTRY_API_ROOT}/search?{urlencode(params)}"
    request = Request(
        api_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "assistant-candidatures/0.1",
        },
    )
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    custom_ca_bundle = os.environ.get(CUSTOM_CA_BUNDLE_ENV, "").strip()
    if custom_ca_bundle:
        try:
            ssl_context.load_verify_locations(cafile=custom_ca_bundle)
        except (OSError, ssl.SSLError) as error:
            raise CompanyRegistryError(
                f"Impossible de charger le certificat CA configuré dans "
                f"{CUSTOM_CA_BUNDLE_ENV}."
            ) from error
    try:
        with urlopen(
            request,
            timeout=REGISTRY_TIMEOUT_SECONDS,
            context=ssl_context,
        ) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        raise CompanyRegistryError(
            f"Le registre public a refusé la recherche (HTTP {error.code})."
        ) from error
    except URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise CompanyRegistryError(
                "La vérification TLS de l'API a échoué : "
                f"{error.reason}. Le magasin certifi est à jour ; vérifie la date/heure "
                "de Windows ou un proxy/antivirus qui intercepte HTTPS. Si ton réseau "
                "utilise un certificat d'entreprise, configure "
                f"{CUSTOM_CA_BUNDLE_ENV} avec un fichier PEM de CA approuvées. "
                "La vérification TLS reste activée."
            ) from error
        raise CompanyRegistryError(
            "Connexion à l'API publique d'entreprises impossible : "
            f"{error.reason}. Vérifie l'accès réseau, le DNS et le certificat HTTPS."
        ) from error
    except TimeoutError as error:
        raise CompanyRegistryError(
            "L'API publique d'entreprises n'a pas répondu dans le délai de "
            f"{REGISTRY_TIMEOUT_SECONDS} secondes."
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CompanyRegistryError(
            "Le registre public a renvoyé une réponse JSON invalide."
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise CompanyRegistryError("La réponse du registre public n'a pas le format attendu.")

    candidates = []
    for item in payload["results"]:
        candidate = _map_candidate(item, department, api_url)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _map_candidate(
    item: object,
    department: CompanyDepartment,
    api_url: str,
) -> CompanyCandidateData | None:
    if not isinstance(item, dict):
        return None
    headquarters = item.get("siege")
    if not isinstance(headquarters, dict):
        return None
    siren = item.get("siren")
    activity_code = item.get("activite_principale")
    local_establishments = item.get("matching_etablissements")
    matching_establishment = None
    if isinstance(local_establishments, list):
        matching_establishment = next(
            (
                establishment
                for establishment in local_establishments
                if isinstance(establishment, dict)
                and establishment.get("departement") == department
                and establishment.get("etat_administratif") in (None, "A")
            ),
            None,
        )
    if matching_establishment is None and headquarters.get("departement") != department:
        return None
    local_address = matching_establishment or headquarters
    location = local_address.get("libelle_commune")
    if matching_establishment and isinstance(
        matching_establishment.get("activite_principale"), str
    ):
        activity_code = matching_establishment["activite_principale"]
    if not isinstance(siren, str) or not isinstance(activity_code, str):
        return None
    if not isinstance(location, str) or not location.strip():
        location = local_address.get("adresse")
    name = item.get("nom_complet") or item.get("nom_raison_sociale")
    if not isinstance(name, str) or not name.strip():
        return None
    employee_range = item.get("tranche_effectif_salarie")
    if not isinstance(employee_range, str) or employee_range in {"NN", "0"}:
        employee_range = None
    try:
        return CompanyCandidateData(
            siren=siren,
            name=name,
            location=location,
            department=department,
            activity_code=activity_code,
            employee_range=employee_range,
            source_url=f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}",
            activity_api_url=api_url,
        )
    except ValidationError:
        return None


def save_company_candidate(engine: Engine, candidate_data: CompanyCandidateData) -> None:
    candidate = CompanyCandidate(
        **candidate_data.model_dump(),
        discovered_at=datetime.now(UTC).isoformat(),
    )
    try:
        with Session(engine) as session, session.begin():
            session.add(candidate)
    except IntegrityError as error:
        raise DuplicateCompanyCandidate(
            "Cette entreprise est déjà enregistrée parmi les pistes à vérifier."
        ) from error


def promote_company_candidate_to_prospect(
    engine: Engine,
    candidate_data: CompanyCandidateData,
) -> Company:
    department_label = COMPANY_DEPARTMENT_LABELS[candidate_data.department]
    company_data = CompanyData(
        name=candidate_data.name,
        location=candidate_data.location,
        source_url=str(candidate_data.source_url),
        development_evidence=(
            f"Le registre public déclare l'activité {candidate_data.activity_code} "
            f"({SOFTWARE_ACTIVITY_CODES.get(candidate_data.activity_code, 'activité déclarée')}) "
            f"pour cette entreprise ayant un établissement dans le département "
            f"{candidate_data.department} ({department_label}). "
            "Cette donnée ne confirme pas la présence d'une équipe de développement interne."
        ),
        notes=(
            "Piste issue de la découverte d'entreprises. Vérifier l'activité réelle, "
            "la présence d'une équipe tech et le contact avant toute candidature."
        ),
    )
    return create_company(engine, company_data)


def list_company_candidates(engine: Engine) -> list[CompanyCandidate]:
    query = select(CompanyCandidate).order_by(
        CompanyCandidate.department,
        CompanyCandidate.name.collate("NOCASE"),
    )
    with Session(engine) as session:
        return list(session.scalars(query))


def delete_company_candidate(engine: Engine, siren: str) -> None:
    with Session(engine) as session, session.begin():
        candidate = session.get(CompanyCandidate, siren)
        if candidate is None:
            raise CompanyRegistryError("Cette piste n'existe plus dans la liste locale.")
        session.delete(candidate)
