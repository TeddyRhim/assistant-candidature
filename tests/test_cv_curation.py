from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from src import cli
from src.config import DATA_DIRECTORY_ENV
from src.db import create_database_engine, initialize_database
from src.models import JobOfferData, ProfileData, ResumeVersion, SkillRating
from src.services.cv_curation import (
    CV_AUTOMATIC,
    CV_CURATED,
    CV_TO_CURATE,
    CurationError,
    curation_brief,
    list_offers_to_curate,
    resolve_offer,
    save_curated_cv,
    set_cv_state,
    validate_curated_cv,
)
from src.services.cv_renderer import load_base_cv_data
from src.services.job_offers import create_offer
from src.services.send_queue import complete_offer_description
from src.services.tailored_resumes import get_tailored_resume


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))
    database = create_database_engine(tmp_path / "assistant-candidatures.sqlite3")
    initialize_database(database)
    with Session(database) as session, session.begin():
        session.add(
            ResumeVersion(
                id="a" * 32,
                original_filename="cv.pdf",
                stored_filename="cv-stored.pdf",
                reviewed_text="Développeur.",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
    return database


def _profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur backend",
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)],
    )


def _offer(engine: Engine, title: str = "Développeur PHP Symfony H/F") -> str:
    offer = create_offer(
        engine,
        JobOfferData(
            title=title,
            company="Atelier Tech",
            location="Bourg-en-Bresse",
            url=f"https://example.com/{title.replace(' ', '-')}",
            description="PHP et Symfony, API REST.",
        ),
    )
    return offer.id


def _curated(base: dict) -> dict:
    curated = copy.deepcopy(base)
    first = curated["experience"][0]
    first["bullets"] = [f"{bullet}" for bullet in first["bullets"]][::-1]
    return curated


def test_new_offers_are_automatic_and_state_changes_are_listed(engine: Engine) -> None:
    offer_id = _offer(engine)
    assert list_offers_to_curate(engine) == []

    set_cv_state(engine, offer_id, CV_TO_CURATE)
    assert [offer.id for offer in list_offers_to_curate(engine)] == [offer_id]

    set_cv_state(engine, offer_id, CV_AUTOMATIC)
    assert list_offers_to_curate(engine) == []
    with pytest.raises(CurationError):
        set_cv_state(engine, offer_id, "Inconnu")


def test_offer_reference_accepts_a_unique_prefix(engine: Engine) -> None:
    offer_id = _offer(engine)

    assert resolve_offer(engine, offer_id[:8]).id == offer_id
    with pytest.raises(CurationError):
        resolve_offer(engine, "zzzz")
    with pytest.raises(CurationError):
        resolve_offer(engine, "")


def test_validation_accepts_reordered_and_reworded_cv() -> None:
    base = load_base_cv_data()
    curated = _curated(base)

    assert validate_curated_cv(base, curated) == []


def test_validation_refuses_invented_technology_number_and_facts() -> None:
    base = load_base_cv_data()
    curated = _curated(base)
    curated["experience"][0]["bullets"].append("Pilotage de Terraform sur 99 serveurs.")
    curated["experience"][0]["company"] = "Autre Société"
    curated["education"] = [{"text": "Doctorat"}]
    curated["experience"].append({"role": "X", "company": "Y", "period": "Z", "bullets": ["a"]})

    errors = " ".join(validate_curated_cv(base, curated))

    assert "Terraform" in errors
    assert "99" in errors
    assert "education" in errors
    assert "expériences" in errors


def test_validation_refuses_changed_company_and_period() -> None:
    base = load_base_cv_data()
    curated = _curated(base)
    curated["experience"][0]["company"] = "Autre Société"
    curated["experience"][0]["period"] = "1999"

    errors = validate_curated_cv(base, curated)

    assert any("company" in error for error in errors)
    assert any("period" in error for error in errors)


def test_saving_a_curated_cv_marks_the_offer_as_curated(engine: Engine) -> None:
    offer = resolve_offer(engine, _offer(engine))
    set_cv_state(engine, offer.id, CV_TO_CURATE)

    result = save_curated_cv(engine, offer, json.dumps(_curated(load_base_cv_data())))

    assert result.accepted
    assert get_tailored_resume(engine, offer.id) is not None
    assert resolve_offer(engine, offer.id).cv_state == CV_CURATED


def test_a_refused_cv_changes_nothing(engine: Engine) -> None:
    offer = resolve_offer(engine, _offer(engine))
    set_cv_state(engine, offer.id, CV_TO_CURATE)
    invented = _curated(load_base_cv_data())
    invented["experience"][0]["bullets"].append("Déploiement avec Terraform.")

    result = save_curated_cv(engine, offer, json.dumps(invented))

    assert not result.accepted
    assert get_tailored_resume(engine, offer.id) is None
    assert resolve_offer(engine, offer.id).cv_state == CV_TO_CURATE
    assert not save_curated_cv(engine, offer, "pas du json").accepted


def test_brief_gives_offer_base_cv_and_keywords(engine: Engine) -> None:
    offer = resolve_offer(engine, _offer(engine))

    brief = curation_brief(engine, offer, _profile())

    assert brief["offer"]["role"] == "Développeur PHP Symfony"
    assert brief["offer"]["description"] == "PHP et Symfony, API REST."
    assert brief["base_cv"] == load_base_cv_data()
    assert "PHP" in brief["keywords"]["found_in_base_cv"]
    assert brief["current_cv"] is None


def test_completing_the_ad_text_sends_a_curated_cv_back_to_the_queue(engine: Engine) -> None:
    offer = create_offer(
        engine,
        JobOfferData(
            title="Développeur PHP",
            company="Atelier Tech",
            location="Bourg-en-Bresse",
            url="https://example.com/court",
            source="Adzuna",
            description="PHP.",
        ),
    )
    set_cv_state(engine, offer.id, CV_CURATED)

    complete_offer_description(
        engine, offer.id, "PHP, Symfony et API REST pour notre équipe produit.", _profile()
    )

    assert resolve_offer(engine, offer.id).cv_state == CV_TO_CURATE


def test_cli_lists_shows_saves_and_changes_state(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    offer_id = _offer(engine)
    cli.main(["soigner", "state", offer_id[:8], CV_TO_CURATE])

    assert cli.main(["soigner", "list"]) == 0
    assert offer_id[:8] in capsys.readouterr().out

    assert cli.main(["soigner", "show", offer_id[:8]]) == 0
    assert json.loads(capsys.readouterr().out)["offer"]["id"] == offer_id

    refused = tmp_path / "refuse.json"
    invented = _curated(load_base_cv_data())
    invented["experience"][0]["bullets"].append("Terraform.")
    refused.write_text(json.dumps(invented), encoding="utf-8")
    assert cli.main(["soigner", "save", offer_id[:8], str(refused)]) == 1
    assert "Terraform" in capsys.readouterr().out

    accepted = tmp_path / "ok.json"
    accepted.write_text(json.dumps(_curated(load_base_cv_data())), encoding="utf-8")
    assert cli.main(["soigner", "save", offer_id[:8], str(accepted)]) == 0
    assert resolve_offer(engine, offer_id).cv_state == CV_CURATED
    assert cli.main(["soigner", "show", "inconnu"]) == 1
