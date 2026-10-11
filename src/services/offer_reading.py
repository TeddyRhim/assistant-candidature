"""Annonces qu'on n'a pas pu lire automatiquement : stockées, puis lues avec le navigateur.

Quand un site masque son contenu (connexion, anti-robot, page dynamique), le lien est gardé tel
quel avec l'état « À lire ». Le skill `lire-annonces` ouvre ensuite ces liens dans Chrome, lit
l'annonce et la complète avec `python -m src.cli lire save`. L'offre devient alors normale : son
dossier est généré et son CV passe à « À soigner ».
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import JobOffer, JobOfferData, ProfileData
from src.services.cv_curation import CV_TO_CURATE, set_cv_state
from src.services.dossier_generator import DossierResult, prepare_dossier_for_offer
from src.services.job_offers import ORIGIN_MANUAL, create_offer
from src.services.urls import clean_tracking

READ_COMPLETE = "Complète"
READ_TO_READ = "À lire"
READ_UNREADABLE = "Illisible"
READ_STATES = (READ_COMPLETE, READ_TO_READ, READ_UNREADABLE)

UNREAD_PLACEHOLDER = (
    "Annonce non lue automatiquement : {reason} "
    "Lance le skill lire-annonces dans Claude Code pour la lire avec ton navigateur."
)


class ReadingError(ValueError):
    """La lecture complétée est refusée ou l'offre est introuvable."""


def store_unread_offer(engine: Engine, url: str, host: str, reason: str) -> JobOffer:
    """Garde le lien d'une annonce illisible, à lire plus tard avec le navigateur."""
    offer = create_offer(
        engine,
        JobOfferData(
            title=f"Annonce à lire ({host})"[:250],
            url=clean_tracking(url),
            source=f"Import URL — {host}"[:200],
            description=UNREAD_PLACEHOLDER.format(reason=reason.strip() or "lecture impossible."),
        ),
        origin=ORIGIN_MANUAL,
    )
    _set_read_state(engine, offer.id, READ_TO_READ)
    return offer


def _set_read_state(engine: Engine, offer_id: str, state: str) -> None:
    with Session(engine) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise ReadingError("Cette offre n'existe plus.")
        offer.read_state = state


def list_offers_to_read(engine: Engine) -> list[JobOffer]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(JobOffer)
                .where(JobOffer.read_state == READ_TO_READ)
                .order_by(JobOffer.collected_at)
            )
        )


def mark_unreadable(engine: Engine, offer_id: str, reason: str) -> None:
    """Lecture impossible même avec le navigateur : l'offre sort de la liste à lire."""
    with Session(engine) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise ReadingError("Cette offre n'existe plus.")
        offer.read_state = READ_UNREADABLE
        offer.description = (
            f"Annonce illisible : {reason.strip()} Colle son texte dans « File d'envoi » "
            "ou saisis-la à la main."
        )


def complete_read_offer(
    engine: Engine, offer_id: str, details: dict[str, Any], profile: ProfileData
) -> DossierResult:
    """Complète une offre à lire avec ce qui a été lu, puis prépare son dossier."""
    with Session(engine, expire_on_commit=False) as session:
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise ReadingError("Cette offre n'existe plus.")
        url, source, status = offer.url, offer.source, offer.status
    try:
        data = JobOfferData(
            title=str(details.get("title", "")),
            company=str(details.get("company", "")),
            location=str(details.get("location", "")),
            contract_type=details.get("contract_type") or None,
            url=url,
            source=source,
            description=str(details.get("description", "")),
            status=status,  # type: ignore[arg-type]
        )
    except ValidationError as error:
        raise ReadingError(
            "Informations de l'annonce invalides : "
            + "; ".join(issue["msg"] for issue in error.errors())
        ) from error
    with Session(engine) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        offer.title = data.title
        offer.company = data.company
        offer.location = data.location
        offer.contract_type = data.contract_type
        offer.description = data.description
        offer.read_state = READ_COMPLETE
    set_cv_state(engine, offer_id, CV_TO_CURATE)
    return prepare_dossier_for_offer(engine, offer_id, data, profile)
