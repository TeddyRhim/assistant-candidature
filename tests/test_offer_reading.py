from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from src import cli
from src.config import DATA_DIRECTORY_ENV
from src.db import create_database_engine, initialize_database
from src.models import ProfileData, ResumeVersion, SkillRating
from src.services.cv_curation import CV_TO_CURATE, resolve_offer
from src.services.job_offers import ORIGIN_MANUAL, DuplicateOfferURL
from src.services.offer_import import OfferImportError, OfferUnreadable, import_offer_from_url
from src.services.offer_reading import (
    READ_COMPLETE,
    READ_TO_READ,
    READ_UNREADABLE,
    ReadingError,
    clean_tracking,
    complete_read_offer,
    list_offers_to_read,
    mark_unreadable,
    store_unread_offer,
)
from src.services.send_queue import build_send_queue
from src.services.tailored_resumes import get_tailored_resume

APEC_URL = (
    "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/179504396W"
    "?xtor=&utm_campaign=google_jobs_apply&utm_source=google_jobs_apply&utm_medium=organic"
)
CLEAN_APEC_URL = (
    "https://www.apec.fr/candidat/recherche-emploi.html/emploi/detail-offre/179504396W"
)


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))
    database = create_database_engine(tmp_path / "assistant-candidatures.sqlite3")
    initialize_database(database)
    with Session(database) as session, session.begin():
        session.add(
            ResumeVersion(
                id="a" * 32,
                original_filename="cv.pdf",
                stored_filename="cv-stored.pdf",
                reviewed_text="Développeur.",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
    return database


def _profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur backend",
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)],
    )


DETAILS = {
    "title": "Développeur PHP Symfony H/F",
    "company": "Atelier Tech",
    "location": "Bourg-en-Bresse",
    "contract_type": "CDI",
    "description": "Nous cherchons un développeur PHP et Symfony pour notre équipe produit.",
}


def test_tracking_parameters_are_removed_from_the_link() -> None:
    assert clean_tracking(APEC_URL) == CLEAN_APEC_URL
    assert clean_tracking("https://x.test/o?id=7&utm_source=a") == "https://x.test/o?id=7"


def test_unreadable_link_is_stored_and_kept_out_of_the_queue(engine: Engine) -> None:
    offer = store_unread_offer(engine, APEC_URL, "www.apec.fr", "HTTP 403.")

    stored = resolve_offer(engine, offer.id)
    assert stored.url == CLEAN_APEC_URL
    assert stored.read_state == READ_TO_READ
    assert stored.origin == ORIGIN_MANUAL
    assert [item.offer.id for item in build_send_queue(engine, _profile())] == []
    assert [o.id for o in list_offers_to_read(engine)] == [offer.id]
    with pytest.raises(DuplicateOfferURL):
        store_unread_offer(engine, APEC_URL, "www.apec.fr", "HTTP 403.")


def test_completing_a_read_offer_prepares_the_dossier_and_queues_the_cv(engine: Engine) -> None:
    offer = store_unread_offer(engine, APEC_URL, "www.apec.fr", "HTTP 403.")

    result = complete_read_offer(engine, offer.id, DETAILS, _profile())

    assert result.error is None
    done = resolve_offer(engine, offer.id)
    assert done.read_state == READ_COMPLETE
    assert done.title == DETAILS["title"]
    assert done.company == "Atelier Tech"
    assert done.cv_state == CV_TO_CURATE
    assert get_tailored_resume(engine, offer.id) is not None
    assert list_offers_to_read(engine) == []
    assert [item.offer.id for item in build_send_queue(engine, _profile())] == [offer.id]


def test_incomplete_details_are_refused_and_change_nothing(engine: Engine) -> None:
    offer = store_unread_offer(engine, APEC_URL, "www.apec.fr", "HTTP 403.")

    with pytest.raises(ReadingError):
        complete_read_offer(engine, offer.id, {**DETAILS, "title": ""}, _profile())
    with pytest.raises(ReadingError):
        complete_read_offer(engine, "0" * 32, DETAILS, _profile())

    assert resolve_offer(engine, offer.id).read_state == READ_TO_READ


def test_an_unreadable_page_leaves_the_list_to_read(engine: Engine) -> None:
    offer = store_unread_offer(engine, APEC_URL, "www.apec.fr", "HTTP 403.")

    mark_unreadable(engine, offer.id, "Connexion requise.")

    assert resolve_offer(engine, offer.id).read_state == READ_UNREADABLE
    assert list_offers_to_read(engine) == []


def test_import_reports_unreadable_pages_but_keeps_invalid_urls_as_errors() -> None:
    opener = MagicMock()
    opener.open.side_effect = HTTPError("https://93.184.216.34/o", 403, "Forbidden", {}, None)

    with patch("src.services.offer_import.build_opener", return_value=opener):
        with pytest.raises(OfferUnreadable) as caught:
            import_offer_from_url("https://93.184.216.34/o?utm_source=x")

    assert caught.value.host == "93.184.216.34"
    assert "403" in str(caught.value)
    with pytest.raises(OfferImportError) as invalid:
        import_offer_from_url("http://localhost/o")
    assert not isinstance(invalid.value, OfferUnreadable)


def test_cli_reads_completes_and_marks_unreadable(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = store_unread_offer(engine, APEC_URL, "www.apec.fr", "HTTP 403.")
    second = store_unread_offer(engine, "https://x.test/offre", "x.test", "HTTP 403.")

    assert cli.main(["lire", "list"]) == 0
    listing = capsys.readouterr().out
    assert first.id[:8] in listing
    assert CLEAN_APEC_URL in listing

    details = tmp_path / "details.json"
    details.write_text(json.dumps(DETAILS), encoding="utf-8")
    assert cli.main(["lire", "save", first.id[:8], str(details)]) == 0
    assert resolve_offer(engine, first.id).read_state == READ_COMPLETE

    assert cli.main(["lire", "unreadable", second.id[:8], "Connexion requise."]) == 0
    assert resolve_offer(engine, second.id).read_state == READ_UNREADABLE
    assert cli.main(["lire", "save", "inconnu", str(details)]) == 1
