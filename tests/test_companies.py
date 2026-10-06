from __future__ import annotations

import pytest
from pydantic import ValidationError

from src import db
from src.models import CompanyData
from src.services.companies import (
    CompanyNotFound,
    DuplicateCompany,
    create_company,
    delete_company,
    list_companies,
    update_company,
)


def company_data(**overrides: object) -> CompanyData:
    data: dict[str, object] = {
        "name": "Studio Exemple",
        "location": "Nice",
        "website_url": "https://studio.example.com",
        "source_url": "https://studio.example.com/equipe",
        "development_evidence": (
            "L'entreprise édite un logiciel métier et publie des offres backend."
        ),
        "public_contact_email": "jobs@studio.example.com",
        "contact_source_url": "https://studio.example.com/contact",
        "notes": "Candidature spontanée à préparer.",
    }
    data.update(overrides)
    return CompanyData(**data)


def make_engine(tmp_path):
    engine = db.create_database_engine(tmp_path / "companies.sqlite3")
    db.initialize_database(engine)
    return engine


def test_company_requires_source_for_public_contact_and_activity() -> None:
    with pytest.raises(ValidationError, match="source de l'adresse e-mail"):
        company_data(contact_source_url=None)
    with pytest.raises(ValidationError):
        company_data(source_url="javascript:alert(1)")
    with pytest.raises(ValidationError):
        company_data(development_evidence=" ")


def test_creates_and_filters_companies_by_location(tmp_path) -> None:
    engine = make_engine(tmp_path)
    nice = create_company(engine, company_data())
    create_company(engine, company_data(name="Studio Var", location="Toulon"))

    assert nice.name == "Studio Exemple"
    assert [company.location for company in list_companies(engine, "nice")] == ["Nice"]
    assert len(list_companies(engine)) == 2
    engine.dispose()


def test_rejects_duplicate_company_name_in_same_location_case_insensitively(tmp_path) -> None:
    engine = make_engine(tmp_path)
    create_company(engine, company_data())

    with pytest.raises(DuplicateCompany):
        create_company(engine, company_data(name="STUDIO EXEMPLE", location="nice"))

    assert len(list_companies(engine)) == 1
    engine.dispose()


def test_updates_verification_date_and_deletes_company(tmp_path) -> None:
    engine = make_engine(tmp_path)
    company = create_company(engine, company_data())
    updated = update_company(
        engine,
        company.id,
        company_data(development_evidence="Page équipe technique et logiciels édités."),
    )
    assert updated.id == company.id
    assert updated.development_evidence == "Page équipe technique et logiciels édités."
    assert updated.verified_at >= company.verified_at

    with pytest.raises(CompanyNotFound):
        delete_company(engine, "missing")
    delete_company(engine, company.id)
    assert list_companies(engine) == []
    engine.dispose()
