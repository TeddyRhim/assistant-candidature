from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import AnyHttpUrl

from src.db import create_database_engine, initialize_database
from src.models import JobOfferData, ProfileData, SkillRating
from src.services.job_offers import (
    DuplicateOfferURL,
    create_offer,
    find_offer_by_url,
    list_offers,
    update_offer,
)
from src.services.job_sources.base import SourceListing
from src.services.job_watcher import MonitoredTarget, WatcherConfig, run_watcher_cycle
from src.services.urls import clean_tracking, normalize_url

BASE = "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/179504396W"


@pytest.mark.parametrize(
    "variant",
    [
        BASE,
        BASE + "/",
        BASE + "?xtor=&utm_campaign=google_jobs_apply&utm_source=x&utm_medium=organic",
        BASE + "#postuler",
        BASE.replace("https://www.", "http://"),
        BASE.replace("apec.fr", "APEC.FR"),
        "  " + BASE + "  ",
    ],
)
def test_variants_of_the_same_link_normalize_identically(variant: str) -> None:
    assert normalize_url(variant) == normalize_url(BASE)


def test_meaningful_parameters_are_kept_and_order_does_not_matter() -> None:
    assert normalize_url("https://x.test/o?id=7&lang=fr") == normalize_url(
        "https://x.test/o?lang=fr&id=7&utm_source=a"
    )
    assert normalize_url("https://x.test/o?id=7") != normalize_url("https://x.test/o?id=8")
    assert normalize_url("https://x.test/a") != normalize_url("https://x.test/b")


def test_adzuna_links_are_identified_by_their_path_only() -> None:
    first = "https://www.adzuna.fr/details/5012345?se=abc&v=111"
    second = "https://www.adzuna.fr/details/5012345?se=zzz&utm_source=x"

    assert normalize_url(first) == normalize_url(second)
    assert normalize_url(first) != normalize_url("https://www.adzuna.fr/details/5012346")


def test_clean_tracking_keeps_a_readable_link() -> None:
    assert clean_tracking(BASE + "?xtor=&utm_source=a#x") == BASE
    assert clean_tracking("https://x.test/o?id=7&utm_source=a") == "https://x.test/o?id=7"


def _offer(url: str, title: str = "Développeur PHP") -> JobOfferData:
    return JobOfferData(title=title, url=url, source="Test", description="PHP et Symfony.")


def _engine(tmp_path: Path):
    engine = create_database_engine(tmp_path / "urls.sqlite3")
    initialize_database(engine)
    return engine


def test_the_same_link_cannot_be_saved_twice_whatever_its_tracking(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    create_offer(engine, _offer(BASE + "?utm_source=google_jobs_apply", "Dev APEC"))

    for variant in (BASE, BASE + "/", BASE.replace("https://www.", "http://") + "?xtor=1"):
        with pytest.raises(DuplicateOfferURL) as caught:
            create_offer(engine, _offer(variant))
        assert "Dev APEC" in str(caught.value)
    assert len(list_offers(engine)) == 1


def test_a_different_link_is_accepted_and_an_offer_may_keep_its_own_link(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    first = create_offer(engine, _offer("https://x.test/a"))
    second = create_offer(engine, _offer("https://x.test/b"))

    update_offer(engine, first.id, _offer("https://x.test/a?utm_source=z", "Retitré"))
    with pytest.raises(DuplicateOfferURL):
        update_offer(engine, second.id, _offer("https://x.test/a/"))


def test_find_offer_by_url_ignores_tracking_and_blank_input(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    created = create_offer(engine, _offer(BASE))

    assert find_offer_by_url(engine, BASE + "?utm_source=x").id == created.id
    assert find_offer_by_url(engine, "https://autre.test/o") is None
    assert find_offer_by_url(engine, "   ") is None


def test_watcher_skips_listing_whose_link_only_differs_by_tracking(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    create_offer(engine, _offer("https://jooble.test/offre/9?utm_source=a", "Déjà là"))
    config = WatcherConfig(
        targets=[MonitoredTarget(platform="greenhouse", target="x")],
        enable_adzuna=False,
        min_match_percentage=0,
        auto_prepare_dossier=False,
    )
    listing = SourceListing(
        source_id="9",
        source_name="Jooble",
        title="Titre totalement différent",
        company="Autre Société",
        location="Lyon",
        original_url=AnyHttpUrl("https://jooble.test/offre/9/?utm_source=b"),
        description="Développeur PHP Symfony, API REST et SQL.",
    )
    profile = ProfileData(
        target_role="Développeur",
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)],
    )

    with patch("src.services.job_watcher.fetch_greenhouse_listings", return_value=[listing]):
        result = run_watcher_cycle(engine, profile, config)

    assert result.duplicates_skipped == 1
    assert result.new_offers_imported == 0
