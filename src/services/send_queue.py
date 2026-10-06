from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from src.models import (
    Application,
    Company,
    CoverLetter,
    JobOffer,
    JobOfferData,
    OfferStatus,
    ProfileData,
    TailoredResume,
)
from src.services.applications import ApplicationNotFound
from src.services.job_offers import OfferNotFound
from src.services.matching import assess_offer_fit

FOLLOW_UP_DAYS = 7
QUEUE_OFFER_STATUSES = ("À examiner", "Intéressante")
UNKNOWN_COMPANY_NAME = "Entreprise non précisée"
UNKNOWN_LOCATION = "Lieu non précisé"


class OfferAlreadySent(ValueError):
    """The offer already has a tracked application."""


@dataclass(frozen=True)
class QueueItem:
    offer: JobOffer
    score: int
    has_resume: bool
    has_letter: bool

    @property
    def is_ready(self) -> bool:
        return self.has_resume and self.has_letter


def offer_to_data(offer: JobOffer) -> JobOfferData:
    return JobOfferData(
        title=offer.title,
        company=offer.company,
        location=offer.location,
        contract_type=offer.contract_type,  # type: ignore[arg-type]
        url=offer.url,
        source=offer.source,
        description=offer.description,
        status=offer.status,  # type: ignore[arg-type]
    )


def build_send_queue(engine: Engine, profile: ProfileData, min_score: int = 0) -> list[QueueItem]:
    """Offres à traiter : sans candidature suivie, non écartées, classées par pertinence.

    Les offres marquées « Intéressante » passent devant, puis le score global décroissant.
    Une offre sous le seuil reste visible si elle a été marquée « Intéressante ».
    """
    with Session(engine) as session:
        sent_offer_ids = set(
            session.scalars(
                select(Application.job_offer_id).where(Application.job_offer_id.is_not(None))
            )
        )
        resume_offer_ids = set(session.scalars(select(TailoredResume.job_offer_id)))
        letter_offer_ids = set(
            session.scalars(
                select(CoverLetter.job_offer_id).where(CoverLetter.job_offer_id.is_not(None))
            )
        )
        offers = list(
            session.scalars(
                select(JobOffer).where(JobOffer.status.in_(QUEUE_OFFER_STATUSES))
            )
        )

    items = []
    for offer in offers:
        if offer.id in sent_offer_ids:
            continue
        score = assess_offer_fit(offer_to_data(offer), profile).overall_percentage or 0
        if score < min_score and offer.status != "Intéressante":
            continue
        items.append(
            QueueItem(
                offer=offer,
                score=score,
                has_resume=offer.id in resume_offer_ids,
                has_letter=offer.id in letter_offer_ids,
            )
        )
    items.sort(key=lambda item: (item.offer.status == "Intéressante", item.score), reverse=True)
    return items


def set_offer_status(engine: Engine, offer_id: str, status: OfferStatus) -> None:
    with Session(engine) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise OfferNotFound("Cette offre n'existe plus.")
        offer.status = status


def _company_for_offer(session: Session, offer: JobOffer, now: str) -> Company:
    if offer.company_id:
        company = session.get(Company, offer.company_id)
        if company is not None:
            return company
    name = offer.company.strip() or UNKNOWN_COMPANY_NAME
    existing = session.scalar(
        select(Company).where(func.lower(Company.name) == name.casefold())
    )
    if existing is not None:
        return existing
    company = Company(
        id=uuid.uuid4().hex,
        name=name,
        location=offer.location.strip() or UNKNOWN_LOCATION,
        website_url=None,
        source_url=offer.url or "Annonce enregistrée localement (sans URL)",
        development_evidence=(
            f"Annonce « {offer.title} » publiée par cette entreprise ({offer.source})."
        ),
        public_contact_email=None,
        contact_source_url=None,
        notes=(
            "Fiche créée automatiquement lors du suivi d'une candidature ; "
            "seule l'annonce atteste le recrutement."
        ),
        created_at=now,
        verified_at=now,
    )
    session.add(company)
    session.flush()
    return company


def mark_offer_sent(
    engine: Engine,
    offer_id: str,
    *,
    sent_on: date | None = None,
    follow_up_days: int = FOLLOW_UP_DAYS,
) -> Application:
    """Enregistre que l'utilisateur a envoyé sa candidature et planifie une relance."""
    sent = sent_on or date.today()
    now = datetime.now(UTC).isoformat()
    with Session(engine, expire_on_commit=False) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise OfferNotFound("Cette offre n'existe plus.")
        if session.scalar(select(Application.id).where(Application.job_offer_id == offer_id)):
            raise OfferAlreadySent("Une candidature est déjà suivie pour cette offre.")
        company = _company_for_offer(session, offer, now)
        application = Application(
            id=uuid.uuid4().hex,
            company_id=company.id,
            job_offer_id=offer.id,
            role=offer.title[:250],
            status="Envoyée",
            applied_on=sent.isoformat(),
            next_action="Relancer si aucune réponse",
            next_action_on=(sent + timedelta(days=follow_up_days)).isoformat(),
            notes="",
            created_at=now,
        )
        session.add(application)
        offer.status = "Candidature liée"
        offer.company_id = company.id
    return application


def due_follow_ups(engine: Engine, today: date | None = None) -> list[Application]:
    """Candidatures envoyées dont la date de relance est atteinte."""
    limit = (today or date.today()).isoformat()
    query = (
        select(Application)
        .where(
            Application.status == "Envoyée",
            Application.next_action_on.is_not(None),
            Application.next_action_on <= limit,
        )
        .order_by(Application.next_action_on)
    )
    with Session(engine) as session:
        return list(session.scalars(query))


def mark_followed_up(
    engine: Engine,
    application_id: str,
    *,
    today: date | None = None,
    follow_up_days: int = FOLLOW_UP_DAYS,
) -> Application:
    """Note la relance effectuée et reporte la prochaine."""
    day = today or date.today()
    with Session(engine, expire_on_commit=False) as session, session.begin():
        application = session.get(Application, application_id)
        if application is None:
            raise ApplicationNotFound("Cette candidature n'existe plus.")
        application.next_action_on = (day + timedelta(days=follow_up_days)).isoformat()
        application.next_action = "Relancer à nouveau si aucune réponse"
        log = f"Relancée le {day.isoformat()}."
        application.notes = f"{application.notes}\n{log}".strip()
    return application
