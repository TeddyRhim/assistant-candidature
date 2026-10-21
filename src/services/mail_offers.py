"""Offres relevées dans les mails d'alerte emploi : import en lot, sans doublon.

Le skill local `chercher-mails` lit les alertes de la boîte mail et écrit une liste JSON
(`title`, `company`, `location`, `url`, `source`). Ce module fait le reste sans modèle : il suit
les liens de suivi jusqu'à l'annonce, retire les paramètres d'abonné, écarte les offres déjà
connues (même lien, ou même intitulé et même entreprise), importe les annonces lisibles comme une
saisie manuelle (CV « À soigner ») et garde les autres « À lire » sous leur vrai titre.
"""

from __future__ import annotations

import json
import re
import ssl
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request, build_opener

import certifi
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.config import get_data_dir
from src.models import JobOffer, JobOfferData, ProfileData
from src.services.cv_curation import CV_TO_CURATE, set_cv_state
from src.services.dossier_generator import prepare_dossier_for_offer
from src.services.job_offers import ORIGIN_MANUAL, create_offer, find_offer_by_url
from src.services.offer_import import (
    USER_AGENT,
    OfferImportError,
    OfferUnreadable,
    import_offer_from_url,
)
from src.services.offer_reading import store_unread_offer
from src.services.urls import clean_tracking

STATE_FILENAME = "mail_search.json"
RESOLVE_TIMEOUT_SECONDS = 15
# Mots trop courants pour dire si deux intitulés parlent du même poste.
_STOP_WORDS = frozenset({"h/f", "f/h", "les", "des", "pour", "avec", "and", "the"})

IMPORTED = "importée"
TO_READ = "à lire"
DUPLICATE = "doublon"
FAILED = "échec"


@dataclass(frozen=True)
class MailOfferResult:
    status: str
    label: str
    detail: str = ""
    offer_id: str = ""


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().casefold()
    text = re.sub(r"\((cdi|cdd|freelance|interim|it)\)", " ", text)
    text = re.sub(r"\b(h\s*/\s*f|f\s*/\s*h|f\s*/\s*m|m\s*/\s*f)\b", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _words(text: str) -> set[str]:
    return {word for word in _norm(text).split() if len(word) >= 3 and word not in _STOP_WORDS}


def find_same_offer(engine: Engine, title: str, company: str) -> JobOffer | None:
    """Offre déjà connue sous le même intitulé et la même entreprise (lien différent)."""
    wanted_title, wanted_company = _norm(title), _norm(company)
    if not wanted_title or not wanted_company:
        return None
    with Session(engine) as session:
        for offer in session.scalars(select(JobOffer)):
            known_company = _norm(offer.company)
            if (
                known_company
                and _norm(offer.title) == wanted_title
                and (wanted_company in known_company or known_company in wanted_company)
            ):
                return offer
    return None


def resolve_redirects(url: str) -> str:
    """Suit les redirections HTTP d'un lien de suivi ; en cas d'échec, garde le lien tel quel."""
    opener = build_opener(HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=RESOLVE_TIMEOUT_SECONDS) as response:
            return response.geturl()
    except Exception as error:  # noqa: BLE001 - un lien non suivi reste utilisable tel quel
        final = getattr(error, "url", None)
        return final if isinstance(final, str) and final.startswith("http") else url


def _is_other_page(mail_title: str, page_title: str) -> bool:
    """La page lue ne parle pas du poste annoncé (page de redirection, liste générique)."""
    wanted = _words(mail_title)
    return bool(wanted) and not (wanted & _words(page_title))


def import_mail_offers(
    engine: Engine,
    profile: ProfileData,
    items: list[dict[str, Any]],
    *,
    resolve: Callable[[str], str] = resolve_redirects,
    read: Callable[[str], JobOfferData] = import_offer_from_url,
) -> list[MailOfferResult]:
    results = []
    for item in items:
        title = str(item.get("title", "")).strip()
        company = str(item.get("company", "")).strip()
        location = str(item.get("location", "")).strip()
        source = str(item.get("source", "")).strip()
        label = f"{title} | {company or '?'}"
        raw_url = str(item.get("url") or "").strip()
        if not title:
            results.append(MailOfferResult(FAILED, label, "intitulé manquant"))
            continue
        twin = find_same_offer(engine, title, company)
        if twin is not None:
            results.append(MailOfferResult(DUPLICATE, label, f"{twin.title} ({twin.status})"))
            continue
        if not raw_url.startswith(("http://", "https://")):
            results.append(MailOfferResult(FAILED, label, "lien de l'offre absent du mail"))
            continue
        url = clean_tracking(resolve(raw_url))
        known = find_offer_by_url(engine, url)
        if known is not None:
            results.append(MailOfferResult(DUPLICATE, label, f"{known.title} ({known.status})"))
            continue
        host = urlsplit(url).hostname or ""
        mail_source = f"Alerte mail — {source or host}"
        try:
            data = read(url)
            if _is_other_page(title, data.title):
                raise OfferUnreadable(
                    f"La page lue ne correspond pas à l'annonce ({data.title[:60]}).", url, host
                )
        except OfferUnreadable as error:
            offer = store_unread_offer(
                engine,
                url,
                host,
                str(error),
                title=title,
                company=company,
                location=location,
                source=mail_source,
            )
            results.append(MailOfferResult(TO_READ, label, str(error), offer.id))
            continue
        except OfferImportError as error:
            results.append(MailOfferResult(FAILED, label, str(error)))
            continue
        try:
            data = data.model_copy(
                update={
                    "company": data.company or company,
                    "location": data.location or location,
                    "source": mail_source[:200],
                }
            )
            created = create_offer(engine, data, origin=ORIGIN_MANUAL)
        except ValidationError as error:
            results.append(MailOfferResult(FAILED, label, str(error)))
            continue
        detail = "dossier prêt"
        try:
            dossier = prepare_dossier_for_offer(engine, created.id, data, profile)
            detail = dossier.error or detail
        except Exception as error:  # noqa: BLE001 - l'offre reste enregistrée
            detail = f"dossier non préparé ({type(error).__name__})"
        set_cv_state(engine, created.id, CV_TO_CURATE)
        results.append(MailOfferResult(IMPORTED, label, detail, created.id))
    return results


def _state_path() -> Path:
    return get_data_dir() / STATE_FILENAME


def last_search_date() -> str | None:
    """Date (AAAA/MM/JJ, format de recherche Gmail) de la dernière recherche dans les mails."""
    try:
        return json.loads(_state_path().read_text(encoding="utf-8")).get("last_search")
    except (OSError, ValueError, AttributeError):
        return None


def record_search_date(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y/%m/%d")
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_search": stamp}), encoding="utf-8")
    return stamp
