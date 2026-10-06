from __future__ import annotations

import json
import ssl
from io import BytesIO
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

import pytest

from src import db
from src.services.companies import list_companies
from src.services.company_registry import (
    SOFTWARE_ACTIVITY_CODES,
    CompanyRegistryError,
    DuplicateCompanyCandidate,
    delete_company_candidate,
    list_company_candidates,
    promote_company_candidate_to_prospect,
    save_company_candidate,
    search_company_registry,
)
from src.services.job_offers import list_offers


def sample_payload() -> dict:
    return {
        "results": [
            {
                "siren": "123456789",
                "nom_complet": "Studio Exemple",
                "activite_principale": "62.01Z",
                "tranche_effectif_salarie": None,
                "etat_administratif": "A",
                "siege": {
                    "libelle_commune": "PARIS",
                    "departement": "75",
                },
                "matching_etablissements": [
                    {
                        "libelle_commune": "NICE",
                        "departement": "06",
                    }
                ],
            }
        ]
    }


def test_search_maps_company_candidates_and_uses_area_filters() -> None:
    response = BytesIO(json.dumps(sample_payload()).encode())

    with patch(
        "src.services.company_registry.urlopen",
        return_value=response,
    ) as open_url:
        candidates = search_company_registry(
            "programmation informatique",
            "06",
            activity_code="62.01Z",
        )

    request = open_url.call_args.args[0]
    params = parse_qs(urlparse(request.full_url).query)
    assert params["q"] == ["programmation informatique"]
    assert params["departement"] == ["06"]
    assert params["activite_principale"] == ["62.01Z"]
    assert params["etat_administratif"] == ["A"]
    assert candidates[0].siren == "123456789"
    assert candidates[0].name == "Studio Exemple"
    assert candidates[0].location == "NICE"
    assert candidates[0].department == "06"
    assert candidates[0].employee_range is None
    assert str(candidates[0].source_url).endswith("/entreprise/123456789")


def test_search_supports_paris_department() -> None:
    response = BytesIO(json.dumps(sample_payload()).encode())

    with patch(
        "src.services.company_registry.urlopen",
        return_value=response,
    ) as open_url:
        candidates = search_company_registry("", "75", activity_code="62.01Z")

    params = parse_qs(urlparse(open_url.call_args.args[0].full_url).query)
    assert params["departement"] == ["75"]
    assert candidates[0].department == "75"
    assert candidates[0].location == "PARIS"


def test_search_rejects_too_short_query_and_unknown_activity() -> None:
    with pytest.raises(CompanyRegistryError, match="trois caractères"):
        search_company_registry("ab", "06")
    with pytest.raises(CompanyRegistryError, match="code d'activité"):
        search_company_registry("informatique", "83", activity_code="99.99Z")


def test_search_without_keywords_scans_and_deduplicates_all_software_activities() -> None:
    response_payload = json.dumps(sample_payload()).encode()

    with patch(
        "src.services.company_registry.urlopen",
        side_effect=lambda *_args, **_kwargs: BytesIO(response_payload),
    ) as open_url:
        candidates = search_company_registry("", "06")

    assert len(open_url.call_args_list) == len(SOFTWARE_ACTIVITY_CODES)
    assert [candidate.siren for candidate in candidates] == ["123456789"]
    requested_codes = {
        parse_qs(urlparse(call.args[0].full_url).query)["activite_principale"][0]
        for call in open_url.call_args_list
    }
    assert requested_codes == {
        "62.01Z",
        "62.02A",
        "62.02B",
        "62.03Z",
        "62.09Z",
    }
    assert all(
        "q" not in parse_qs(urlparse(call.args[0].full_url).query)
        for call in open_url.call_args_list
    )


def test_company_discovery_default_keyword_is_usable() -> None:
    response = BytesIO(json.dumps(sample_payload()).encode())

    with patch(
        "src.services.company_registry.urlopen",
        return_value=response,
    ) as open_url:
        search_company_registry("informatique", "06")

    params = parse_qs(urlparse(open_url.call_args.args[0].full_url).query)
    assert params["q"] == ["informatique"]


def test_search_skips_incomplete_registry_results() -> None:
    payload = {"results": [{"siren": "invalid"}]}
    response = BytesIO(json.dumps(payload).encode())

    with patch("src.services.company_registry.urlopen", return_value=response):
        assert search_company_registry("informatique", "83") == []


def test_search_reports_network_failure_reason() -> None:
    with (
        patch(
            "src.services.company_registry.urlopen",
            side_effect=URLError("DNS lookup failed"),
        ),
        pytest.raises(CompanyRegistryError, match="DNS lookup failed"),
    ):
        search_company_registry("informatique", "06", activity_code="62.01Z")


def test_search_reports_expired_tls_certificate_without_disabling_verification() -> None:
    certificate_error = ssl.SSLCertVerificationError(
        1,
        "certificate verify failed: certificate has expired",
    )
    with (
        patch(
            "src.services.company_registry.urlopen",
            side_effect=URLError(certificate_error),
        ) as open_url,
        pytest.raises(CompanyRegistryError, match="vérification TLS de l'API a échoué"),
    ):
        search_company_registry("informatique", "06", activity_code="62.01Z")

    assert open_url.call_args.kwargs["context"].check_hostname is True
    assert open_url.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED


def test_registry_candidates_are_saved_once_by_siren(tmp_path) -> None:
    engine = db.create_database_engine(tmp_path / "companies.sqlite3")
    db.initialize_database(engine)
    response = BytesIO(json.dumps(sample_payload()).encode())
    with patch("src.services.company_registry.urlopen", return_value=response):
        candidate = search_company_registry("programmation", "06")[0]

    save_company_candidate(engine, candidate)
    assert [item.siren for item in list_company_candidates(engine)] == ["123456789"]
    assert list_offers(engine) == []
    with pytest.raises(DuplicateCompanyCandidate):
        save_company_candidate(engine, candidate)
    delete_company_candidate(engine, candidate.siren)
    assert list_company_candidates(engine) == []
    engine.dispose()


def test_discovered_candidate_can_become_prospect_without_creating_job_offer(
    tmp_path,
) -> None:
    engine = db.create_database_engine(tmp_path / "promotion.sqlite3")
    db.initialize_database(engine)
    with patch(
        "src.services.company_registry.urlopen",
        return_value=BytesIO(json.dumps(sample_payload()).encode()),
    ):
        candidate = search_company_registry("", "06", activity_code="62.01Z")[0]
    save_company_candidate(engine, candidate)

    prospect = promote_company_candidate_to_prospect(engine, candidate)

    assert [item.siren for item in list_company_candidates(engine)] == ["123456789"]
    assert [(company.name, company.location) for company in list_companies(engine)] == [
        ("Studio Exemple", "NICE")
    ]
    assert "ne confirme pas la présence" in prospect.development_evidence
    assert "équipe tech" in prospect.notes
    assert list_offers(engine) == []
    engine.dispose()
