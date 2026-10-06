from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from langdetect import DetectorFactory, LangDetectException, detect
from pydantic import AnyHttpUrl, ValidationError

from src.models import JobOfferData, ProfileData
from src.services.job_sources.base import JobSearchQuery, JobSourceError, SourceListing
from src.services.matching import compare_offer_to_profile, search_languages

ADZUNA_API_ROOT = "https://api.adzuna.com/v1/api/jobs"
ADZUNA_RESULTS_PER_PAGE = 20
ADZUNA_TIMEOUT_SECONDS = 15
PROFILE_SEARCH_WORKERS = 4
PROFILE_SEARCH_BACKGROUND_WORKERS = 1
PROFILE_SEARCH_SKILL_LIMIT = 8
# Veille : une requête à la fois, espacées, pour rester sous la limite de débit d'Adzuna.
THROTTLED_PAUSE_SECONDS = 3.0
THROTTLED_RETRY_WAIT_SECONDS = 65.0
LOCAL_SEARCH_LOCATIONS = (
    "Nice",
    "Cannes",
    "Mougins",
    "Sophia Antipolis",
    "Antibes",
    "Grasse",
    "Cagnes-sur-Mer",
    "Menton",
    "Var",
    "Toulon",
    "Marseille",
    "Paris",
)
ADZUNA_MARKETS = {
    "at": "Autriche",
    "be": "Belgique",
    "ca": "Canada",
    "ch": "Suisse",
    "de": "Allemagne",
    "es": "Espagne",
    "fr": "France",
    "gb": "Royaume-Uni",
    "ie": "Irlande",
    "it": "Italie",
    "nl": "Pays-Bas",
    "pl": "Pologne",
}
DetectorFactory.seed = 0
_PROFILE_SEARCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=PROFILE_SEARCH_BACKGROUND_WORKERS,
    thread_name_prefix="adzuna-search",
)
_PROFILE_SEARCH_LOCK = threading.Lock()
_PROFILE_SEARCH_FUTURES: dict[str, Future[ProfileSearchResult]] = {}


@dataclass(frozen=True)
class ProfileSearchResult:
    listings: tuple[SourceListing, ...]
    failed_targets: tuple[tuple[str, str], ...]
    searched_targets: tuple[str, ...]
    search_terms: tuple[str, ...]
    filtered_language_count: int = 0
    rate_limited: bool = False


class AdzunaRateLimited(JobSourceError):
    """Adzuna a répondu 429 : trop de requêtes, il faut ralentir ou réessayer plus tard."""


@dataclass(frozen=True)
class ProfileSearchJobStatus:
    is_running: bool
    result: ProfileSearchResult | None = None
    error: str | None = None


def ordered_profile_terms(profile: ProfileData) -> tuple[str, ...]:
    category_priority = {
        "Forte": 0,
        "Intermédiaire": 1,
        "En développement": 2,
        "IA et nouvelles technologies": 3,
    }
    skills = sorted(
        profile.skills,
        key=lambda skill: (
            category_priority[skill.category],
            -skill.level_max,
        ),
    )
    return tuple(
        dict.fromkeys(
            term.strip()
            for term in [*(skill.name for skill in skills), profile.target_role]
            if term.strip()
        )
    )


def profile_search_keywords(profile: ProfileData, country_code: str) -> tuple[str, ...]:
    skills_by_priority = ordered_profile_terms(
        profile.model_copy(update={"target_role": ""})
    )
    # Les langages acceptés passent en premier : sans eux, une compétence secondaire comme
    # Python n'apparaîtrait jamais dans les requêtes, derrière les compétences les mieux notées.
    prioritized = dict.fromkeys([*search_languages(profile), *skills_by_priority])
    role_term = profile.target_role or (
        "développeur" if country_code == "fr" else "developer"
    )
    terms = [*list(prioritized)[:PROFILE_SEARCH_SKILL_LIMIT], role_term]
    if country_code != "fr":
        terms.append("developer")
    return tuple(dict.fromkeys(term for term in terms if term.strip()))


def search_adzuna_for_profile(
    profile: ProfileData,
    targets: list[tuple[str, str]],
    app_id: str,
    app_key: str,
    page: int = 1,
) -> ProfileSearchResult:
    terms = ordered_profile_terms(profile)
    if not terms:
        raise JobSourceError(
            "Renseigne le poste visé ou au moins une compétence dans ton profil."
        )
    if not targets:
        raise JobSourceError("Sélectionne au moins une zone ou un pays.")

    listings_by_url: dict[str, SourceListing] = {}
    failures = []
    filtered_language_count = 0
    with ThreadPoolExecutor(max_workers=PROFILE_SEARCH_WORKERS) as executor:
        futures = {
            executor.submit(
                search_adzuna,
                JobSearchQuery(
                    keywords=[keyword],
                    locations=[location] if location else [],
                ),
                country_code,
                app_id,
                app_key,
                page,
            ): (country_code, _target_label(country_code, location), keyword)
            for country_code, location in targets
            for keyword in profile_search_keywords(profile, country_code)
        }
        for future in as_completed(futures):
            country_code, target, keyword = futures[future]
            try:
                listings = future.result()
            except JobSourceError as error:
                failures.append((f"{target} — « {keyword} »", str(error)))
                continue
            for listing in listings:
                if country_code != "fr" and not is_french_or_english(listing):
                    filtered_language_count += 1
                    continue
                listings_by_url.setdefault(str(listing.original_url), listing)

    return ProfileSearchResult(
        listings=tuple(listings_by_url.values()),
        failed_targets=tuple(failures),
        searched_targets=tuple(
            _target_label(country, location) for country, location in targets
        ),
        search_terms=terms,
        filtered_language_count=filtered_language_count,
    )


def search_adzuna_throttled(
    profile: ProfileData,
    country_code: str,
    location: str,
    app_id: str,
    app_key: str,
    pause_seconds: float = THROTTLED_PAUSE_SECONDS,
    retry_wait_seconds: float = THROTTLED_RETRY_WAIT_SECONDS,
) -> ProfileSearchResult:
    """Cherche une seule zone, un mot-clé après l'autre, en respectant la limite de débit.

    Sur un 429, attend une fois puis réessaie ; si Adzuna refuse encore, s'arrête et signale
    `rate_limited` pour que l'appelant reprenne plus tard au lieu d'enchaîner les refus.
    """
    terms = ordered_profile_terms(profile)
    if not terms:
        raise JobSourceError("Renseigne le poste visé ou au moins une compétence dans ton profil.")

    label = _target_label(country_code, location)
    listings_by_url: dict[str, SourceListing] = {}
    failures: list[tuple[str, str]] = []
    filtered_language_count = 0
    rate_limited = False

    for index, keyword in enumerate(profile_search_keywords(profile, country_code)):
        if index:
            time.sleep(pause_seconds)
        query = JobSearchQuery(keywords=[keyword], locations=[location] if location else [])
        listings: list[SourceListing] | None = None
        for attempt in range(2):
            try:
                listings = search_adzuna(query, country_code, app_id, app_key, 1)
                break
            except AdzunaRateLimited:
                if attempt == 0:
                    time.sleep(retry_wait_seconds)
                    continue
                rate_limited = True
            except JobSourceError as error:
                failures.append((f"{label} — « {keyword} »", str(error)))
            break
        if rate_limited:
            break
        for listing in listings or []:
            if country_code != "fr" and not is_french_or_english(listing):
                filtered_language_count += 1
                continue
            listings_by_url.setdefault(str(listing.original_url), listing)

    return ProfileSearchResult(
        listings=tuple(listings_by_url.values()),
        failed_targets=tuple(failures),
        searched_targets=(label,),
        search_terms=terms,
        filtered_language_count=filtered_language_count,
        rate_limited=rate_limited,
    )


def start_profile_search(
    profile: ProfileData,
    targets: list[tuple[str, str]],
    app_id: str,
    app_key: str,
    page: int = 1,
) -> str:
    with _PROFILE_SEARCH_LOCK:
        if any(not future.done() for future in _PROFILE_SEARCH_FUTURES.values()):
            raise JobSourceError(
                "Une recherche Adzuna est déjà en cours. Attends sa fin avant "
                "d'en lancer une autre."
            )
        job_id = uuid.uuid4().hex
        _PROFILE_SEARCH_FUTURES[job_id] = _PROFILE_SEARCH_EXECUTOR.submit(
            search_adzuna_for_profile,
            profile,
            targets,
            app_id,
            app_key,
            page,
        )
    return job_id


def get_profile_search_status(job_id: str) -> ProfileSearchJobStatus:
    with _PROFILE_SEARCH_LOCK:
        future = _PROFILE_SEARCH_FUTURES.get(job_id)
    if future is None:
        raise JobSourceError("Cette recherche n'existe plus ou a déjà été récupérée.")
    if not future.done():
        return ProfileSearchJobStatus(is_running=True)
    with _PROFILE_SEARCH_LOCK:
        _PROFILE_SEARCH_FUTURES.pop(job_id, None)
    try:
        return ProfileSearchJobStatus(is_running=False, result=future.result())
    except JobSourceError as error:
        return ProfileSearchJobStatus(is_running=False, error=str(error))
    except Exception as error:
        return ProfileSearchJobStatus(
            is_running=False,
            error=f"La recherche Adzuna a échoué : {error}",
        )


def _target_label(country_code: str, location: str) -> str:
    country_name = ADZUNA_MARKETS.get(country_code, country_code.upper())
    return f"{location}, {country_name}" if location else country_name


def is_paris_listing(listing: SourceListing) -> bool:
    location = _normalize_search_text(listing.location)
    return re.search(r"\bparis\b|\bile[\s-]+de[\s-]+france\b", location) is not None


def listing_title_with_location(listing: SourceListing) -> str:
    title = listing.title.strip()
    location = listing.location.strip()
    if not location:
        return title
    primary_location = location.split(",", maxsplit=1)[0].strip()
    normalized_title = _normalize_search_text(title)
    normalized_location = _normalize_search_text(primary_location)
    location_is_in_title = bool(
        normalized_location
        and re.search(
            rf"(?<!\w){re.escape(normalized_location)}(?!\w)",
            normalized_title,
        )
    )
    if location_is_in_title:
        return title
    return f"{title} — {location}"


def rank_adzuna_results(
    listings: list[SourceListing],
    profile: ProfileData,
    *,
    prioritize_local_areas: bool,
) -> list[SourceListing]:
    def sort_key(listing: SourceListing) -> tuple[bool, int]:
        match = compare_offer_to_profile(
            JobOfferData(
                title=listing.title,
                description=listing.description or "Aucun extrait fourni.",
            ),
            profile,
        )
        local_priority = prioritize_local_areas and not is_paris_listing(listing)
        return local_priority, match.match_percentage or 0

    return sorted(listings, key=sort_key, reverse=True)


def _normalize_search_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


def is_french_or_english(listing: SourceListing) -> bool:
    text = f"{listing.title}. {listing.description}".strip()
    if len(text) < 20:
        return False
    try:
        return detect(text) in {"en", "fr"}
    except LangDetectException:
        return False


def search_adzuna(
    query: JobSearchQuery,
    country_code: str,
    app_id: str,
    app_key: str,
    page: int = 1,
) -> list[SourceListing]:
    country = country_code.strip().lower()
    if len(country) != 2 or not country.isalpha():
        raise JobSourceError("Le pays doit être indiqué avec un code de deux lettres.")
    if not app_id.strip() or not app_key.strip():
        raise JobSourceError("Les identifiants Adzuna doivent être configurés localement.")
    if page < 1 or page > 50:
        raise JobSourceError("Le numéro de page doit être compris entre 1 et 50.")

    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": ADZUNA_RESULTS_PER_PAGE,
        "content-type": "application/json",
    }
    if query.keywords:
        params["what"] = " ".join(query.keywords)
    if query.locations:
        params["where"] = ", ".join(query.locations)
    if query.contracts:
        contracts = set(query.contracts)
        if "CDI" in contracts:
            params["permanent"] = 1
        if "CDD" in contracts or "Freelance" in contracts:
            params["contract"] = 1
        if "Stage" in contracts or "Alternance" in contracts:
            params["contract"] = 1

    url = f"{ADZUNA_API_ROOT}/{country}/search/{page}?{urlencode(params)}"
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "assistant-candidatures/0.1",
        },
    )
    try:
        with urlopen(request, timeout=ADZUNA_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code == 429:
            raise AdzunaRateLimited(
                "Adzuna a refusé la recherche (HTTP 429) : limite de requêtes atteinte."
            ) from error
        raise JobSourceError(f"Adzuna a refusé la recherche (HTTP {error.code}).") from error
    except (URLError, TimeoutError, ConnectionResetError) as error:
        raise JobSourceError("Impossible de joindre l'API Adzuna. Vérifie la connexion.") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JobSourceError("Adzuna a renvoyé une réponse JSON invalide.") from error

    if not isinstance(payload, dict):
        raise JobSourceError("La réponse Adzuna n'a pas le format attendu.")
    results = payload.get("results")
    if not isinstance(results, list):
        raise JobSourceError("La réponse Adzuna n'a pas le format attendu.")

    listings = []
    for item in results:
        listing = _map_listing(item, country)
        if listing is not None:
            listings.append(listing)
    return listings


def _map_listing(item: object, country_code: str) -> SourceListing | None:
    if not isinstance(item, dict):
        return None
    title = item.get("title")
    listing_url = item.get("redirect_url")
    if not isinstance(title, str) or not isinstance(listing_url, str):
        return None

    company = item.get("company")
    location = item.get("location")
    contract_type = item.get("contract_type")
    mapped_contract = "CDI" if contract_type == "permanent" else None
    try:
        return SourceListing(
            source_id=str(item.get("id", listing_url)),
            source_name="Adzuna",
            title=title,
            company=company.get("display_name", "") if isinstance(company, dict) else "",
            location=location.get("display_name", "") if isinstance(location, dict) else "",
            country_code=country_code.upper(),
            contract_type=mapped_contract,
            original_url=AnyHttpUrl(listing_url),
            description=item.get("description", "")
            if isinstance(item.get("description"), str)
            else "",
        )
    except ValidationError:
        return None
