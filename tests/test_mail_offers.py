from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from src import cli
from src.config import DATA_DIRECTORY_ENV
from src.db import create_database_engine, initialize_database
from src.models import JobOfferData, ProfileData, ResumeVersion, SkillRating
from src.services.cv_curation import CV_TO_CURATE, resolve_offer
from src.services.job_offers import ORIGIN_MANUAL, create_offer
from src.services.mail_offers import (
    DUPLICATE,
    FAILED,
    IMPORTED,
    TO_READ,
    import_mail_offers,
    last_search_date,
    record_search_date,
)
from src.services.offer_import import OfferUnreadable
from src.services.offer_reading import READ_TO_READ
from src.services.urls import clean_tracking, normalize_url

DESCRIPTION = (
    "Nous cherchons un développeur PHP et Symfony pour faire évoluer notre plateforme produit. "
    "Vous concevez des API REST, écrivez des tests et participez aux revues de code dans une "
    "équipe agile de six personnes. Docker et Git font partie du quotidien."
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


def _item(**changes: str) -> dict[str, str]:
    item = {
        "title": "Développeur PHP Symfony H/F",
        "company": "Atelier Tech",
        "location": "Bourg-en-Bresse",
        "url": "https://alertes.example.test/clic/abc",
        "source": "alertes.example",
    }
    item.update(changes)
    return item


def _page(url: str, title: str = "Développeur PHP Symfony H/F") -> JobOfferData:
    return JobOfferData(
        title=title, company="", location="", url=url, source="x", description=DESCRIPTION
    )


def test_readable_offer_is_imported_as_manual_with_cv_to_curate(engine: Engine) -> None:
    results = import_mail_offers(
        engine,
        _profile(),
        [_item()],
        resolve=lambda url: "https://emplois.example.test/offre/42?utm_source=mail",
        read=_page,
    )

    assert [r.status for r in results] == [IMPORTED]
    offer = resolve_offer(engine, results[0].offer_id)
    assert offer.url == "https://emplois.example.test/offre/42"
    assert offer.company == "Atelier Tech"
    assert offer.origin == ORIGIN_MANUAL
    assert offer.cv_state == CV_TO_CURATE
    assert offer.source.startswith("Alerte mail")


def test_known_title_and_company_is_a_duplicate_even_with_another_link(engine: Engine) -> None:
    create_offer(
        engine,
        JobOfferData(
            title="Développeur PHP Symfony (H/F)",
            company="ATELIER TECH",
            url="https://autre.example.test/annonce/1",
            source="Veille",
            description=DESCRIPTION,
        ),
    )

    def must_not_read(url: str) -> JobOfferData:
        raise AssertionError("une offre déjà connue ne doit pas être relue")

    results = import_mail_offers(
        engine, _profile(), [_item()], resolve=lambda url: url, read=must_not_read
    )

    assert [r.status for r in results] == [DUPLICATE]
    without_link = import_mail_offers(
        engine, _profile(), [_item(url=None)], resolve=lambda url: url, read=must_not_read
    )
    assert [r.status for r in without_link] == [DUPLICATE]
    with_contract = import_mail_offers(
        engine,
        _profile(),
        [_item(title="DÉVELOPPEUR PHP SYMFONY H/F (CDI)")],
        resolve=lambda url: url,
        read=must_not_read,
    )
    assert [r.status for r in with_contract] == [DUPLICATE]


def test_unreadable_page_is_kept_to_read_under_its_mail_title(engine: Engine) -> None:
    def blocked(url: str) -> JobOfferData:
        raise OfferUnreadable("HTTP 403.", url, "emplois.example.test")

    results = import_mail_offers(
        engine,
        _profile(),
        [_item()],
        resolve=lambda url: "https://emplois.example.test/offre/7",
        read=blocked,
    )

    assert [r.status for r in results] == [TO_READ]
    offer = resolve_offer(engine, results[0].offer_id)
    assert offer.title == "Développeur PHP Symfony H/F"
    assert offer.company == "Atelier Tech"
    assert offer.read_state == READ_TO_READ


def test_redirect_page_that_names_no_word_of_the_job_is_kept_to_read(engine: Engine) -> None:
    results = import_mail_offers(
        engine,
        _profile(),
        [_item()],
        resolve=lambda url: url,
        read=lambda url: _page(url, title="Nous vous redirigeons vers votre prochain emploi"),
    )

    assert [r.status for r in results] == [TO_READ]


def test_missing_link_fails_without_stopping_the_batch(engine: Engine) -> None:
    results = import_mail_offers(
        engine,
        _profile(),
        [_item(url=""), _item(title="Développeur Python H/F", url="https://e.test/p")],
        resolve=lambda url: url,
        read=lambda url: _page(url, title="Développeur Python H/F"),
    )

    assert [r.status for r in results] == [FAILED, IMPORTED]


def test_subscriber_tokens_never_stay_in_a_stored_link() -> None:
    cadremploi = (
        "https://www.cadremploi.fr/emploi/detail_offre?alerteId=1&offreId=156&token=abc&seen=2"
    )
    talent = "https://fr.talent.com/redirect?id=99&user_id=u-1&email_id=e-2&source=talent"
    other = "https://site.example.test/job?ref=5&jj_token=s&hcid=h"

    assert clean_tracking(cadremploi) == "https://www.cadremploi.fr/emploi/detail_offre?offreId=156"
    assert clean_tracking(talent) == "https://fr.talent.com/redirect?id=99"
    assert clean_tracking(other) == "https://site.example.test/job?ref=5"
    assert normalize_url(talent) == normalize_url("https://fr.talent.com/redirect?id=99")


def test_last_search_date_is_recorded_in_the_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))
    assert last_search_date() is None

    stamp = record_search_date(datetime(2026, 3, 4, tzinfo=UTC))

    assert stamp == "2026/03/04"
    assert last_search_date() == "2026/03/04"


def test_cli_import_reports_each_offer_and_notes_the_search(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        cli, "import_mail_offers", lambda engine, profile, items: import_mail_offers(
            engine, profile, items, resolve=lambda url: url, read=_page
        )
    )
    monkeypatch.setattr(cli, "_open_engine", lambda: engine)
    source = tmp_path / "offres.json"
    source.write_text(json.dumps([_item()]), encoding="utf-8")

    assert cli.main(["mails", "import", str(source)]) == 0

    output = capsys.readouterr().out
    assert "[importée]" in output
    assert "1 importée, 0 à lire, 0 doublon, 0 échec" in output
    assert last_search_date() is not None
    assert cli.main(["mails", "depuis"]) == 0
