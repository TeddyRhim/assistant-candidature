from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import AnyHttpUrl
from sqlalchemy.orm import Session

from src.db import create_database_engine, initialize_database
from src.models import ProfileData, ResumeVersion, SkillRating
from src.services.job_offers import list_offers
from src.services.job_sources.adzuna import ProfileSearchResult
from src.services.job_sources.base import JobSourceError, SourceListing
from src.services.job_watcher import (
    MonitoredTarget,
    PeriodicJobWatcher,
    WatcherConfig,
    find_excluded_keyword,
    load_watcher_config,
    run_watcher_cycle,
    save_watcher_config,
)


def _sample_profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur Symfony",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
            SkillRating(name="API REST", category="Forte", level_min=8, level_max=8),
            SkillRating(name="SQL", category="Forte", level_min=7, level_max=7),
            SkillRating(name="Docker", category="Intermédiaire", level_min=5, level_max=5),
        ],
    )


def test_watcher_config_save_and_load(tmp_path: Path) -> None:
    config_file = tmp_path / "watcher_config.json"
    with patch("src.services.job_watcher.get_watcher_config_path", return_value=config_file):
        default_cfg = load_watcher_config()
        assert default_cfg.targets == []
        assert default_cfg.min_match_percentage == 50
        assert "stage" in default_cfg.excluded_title_keywords

        custom_cfg = WatcherConfig(
            targets=[
                MonitoredTarget(
                    platform="greenhouse",
                    target="ateliertech",
                    display_name="Atelier Tech",
                ),
                MonitoredTarget(platform="lever", target="exampleco", is_eu=True),
            ],
            enable_adzuna=False,
            min_match_percentage=50,
        )
        save_watcher_config(custom_cfg)
        loaded = load_watcher_config()
        assert len(loaded.targets) == 2
        assert loaded.targets[0].platform == "greenhouse"
        assert loaded.targets[0].target == "ateliertech"
        assert loaded.targets[1].is_eu is True
        assert loaded.enable_adzuna is False
        assert loaded.min_match_percentage == 50


def test_run_watcher_cycle_imports_matching_and_skips_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "test_watcher.sqlite3"
    engine = create_database_engine(db_path)
    initialize_database(engine)
    profile = _sample_profile()

    with Session(engine) as session, session.begin():
        session.add(
            ResumeVersion(
                id="resume-id",
                original_filename="cv_ref.pdf",
                stored_filename="cv_ref.pdf",
                reviewed_text="Alexandre Martin Développeur PHP Symfony API REST SQL",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )

    mock_greenhouse_listings = [
        SourceListing(
            source_id="gh-1",
            source_name="Greenhouse (Atelier Tech)",
            title="Développeur Backend PHP Symfony",
            company="Atelier Tech",
            location="Nice",
            original_url=AnyHttpUrl("https://boards.greenhouse.io/ateliertech/jobs/1"),
            description="Recherche développeur PHP Symfony, API REST et SQL pour CDI.",
        ),
        SourceListing(
            source_id="gh-2",
            source_name="Greenhouse (Atelier Tech)",
            title="Data Scientist R & Deep Learning",
            company="Atelier Tech",
            location="Paris",
            original_url=AnyHttpUrl("https://boards.greenhouse.io/ateliertech/jobs/2"),
            description="Recherche spécialiste PyTorch et R.",
        ),
    ]

    mock_lever_listings = [
        SourceListing(
            source_id="lev-1",
            source_name="Lever (ExampleCo)",
            title="Senior Symfony Engineer",
            company="ExampleCo",
            location="Télétravail",
            original_url=AnyHttpUrl("https://jobs.lever.co/exampleco/lev-1"),
            description="Mission sur API REST, Doctrine ORM et Docker avec Symfony.",
            public_contact_email="jobs@exampleco.com",
            contact_source_url=AnyHttpUrl("https://jobs.lever.co/exampleco/lev-1"),
        )
    ]

    config = WatcherConfig(
        targets=[
            MonitoredTarget(platform="greenhouse", target="ateliertech"),
            MonitoredTarget(platform="lever", target="exampleco"),
        ],
        enable_adzuna=False,
        min_match_percentage=40,
        auto_import=True,
    )

    with (
        patch(
            "src.services.job_watcher.fetch_greenhouse_listings",
            return_value=mock_greenhouse_listings,
        ),
        patch(
            "src.services.job_watcher.fetch_lever_listings",
            return_value=mock_lever_listings,
        ),
    ):
        result = run_watcher_cycle(engine, profile, config)

    assert result.targets_scanned == 2
    assert result.total_listings_found == 3
    # gh-1 et lev-1 matchent PHP/Symfony, gh-2 a un score faible
    assert result.new_offers_imported == 2
    assert result.dossiers_prepared == 2
    assert result.low_match_skipped == 1
    assert result.duplicates_skipped == 0
    assert len(result.imported_offer_titles) == 2

    # Vérification en base de données
    offers = list_offers(engine)
    assert len(offers) == 2
    titles = [o.title for o in offers]
    assert "Développeur Backend PHP Symfony" in titles
    assert "Senior Symfony Engineer" in titles

    # Deuxième cycle avec les mêmes données : toutes doivent être détectées en doublon sans réimport
    with (
        patch(
            "src.services.job_watcher.fetch_greenhouse_listings",
            return_value=mock_greenhouse_listings,
        ),
        patch(
            "src.services.job_watcher.fetch_lever_listings",
            return_value=mock_lever_listings,
        ),
    ):
        second_result = run_watcher_cycle(engine, profile, config)

    assert second_result.new_offers_imported == 0
    assert second_result.duplicates_skipped == 2
    assert second_result.low_match_skipped == 1


def test_default_exclusions_filter_lead_positions() -> None:
    from src.services.job_watcher import DEFAULT_EXCLUDED_TITLE_KEYWORDS

    keywords = list(DEFAULT_EXCLUDED_TITLE_KEYWORDS)
    assert find_excluded_keyword("Lead Dev SYMFONY Senior (IT)", keywords) == "lead"
    assert find_excluded_keyword("Architecte / Lead PHP - CDI", keywords) == "lead"
    assert find_excluded_keyword("Développeur PHP Symfony", keywords) is None
    assert find_excluded_keyword("Développeur Leadership Tools", keywords) is None


def test_default_exclusions_filter_senior_positions() -> None:
    from src.services.job_watcher import DEFAULT_EXCLUDED_TITLE_KEYWORDS

    keywords = list(DEFAULT_EXCLUDED_TITLE_KEYWORDS)
    assert find_excluded_keyword("Développeur Symfony Senior", keywords) == "senior"
    assert find_excluded_keyword("Ingénieur Python Sénior", keywords) == "sénior"
    assert find_excluded_keyword("Développeur PHP confirmé(e) F/H", keywords) == "confirmé"
    assert find_excluded_keyword("Expert PrestaShop", keywords) == "expert"
    assert find_excluded_keyword("Développeur PHP Symfony junior", keywords) is None
    assert find_excluded_keyword("Développeur PHP H/F", keywords) is None


def test_find_excluded_keyword_matches_whole_words_only() -> None:
    keywords = ["stage", "java", "freelance"]

    assert find_excluded_keyword("Stage - Ingénieur Développement", keywords) == "stage"
    assert find_excluded_keyword("Développeur Java/Angular F/H", keywords) == "java"
    assert find_excluded_keyword("Développeur JavaScript", keywords) is None
    assert find_excluded_keyword("Développeur PHP Symfony", keywords) is None
    assert find_excluded_keyword("Développeur PHP", [" ", ""]) is None


def test_cycle_skips_excluded_titles_and_keeps_score_out_of_description(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "test_excluded.sqlite3")
    initialize_database(engine)
    profile = _sample_profile()

    listings = [
        SourceListing(
            source_id="ok",
            source_name="Greenhouse (Atelier Tech)",
            title="Développeur Symfony",
            company="Atelier Tech",
            location="Nice",
            original_url=AnyHttpUrl("https://boards.greenhouse.io/ateliertech/jobs/10"),
            description="PHP Symfony API REST SQL.",
            public_contact_email="jobs@ateliertech.example",
            contact_source_url=AnyHttpUrl("https://boards.greenhouse.io/ateliertech/jobs/10"),
        ),
        SourceListing(
            source_id="stage",
            source_name="Greenhouse (Atelier Tech)",
            title="Stage Développeur Symfony",
            company="Atelier Tech",
            location="Nice",
            original_url=AnyHttpUrl("https://boards.greenhouse.io/ateliertech/jobs/11"),
            description="PHP Symfony API REST SQL.",
        ),
    ]
    config = WatcherConfig(
        targets=[MonitoredTarget(platform="greenhouse", target="ateliertech")],
        enable_adzuna=False,
        min_match_percentage=40,
        auto_prepare_dossier=False,
    )

    with patch("src.services.job_watcher.fetch_greenhouse_listings", return_value=listings):
        result = run_watcher_cycle(engine, profile, config)

    assert result.excluded_skipped == 1
    assert result.new_offers_imported == 1
    [offer] = list_offers(engine)
    assert offer.title == "Développeur Symfony"
    assert "Score de pertinence" not in offer.description
    assert "Contact public : jobs@ateliertech.example" in offer.description


FT_SECRETS = {"FRANCE_TRAVAIL_CLIENT_ID": "id", "FRANCE_TRAVAIL_CLIENT_SECRET": "secret"}


def _france_travail_listing() -> SourceListing:
    return SourceListing(
        source_id="francetravail-1",
        source_name="France Travail",
        title="Développeur PHP Symfony",
        company="Atelier Exemple",
        location="Nice (06)",
        contract_type="CDI",
        original_url=AnyHttpUrl("https://candidat.francetravail.fr/offres/recherche/detail/1"),
        description="PHP Symfony API REST SQL.",
    )


def _ft_cycle_config(**overrides: object) -> WatcherConfig:
    return WatcherConfig(
        enable_adzuna=False,
        min_match_percentage=40,
        auto_prepare_dossier=False,
        **overrides,
    )


def test_cycle_imports_france_travail_offers_with_cdi_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_SECRET", raising=False)
    engine = create_database_engine(tmp_path / "ft.sqlite3")
    initialize_database(engine)
    search_result = ProfileSearchResult(
        listings=(_france_travail_listing(),),
        failed_targets=(("Var — « PHP »", "HTTP 500"),),
        searched_targets=("Alpes-Maritimes",),
        search_terms=("PHP",),
    )

    with patch(
        "src.services.job_watcher.search_france_travail_for_profile",
        return_value=search_result,
    ) as search:
        result = run_watcher_cycle(
            engine, _sample_profile(), _ft_cycle_config(), secrets=FT_SECRETS
        )

    assert result.france_travail_scanned is True
    assert result.new_offers_imported == 1
    assert any("France Travail (Var" in error for error in result.errors)
    assert search.call_args.kwargs["contract_codes"] == ("CDI",)
    [offer] = list_offers(engine)
    assert offer.source == "France Travail"
    assert offer.url == "https://candidat.francetravail.fr/offres/recherche/detail/1"


def test_cycle_survives_france_travail_failure_and_missing_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("FRANCE_TRAVAIL_CLIENT_SECRET", raising=False)
    engine = create_database_engine(tmp_path / "ft2.sqlite3")
    initialize_database(engine)

    with patch("src.services.job_watcher.search_france_travail_for_profile") as search:
        no_credentials = run_watcher_cycle(engine, _sample_profile(), _ft_cycle_config())
        assert no_credentials.france_travail_scanned is False
        search.assert_not_called()

    with patch(
        "src.services.job_watcher.search_france_travail_for_profile",
        side_effect=JobSourceError("authentification refusée"),
    ):
        failed = run_watcher_cycle(
            engine,
            _sample_profile(),
            _ft_cycle_config(france_travail_cdi_only=False),
            secrets=FT_SECRETS,
        )
    assert failed.france_travail_scanned is True
    assert failed.new_offers_imported == 0
    assert failed.errors == ["France Travail: authentification refusée"]

    disabled = run_watcher_cycle(
        engine,
        _sample_profile(),
        _ft_cycle_config(enable_france_travail=False),
        secrets=FT_SECRETS,
    )
    assert disabled.france_travail_scanned is False


def test_periodic_watcher_thread_start_and_stop(tmp_path: Path) -> None:
    db_path = tmp_path / "test_periodic.sqlite3"
    engine = create_database_engine(db_path)
    initialize_database(engine)
    profile = _sample_profile()

    watcher = PeriodicJobWatcher()
    assert not watcher.is_active

    with (
        patch("src.services.job_watcher.fetch_greenhouse_listings", return_value=[]),
        patch("src.services.job_watcher.fetch_lever_listings", return_value=[]),
    ):
        config = WatcherConfig(targets=[], enable_adzuna=False, interval_seconds=300)
        started = watcher.start(engine, profile, config)
        assert started is True
        assert watcher.is_active

        # Deuxième start immédiat doit retourner False
        assert watcher.start(engine, profile, config) is False

        stopped = watcher.stop()
        assert stopped is True
        assert not watcher.is_active


def _adzuna_listing(ad_id: str, tracking: str, title: str = "Développeur Symfony") -> SourceListing:
    return SourceListing(
        source_id=ad_id,
        source_name="Adzuna",
        title=title,
        company="Cabinet Ekinox",
        location="Paris",
        original_url=AnyHttpUrl(f"https://www.adzuna.fr/details/{ad_id}?se={tracking}"),
        description="Développeur PHP Symfony, API REST et SQL.",
    )


def test_cycle_skips_same_offer_republished_under_another_url(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    config = WatcherConfig(
        targets=[MonitoredTarget(platform="greenhouse", target="x")],
        enable_adzuna=False,
        min_match_percentage=0,
        auto_prepare_dossier=False,
    )
    listings = [_adzuna_listing("1", "a"), _adzuna_listing("2", "b")]

    with patch("src.services.job_watcher.fetch_greenhouse_listings", return_value=listings):
        first = run_watcher_cycle(engine, _sample_profile(), config)
        second = run_watcher_cycle(engine, _sample_profile(), config)

    assert first.new_offers_imported == 1
    assert first.duplicates_skipped == 1
    assert second.new_offers_imported == 0
    assert len(list_offers(engine)) == 1


def test_adzuna_cycle_goes_zone_by_zone_and_resumes_after_rate_limit(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ASSISTANT_CANDIDATURES_DATA_DIR", str(tmp_path))
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    config = WatcherConfig(
        enable_adzuna=True,
        enable_france_travail=False,
        min_match_percentage=0,
        auto_prepare_dossier=False,
    )
    secrets = {"ADZUNA_APP_ID": "id", "ADZUNA_APP_KEY": "key"}
    visited: list[str] = []

    def limited_on_third_zone(_profile, _country, location, _id, _key):
        visited.append(location)
        return ProfileSearchResult(
            listings=(),
            failed_targets=(),
            searched_targets=(location,),
            search_terms=(),
            rate_limited=len(visited) == 3,
        )

    with patch(
        "src.services.job_watcher.search_adzuna_throttled", side_effect=limited_on_third_zone
    ):
        first = run_watcher_cycle(engine, _sample_profile(), config, secrets)
        assert len(visited) == 3
        assert any("reprendra" in error for error in first.errors)

        visited.clear()
        run_watcher_cycle(engine, _sample_profile(), config, secrets)

    # La seconde récupération reprend à la zone interrompue (3e), pas au début.
    assert visited[0] == "Mougins"
    assert "Nice" not in visited
