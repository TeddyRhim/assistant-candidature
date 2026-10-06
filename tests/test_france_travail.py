from __future__ import annotations

import io
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from src.models import ProfileData, SkillRating
from src.services.job_sources import france_travail
from src.services.job_sources.base import JobSourceError


class FakeResponse:
    def __init__(self, body: object = b"", status: int = 200) -> None:
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _http_error(url: str, code: int, body: bytes = b"{}") -> HTTPError:
    return HTTPError(url, code, "erreur", {}, io.BytesIO(body))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_token_cache() -> None:
    france_travail._clear_token_cache()


def _offer(**overrides: object) -> dict[str, object]:
    offer: dict[str, object] = {
        "id": "187ABCD",
        "intitule": "Développeur PHP Symfony H/F",
        "description": "Nous recherchons un développeur PHP.\nMaîtrise de Symfony requise.",
        "lieuTravail": {"libelle": "06 - NICE", "commune": "06088"},
        "entreprise": {"nom": "Atelier Exemple"},
        "typeContrat": "CDI",
        "natureContrat": "Contrat travail",
        "experienceLibelle": "3 ans",
        "salaire": {"libelle": "Mensuel de 3000 Euros sur 12 mois"},
        "competences": [{"libelle": "PHP", "code": "1"}, {"libelle": "Symfony", "code": "2"}],
        "origineOffre": {"origine": "1", "urlOrigine": ""},
        "contact": {"nom": "RH", "courriel": "recrutement@atelier.example"},
    }
    offer.update(overrides)
    return offer


class FakeApi:
    """Simule le point d'authentification et l'API de recherche."""

    def __init__(self, search_payload: object = None, *, search_status: int = 200) -> None:
        self.search_payload = (
            {"resultats": [_offer()]} if search_payload is None else search_payload
        )
        self.search_status = search_status
        self.token_requests: list[dict[str, list[str]]] = []
        self.search_urls: list[str] = []
        self.search_headers: list[dict[str, str]] = []
        self.reject_extended_scope = False
        self.search_error_code: int | None = None

    def __call__(self, request, timeout: float = 0):  # noqa: ANN001
        url = request.full_url
        if "access_token" in url:
            form = parse_qs(request.data.decode())
            self.token_requests.append(form)
            scope = form["scope"][0]
            if self.reject_extended_scope and "o2dsoffre" in scope:
                raise _http_error(url, 400)
            token = f"tok-{len(self.token_requests)}"
            return FakeResponse({"access_token": token, "expires_in": 1499})
        self.search_urls.append(url)
        self.search_headers.append(dict(request.header_items()))
        if self.search_error_code:
            raise _http_error(url, self.search_error_code)
        return FakeResponse(self.search_payload, status=self.search_status)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()
    monkeypatch.setattr(france_travail, "urlopen", fake)
    return fake


def test_token_is_requested_with_client_credentials_and_cached(api: FakeApi) -> None:
    first = france_travail.get_access_token("id-1", "secret-1")
    second = france_travail.get_access_token("id-1", "secret-1")

    assert first == second == "tok-1"
    assert len(api.token_requests) == 1
    form = api.token_requests[0]
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == ["id-1"]
    assert form["scope"] == [france_travail.FRANCE_TRAVAIL_SCOPE]


def test_token_falls_back_to_minimal_scope_when_extended_scope_is_refused(api: FakeApi) -> None:
    api.reject_extended_scope = True

    token = france_travail.get_access_token("id-1", "secret-1")

    assert token == "tok-2"
    assert [form["scope"][0] for form in api.token_requests] == [
        france_travail.FRANCE_TRAVAIL_SCOPE,
        france_travail.FRANCE_TRAVAIL_FALLBACK_SCOPE,
    ]


def test_missing_or_refused_credentials_raise_clear_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(JobSourceError, match="doivent être configurés"):
        france_travail.get_access_token("", "secret")

    def refuse(request, timeout: float = 0):  # noqa: ANN001
        raise _http_error(request.full_url, 401)

    monkeypatch.setattr(france_travail, "urlopen", refuse)
    with pytest.raises(JobSourceError, match="refusé l'authentification"):
        france_travail.get_access_token("id", "mauvais")


def test_unsubscribed_api_is_distinguished_from_wrong_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse_with(error_code: str):  # noqa: ANN202
        def refuse(request, timeout: float = 0):  # noqa: ANN001
            body = json.dumps({"error": error_code}).encode()
            raise _http_error(request.full_url, 400, body)

        return refuse

    monkeypatch.setattr(france_travail, "urlopen", refuse_with("invalid_scope"))
    with pytest.raises(JobSourceError, match="pas abonnée à l'API « Offres d'emploi »"):
        france_travail.get_access_token("id", "secret")

    monkeypatch.setattr(france_travail, "urlopen", refuse_with("invalid_client"))
    with pytest.raises(JobSourceError, match="client id et le secret"):
        france_travail.get_access_token("id", "secret")


def test_search_sends_bearer_token_and_documented_parameters(api: FakeApi) -> None:
    france_travail.search_france_travail(
        ["PHP", "Debug / maintenance"],
        "id-1",
        "secret-1",
        department="06",
        contract_codes=("CDI", "CDD"),
        published_since_days=14,
        limit=30,
    )

    [url] = api.search_urls
    parts = urlsplit(url)
    assert parts.path == "/partenaire/offresdemploi/v2/offres/search"
    params = parse_qs(parts.query)
    assert params["motsCles"] == ["PHP Debug maintenance"]
    assert params["departement"] == ["06"]
    assert params["typeContrat"] == ["CDI,CDD"]
    assert params["publieeDepuis"] == ["14"]
    assert params["range"] == ["0-29"]
    assert api.search_headers[0]["Authorization"] == "Bearer tok-1"


def test_offer_is_mapped_with_clean_location_contract_and_sourced_contact(api: FakeApi) -> None:
    [listing] = france_travail.search_france_travail(["PHP"], "id-1", "secret-1")

    assert listing.source_id == "francetravail-187ABCD"
    assert listing.source_name == "France Travail"
    assert listing.title == "Développeur PHP Symfony H/F"
    assert listing.company == "Atelier Exemple"
    assert listing.location == "Nice (06)"
    assert listing.contract_type == "CDI"
    assert listing.country_code == "FR"
    assert str(listing.original_url) == (
        "https://candidat.francetravail.fr/offres/recherche/detail/187ABCD"
    )
    assert "Maîtrise de Symfony requise." in listing.description
    assert "Salaire : Mensuel de 3000 Euros sur 12 mois" in listing.description
    assert "Compétences demandées : PHP, Symfony" in listing.description
    assert listing.public_contact_email == "recrutement@atelier.example"
    assert listing.contact_source_url == listing.original_url


def test_partner_origin_url_is_kept_and_contact_text_without_email_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "resultats": [
            _offer(
                origineOffre={"origine": "2", "urlOrigine": "https://partenaire.example/offre/9"},
                contact={"courriel": "Pour postuler, utiliser le lien suivant : https://x.example"},
                natureContrat="Contrat apprentissage",
                typeContrat="CDD",
            )
        ]
    }
    monkeypatch.setattr(france_travail, "urlopen", FakeApi(payload))

    [listing] = france_travail.search_france_travail(["PHP"], "id", "secret")

    assert str(listing.original_url) == "https://partenaire.example/offre/9"
    assert listing.public_contact_email is None
    assert listing.contract_type == "Alternance"


def test_no_content_and_malformed_items_are_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(france_travail, "urlopen", FakeApi(b"", search_status=204))
    assert france_travail.search_france_travail(["PHP"], "id", "secret") == []

    payload = {"resultats": ["texte", {"id": "1"}, {"intitule": "Sans identifiant"}, _offer()]}
    monkeypatch.setattr(france_travail, "urlopen", FakeApi(payload, search_status=206))
    listings = france_travail.search_france_travail(["PHP"], "id", "secret")
    assert [listing.source_id for listing in listings] == ["francetravail-187ABCD"]

    monkeypatch.setattr(france_travail, "urlopen", FakeApi({"inattendu": True}))
    with pytest.raises(JobSourceError, match="format attendu"):
        france_travail.search_france_travail(["PHP"], "id", "secret")


def test_http_errors_become_actionable_messages(api: FakeApi) -> None:
    api.search_error_code = 429
    with pytest.raises(JobSourceError, match="limite le débit"):
        france_travail.search_france_travail(["PHP"], "id", "secret")

    api.search_error_code = 403
    with pytest.raises(JobSourceError, match="abonnement"):
        france_travail.search_france_travail(["PHP"], "id", "secret")


def test_invalid_arguments_are_rejected_before_any_request(api: FakeApi) -> None:
    with pytest.raises(JobSourceError, match="département"):
        france_travail.search_france_travail(["PHP"], "id", "secret", department="Nice")
    with pytest.raises(JobSourceError, match="période"):
        france_travail.search_france_travail(["PHP"], "id", "secret", published_since_days=5)
    with pytest.raises(JobSourceError, match="résultats"):
        france_travail.search_france_travail(["PHP"], "id", "secret", limit=500)
    assert api.search_urls == []


def _profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur backend",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ],
    )


def test_profile_search_queries_each_keyword_and_department_and_deduplicates(
    api: FakeApi,
) -> None:
    result = france_travail.search_france_travail_for_profile(
        _profile(),
        "id",
        "secret",
        departments=("06", "83"),
        contract_codes=("CDI",),
    )

    # 3 mots-clés (PHP, Symfony, poste visé) x 2 départements ; une seule offre unique.
    assert len(api.search_urls) == 6
    assert len(api.token_requests) == 1
    assert [listing.source_id for listing in result.listings] == ["francetravail-187ABCD"]
    assert result.failed_targets == ()
    assert result.searched_targets == ("Alpes-Maritimes", "Var")


def test_profile_search_reports_partial_failures_without_losing_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeApi()
    original = fake.__call__

    def flaky(request, timeout: float = 0):  # noqa: ANN001
        if "departement=83" in request.full_url:
            raise _http_error(request.full_url, 500)
        return original(request, timeout)

    monkeypatch.setattr(france_travail, "urlopen", flaky)

    result = france_travail.search_france_travail_for_profile(
        _profile(), "id", "secret", departments=("06", "83")
    )

    assert len(result.listings) == 1
    assert len(result.failed_targets) == 3
    assert all(target.startswith("Var") for target, _message in result.failed_targets)


def test_profile_search_requires_terms_and_departments(api: FakeApi) -> None:
    with pytest.raises(JobSourceError, match="poste visé"):
        france_travail.search_france_travail_for_profile(ProfileData(), "id", "secret")
    with pytest.raises(JobSourceError, match="département"):
        france_travail.search_france_travail_for_profile(
            _profile(), "id", "secret", departments=()
        )
