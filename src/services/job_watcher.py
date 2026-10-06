from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.config import get_adzuna_credentials, get_data_dir, get_france_travail_credentials
from src.models import JobOffer, JobOfferData, ProfileData
from src.services.dossier_generator import prepare_dossier_for_offer
from src.services.job_offers import DuplicateOfferURL, create_offer
from src.services.job_sources.adzuna import (
    LOCAL_SEARCH_LOCATIONS,
    is_french_or_english,
    search_adzuna_throttled,
)
from src.services.job_sources.base import JobSourceError, SourceListing
from src.services.job_sources.france_travail import (
    DEFAULT_DEPARTMENTS as FRANCE_TRAVAIL_DEFAULT_DEPARTMENTS,
)
from src.services.job_sources.france_travail import search_france_travail_for_profile
from src.services.job_sources.greenhouse import fetch_greenhouse_listings
from src.services.job_sources.lever import fetch_lever_listings
from src.services.matching import assess_offer_fit

logger = logging.getLogger(__name__)

PlatformType = Literal["greenhouse", "lever"]
MIN_LANGUAGE_CHECK_CHARACTERS = 120
FRANCE_TRAVAIL_WATCH_DAYS = 14  # fenêtre de publication interrogée à chaque cycle

# Mots (entiers) du titre qui écartent une annonce : hors cible pour un CDI backend PHP junior.
DEFAULT_EXCLUDED_TITLE_KEYWORDS = (
    "stage",
    "stagiaire",
    "alternance",
    "alternant",
    "apprenti",
    "apprentissage",
    "internship",
    "freelance",
    "java",
    "angular",
    "lead",
    "senior",
    "sénior",
    "confirmé",
    "expert",
    "architecte",
)


def find_excluded_keyword(title: str, keywords: list[str]) -> str | None:
    """Retourne le premier mot-clé exclu présent comme mot entier dans le titre."""
    for keyword in keywords:
        word = keyword.strip()
        if word and re.search(rf"(?<!\w){re.escape(word)}(?!\w)", title, re.IGNORECASE):
            return word
    return None


class MonitoredTarget(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    platform: PlatformType
    target: str = Field(min_length=1, max_length=100)
    display_name: str = Field(default="", max_length=150)
    is_eu: bool = False
    enabled: bool = True


class WatcherConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    targets: list[MonitoredTarget] = Field(default_factory=list)
    enable_adzuna: bool = True
    adzuna_countries: list[str] = Field(default_factory=lambda: ["fr"])
    enable_france_travail: bool = True
    france_travail_departments: list[str] = Field(
        default_factory=lambda: list(FRANCE_TRAVAIL_DEFAULT_DEPARTMENTS)
    )
    france_travail_cdi_only: bool = True
    min_match_percentage: int = Field(default=50, ge=0, le=100)
    excluded_title_keywords: list[str] = Field(
        default_factory=lambda: list(DEFAULT_EXCLUDED_TITLE_KEYWORDS)
    )
    auto_import: bool = True
    auto_prepare_dossier: bool = True
    interval_seconds: int = Field(default=3600, ge=300, le=86400)


@dataclass
class WatcherCycleResult:
    started_at: str
    completed_at: str = ""
    targets_scanned: int = 0
    adzuna_scanned: bool = False
    france_travail_scanned: bool = False
    total_listings_found: int = 0
    new_offers_imported: int = 0
    dossiers_prepared: int = 0
    duplicates_skipped: int = 0
    low_match_skipped: int = 0
    excluded_skipped: int = 0
    imported_offer_titles: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def get_watcher_config_path() -> Path:
    return get_data_dir() / "watcher_config.json"


def load_watcher_config() -> WatcherConfig:
    path = get_watcher_config_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return WatcherConfig.model_validate(raw)
        except Exception as error:
            logger.warning("Erreur de lecture de watcher_config.json: %s", error)
    return WatcherConfig()


def save_watcher_config(config: WatcherConfig) -> None:
    path = get_watcher_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_adzuna_cursor_path() -> Path:
    return get_data_dir() / "adzuna_cursor.json"


def _load_adzuna_cursor(target_count: int) -> int:
    """Zone d'Adzuna où reprendre après une limite de requêtes (0 = depuis le début)."""
    try:
        position = int(json.loads(get_adzuna_cursor_path().read_text(encoding="utf-8"))["next"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    return position if 0 <= position < target_count else 0


def _save_adzuna_cursor(position: int) -> None:
    path = get_adzuna_cursor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"next": position}), encoding="utf-8")


def _normalize_key_part(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\W+", " ", text.casefold()).strip()


def offer_identity_key(title: str, company: str | None, location: str | None) -> str:
    """Clé d'unicité d'une annonce : une même offre republiée change d'URL (suivi, id)."""
    return "|".join(_normalize_key_part(part) for part in (title, company, location))


def _is_url_already_in_db(session: Session, url: str) -> bool:
    if not url:
        return False
    return session.scalar(select(JobOffer.id).where(JobOffer.url == url)) is not None


def run_watcher_cycle(
    engine: Engine,
    profile: ProfileData,
    config: WatcherConfig | None = None,
    secrets: Mapping[str, object] | None = None,
) -> WatcherCycleResult:
    cfg = config or load_watcher_config()
    now_iso = datetime.now(UTC).isoformat()
    result = WatcherCycleResult(started_at=now_iso)

    collected_listings: list[SourceListing] = []

    # 1. Collecte ciblée Greenhouse & Lever
    for target in cfg.targets:
        if not target.enabled:
            continue
        result.targets_scanned += 1
        try:
            if target.platform == "greenhouse":
                listings = fetch_greenhouse_listings(target.target)
            else:
                listings = fetch_lever_listings(target.target, eu_endpoint=target.is_eu)
            collected_listings.extend(listings)
        except (JobSourceError, Exception) as error:
            err_msg = f"{target.platform.title()} ({target.target}): {error}"
            logger.warning(err_msg)
            result.errors.append(err_msg)

    # 2. Collecte Adzuna (si activée et identifiants disponibles) : une zone à la fois, espacée.
    if cfg.enable_adzuna:
        app_id, app_key = get_adzuna_credentials(secrets)
        if app_id and app_key:
            result.adzuna_scanned = True
            adzuna_targets = [
                (country, location)
                for country in cfg.adzuna_countries
                for location in (LOCAL_SEARCH_LOCATIONS if country == "fr" else ("",))
            ]
            start = _load_adzuna_cursor(len(adzuna_targets))
            next_cursor = 0
            for position in range(start, len(adzuna_targets)):
                country, location = adzuna_targets[position]
                try:
                    search_res = search_adzuna_throttled(
                        profile, country, location, app_id, app_key
                    )
                except Exception as error:
                    zone = location or "pays"
                    result.errors.append(f"Adzuna ({country}/{zone}): {error}")
                    continue
                collected_listings.extend(search_res.listings)
                for err_target, err_msg in search_res.failed_targets:
                    result.errors.append(f"Adzuna ({err_target}): {err_msg}")
                if search_res.rate_limited:
                    next_cursor = position
                    result.errors.append(
                        f"Adzuna : limite de requêtes atteinte à {location or country} ; "
                        "la prochaine récupération reprendra à cette zone."
                    )
                    break
            _save_adzuna_cursor(next_cursor)

    # 3. Collecte France Travail (si activée et identifiants disponibles)
    if cfg.enable_france_travail:
        client_id, client_secret = get_france_travail_credentials(secrets)
        if client_id and client_secret:
            result.france_travail_scanned = True
            try:
                ft_result = search_france_travail_for_profile(
                    profile,
                    client_id,
                    client_secret,
                    departments=tuple(cfg.france_travail_departments),
                    contract_codes=("CDI",) if cfg.france_travail_cdi_only else (),
                    published_since_days=FRANCE_TRAVAIL_WATCH_DAYS,
                )
                collected_listings.extend(ft_result.listings)
                for err_target, err_msg in ft_result.failed_targets:
                    result.errors.append(f"France Travail ({err_target}): {err_msg}")
            except Exception as error:
                result.errors.append(f"France Travail: {error}")

    result.total_listings_found = len(collected_listings)

    # 4. Filtrage, déduplication et import en base
    with Session(engine) as session:
        known_keys = {
            offer_identity_key(title, company, location)
            for title, company, location in session.execute(
                select(JobOffer.title, JobOffer.company, JobOffer.location)
            )
        }
        for listing in collected_listings:
            listing_url = str(listing.original_url) if listing.original_url else ""
            if _is_url_already_in_db(session, listing_url):
                result.duplicates_skipped += 1
                continue

            identity = offer_identity_key(listing.title, listing.company, listing.location)
            if identity in known_keys:
                result.duplicates_skipped += 1
                continue

            # Évaluation du matching par rapport au profil candidat
            temp_offer = JobOfferData(
                title=listing.title,
                company=listing.company,
                location=listing.location,
                contract_type=listing.contract_type,
                url=listing_url or None,
                source=listing.source_name,
                description=listing.description or "Sans description fournie.",
            )
            if find_excluded_keyword(temp_offer.title, cfg.excluded_title_keywords):
                result.excluded_skipped += 1
                continue

            # Annonces ni en français ni en anglais (néerlandais, roumain…) : écartées. Sous
            # MIN_LANGUAGE_CHECK_CHARACTERS, la détection n'est pas fiable : on garde l'annonce.
            description_text = f"{listing.title}. {listing.description or ''}"
            if len(description_text) >= MIN_LANGUAGE_CHECK_CHARACTERS and (
                not is_french_or_english(listing)
            ):
                result.excluded_skipped += 1
                continue

            # Même score global que l'interface (compétences, intitulé, contrat, lieu).
            score = assess_offer_fit(temp_offer, profile).overall_percentage or 0

            if score < cfg.min_match_percentage:
                result.low_match_skipped += 1
                continue

            if cfg.auto_import:
                try:
                    # Le score n'est pas écrit dans le texte : il fausserait le calcul
                    # ultérieur de correspondance et deviendrait obsolète.
                    full_desc = temp_offer.description
                    if listing.public_contact_email:
                        full_desc = (
                            f"Contact public : {listing.public_contact_email}\n\n{full_desc}"
                        )
                    offer_to_save = JobOfferData(
                        title=temp_offer.title,
                        company=temp_offer.company,
                        location=temp_offer.location,
                        contract_type=temp_offer.contract_type,
                        url=temp_offer.url,
                        source=temp_offer.source,
                        description=full_desc,
                        status="À examiner",
                    )
                    created_offer = create_offer(engine, offer_to_save)
                    known_keys.add(identity)
                    result.new_offers_imported += 1
                    company_name = temp_offer.company or "Entreprise inconnue"
                    result.imported_offer_titles.append(
                        f"{temp_offer.title} - {company_name} ({score}%)"
                    )

                    if cfg.auto_prepare_dossier:
                        try:
                            dossier = prepare_dossier_for_offer(
                                engine=engine,
                                job_offer_id=created_offer.id,
                                offer_data=offer_to_save,
                                profile=profile,
                            )
                            if dossier.tailored_resume_id:
                                result.dossiers_prepared += 1
                        except Exception as dossier_err:
                            logger.warning(
                                "Erreur pré-génération dossier '%s': %s",
                                temp_offer.title,
                                dossier_err,
                            )
                except DuplicateOfferURL:
                    result.duplicates_skipped += 1
                except Exception as error:
                    result.errors.append(f"Erreur enregistrement '{listing.title}': {error}")

    result.completed_at = datetime.now(UTC).isoformat()
    return result


class PeriodicJobWatcher:
    """Ordonnanceur de veille d'annonces s'exécutant en tâche de fond."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._last_result: WatcherCycleResult | None = None
        self._last_run_time: float | None = None
        self._is_running = False

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._is_running and self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self) -> WatcherCycleResult | None:
        with self._lock:
            return self._last_result

    def start(
        self,
        engine: Engine,
        profile: ProfileData,
        config: WatcherConfig | None = None,
        secrets: Mapping[str, object] | None = None,
    ) -> bool:
        with self._lock:
            if self._is_running and self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._is_running = True

            def _worker() -> None:
                cfg = config or load_watcher_config()
                while not self._stop_event.is_set():
                    if config is None:
                        # Relit la configuration à chaque cycle pour prendre en compte
                        # les modifications enregistrées pendant que la veille tourne.
                        cfg = load_watcher_config()
                    try:
                        res = run_watcher_cycle(engine, profile, cfg, secrets)
                        with self._lock:
                            self._last_result = res
                            self._last_run_time = time.time()
                    except Exception as err:
                        logger.error("Erreur durant le cycle de veille: %s", err)

                    interval = max(cfg.interval_seconds, 60)
                    # Attente par tranches pour réagir vite au stop_event
                    for _ in range(int(interval)):
                        if self._stop_event.is_set():
                            break
                        time.sleep(1)

                with self._lock:
                    self._is_running = False

            self._thread = threading.Thread(
                target=_worker,
                name="job-watcher-daemon",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self._is_running:
                return False
            self._stop_event.set()
            self._is_running = False
            return True


_GLOBAL_WATCHER = PeriodicJobWatcher()


def get_global_watcher() -> PeriodicJobWatcher:
    return _GLOBAL_WATCHER
