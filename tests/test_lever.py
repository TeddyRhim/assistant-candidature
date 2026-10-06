from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from src.services.job_sources.base import JobSourceError
from src.services.job_sources.lever import (
    LEVER_DESCRIPTOR,
    fetch_lever_listings,
)

SAMPLE_LEVER_PAYLOAD = [
    {
        "id": "lever-12345",
        "text": "Lead Backend Engineer (Python / FastAPI)",
        "hostedUrl": "https://jobs.lever.co/exampleco/lever-12345",
        "categories": {
            "commitment": "Full time",
            "department": "Engineering",
            "location": "Berlin, Germany",
            "workplaceType": "hybrid",
        },
        "descriptionPlain": "We are seeking a Lead Backend Engineer.",
        "lists": [
            {
                "text": "What you will do",
                "content": "<ul><li>Architect microservices</li><li>Lead team</li></ul>",
            },
            {
                "text": "Requirements",
                "content": "<ul><li>Relocation assistance provided for EU residents</li></ul>",
            },
        ],
        "additionalPlain": "Contact recruiting@exampleco.com for inquiries.",
    },
    {
        "id": "lever-67890",
        "text": "Freelance Cloud DevOps Consultant",
        "hostedUrl": "https://jobs.lever.co/exampleco/lever-67890",
        "categories": {
            "commitment": "Contract",
            "location": "Remote",
            "workplaceType": "remote",
        },
        "descriptionPlain": "Short-term DevOps migration.",
    },
]


def test_lever_descriptor() -> None:
    assert LEVER_DESCRIPTOR.source_id == "lever"
    assert LEVER_DESCRIPTOR.enabled is True
    assert LEVER_DESCRIPTOR.access_method == "official_api"


def test_fetch_lever_listings_success() -> None:
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(SAMPLE_LEVER_PAYLOAD).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch("src.services.job_sources.lever.urlopen", return_value=mock_response):
        listings = fetch_lever_listings("exampleco", company_name="Example Co")

    assert len(listings) == 2

    first = listings[0]
    assert first.source_id == "lever-lever-12345"
    assert first.source_name == "Lever"
    assert first.title == "Lead Backend Engineer (Python / FastAPI)"
    assert first.company == "Example Co"
    assert "Berlin, Germany" in first.location
    assert first.country_code == "DE"
    assert first.contract_type == "CDI"
    assert str(first.original_url) == "https://jobs.lever.co/exampleco/lever-12345"
    assert "What you will do:" in first.description
    assert "Architect microservices" in first.description
    assert first.relocation_signal == "mentioned"
    assert "relocation assistance" in (first.relocation_evidence or "").lower()
    assert first.public_contact_email == "recruiting@exampleco.com"
    assert str(first.contact_source_url) == "https://jobs.lever.co/exampleco/lever-12345"

    second = listings[1]
    assert second.contract_type == "Freelance"
    assert second.relocation_signal == "not_mentioned"


def test_fetch_lever_listings_uses_eu_endpoint() -> None:
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps([]).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch(
        "src.services.job_sources.lever.urlopen", return_value=mock_response
    ) as mock_urlopen:
        fetch_lever_listings("my-eu-company", use_eu_endpoint=True)

    request_arg = mock_urlopen.call_args[0][0]
    assert "api.eu.lever.co" in request_arg.full_url


def test_fetch_lever_listings_handles_404() -> None:
    http_error = HTTPError(
        url="https://api.lever.co/v0/postings/nonexistent?mode=json",
        code=404,
        msg="Not Found",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b"[]"),
    )
    with patch("src.services.job_sources.lever.urlopen", side_effect=http_error):
        with pytest.raises(JobSourceError, match="introuvable sur Lever"):
            fetch_lever_listings("nonexistent")


def test_fetch_lever_listings_validates_site_slug() -> None:
    with pytest.raises(JobSourceError, match="doit comporter entre 2 et 100 caractères"):
        fetch_lever_listings("x")

    with pytest.raises(JobSourceError, match="doit comporter entre 2 et 100 caractères"):
        fetch_lever_listings("invalid site name?")


def test_fetch_lever_listings_handles_network_error() -> None:
    with patch(
        "src.services.job_sources.lever.urlopen",
        side_effect=URLError("Connection refused"),
    ):
        with pytest.raises(JobSourceError, match="Impossible de joindre l'API Lever"):
            fetch_lever_listings("mycompany")


def test_extract_lever_site_slug() -> None:
    from src.services.job_sources.lever import extract_lever_site_slug

    assert extract_lever_site_slug("exampleco") == "exampleco"
    assert (
        extract_lever_site_slug("https://jobs.lever.co/exampleco")
        == "exampleco"
    )
    assert (
        extract_lever_site_slug("https://jobs.eu.lever.co/exampleco")
        == "exampleco"
    )


def test_fetch_lever_listings_auto_detects_eu_url() -> None:
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps([]).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    with patch(
        "src.services.job_sources.lever.urlopen", return_value=mock_response
    ) as mock_urlopen:
        fetch_lever_listings("https://jobs.eu.lever.co/my-eu-company")

    request_arg = mock_urlopen.call_args[0][0]
    assert "api.eu.lever.co/v0/postings/my-eu-company" in request_arg.full_url
