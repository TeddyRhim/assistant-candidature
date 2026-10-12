from __future__ import annotations

from email.message import Message
from io import BytesIO
from unittest.mock import patch

import pytest

from src.services.offer_import import (
    OfferImportError,
    OfferUnreadable,
    _job_posting,
    import_offer_from_url,
)

LONG_DESCRIPTION = (
    "Concevoir des API et services web. Maintenir les applications existantes, participer aux "
    "revues de code, écrire des tests et collaborer avec les équipes produit au quotidien. "
    "Environnement exigeant, outils modernes, grande autonomie technique."
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
         "description":"<p>@@DESC@@</p>",
         "hiringOrganization":{"@type":"Organization","name":"Studio Exemple"},
         "jobLocation":{"address":{"addressLocality":"Nice","addressCountry":"FR"}}}
      </script>
    </head><body><main><h1>Annonce generique</h1></main></body></html>
    """
    response = _FakeResponse(html.replace(b"@@DESC@@", LONG_DESCRIPTION.encode()))

    with (
        patch("src.services.offer_import._validate_public_url"),
        patch("src.services.offer_import.build_opener", return_value=_FakeOpener(response)),
    ):
        offer = import_offer_from_url("https://jobs.example.org/role")

    assert offer.title == "Developpeur backend"
    assert offer.company == "Studio Exemple"
    assert offer.location == "Nice, FR"
    assert offer.description == LONG_DESCRIPTION
    assert offer.url == "https://jobs.example.org/role"
    assert offer.source == "Import URL — jobs.example.org"


def test_import_falls_back_to_article_text_and_open_graph_metadata() -> None:
    response = _FakeResponse(
        b"<html><head><meta property='og:title' content='Ingenieur logiciel'>"
        b"<meta property='og:description' content='"
        + LONG_DESCRIPTION.encode()
        + b"'></head><body><main><p>Texte annonce detaille.</p></main></body></html>"
    )
    with (
        patch("src.services.offer_import._validate_public_url"),
        patch("src.services.offer_import.build_opener", return_value=_FakeOpener(response)),
    ):
        offer = import_offer_from_url("https://jobs.example.org/role")

    assert offer.title == "Ingenieur logiciel"
    assert offer.description == LONG_DESCRIPTION


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


def _import(body: bytes):
    with (
        patch("src.services.offer_import._validate_public_url"),
        patch(
            "src.services.offer_import.build_opener",
            return_value=_FakeOpener(_FakeResponse(body)),
        ),
    ):
        return import_offer_from_url("https://jobs.example.org/role")


def test_import_decodes_html_entities_left_in_metadata() -> None:
    body = (
        b"<html><head><meta property='og:title' content='D&amp;#39;ingenieur backend PHP'>"
        b"<meta property='og:description' content='"
        + LONG_DESCRIPTION.replace("API", "API &amp;amp; BDD").encode()
        + b"'></head></html>"
    )

    offer = _import(body)

    assert offer.title == "D'ingenieur backend PHP"
    assert "&amp;" not in offer.description
    assert "API & BDD" in offer.description


def test_import_treats_a_one_line_summary_as_unreadable() -> None:
    body = (
        b"<html><head><meta property='og:title' content='Developpeur Back-End PHP at Acme'>"
        b"<meta property='og:description' content='Apply for Developpeur Back-End PHP at Acme. "
        b"See responsibilities, qualifications, and similar jobs on LinkedIn.'></head></html>"
    )

    with pytest.raises(OfferUnreadable):
        _import(body)


def test_a_slow_site_cannot_block_the_import_beyond_the_total_deadline() -> None:
    import time

    class _SlowOpener:
        def open(self, *_args: object, **_kwargs: object):
            time.sleep(2)
            raise AssertionError("la lecture aurait dû être abandonnée")

    started = time.monotonic()
    with (
        patch("src.services.offer_import.TOTAL_TIMEOUT_SECONDS", 0.2),
        patch("src.services.offer_import._validate_public_url"),
        patch("src.services.offer_import.build_opener", return_value=_SlowOpener()),
        pytest.raises(OfferUnreadable) as caught,
    ):
        import_offer_from_url("https://jobs.example.org/role")

    assert time.monotonic() - started < 1.5
    assert "trop de temps" in str(caught.value)
    assert caught.value.host == "jobs.example.org"


def test_an_unexpected_failure_while_reading_keeps_the_link_instead_of_crashing() -> None:
    import http.client

    class _BrokenResponse(_FakeResponse):
        def read(self, size: int = -1) -> bytes:
            raise http.client.IncompleteRead(b"abc")

    with (
        patch("src.services.offer_import._validate_public_url"),
        patch(
            "src.services.offer_import.build_opener",
            return_value=_FakeOpener(_BrokenResponse(b"")),
        ),
        pytest.raises(OfferUnreadable),
    ):
        import_offer_from_url("https://jobs.example.org/role")


def test_a_programming_error_in_the_reader_is_reported_as_unreadable() -> None:
    with (
        patch("src.services.offer_import._import_offer_from_url", side_effect=KeyError("x")),
        pytest.raises(OfferUnreadable) as caught,
    ):
        import_offer_from_url("https://jobs.example.org/role")

    assert "KeyError" in str(caught.value)
