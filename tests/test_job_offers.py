from __future__ import annotations

import pytest
from pydantic import ValidationError

from src import db
from src.models import CompanyData, JobOfferData
from src.services.companies import create_company, delete_company
from src.services.job_offers import (
    DuplicateOfferURL,
    OfferNotFound,
    create_offer,
    delete_offer,
    list_offers,
    list_offers_for_company,
    update_offer,
)


def offer_data(**overrides: object) -> JobOfferData:
    data: dict[str, object] = {
        "title": "Développeur Symfony",
        "company": "Exemple",
        "location": "Nice",
        "contract_type": "CDI",
        "url": "https://jobs.example.com/backend",
        "description": "Développement et maintenance d'API.",
    }
    data.update(overrides)
    return JobOfferData(**data)


def make_engine(tmp_path):
    engine = db.create_database_engine(tmp_path / "offers.sqlite3")
    db.initialize_database(engine)
    return engine


def test_offer_validation_normalizes_url_and_rejects_invalid_values() -> None:
    offer = offer_data(url=" HTTPS://jobs.example.com/backend ")
    assert offer.url == "https://jobs.example.com/backend"

    with pytest.raises(ValidationError):
        offer_data(title=" ")
    with pytest.raises(ValidationError):
        offer_data(url="javascript:alert(1)")


def test_creates_and_lists_manual_offer(tmp_path) -> None:
    engine = make_engine(tmp_path)

    created = create_offer(engine, offer_data())
    offers = list_offers(engine)

    assert created.title == "Développeur Symfony"
    assert created.source == "Saisie manuelle"
    assert len(created.id) == 32
    assert [offer.id for offer in offers] == [created.id]
    engine.dispose()


def test_rejects_duplicate_normalized_url(tmp_path) -> None:
    engine = make_engine(tmp_path)
    create_offer(engine, offer_data())

    with pytest.raises(DuplicateOfferURL):
        create_offer(engine, offer_data(url="https://JOBS.example.com/backend"))
    assert len(list_offers(engine)) == 1
    engine.dispose()


def test_updates_offer_and_allows_unchanged_url(tmp_path) -> None:
    engine = make_engine(tmp_path)
    created = create_offer(engine, offer_data())

    updated = update_offer(
        engine,
        created.id,
        offer_data(title="Développeur backend Symfony", status="Intéressante"),
    )

    assert updated.id == created.id
    assert updated.title == "Développeur backend Symfony"
    assert updated.status == "Intéressante"
    assert len(list_offers(engine)) == 1
    engine.dispose()


def test_rejects_url_conflict_on_update_and_deletes_offer(tmp_path) -> None:
    engine = make_engine(tmp_path)
    first = create_offer(engine, offer_data())
    second = create_offer(
        engine,
        offer_data(title="Autre poste", url="https://jobs.example.com/other"),
    )

    with pytest.raises(DuplicateOfferURL):
        update_offer(engine, second.id, offer_data(title="Autre poste"))
    with pytest.raises(OfferNotFound):
        delete_offer(engine, "missing")

    delete_offer(engine, first.id)
    assert [offer.id for offer in list_offers(engine)] == [second.id]
    engine.dispose()


def _company_data(**overrides: object) -> CompanyData:
    data: dict[str, object] = {
        "name": "Exemple",
        "location": "Nice",
        "source_url": "https://example.com",
        "development_evidence": "Équipe tech confirmée via le site.",
    }
    data.update(overrides)
    return CompanyData(**data)


def test_offer_auto_links_to_matching_company(tmp_path) -> None:
    engine = make_engine(tmp_path)
    company = create_company(engine, _company_data())

    created = create_offer(engine, offer_data(company="Exemple"))

    assert created.company_id == company.id
    assert len(list_offers_for_company(engine, company.id)) == 1
    engine.dispose()


def test_offer_links_to_company_case_insensitive(tmp_path) -> None:
    engine = make_engine(tmp_path)
    company = create_company(engine, _company_data(name="Atelier Tech"))

    created = create_offer(engine, offer_data(company="atelier tech"))

    assert created.company_id == company.id
    engine.dispose()


def test_offer_without_matching_company_has_null_company_id(tmp_path) -> None:
    engine = make_engine(tmp_path)

    created = create_offer(engine, offer_data(company="Inconnue"))

    assert created.company_id is None
    engine.dispose()


def test_update_offer_resolves_company(tmp_path) -> None:
    engine = make_engine(tmp_path)
    created = create_offer(engine, offer_data(company="Inconnue"))
    assert created.company_id is None

    company = create_company(engine, _company_data(name="Inconnue"))
    updated = update_offer(engine, created.id, offer_data(company="Inconnue"))

    assert updated.company_id == company.id
    engine.dispose()


def test_delete_company_sets_offer_company_id_to_null(tmp_path) -> None:
    engine = make_engine(tmp_path)
    company = create_company(engine, _company_data())
    created = create_offer(engine, offer_data(company="Exemple"))
    assert created.company_id == company.id

    delete_company(engine, company.id)

    offers = list_offers(engine)
    assert len(offers) == 1
    assert offers[0].company_id is None
    engine.dispose()
