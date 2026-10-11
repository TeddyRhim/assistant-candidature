from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

from pydantic import AnyHttpUrl
from sqlalchemy import Engine

from src.db import create_database_engine, initialize_database
from src.models import ApplicationData, CompanyData, JobOfferData, ProfileData, SkillRating
from src.services.applications import create_application
from src.services.companies import create_company
from src.services.job_offers import create_offer, list_offers
from src.services.job_sources.base import SourceListing
from src.services.job_watcher import (
    MonitoredTarget,
    WatcherConfig,
    run_watcher_cycle,
)
from src.services.recent_applications import recent_application_keys, was_recently_applied
from src.services.send_queue import build_send_queue

TODAY = date(2026, 10, 12)


def _engine(tmp_path: Path) -> Engine:
    engine = create_database_engine(tmp_path / "recent.sqlite3")
    initialize_database(engine)
    return engine


def _apply(
    engine: Engine, company_name: str, role: str, applied_on: str, status: str = "Envoyée"
) -> None:
    company = create_company(
        engine,
        CompanyData(
            name=company_name,
            location="Nice",
            source_url="https://example.com/team",
            development_evidence="Équipe logicielle présentée sur le site.",
        ),
    )
    create_application(
        engine,
        ApplicationData(
            company_id=company.id, role=role, status=status, applied_on=applied_on
        ),
    )


def test_only_applications_from_the_last_seven_days_count(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _apply(engine, "Atelier Tech", "Développeur PHP", "2026-10-08")
    _apply(engine, "Vieille Maison", "Développeur PHP", "2026-09-20")
    _apply(engine, "Brouillon SA", "Développeur PHP", "2026-10-10", status="À préparer")

    keys = recent_application_keys(engine, today=TODAY)

    assert was_recently_applied(keys, "Développeur PHP", "Atelier Tech")
    assert not was_recently_applied(keys, "Développeur PHP", "Vieille Maison")
    assert not was_recently_applied(keys, "Développeur PHP", "Brouillon SA")


def test_same_job_republished_with_other_wording_is_recognised(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _apply(engine, "Astek", "Développeur PHP - Symfony H/F", "2026-10-11")

    keys = recent_application_keys(engine, today=TODAY)

    assert was_recently_applied(keys, "Developpeur PHP Symfony (IT) F/H - CDI", "ASTEK SA")
    assert not was_recently_applied(keys, "Développeur Python", "Astek")
    assert not was_recently_applied(keys, "Développeur PHP Symfony", "Scalian")
    assert not was_recently_applied(keys, "Développeur PHP Symfony", "")


def test_queue_hides_twin_of_recently_applied_offer(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    profile = ProfileData(
        target_role="Développeur backend",
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)],
    )
    _apply(engine, "Atelier Tech", "Développeur PHP", date.today().isoformat())
    for url, company in (("https://x.test/1", "Atelier Tech"), ("https://x.test/2", "Autre SA")):
        create_offer(
            engine,
            JobOfferData(
                title="Développeur PHP H/F",
                company=company,
                location="Nice",
                url=url,
                description="PHP.",
            ),
        )

    queue = build_send_queue(engine, profile, priority_areas=[])

    assert [item.offer.company for item in queue] == ["Autre SA"]


def test_cycle_skips_offer_already_applied_to_this_week(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _apply(engine, "Cabinet Ekinox", "Développeur Symfony", date.today().isoformat())
    config = WatcherConfig(
        targets=[MonitoredTarget(platform="greenhouse", target="x")],
        enable_adzuna=False,
        min_match_percentage=0,
        auto_prepare_dossier=False,
    )
    listing = SourceListing(
        source_id="9",
        source_name="Jooble",
        title="Développeur Symfony H/F",
        company="Cabinet Ekinox",
        location="Paris",
        original_url=AnyHttpUrl("https://jooble.example/offre/9"),
        description="Développeur PHP Symfony, API REST et SQL.",
    )
    profile = ProfileData(
        target_role="Développeur Symfony",
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)],
    )

    with patch("src.services.job_watcher.fetch_greenhouse_listings", return_value=[listing]):
        result = run_watcher_cycle(engine, profile, config)

    assert result.recently_applied_skipped == 1
    assert result.new_offers_imported == 0
    assert list_offers(engine) == []
