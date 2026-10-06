from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from src.services.job_sources.base import JobSourceError
from src.services.job_sources.greenhouse import (
    GREENHOUSE_DESCRIPTOR,
    fetch_greenhouse_listings,
)

SAMPLE_GREENHOUSE_PAYLOAD = {
    "jobs": [
        {
            "id": 123456,
            "title": "Backend Software Engineer (PHP / Symfony)",
            "updated_at": "2026-03-01T12:00:00Z",
            "absolute_url": "https://boards.greenhouse.io/ateliertech/jobs/123456",
            "location": {"name": "Paris, France"},
            "content": (
                "<h2>About the team</h2>"
                "<p>We build high-load backend services using PHP and Symfony.</p>"
                "<p>Full-time CDI role. We offer a comprehensive "
                "relocation package for candidates.</p>"
                "<p>Questions? Contact us at recruiting@ateliertech.example.</p>"
            ),
        },
        {
            "id": 789012,
            "title": "Stagiaire Développeur Python",
            "absolute_url": "https://boards.greenhouse.io/ateliertech/jobs/789012",
            "location": {"name": "Nice, France"},
            "content": "<p>Stage de 6 mois pour étudiant en fin d'études.</p>",
        },
    ]
}


def test_greenhouse_descriptor() -> None:
    assert GREENHOUSE_DESCRIPTOR.source_id == "greenhouse"
    assert GREENHOUSE_DESCRIPTOR.enabled is True
    assert GREENHOUSE_DESCRIPTOR.access_method == "official_api"


def test_fetch_greenhouse_listings_success() -> None:
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(SAMPLE_GREENHOUSE_PAYLOAD).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("src.services.job_sources.greenhouse.urlopen", return_value=mock_response):
        listings = fetch_greenhouse_listings("ateliertech", company_name="Atelier Tech")

    assert len(listings) == 2

    first = listings[0]
    assert first.source_id == "greenhouse-123456"
    assert first.source_name == "Greenhouse"
    assert first.title == "Backend Software Engineer (PHP / Symfony)"
    assert first.company == "Atelier Tech"
    assert first.location == "Paris, France"
    assert first.country_code == "FR"
    assert first.contract_type == "CDI"
    assert str(first.original_url) == "https://boards.greenhouse.io/ateliertech/jobs/123456"
    assert "<h2>" not in first.description
    assert "About the team" in first.description
    assert first.relocation_signal == "mentioned"
    assert "relocation package" in (first.relocation_evidence or "").lower()
    assert first.public_contact_email == "recruiting@ateliertech.example"
    assert str(first.contact_source_url) == "https://boards.greenhouse.io/ateliertech/jobs/123456"

    second = listings[1]
    assert second.contract_type == "Stage"
    assert second.location == "Nice, France"
    assert second.country_code == "FR"
    assert second.relocation_signal == "not_mentioned"


def test_fetch_greenhouse_listings_handles_404() -> None:
    http_error = HTTPError(
        url="https://boards-api.greenhouse.io/v1/boards/nonexistent/jobs?content=true",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b"{}"),
    )
    with patch("src.services.job_sources.greenhouse.urlopen", side_effect=http_error):
        with pytest.raises(JobSourceError, match="introuvable sur Greenhouse"):
            fetch_greenhouse_listings("nonexistent")


def test_fetch_greenhouse_listings_validates_token() -> None:
    with pytest.raises(JobSourceError, match="doit comporter entre 2 et 100 caractères"):
        fetch_greenhouse_listings("a")

    with pytest.raises(JobSourceError, match="doit comporter entre 2 et 100 caractères"):
        fetch_greenhouse_listings("bad token with spaces!")


def test_fetch_greenhouse_listings_handles_network_error() -> None:
    side_effect = URLError("Network unreachable")
    with patch("src.services.job_sources.greenhouse.urlopen", side_effect=side_effect):
        with pytest.raises(JobSourceError, match="Impossible de joindre"):
            fetch_greenhouse_listings("valid-token")


def test_extract_greenhouse_board_token() -> None:
    from src.services.job_sources.greenhouse import extract_greenhouse_board_token

    assert extract_greenhouse_board_token("ateliertech") == "ateliertech"
    assert (
        extract_greenhouse_board_token("https://boards.greenhouse.io/ateliertech")
        == "ateliertech"
    )
    assert (
        extract_greenhouse_board_token("https://boards.greenhouse.io/ateliertech/jobs")
        == "ateliertech"
    )
    assert (
        extract_greenhouse_board_token(
            "https://boards.greenhouse.io/embed/job_board?for=ateliertech"
        )
        == "ateliertech"
    )
