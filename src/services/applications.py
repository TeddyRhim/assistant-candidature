from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import Application, ApplicationData, Company, JobOffer


class ApplicationNotFound(LookupError):
    """The requested application does not exist."""


class ApplicationReferenceNotFound(LookupError):
    """A referenced company or offer does not exist."""


def _validate_references(session: Session, data: ApplicationData) -> None:
    if session.get(Company, data.company_id) is None:
        raise ApplicationReferenceNotFound("L'entreprise sélectionnée n'existe plus.")
    if data.job_offer_id and session.get(JobOffer, data.job_offer_id) is None:
        raise ApplicationReferenceNotFound("L'offre sélectionnée n'existe plus.")


def create_application(engine: Engine, data: ApplicationData) -> Application:
    application = Application(
        id=uuid.uuid4().hex,
        **data.model_dump(),
        created_at=datetime.now(UTC).isoformat(),
    )
    with Session(engine, expire_on_commit=False) as session, session.begin():
        _validate_references(session, data)
        session.add(application)
    return application


def update_application(engine: Engine, application_id: str, data: ApplicationData) -> Application:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        application = session.get(Application, application_id)
        if application is None:
            raise ApplicationNotFound("Cette candidature n'existe plus.")
        _validate_references(session, data)
        for field, value in data.model_dump().items():
            setattr(application, field, value)
    return application


def delete_application(engine: Engine, application_id: str) -> None:
    with Session(engine) as session, session.begin():
        application = session.get(Application, application_id)
        if application is None:
            raise ApplicationNotFound("Cette candidature n'existe plus.")
        session.delete(application)


def list_applications(engine: Engine) -> list[Application]:
    query = select(Application).order_by(Application.created_at.desc())
    with Session(engine) as session:
        return list(session.scalars(query))
