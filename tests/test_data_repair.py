from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from src.db import create_database_engine, initialize_database
from src.models import JobOffer, JobOfferData, ResumeVersion
from src.services.data_repair import repair_legacy_data
from src.services.job_offers import create_offer
from src.services.tailored_resumes import get_tailored_resume, save_tailored_resume


def _engine(tmp_path: Path):
    engine = create_database_engine(tmp_path / "repair.sqlite3")
    initialize_database(engine)
    with Session(engine) as session, session.begin():
        session.add(
            ResumeVersion(
                id="a" * 32,
                original_filename="cv.pdf",
                stored_filename="cv-stored.pdf",
                reviewed_text="Dev.",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
    return engine


def _cv(role: str) -> str:
    return json.dumps(
        {
            "name": "Alex Martin",
            "profile": "Dev.",
            "experience": [],
            "education": [],
            "skills": [],
            "target_role": role,
        },
        ensure_ascii=False,
    )


def test_stale_target_role_and_html_entities_are_repaired_once(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    offer = create_offer(
        engine,
        JobOfferData(
            title="Développeur PHP H/F at Acme — Nice | LinkedIn Jobs",
            company="Acme",
            location="Provence-Alpes-Côte d&#39;Azur",
            url="https://x.test/o",
            description="Poste d&amp;ev sur l&#39;API.",
        ),
    )
    save_tailored_resume(
        engine, offer.id, "a" * 32, _cv("Développeur PHP H/F at Acme — Nice | LinkedIn Jobs")
    )

    first = repair_legacy_data(engine)
    second = repair_legacy_data(engine)

    assert (first.offers_decoded, first.resumes_fixed) == (1, 1)
    assert not second.changed
    with Session(engine) as session:
        stored = session.get(JobOffer, offer.id)
        assert stored.location == "Provence-Alpes-Côte d'Azur"
        assert stored.description == "Poste d&ev sur l'API."
    repaired = json.loads(get_tailored_resume(engine, offer.id).content)
    assert repaired["target_role"] == "Développeur PHP"


def test_clean_data_and_unreadable_json_are_left_alone(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    offer = create_offer(
        engine,
        JobOfferData(title="Développeur PHP", url="https://x.test/p", description="PHP."),
    )
    save_tailored_resume(engine, offer.id, "a" * 32, _cv("Développeur PHP"))

    assert not repair_legacy_data(engine).changed
