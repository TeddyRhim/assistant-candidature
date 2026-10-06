from __future__ import annotations

import pytest
from pydantic import ValidationError

from src import db
from src.models import ApplicationData, CompanyData, JobOfferData
from src.services.applications import (
    ApplicationReferenceNotFound,
    create_application,
    delete_application,
    list_applications,
)
from src.services.companies import CompanyInUse, create_company, delete_company
from src.services.job_offers import create_offer, delete_offer


def make_engine(tmp_path):
    engine = db.create_database_engine(tmp_path / "applications.sqlite3")
    db.initialize_database(engine)
    return engine


def make_company(engine):
    return create_company(
        engine,
        CompanyData(
            name="Studio Exemple",
            location="Nice",
            source_url="https://example.com/team",
            development_evidence="Page de présentation de l'équipe logicielle.",
        ),
    )


def test_application_tracks_spontaneous_outreach_and_next_action(tmp_path) -> None:
    engine = make_engine(tmp_path)
    company = make_company(engine)
    application = create_application(
        engine,
        ApplicationData(
            company_id=company.id,
            role="Développeur backend",
            status="Envoyée",
            applied_on="2026-10-05",
            next_action="Relancer le service recrutement",
            next_action_on="2026-10-19",
        ),
    )

    assert application.job_offer_id is None
    assert application.status == "Envoyée"
    assert application.next_action_on == "2026-10-19"
    assert [record.id for record in list_applications(engine)] == [application.id]
    engine.dispose()


def test_application_rejects_invalid_dates_and_missing_references(tmp_path) -> None:
    with pytest.raises(ValidationError, match="AAAA-MM-JJ"):
        ApplicationData(company_id="x" * 32, role="Développeur", applied_on="05/10/2026")

    engine = make_engine(tmp_path)
    with pytest.raises(ApplicationReferenceNotFound):
        create_application(
            engine,
            ApplicationData(company_id="x" * 32, role="Développeur"),
        )
    engine.dispose()


def test_company_cannot_be_deleted_while_spontaneous_application_exists(tmp_path) -> None:
    engine = make_engine(tmp_path)
    company = make_company(engine)
    application = create_application(
        engine,
        ApplicationData(company_id=company.id, role="Développeur backend"),
    )

    with pytest.raises(CompanyInUse):
        delete_company(engine, company.id)

    delete_application(engine, application.id)
    delete_company(engine, company.id)
    engine.dispose()


def test_deleting_offer_keeps_application_as_spontaneous_outreach(tmp_path) -> None:
    engine = make_engine(tmp_path)
    company = make_company(engine)
    offer = create_offer(
        engine,
        JobOfferData(title="Développeur", company=company.name, description="API backend"),
    )
    application = create_application(
        engine,
        ApplicationData(
            company_id=company.id,
            job_offer_id=offer.id,
            role="Développeur",
        ),
    )

    delete_offer(engine, offer.id)

    records = list_applications(engine)
    assert len(records) == 1
    assert records[0].id == application.id
    assert records[0].job_offer_id is None
    engine.dispose()
