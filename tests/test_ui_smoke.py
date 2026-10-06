"""Tests de fumée de l'interface Streamlit (AppTest) sur une base temporaire."""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from src.config import DATA_DIRECTORY_ENV
from src.db import create_database_engine, initialize_database, save_profile
from src.models import JobOfferData, ProfileData, ResumeVersion, SkillRating
from src.services.dossier_generator import prepare_dossier_for_offer
from src.services.job_offers import create_offer

APP_PATH = str(Path(__file__).parents[1] / "src" / "app.py")


def _profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur backend",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        ],
    )


@pytest.fixture
def seeded_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))
    st.cache_resource.clear()
    engine = create_database_engine(tmp_path / "assistant-candidatures.sqlite3")
    initialize_database(engine)
    profile = _profile()
    save_profile(engine, profile)
    offer_data = JobOfferData(
        title="Développeur PHP Symfony H/F - CDI - Paris",
        company="Acme",
        location="Paris",
        contract_type="CDI",
        url="https://example.com/offre/1",
        source="Test",
        description="Nous cherchons un développeur PHP et Symfony pour notre équipe.",
    )
    with Session(engine) as session, session.begin():
        session.add(
            ResumeVersion(
                id="a" * 32,
                original_filename="cv.pdf",
                stored_filename="cv-stored.pdf",
                reviewed_text="Développeur PHP Symfony.",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )
    offer = create_offer(engine, offer_data)
    prepare_dossier_for_offer(engine, offer.id, offer_data, profile)
    engine.dispose()
    return tmp_path


def _open_page(at: AppTest, url_path: str) -> AppTest:
    """Ouvre une page `st.Page` à callable (AppTest.switch_page n'accepte que des fichiers)."""
    page_hash = next(
        key
        for key, info in at._registered_pages.items()  # noqa: SLF001
        if info.get("url_pathname") == url_path
    )
    at._page_hash = page_hash  # noqa: SLF001
    return at.run()


def _messages(element_list) -> str:
    return " ".join(str(element.value) for element in element_list)


def test_home_shows_today_dashboard(seeded_data: Path) -> None:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "Aujourd'hui"
    labels = [metric.label for metric in at.metric]
    assert "Relances à faire" in labels
    assert "Temps moyen / candidature" in labels
    assert "Développeur PHP Symfony" in _messages(at.markdown)


def test_queue_page_renders_compact_card(seeded_data: Path) -> None:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "file-d-envoi")
    assert not at.exception
    assert at.title[0].value == "File d'envoi"
    assert any(button.key.startswith("queue_open_") for button in at.button)


def test_application_dialog_shows_documents_and_send_button(seeded_data: Path) -> None:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "file-d-envoi")
    opener = next(button for button in at.button if button.key.startswith("queue_open_"))
    opener.click().run()
    assert not at.exception
    assert any(button.key.startswith("queue_sent_") for button in at.button)
    assert "Lettre à relire" in [tab.label for tab in at.tabs]
