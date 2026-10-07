from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.db import create_database_engine, initialize_database
from src.models import ProfileData, SkillRating
from src.services.job_sources.base import JobSourceError
from src.services.job_sources.himalayas import map_himalayas_job, search_himalayas
from src.services.job_sources.remote_common import (
    RemoteBoardRateLimited,
    RemoteBoardResult,
    is_open_to_france,
    remote_search_terms,
)
from src.services.job_sources.remoteok import map_remoteok_job, search_remoteok
from src.services.job_watcher import WatcherConfig, run_watcher_cycle


def _himalayas_job(**overrides: object) -> dict[str, object]:
    job: dict[str, object] = {
        "title": "Backend PHP Developer",
        "companyName": "Exemple SAS",
        "employmentType": "Full Time",
        "seniority": ["Mid-level"],
        "locationRestrictions": ["France", "Germany"],
        "description": "<p>Build APIs with <strong>PHP</strong> and Symfony.</p>",
        "applicationLink": "https://himalayas.app/companies/exemple/jobs/backend-php",
        "guid": "https://himalayas.app/companies/exemple/jobs/backend-php",
    }
    job.update(overrides)
    return job


def _remoteok_job(**overrides: object) -> dict[str, object]:
    job: dict[str, object] = {
        "id": "1001",
        "position": "PHP Engineer",
        "company": "Exemple Inc",
        "location": "Worldwide",
        "tags": ["php", "symfony"],
        "description": "<p>Work on <em>APIs</em>.</p>",
        "url": "https://remoteOK.com/remote-jobs/php-engineer-1001",
    }
    job.update(overrides)
    return job


def test_himalayas_job_is_mapped_as_full_remote_with_clean_text() -> None:
    listing = map_himalayas_job(_himalayas_job())

    assert listing is not None
    assert listing.source_name == "Himalayas"
    assert listing.location == "Full remote"
    assert listing.company == "Exemple SAS"
    assert listing.contract_type is None
    assert "Build APIs with PHP and Symfony." in listing.description
    assert "Niveau indiqué : Mid-level." in listing.description
    assert "<" not in listing.description
    assert str(listing.original_url).startswith("https://himalayas.app/")


def test_himalayas_skips_jobs_closed_to_france_and_maps_contracts() -> None:
    assert map_himalayas_job(_himalayas_job(locationRestrictions=["United States"])) is None
    assert map_himalayas_job(_himalayas_job(locationRestrictions=[])) is not None
    contractor = map_himalayas_job(_himalayas_job(employmentType="Contractor"))
    intern = map_himalayas_job(_himalayas_job(employmentType="Intern"))
    assert contractor is not None and contractor.contract_type == "Freelance"
    assert intern is not None and intern.contract_type == "Stage"
    assert map_himalayas_job("pas un objet") is None
    assert map_himalayas_job({"title": "Sans lien"}) is None


def test_himalayas_search_queries_each_term_and_deduplicates() -> None:
    urls: list[str] = []

    def fetch(url: str, *, source: str):
        urls.append(url)
        return {"jobs": [_himalayas_job()]} if "page=1" in url else {"jobs": []}

    result = search_himalayas(["PHP"], fetch=fetch, sleep=lambda _s: None)

    assert len(result.listings) == 1  # la même offre ressort par plusieurs requêtes
    assert any("country=FR" in url for url in urls)
    assert any("worldwide=true" in url for url in urls)
    assert all("q=PHP" in url for url in urls)


def test_himalayas_search_stops_on_rate_limit_and_keeps_what_it_read() -> None:
    calls = {"count": 0}

    def fetch(url: str, *, source: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"jobs": [_himalayas_job()]}
        raise RemoteBoardRateLimited("429")

    result = search_himalayas(["PHP", "Symfony"], fetch=fetch, sleep=lambda _s: None)

    assert result.rate_limited is True
    assert len(result.listings) == 1
    assert calls["count"] == 2


def test_himalayas_search_reports_a_failing_request_and_continues() -> None:
    def fetch(url: str, *, source: str):
        raise JobSourceError("HTTP 500")

    result = search_himalayas(["PHP"], fetch=fetch, sleep=lambda _s: None)

    assert result.listings == []
    assert result.rate_limited is False
    assert any("PHP" in error for error in result.errors)


def test_remoteok_ignores_legal_notice_and_foreign_only_jobs() -> None:
    assert map_remoteok_job({"legal": "Please link back"}) is None
    assert map_remoteok_job(_remoteok_job(location="United States")) is None
    assert map_remoteok_job(_remoteok_job(location="")) is not None
    assert map_remoteok_job(_remoteok_job(location="Europe")) is not None

    listing = map_remoteok_job(_remoteok_job())
    assert listing is not None
    assert listing.source_name == "Remote OK"
    assert listing.location == "Full remote"
    assert "Technologies : php, symfony." in listing.description
    # Lien vers la page Remote OK, comme l'exigent leurs conditions.
    assert str(listing.original_url).lower().startswith("https://remoteok.com/")


def test_remoteok_search_uses_one_tag_per_request() -> None:
    urls: list[str] = []

    def fetch(url: str, *, source: str):
        urls.append(url)
        return [{"legal": "notice"}, _remoteok_job()]

    result = search_remoteok(["PHP", "Symfony"], fetch=fetch, sleep=lambda _s: None)

    assert [url.rsplit("=", 1)[-1] for url in urls] == ["php", "symfony"]
    assert len(result.listings) == 1


def test_remote_search_terms_keep_short_top_skills() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Debug / maintenance", category="Forte", level_min=9, level_max=9),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
            SkillRating(
                name="Une compétence au nom beaucoup trop long",
                category="Forte",
                level_min=7,
                level_max=7,
            ),
            SkillRating(name="SQL", category="Forte", level_min=7, level_max=7),
        ]
    )

    assert remote_search_terms(profile, limit=3) == ["PHP", "Symfony", "SQL"]


def test_open_location_detection() -> None:
    assert is_open_to_france("")
    assert is_open_to_france("Worldwide")
    assert is_open_to_france("Europe / EMEA")
    assert not is_open_to_france("United States")
    assert not is_open_to_france("Canada only")


def test_cycle_imports_remote_offers_and_drops_freelance_contracts(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    profile = ProfileData(
        target_role="Développeur backend PHP",
        local_locations=["Ville Exemple"],
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ],
    )
    permanent = map_himalayas_job(_himalayas_job())
    contractor = map_himalayas_job(
        _himalayas_job(
            title="PHP Freelance API",
            employmentType="Contractor",
            guid="https://himalayas.app/companies/x/jobs/contractor",
            applicationLink="https://himalayas.app/companies/x/jobs/contractor",
        )
    )
    assert permanent is not None and contractor is not None
    config = WatcherConfig(
        enable_adzuna=False,
        enable_france_travail=False,
        enable_remoteok=False,
        min_match_percentage=0,
        auto_prepare_dossier=False,
        excluded_title_keywords=[],
    )

    with patch(
        "src.services.job_watcher.search_himalayas",
        return_value=RemoteBoardResult(listings=[permanent, contractor], errors=["Himalayas : x"]),
    ):
        result = run_watcher_cycle(engine, profile, config)

    assert result.new_offers_imported == 1
    assert result.excluded_skipped == 1  # le contrat « Freelance » est écarté
    assert "Himalayas : x" in result.errors


def test_cycle_can_disable_remote_boards(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    profile = ProfileData(
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)]
    )
    config = WatcherConfig(
        enable_adzuna=False,
        enable_france_travail=False,
        enable_himalayas=False,
        enable_remoteok=False,
    )

    with (
        patch("src.services.job_watcher.search_himalayas") as himalayas,
        patch("src.services.job_watcher.search_remoteok") as remoteok,
    ):
        run_watcher_cycle(engine, profile, config)

    himalayas.assert_not_called()
    remoteok.assert_not_called()


def test_php_currency_amounts_are_not_the_language() -> None:
    from src.models import JobOfferData
    from src.services.matching import assess_offer_fit, find_skill_evidence

    assert find_skill_evidence("Salary: 1,000 PHP De Minimis", "PHP") is None
    assert find_skill_evidence("Salary PHP 50,000 per month", "PHP") is None
    assert find_skill_evidence("Offer: 25k PHP monthly", "PHP") is None
    # Le langage reste reconnu, y compris avec une version.
    assert find_skill_evidence("Strong PHP 8 experience", "PHP") is not None
    assert find_skill_evidence("PHP 7/8 and Symfony", "PHP") is not None

    profile = ProfileData(
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)]
    )
    offer = JobOfferData(
        title="HR Administrator",
        company="Exemple",
        location="Full remote",
        url=None,
        source="Himalayas",
        description="Full remote.\nSalary 1,000 PHP De Minimis allowance and HR duties.",
    )
    assert assess_offer_fit(offer, profile).skills.requirements_percentage is None


def test_title_must_cite_a_profile_technology() -> None:
    from src.services.matching import title_mentions_profile_technology

    profile = ProfileData(
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)]
    )
    assert title_mentions_profile_technology("Senior Backend Developer PHP/Symfony", profile)
    assert not title_mentions_profile_technology("HR Administrator", profile)
    assert not title_mentions_profile_technology("Backend Developer", profile)
    assert not title_mentions_profile_technology("Rust Engineer", profile)


def test_cycle_drops_remote_offers_whose_title_has_no_profile_technology(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ],
    )
    relevant = map_himalayas_job(_himalayas_job(title="Backend PHP Developer"))
    noise = map_himalayas_job(
        _himalayas_job(
            title="Executive Assistant",
            description="<p>PHP and Symfony everywhere.</p>",
            guid="https://himalayas.app/companies/x/jobs/assistant",
            applicationLink="https://himalayas.app/companies/x/jobs/assistant",
        )
    )
    assert relevant is not None and noise is not None
    config = WatcherConfig(
        enable_adzuna=False,
        enable_france_travail=False,
        enable_remoteok=False,
        min_match_percentage=0,
        auto_prepare_dossier=False,
        excluded_title_keywords=[],
    )

    with patch(
        "src.services.job_watcher.search_himalayas",
        return_value=RemoteBoardResult(listings=[relevant, noise]),
    ):
        result = run_watcher_cycle(engine, profile, config)

    assert result.new_offers_imported == 1
    assert result.excluded_skipped == 1
