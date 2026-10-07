from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.db import create_database_engine, initialize_database
from src.models import ProfileData, SkillRating
from src.services.job_sources import jooble
from src.services.job_sources.base import JobSourceError
from src.services.job_sources.jooble import (
    JoobleResult,
    JoobleUsage,
    load_usage,
    map_jooble_job,
    record_request,
    reset_usage,
    save_usage,
    search_jooble,
)
from src.services.job_sources.remote_common import RemoteBoardRateLimited
from src.services.job_watcher import WatcherConfig, run_watcher_cycle

SECRET = "cle-secrete-de-test"


def _job(**overrides: object) -> dict[str, object]:
    job: dict[str, object] = {
        "id": "42",
        "title": "Développeur <b>PHP</b> Symfony H/F",
        "company": "Exemple SAS",
        "location": "Nice",
        "snippet": "&nbsp;...maîtrise de <b>Symfony</b> et API REST...",
        "salary": "35k €/an",
        "source": "hellowork.com",
        "type": "Temps plein",
        "link": "https://fr.jooble.org/desc/42",
    }
    job.update(overrides)
    return job


def _post_returning(jobs: list[dict[str, object]]):
    calls: list[dict[str, object]] = []

    def post(url: str, body: dict[str, object], **_kwargs: object):
        calls.append({"url": url, **body})
        return {"totalCount": len(jobs), "jobs": jobs}

    post.calls = calls  # type: ignore[attr-defined]
    return post


def test_job_is_mapped_with_clean_text_and_origin_site() -> None:
    listing = map_jooble_job(_job())

    assert listing is not None
    assert listing.title == "Développeur PHP Symfony H/F"
    assert listing.source_name == "Jooble (hellowork.com)"
    assert listing.company == "Exemple SAS"
    assert listing.location == "Nice"
    assert "<" not in listing.description
    assert "Salaire indiqué : 35k €/an." in listing.description
    assert str(listing.original_url) == "https://fr.jooble.org/desc/42"
    assert listing.contract_type is None


def test_contract_types_are_detected_from_the_type_field() -> None:
    def contract(raw: str):
        listing = map_jooble_job(_job(type=raw))
        assert listing is not None
        return listing.contract_type

    assert contract("Stage") == "Stage"
    assert contract("Alternance") == "Alternance"
    assert contract("Freelance") == "Freelance"
    assert contract("CDD") == "CDD"
    assert contract("Temps plein") is None
    assert map_jooble_job({"title": "Sans lien"}) is None
    assert map_jooble_job("pas un objet") is None


def test_every_request_is_counted_and_capped_per_run(tmp_path: Path) -> None:
    post = _post_returning([_job()])

    result = search_jooble(
        SECRET,
        ["PHP", "Symfony", "SQL"],
        ["Nice", "Toulon", "Cannes"],
        max_requests=4,
        post=post,
        sleep=lambda _s: None,
    )

    assert result.requests_made == 4
    assert len(post.calls) == 4
    assert load_usage().used == 4  # compteur persisté
    # Technologie par technologie : « PHP » est cherché dans toutes les zones d'abord.
    assert [(c["keywords"], c["location"]) for c in post.calls][:3] == [
        ("PHP", "Nice"),
        ("PHP", "Toulon"),
        ("PHP", "Cannes"),
    ]
    assert len(result.listings) == 1  # la même offre n'est gardée qu'une fois


def test_search_stops_when_the_quota_is_nearly_exhausted() -> None:
    save_usage(JoobleUsage(used=470, limit=500, reserve=25))  # il reste 5 requêtes utilisables
    post = _post_returning([])

    result = search_jooble(
        SECRET, ["PHP", "Symfony"], ["Nice", "Toulon", "Cannes"], max_requests=50,
        post=post, sleep=lambda _s: None,
    )

    assert result.requests_made == 5
    assert result.budget_exhausted is True
    assert load_usage().used == 475
    assert any("quota" in error for error in result.errors)
    # Quota épuisé : plus aucune requête.
    again = search_jooble(
        SECRET, ["PHP"], ["Nice"], max_requests=50, post=post, sleep=lambda _s: None
    )
    assert again.requests_made == 0 and len(post.calls) == 5


def test_rate_limit_stops_the_run_and_the_key_never_appears_in_errors() -> None:
    calls = {"count": 0}

    def post(url: str, body: dict[str, object], **_kwargs: object):
        calls["count"] += 1
        if calls["count"] == 1:
            raise JobSourceError("Jooble a refusé la requête (HTTP 500).")
        raise RemoteBoardRateLimited("Jooble : limite de requêtes atteinte (HTTP 429).")

    result = search_jooble(
        SECRET, ["PHP"], ["Nice", "Toulon", "Cannes"], post=post, sleep=lambda _s: None
    )

    assert result.rate_limited is True
    assert result.requests_made == 2  # arrêt dès le 429
    assert all(SECRET not in error for error in result.errors)


def test_post_json_errors_never_contain_the_key() -> None:
    from urllib.error import HTTPError

    def boom(*_args: object, **_kwargs: object):
        raise HTTPError(f"https://fr.jooble.org/api/{SECRET}", 403, "Forbidden", {}, None)

    with patch.object(jooble, "urlopen", side_effect=boom):
        try:
            jooble.post_json(f"https://fr.jooble.org/api/{SECRET}", {"keywords": "php"})
        except JobSourceError as error:
            assert SECRET not in str(error)
            assert "403" in str(error)
        else:
            raise AssertionError("une erreur était attendue")


def test_empty_key_makes_no_request() -> None:
    post = _post_returning([_job()])

    result = search_jooble("  ", ["PHP"], ["Nice"], post=post)

    assert result.requests_made == 0 and post.calls == []


def test_usage_counter_roundtrip_and_reset() -> None:
    assert load_usage().used == 0
    record_request()
    record_request()
    usage = load_usage()
    assert (usage.used, usage.limit, usage.reserve) == (2, 500, 25)
    assert usage.remaining == 473
    reset_usage()
    assert load_usage().used == 0


def test_cycle_uses_profile_zone_and_the_per_run_cap(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    profile = ProfileData(
        local_locations=["Ville A", "Ville B"],
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ],
    )
    config = WatcherConfig(
        enable_adzuna=False,
        enable_france_travail=False,
        enable_himalayas=False,
        enable_remoteok=False,
        jooble_max_requests_per_run=3,
        min_match_percentage=0,
        auto_prepare_dossier=False,
        excluded_title_keywords=[],
    )
    listing = map_jooble_job(_job())
    assert listing is not None
    seen: dict[str, object] = {}

    def fake_search(key, terms, zones, *, max_requests):
        seen.update(key=key, terms=list(terms), zones=list(zones), max_requests=max_requests)
        return JoobleResult(listings=[listing], requests_made=1)

    with patch("src.services.job_watcher.search_jooble", side_effect=fake_search):
        result = run_watcher_cycle(engine, profile, config, {"JOOBLE_API_KEY": SECRET})

    assert seen["zones"] == ["Ville A", "Ville B"]
    assert seen["terms"] == ["PHP", "Symfony"]
    assert seen["max_requests"] == 3
    assert result.new_offers_imported == 1


def test_cycle_skips_jooble_without_key_or_when_disabled(tmp_path: Path) -> None:
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    profile = ProfileData(
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)]
    )
    base = {
        "enable_adzuna": False,
        "enable_france_travail": False,
        "enable_himalayas": False,
        "enable_remoteok": False,
    }

    with patch("src.services.job_watcher.search_jooble") as search:
        run_watcher_cycle(engine, profile, WatcherConfig(**base), {})  # pas de clé
        run_watcher_cycle(
            engine,
            profile,
            WatcherConfig(**base, enable_jooble=False),
            {"JOOBLE_API_KEY": SECRET},
        )

    search.assert_not_called()
