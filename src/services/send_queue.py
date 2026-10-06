from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote_plus

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
from src.services.companies import CompanyNotFound
from src.services.dossier_generator import prepare_dossier_for_offer
from src.services.job_offers import OfferNotFound
from src.services.matching import assess_offer_fit
from src.services.tailored_resumes import get_tailored_resume

FOLLOW_UP_DAYS = 7
QUEUE_OFFER_STATUSES = ("À examiner", "Intéressante")
UNKNOWN_COMPANY_NAME = "Entreprise non précisée"
UNKNOWN_LOCATION = "Lieu non précisé"


# Adzuna coupe ses extraits à 500 caractères : une description de cette longueur est suspecte.
TRUNCATED_DESCRIPTION_LENGTHS = range(480, 521)
MAX_DESCRIPTION_CHARACTERS = 100_000


class OfferAlreadySent(ValueError):
    """The offer already has a tracked application."""


def is_probably_truncated(offer: JobOffer) -> bool:
    """Vrai pour un extrait Adzuna coupé : le texte complet reste à coller depuis l'annonce."""
    return (
        offer.source.startswith("Adzuna")
        and len(offer.description.strip()) in TRUNCATED_DESCRIPTION_LENGTHS
    )


@dataclass(frozen=True)
class DescriptionUpdate:
    score_before: int
    score_after: int
    dossier_regenerated: bool
    dossier_error: str | None = None


def complete_offer_description(
    engine: Engine,
    offer_id: str,
    text: str,
    profile: ProfileData,
    *,
    regenerate: bool = True,
) -> DescriptionUpdate:
    """Remplace l'extrait d'une offre par le texte complet collé, et refait le dossier.

    La régénération écrase le CV ciblé et la lettre déjà enregistrés pour cette offre.
    """
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Colle le texte complet de l'annonce.")
    if len(cleaned) > MAX_DESCRIPTION_CHARACTERS:
        raise ValueError("Le texte collé dépasse la taille maximale autorisée.")
    with Session(engine, expire_on_commit=False) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise OfferNotFound("Cette offre n'existe plus.")
        if len(cleaned) <= len(offer.description.strip()):
            raise ValueError(
                "Le texte collé n'est pas plus complet que celui déjà enregistré."
            )
        score_before = assess_offer_fit(offer_to_data(offer), profile).overall_percentage or 0
        offer.description = cleaned
        updated_data = offer_to_data(offer)
    score_after = assess_offer_fit(updated_data, profile).overall_percentage or 0

    regenerated = False
    dossier_error = None
    if regenerate:
        existing = get_tailored_resume(engine, offer_id)
        result = prepare_dossier_for_offer(
            engine,
            offer_id,
            updated_data,
            profile,
            source_resume_id=existing.source_resume_id if existing else None,
        )
        regenerated = bool(result.tailored_resume_id or result.cover_letter_id)
        dossier_error = result.error
    return DescriptionUpdate(score_before, score_after, regenerated, dossier_error)


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
    prep_seconds: int | None = None,
) -> Application:
    """Enregistre que l'utilisateur a envoyé sa candidature et planifie une relance.

    `prep_seconds` est le temps passé sur le dossier avant l'envoi (mesure facultative).
    """
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
            prep_seconds=prep_seconds,
        )
        session.add(application)
        offer.status = "Candidature liée"
        offer.company_id = company.id
    return application


@dataclass(frozen=True)
class SpontaneousItem:
    company: Company
    has_letter: bool


class SpontaneousAlreadySent(ValueError):
    """The company already has a tracked spontaneous application."""


def build_spontaneous_queue(engine: Engine) -> list[SpontaneousItem]:
    """Entreprises à prospecter sans aucune candidature suivie, les plus récentes d'abord.

    Une entreprise déjà visée par une candidature (offre ou spontanée) n'est pas reproposée.
    """
    with Session(engine) as session:
        contacted_ids = set(session.scalars(select(Application.company_id)))
        letter_company_ids = set(
            session.scalars(
                select(CoverLetter.company_id)
                .where(CoverLetter.company_id.is_not(None))
                .where(CoverLetter.job_offer_id.is_(None))
            )
        )
        companies = list(session.scalars(select(Company).order_by(Company.created_at.desc())))
    return [
        SpontaneousItem(company=company, has_letter=company.id in letter_company_ids)
        for company in companies
        if company.id not in contacted_ids
    ]


def contact_search_links(company: Company) -> list[tuple[str, str]]:
    """Liens de recherche à ouvrir soi-même pour trouver un contact (rien n'est collecté)."""
    name = company.name.strip()
    place = "" if company.location == UNKNOWN_LOCATION else company.location.strip()
    web_query = quote_plus(f"{name} {place} contact recrutement".strip())
    return [
        ("Chercher le site et le contact", f"https://www.google.com/search?q={web_query}"),
        (
            "Chercher sur LinkedIn",
            f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(name)}",
        ),
    ]


def mark_spontaneous_sent(
    engine: Engine,
    company_id: str,
    *,
    role: str,
    sent_on: date | None = None,
    follow_up_days: int = FOLLOW_UP_DAYS,
) -> Application:
    """Enregistre une candidature spontanée envoyée (sans offre) et planifie la relance."""
    sent = sent_on or date.today()
    cleaned_role = role.strip()
    if not cleaned_role:
        raise ValueError("Indique le poste visé pour suivre la candidature spontanée.")
    with Session(engine, expire_on_commit=False) as session, session.begin():
        if session.get(Company, company_id) is None:
            raise CompanyNotFound("Cette entreprise n'existe plus.")
        if session.scalar(
            select(Application.id).where(
                Application.company_id == company_id, Application.job_offer_id.is_(None)
            )
        ):
            raise SpontaneousAlreadySent(
                "Une candidature spontanée est déjà suivie pour cette entreprise."
            )
        application = Application(
            id=uuid.uuid4().hex,
            company_id=company_id,
            job_offer_id=None,
            role=cleaned_role[:250],
            status="Envoyée",
            applied_on=sent.isoformat(),
            next_action="Relancer si aucune réponse",
            next_action_on=(sent + timedelta(days=follow_up_days)).isoformat(),
            notes="Candidature spontanée.",
            created_at=datetime.now(UTC).isoformat(),
        )
        session.add(application)
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


MAX_PREP_SECONDS = 3600


def clamp_prep_seconds(seconds: float) -> int:
    """Borne une durée mesurée : une fenêtre laissée ouverte ne doit pas fausser la moyenne."""
    return max(0, min(int(seconds), MAX_PREP_SECONDS))


def average_prep_seconds(engine: Engine) -> int | None:
    """Temps moyen passé sur un dossier avant envoi, ou None si aucune mesure."""
    with Session(engine) as session:
        values = list(
            session.scalars(
                select(Application.prep_seconds).where(Application.prep_seconds.is_not(None))
            )
        )
    if not values:
        return None
    return round(sum(values) / len(values))
