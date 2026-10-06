from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import Engine

from src.db import create_database_engine, initialize_database
from src.services.companies import DuplicateCompany, list_companies
from src.services.job_sources import bonne_boite, france_travail
from src.services.job_sources.base import JobSourceError
from src.services.job_sources.bonne_boite import (
    BonneBoiteCompany,
    promote_bonne_boite_company,
    search_bonne_boite,
)


class FakeResponse:
    def __init__(self, body: object) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _http_error(url: str, code: int, body: bytes = b"{}") -> HTTPError:
    return HTTPError(url, code, "erreur", {}, io.BytesIO(body))  # type: ignore[arg-type]


def _item(siret: str = "31500094300706", **overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "rome": "M1805",
        "id": 916555,
        "siret": siret,
        "email": "yes",
        "company_name": "SOCIETE EXEMPLE INFORMATIQUE",
        "office_name": "SEI",
        "headcount_min": 200,
        "headcount_max": 249,
        "naf": "6202A",
        "naf_label": "Conseil en systèmes et logiciels informatiques",
        "location": {"lat": 43.6, "lon": 7.0},
        "city": "VALBONNE",
        "citycode": "06152",
        "postcode": "06560",
        "department": "Alpes-Maritimes",
        "department_number": "6",
        "hiring_potential": 27.6,
        "is_high_potential": False,
    }
    item.update(overrides)
    return item


class FakeApi:
    def __init__(self, pages: list[dict[str, object]] | None = None) -> None:
        self.pages = pages or [{"hits": 1, "items": [_item()]}]
        self.token_forms: list[dict[str, list[str]]] = []
        self.search_urls: list[str] = []
        self.search_headers: list[dict[str, str]] = []
        self.search_error: int | None = None
        self.token_error_body: bytes | None = None

    def __call__(self, request, timeout: float = 0):  # noqa: ANN001
        url = request.full_url
        if "access_token" in url:
            self.token_forms.append(parse_qs(request.data.decode()))
            if self.token_error_body is not None:
                raise _http_error(url, 400, self.token_error_body)
            return FakeResponse({"access_token": "tok-lbb", "expires_in": 1499})
        self.search_urls.append(url)
        self.search_headers.append(dict(request.header_items()))
        if self.search_error:
            raise _http_error(url, self.search_error)
        index = min(len(self.search_urls) - 1, len(self.pages) - 1)
        return FakeResponse(self.pages[index])


@pytest.fixture(autouse=True)
def _fast_and_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bonne_boite, "BONNE_BOITE_MIN_INTERVAL_SECONDS", 0)
    france_travail._clear_token_cache()


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeApi) -> FakeApi:
    monkeypatch.setattr(bonne_boite, "urlopen", fake)
    monkeypatch.setattr(france_travail, "urlopen", fake)
    france_travail._clear_token_cache()
    return fake


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    return _install(monkeypatch, FakeApi())


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    database = create_database_engine(tmp_path / "lbb.sqlite3")
    initialize_database(database)
    return database


def test_search_uses_required_scopes_and_whole_department_parameters(api: FakeApi) -> None:
    search_bonne_boite("id", "secret", rome_codes=("M1805",), departments=("06", "83"))

    assert api.token_forms[0]["scope"] == ["api_labonneboitev2 search office"]
    [url] = api.search_urls
    parts = urlsplit(url)
    assert parts.path == "/partenaire/labonneboite/v2/recherche"
    params = parse_qs(parts.query)
    assert params["rome"] == ["M1805"]
    assert params["department_number"] == ["6", "83"]
    assert params["page_size"] == ["100"]
    assert "sort_by" not in params  # le tri est refusé par l'API
    assert api.search_headers[0]["Authorization"] == "Bearer tok-lbb"


def test_companies_are_normalised_sorted_and_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    page = {
        "hits": 5,
        "items": [
            _item("31500094300706", hiring_potential=10.0),
            _item("31500094300706", hiring_potential=40.0, rome="M1806"),
            _item(
                "82193994900045",
                company_name="ATELIER SANS EFFECTIF",
                headcount_min=0,
                headcount_max=0,
                naf="6203Z",
                city="CANNES",
                department_number="83",
                email="no",
                hiring_potential=25.0,
            ),
            _item("123", company_name="SIRET INVALIDE"),
            _item("11111111111111", department_number="13", company_name="HORS ZONE"),
        ],
    }
    _install(monkeypatch, FakeApi([page]))

    result = search_bonne_boite("id", "secret")

    assert result.total_hits == 5
    assert [c.siret for c in result.companies] == ["31500094300706", "82193994900045"]
    first, second = result.companies
    assert first.hiring_potential == 40.0  # le meilleur score du doublon est conservé
    assert (first.siren, first.naf, first.department, first.city) == (
        "315000943",
        "62.02A",
        "06",
        "Valbonne",
    )
    assert first.employee_range == "200 à 249 salariés"
    assert first.has_known_email is True
    assert first.is_software_activity is True
    assert second.employee_range is None
    assert second.has_known_email is False
    assert first.source_url == "https://annuaire-entreprises.data.gouv.fr/entreprise/315000943"


def test_pagination_follows_hits_and_reports_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    def page(start: int, count: int, hits: int) -> dict[str, object]:
        return {
            "hits": hits,
            "items": [_item(f"{start + n:014d}") for n in range(count)],
        }

    complete = _install(monkeypatch, FakeApi([page(1, 100, 150), page(101, 50, 150)]))
    result = search_bonne_boite("id", "secret")
    assert len(result.companies) == 150
    assert len(complete.search_urls) == 2
    assert "page=2" in complete.search_urls[1]
    assert result.truncated is False

    capped = _install(monkeypatch, FakeApi([page(1, 100, 900)]))
    limited = search_bonne_boite("id", "secret", max_pages=1)
    assert len(capped.search_urls) == 1
    assert limited.truncated is True
    assert limited.total_hits == 900


def test_errors_are_actionable_and_arguments_validated(api: FakeApi) -> None:
    api.search_error = 403
    with pytest.raises(JobSourceError, match="abonnée à l'API « La Bonne Boîte »"):
        search_bonne_boite("id", "secret")
    api.search_error = 429
    with pytest.raises(JobSourceError, match="limite le débit"):
        search_bonne_boite("id", "secret")

    api.search_error = None
    api.token_error_body = json.dumps({"error": "invalid_scope"}).encode()
    france_travail._clear_token_cache()
    with pytest.raises(JobSourceError, match="pas abonnée à l'API « La Bonne Boîte »"):
        search_bonne_boite("id", "secret")

    with pytest.raises(JobSourceError, match="code métier"):
        search_bonne_boite("id", "secret", rome_codes=("X0000",))
    with pytest.raises(JobSourceError, match="département"):
        search_bonne_boite("id", "secret", departments=("13",))
    with pytest.raises(JobSourceError, match="pages"):
        search_bonne_boite("id", "secret", max_pages=0)


def _company(**overrides: object) -> BonneBoiteCompany:
    values: dict[str, object] = {
        "siret": "31500094300706",
        "siren": "315000943",
        "name": "SOCIETE EXEMPLE INFORMATIQUE",
        "city": "Valbonne",
        "department": "06",
        "naf": "62.02A",
        "naf_label": "Conseil en systèmes et logiciels informatiques",
        "hiring_potential": 27.6,
        "has_known_email": True,
        "rome": "M1805",
        "api_url": "https://api.francetravail.io/partenaire/labonneboite/v2/recherche?rome=M1805",
    }
    values.update(overrides)
    return BonneBoiteCompany(**values)  # type: ignore[arg-type]


def test_company_converts_to_registry_candidate() -> None:
    candidate = _company(headcount_min=20, headcount_max=49).to_candidate()

    assert candidate.siren == "315000943"
    assert candidate.activity_code == "62.02A"
    assert candidate.department == "06"
    assert candidate.employee_range == "20 à 49 salariés"
    assert str(candidate.source_url).endswith("/entreprise/315000943")


def test_promotion_creates_sourced_prospect_without_inventing_contact(engine: Engine) -> None:
    created = promote_bonne_boite_company(engine, _company())

    assert created.name == "SOCIETE EXEMPLE INFORMATIQUE"
    assert created.location == "Valbonne"
    assert created.public_contact_email is None
    assert "28/100" in created.development_evidence
    assert "ne prouve ni une offre ouverte ni une équipe" in created.development_evidence
    assert "adresse de contact (non communiquée par l'API)" in created.notes
    assert [company.id for company in list_companies(engine)] == [created.id]

    with pytest.raises(DuplicateCompany):
        promote_bonne_boite_company(engine, _company())


def test_promotion_notes_reflect_unknown_contact(engine: Engine) -> None:
    created = promote_bonne_boite_company(engine, _company(has_known_email=False))

    assert "ne connaît pas d'adresse de contact" in created.notes
