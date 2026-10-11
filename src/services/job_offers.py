from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from src.models import Application, Company, JobOffer, JobOfferData
from src.services.urls import normalize_url


class DuplicateOfferURL(ValueError):
    """An offer with the same normalized URL already exists."""


class OfferNotFound(LookupError):
    """The requested offer does not exist."""


def _find_by_url(
    session: Session, url: str | None, excluding_offer_id: str | None = None
) -> JobOffer | None:
    """Offre déjà enregistrée pour ce lien, en ignorant le suivi, www, la barre finale…"""
    if not url or not url.strip():
        return None
    wanted = normalize_url(url)
    query = select(JobOffer).where(JobOffer.url.is_not(None))
    if excluding_offer_id is not None:
        query = query.where(JobOffer.id != excluding_offer_id)
    for offer in session.scalars(query):
        if normalize_url(offer.url) == wanted:
            return offer
    return None


def find_offer_by_url(engine: Engine, url: str) -> JobOffer | None:
    with Session(engine) as session:
        return _find_by_url(session, url)


def _ensure_unique_url(
    session: Session,
    url: str | None,
    excluding_offer_id: str | None = None,
) -> None:
    existing = _find_by_url(session, url, excluding_offer_id)
    if existing is not None:
        raise DuplicateOfferURL(
            f"Cette annonce est déjà enregistrée : « {existing.title} » ({existing.status})."
        )


def _resolve_company_id(
    session: Session,
    company_name: str,
    explicit_company_id: str | None,
) -> str | None:
    if explicit_company_id:
        company_exists = session.get(Company, explicit_company_id)
        if company_exists is not None:
            return explicit_company_id
    cleaned = company_name.strip()
    if not cleaned:
        return None
    return session.scalar(
        select(Company.id).where(func.lower(Company.name) == cleaned.casefold())
    )


ORIGIN_APPLICATION = "Application"
ORIGIN_MANUAL = "Manuel"


def create_offer(
    engine: Engine, offer_data: JobOfferData, *, origin: str = ORIGIN_APPLICATION
) -> JobOffer:
    """Enregistre une offre ; `origin` dit si l'application l'a importée ou si tu l'as saisie."""
    data_dict = offer_data.model_dump()
    with Session(engine, expire_on_commit=False) as session, session.begin():
        _ensure_unique_url(session, offer_data.url)
        resolved_company_id = _resolve_company_id(
            session, offer_data.company, offer_data.company_id
        )
        data_dict["company_id"] = resolved_company_id
        offer = JobOffer(
            id=uuid.uuid4().hex,
            **data_dict,
            collected_at=datetime.now(UTC).isoformat(),
            origin=origin,
        )
        session.add(offer)
    return offer


def update_offer(engine: Engine, offer_id: str, offer_data: JobOfferData) -> JobOffer:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise OfferNotFound("Cette offre n'existe plus.")
        _ensure_unique_url(session, offer_data.url, excluding_offer_id=offer_id)
        resolved_company_id = _resolve_company_id(
            session, offer_data.company, offer_data.company_id
        )
        for field, value in offer_data.model_dump().items():
            setattr(offer, field, value)
        offer.company_id = resolved_company_id
    return offer


def delete_offer(engine: Engine, offer_id: str) -> None:
    with Session(engine) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise OfferNotFound("Cette offre n'existe plus.")
        for application in session.scalars(
            select(Application).where(Application.job_offer_id == offer_id)
        ):
            application.job_offer_id = None
        session.delete(offer)


def get_offer(engine: Engine, offer_id: str) -> JobOffer | None:
    with Session(engine) as session:
        return session.get(JobOffer, offer_id)


def list_offers(engine: Engine) -> list[JobOffer]:
    with Session(engine) as session:
        return list(
            session.scalars(select(JobOffer).order_by(JobOffer.collected_at.desc()))
        )


def list_offers_for_company(engine: Engine, company_id: str) -> list[JobOffer]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(JobOffer)
                .where(JobOffer.company_id == company_id)
                .order_by(JobOffer.collected_at.desc())
            )
        )
