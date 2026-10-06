from __future__ import annotations

from email.message import Message
from io import BytesIO
from unittest.mock import patch

import pytest

from src.services.offer_import import (
    OfferImportError,
    _job_posting,
    import_offer_from_url,
)


class _FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/html") -> None:
        self._body = BytesIO(body)
        self.headers = Message()
        self.headers["Content-Type"] = f"{content_type}; charset=utf-8"

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return "https://jobs.example.org/role"

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


class _FakeOpener:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response

    def open(self, *_args: object, **_kwargs: object) -> _FakeResponse:
        return self.response


def test_import_uses_jobposting_metadata_and_keeps_source_url() -> None:
    html = b"""
    <html><head>
      <title>Annonce</title>
      <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"JobPosting",
         "title":"Developpeur backend (m/f/d)",
         "description":"<p>Concevoir des API et services web.</p>",
         "hiringOrganization":{"@type":"Organization","name":"Studio Exemple"},
         "jobLocation":{"address":{"addressLocality":"Nice","addressCountry":"FR"}}}
      </script>
    </head><body><main><h1>Annonce generique</h1></main></body></html>
    """
    response = _FakeResponse(html)

    with (
        patch("src.services.offer_import._validate_public_url"),
        patch("src.services.offer_import.build_opener", return_value=_FakeOpener(response)),
    ):
        offer = import_offer_from_url("https://jobs.example.org/role")

    assert offer.title == "Developpeur backend"
    assert offer.company == "Studio Exemple"
    assert offer.location == "Nice, FR"
    assert offer.description == "Concevoir des API et services web."
    assert offer.url == "https://jobs.example.org/role"
    assert offer.source == "Import URL — jobs.example.org"


def test_import_falls_back_to_article_text_and_open_graph_metadata() -> None:
    response = _FakeResponse(
        b"<html><head><meta property='og:title' content='Ingenieur logiciel'>"
        b"<meta property='og:description' content='Description partagee'></head>"
        b"<body><main><p>Texte annonce detaille.</p></main></body></html>"
    )
    with (
        patch("src.services.offer_import._validate_public_url"),
        patch("src.services.offer_import.build_opener", return_value=_FakeOpener(response)),
    ):
        offer = import_offer_from_url("https://jobs.example.org/role")

    assert offer.title == "Ingenieur logiciel"
    assert offer.description == "Description partagee"


def test_import_rejects_private_hosts_and_non_html_pages() -> None:
    with pytest.raises(OfferImportError, match="site public"):
        import_offer_from_url("http://127.0.0.1/private")

    response = _FakeResponse(b"%PDF", content_type="application/pdf")
    with (
        patch("src.services.offer_import._validate_public_url"),
        patch("src.services.offer_import.build_opener", return_value=_FakeOpener(response)),
        pytest.raises(OfferImportError, match="page HTML lisible"),
    ):
        import_offer_from_url("https://jobs.example.org/role")


def test_jobposting_searches_graph_and_nested_values() -> None:
    result = _job_posting(
        [
            '{"@graph":[{"@type":"Organization","name":"Studio"},'
            '{"@type":"JobPosting","title":"Backend"}]}'
        ]
    )

    assert result["title"] == "Backend"
