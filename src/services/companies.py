from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import Application, Company, CompanyData


class DuplicateCompany(ValueError):
    """A company with the same name and location already exists."""


class CompanyNotFound(LookupError):
    """The requested company does not exist."""


class CompanyInUse(ValueError):
    """The company is referenced by one or more application records."""


def _ensure_company_is_new(
    session: Session,
    company_data: CompanyData,
    excluding_company_id: str | None = None,
) -> None:
    query = select(Company.id).where(
        Company.name.collate("NOCASE") == company_data.name,
        Company.location.collate("NOCASE") == company_data.location,
    )
    if excluding_company_id is not None:
        query = query.where(Company.id != excluding_company_id)
    if session.scalar(query) is not None:
        raise DuplicateCompany("Cette entreprise est déjà enregistrée dans cette zone.")


def create_company(engine: Engine, company_data: CompanyData) -> Company:
    now = datetime.now(UTC).isoformat()
    company = Company(
        id=uuid.uuid4().hex,
        **company_data.model_dump(),
        created_at=now,
        verified_at=now,
    )
    with Session(engine, expire_on_commit=False) as session, session.begin():
        _ensure_company_is_new(session, company_data)
        session.add(company)
    return company


def update_company(engine: Engine, company_id: str, company_data: CompanyData) -> Company:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        company = session.get(Company, company_id)
        if company is None:
            raise CompanyNotFound("Cette entreprise n'existe plus.")
        _ensure_company_is_new(session, company_data, excluding_company_id=company_id)
        for field, value in company_data.model_dump().items():
            setattr(company, field, value)
        company.verified_at = datetime.now(UTC).isoformat()
    return company


def delete_company(engine: Engine, company_id: str) -> None:
    with Session(engine) as session, session.begin():
        company = session.get(Company, company_id)
        if company is None:
            raise CompanyNotFound("Cette entreprise n'existe plus.")
        if session.scalar(select(Application.id).where(Application.company_id == company_id)):
            raise CompanyInUse(
                "Cette entreprise est liée à une candidature ; supprime ou réaffecte la "
                "candidature avant de supprimer la fiche."
            )
        session.delete(company)


def list_companies(engine: Engine, location_filter: str | None = None) -> list[Company]:
    query = select(Company).order_by(Company.name.collate("NOCASE"))
    if location_filter:
        query = query.where(Company.location.ilike(f"%{location_filter.strip()}%"))
    with Session(engine) as session:
        return list(session.scalars(query))
