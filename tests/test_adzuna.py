from __future__ import annotations

import json
import time
from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from src.models import ProfileData, SkillRating
from src.services.job_sources.adzuna import (
    ADZUNA_MARKETS,
    LOCAL_SEARCH_LOCATIONS,
    JobSourceError,
    ProfileSearchResult,
    get_profile_search_status,
    is_french_or_english,
    is_paris_listing,
    listing_title_with_location,
    ordered_profile_terms,
    profile_search_keywords,
    rank_adzuna_results,
    search_adzuna,
    search_adzuna_for_profile,
    start_profile_search,
)
from src.services.job_sources.base import JobSearchQuery, SourceListing


def test_search_maps_adzuna_listing_and_query_parameters() -> None:
    payload = {
        "results": [
            {
                "id": "123",
                "title": "Développeur PHP",
                "company": {"display_name": "Studio Exemple"},
                "location": {"display_name": "Nice"},
                "contract_type": "permanent",
                "redirect_url": "https://www.adzuna.fr/land/123",
                "description": "Développement Symfony et API.",
            }
        ]
    }
    response = BytesIO(json.dumps(payload).encode())
    query = JobSearchQuery(
        keywords=["PHP", "Symfony", "developer"],
        locations=["Nice"],
    )

    with patch("src.services.job_sources.adzuna.urlopen", return_value=response) as open_url:
        results = search_adzuna(query, "fr", "test-id", "test-key")

    request = open_url.call_args.args[0]
    params = parse_qs(urlparse(request.full_url).query)
    assert params["app_id"] == ["test-id"]
    assert params["app_key"] == ["test-key"]
    assert params["what"] == ["PHP Symfony developer"]
    assert params["where"] == ["Nice"]
    assert results[0].source_name == "Adzuna"
    assert results[0].source_id == "123"
    assert results[0].title == "Développeur PHP"
    assert results[0].contract_type == "CDI"
    assert results[0].country_code == "FR"
    assert results[0].relocation_signal == "not_mentioned"


def test_search_rejects_missing_credentials_without_network_call() -> None:
    with patch("src.services.job_sources.adzuna.urlopen") as open_url:
        with pytest.raises(JobSourceError, match="configurés localement"):
            search_adzuna(JobSearchQuery(), "fr", "", "")

    open_url.assert_not_called()


def test_search_rejects_invalid_country_and_page() -> None:
    with pytest.raises(JobSourceError, match="de deux lettres"):
        search_adzuna(JobSearchQuery(), "France", "id", "key")
    with pytest.raises(JobSourceError, match="page"):
        search_adzuna(JobSearchQuery(), "fr", "id", "key", page=51)


def test_search_returns_visible_error_for_invalid_payload() -> None:
    response = BytesIO(json.dumps({"unexpected": []}).encode())

    with patch("src.services.job_sources.adzuna.urlopen", return_value=response):
        with pytest.raises(JobSourceError, match="format attendu"):
            search_adzuna(JobSearchQuery(), "fr", "test-id", "test-key")


def test_search_rejects_non_object_json_payload() -> None:
    response = BytesIO(json.dumps(["unexpected"]).encode())

    with patch("src.services.job_sources.adzuna.urlopen", return_value=response):
        with pytest.raises(JobSourceError, match="format attendu"):
            search_adzuna(JobSearchQuery(), "fr", "test-id", "test-key")


def test_profile_search_uses_prioritized_terms_across_local_areas() -> None:
    profile = ProfileData(
        target_role="Développeur backend",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ],
    )
    listing = {
        "source_id": "123",
        "source_name": "Adzuna",
        "title": "Développeur PHP",
        "country_code": "FR",
        "original_url": "https://jobs.example.com/123",
    }
    with patch(
        "src.services.job_sources.adzuna.search_adzuna",
        return_value=[SourceListing.model_validate(listing)],
    ) as search:
        result = search_adzuna_for_profile(
            profile,
            [("fr", "Nice"), ("fr", "Cannes"), ("fr", "Var"), ("fr", "Paris")],
            "id",
            "key",
        )

    assert result.search_terms == ("PHP", "Symfony", "Développeur backend")
    assert result.searched_targets == (
        "Nice, France",
        "Cannes, France",
        "Var, France",
        "Paris, France",
    )
    assert len(result.listings) == 1
    assert search.call_count == 12
    assert {
        call.args[0].locations[0] for call in search.call_args_list
    } == {"Nice", "Cannes", "Var", "Paris"}
    assert {
        call.args[0].keywords[0] for call in search.call_args_list
    } == {"PHP", "Symfony", "Développeur backend"}
    assert all(len(call.args[0].keywords) == 1 for call in search.call_args_list)


def test_profile_search_reports_failed_areas_without_hiding_partial_results() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ]
    )

    def search_for_area(_query, _country, _app_id, _app_key, _page):
        if _query.locations == ["Nice"]:
            raise JobSourceError("HTTP 429")
        return []

    with patch(
        "src.services.job_sources.adzuna.search_adzuna",
        side_effect=search_for_area,
    ):
        result = search_adzuna_for_profile(
            profile,
            [("fr", "Nice"), ("fr", "Cannes")],
            "id",
            "key",
        )

    assert set(result.failed_targets) == {
        ("Nice, France — « PHP »", "HTTP 429"),
        ("Nice, France — « Symfony »", "HTTP 429"),
        ("Nice, France — « développeur »", "HTTP 429"),
    }
    assert result.searched_targets == ("Nice, France", "Cannes, France")


def test_profile_search_rejects_profile_without_role_or_skills() -> None:
    with pytest.raises(JobSourceError, match="poste visé"):
        search_adzuna_for_profile(ProfileData(), [("fr", "Nice")], "id", "key")


def test_profile_search_runs_in_background_and_returns_result() -> None:
    expected = ProfileSearchResult(
        listings=(),
        failed_targets=(),
        searched_targets=("Nice, France",),
        search_terms=("Développeur backend",),
    )
    with patch(
        "src.services.job_sources.adzuna.search_adzuna_for_profile",
        return_value=expected,
    ) as search:
        job_id = start_profile_search(
            ProfileData(target_role="Développeur backend"),
            [("fr", "Nice")],
            "id",
            "key",
        )

        deadline = time.monotonic() + 2
        status = get_profile_search_status(job_id)
        while status.is_running and time.monotonic() < deadline:
            time.sleep(0.01)
            status = get_profile_search_status(job_id)

    assert search.called
    assert not status.is_running
    assert status.result == expected


def test_profile_terms_are_ordered_by_strength_before_target_role() -> None:
    profile = ProfileData(
        target_role="Développeur backend",
        skills=[
            SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
            SkillRating(name="API REST", category="Forte", level_min=8, level_max=8),
            SkillRating(name="PHP", category="Forte", level_min=9, level_max=9),
            SkillRating(
                name="RAG",
                category="IA et nouvelles technologies",
                level_min=3,
                level_max=3,
            ),
        ],
    )

    assert ordered_profile_terms(profile) == (
        "PHP",
        "API REST",
        "Python",
        "RAG",
        "Développeur backend",
    )
    # Les langages assez maîtrisés (niveau 5 ou plus) sont cherchés en premier.
    assert profile_search_keywords(profile, "fr") == (
        "PHP",
        "Python",
        "API REST",
        "RAG",
        "Développeur backend",
    )
    assert profile_search_keywords(profile, "de") == (
        "PHP",
        "Python",
        "API REST",
        "RAG",
        "Développeur backend",
        "developer",
    )


def test_secondary_language_is_searched_even_when_strong_skills_fill_the_limit() -> None:
    profile = ProfileData(
        target_role="Développeur backend",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=9, level_max=9),
            *(
                SkillRating(name=f"Outil {index}", category="Forte", level_min=8, level_max=8)
                for index in range(9)
            ),
            SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
            SkillRating(name="Java", category="En développement", level_min=4, level_max=4),
        ],
    )

    keywords = profile_search_keywords(profile, "fr")

    assert keywords[:2] == ("PHP", "Python")
    assert "Java" not in keywords  # niveau 4 : pas assez maîtrisé pour être cherché
    assert len(keywords) == 9  # 8 compétences au plus + le poste visé : pas plus de requêtes


def test_profile_search_limits_distinct_skill_queries_to_top_eight() -> None:
    profile = ProfileData(
        target_role="Développeur backend",
        skills=[
            SkillRating(
                name=f"Skill {index}",
                category="Forte",
                level_min=10 - index,
                level_max=10 - index,
            )
            for index in range(10)
        ],
    )

    assert profile_search_keywords(profile, "fr") == (
        "Skill 0",
        "Skill 1",
        "Skill 2",
        "Skill 3",
        "Skill 4",
        "Skill 5",
        "Skill 6",
        "Skill 7",
        "Développeur backend",
    )


def test_local_search_locations_cover_requested_cities_and_region_priority() -> None:
    assert {
        "Marseille",
        "Toulon",
        "Mougins",
        "Sophia Antipolis",
        "Nice",
        "Cannes",
    }.issubset(LOCAL_SEARCH_LOCATIONS)
    non_paris = SourceListing(
        source_id="nice",
        source_name="Adzuna",
        title="Backend developer",
        location="Nice, France",
        original_url="https://jobs.example.com/nice",
    )
    paris = SourceListing(
        source_id="paris",
        source_name="Adzuna",
        title="Backend developer",
        location="Paris, Île-de-France",
        original_url="https://jobs.example.com/paris",
    )
    assert not is_paris_listing(non_paris)
    assert is_paris_listing(paris)
    assert listing_title_with_location(non_paris) == "Backend developer — Nice, France"


def test_listing_title_does_not_repeat_location_already_in_title() -> None:
    listing = SourceListing(
        source_id="nice",
        source_name="Adzuna",
        title="Backend developer — NICE",
        location="Nice, France",
        original_url="https://jobs.example.com/nice",
    )

    assert listing_title_with_location(listing) == "Backend developer — NICE"


def test_local_search_prioritizes_non_paris_before_score_but_global_does_not() -> None:
    profile = ProfileData(
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)]
    )
    paris_high_match = SourceListing(
        source_id="paris",
        source_name="Adzuna",
        title="PHP developer",
        location="Paris, Île-de-France",
        original_url="https://jobs.example.com/paris",
        description="PHP development.",
    )
    nice_lower_match = SourceListing(
        source_id="nice",
        source_name="Adzuna",
        title="Backend developer",
        location="Nice, France",
        original_url="https://jobs.example.com/nice",
        description="Build backend services.",
    )

    local_ranked = rank_adzuna_results(
        [paris_high_match, nice_lower_match],
        profile,
        prioritize_local_areas=True,
    )
    international_ranked = rank_adzuna_results(
        [nice_lower_match, paris_high_match],
        profile,
        prioritize_local_areas=False,
    )

    assert [listing.source_id for listing in local_ranked] == ["nice", "paris"]
    assert [listing.source_id for listing in international_ranked] == ["paris", "nice"]


@pytest.mark.parametrize(
    ("title", "description", "expected"),
    [
        (
            "Backend engineer",
            "We are looking for an experienced software engineer to join our team.",
            True,
        ),
        (
            "Développeur backend",
            "Nous recherchons un développeur expérimenté pour rejoindre notre équipe.",
            True,
        ),
        (
            "Softwareentwickler",
            "Wir suchen einen erfahrenen Softwareentwickler für unser Team.",
            False,
        ),
        ("PHP", "", False),
    ],
)
def test_international_language_filter_keeps_french_english_and_unknown(
    title: str,
    description: str,
    expected: bool,
) -> None:
    listing = SourceListing(
        source_id="123",
        source_name="Adzuna",
        title=title,
        country_code="DE",
        original_url="https://jobs.example.com/123",
        description=description,
    )

    assert is_french_or_english(listing) is expected


def test_international_search_filters_other_languages_and_supports_canada() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
        ]
    )
    english = SourceListing(
        source_id="en",
        source_name="Adzuna",
        title="Backend engineer",
        country_code="CA",
        original_url="https://jobs.example.com/en",
        description=(
            "We are looking for a backend software engineer to join our development team."
        ),
    )
    german = SourceListing(
        source_id="de",
        source_name="Adzuna",
        title="Softwareentwickler",
        country_code="DE",
        original_url="https://jobs.example.com/de",
        description="Wir suchen einen erfahrenen Softwareentwickler für unser Team.",
    )
    def search_country(query, *_args):
        return [english, german] if query.keywords == ["PHP"] else []

    with patch(
        "src.services.job_sources.adzuna.search_adzuna",
        side_effect=search_country,
    ) as search:
        result = search_adzuna_for_profile(
            profile,
            [("ca", "")],
            "id",
            "key",
        )

    assert search.call_args.args[1] == "ca"
    assert [listing.source_id for listing in result.listings] == ["en"]
    assert result.filtered_language_count == 1
    assert ADZUNA_MARKETS["ca"] == "Canada"
    assert search.call_count == 2
