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
from src.services.job_offers import create_offer, list_offers

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


@pytest.mark.parametrize(
    ("url_path", "title"),
    [
        ("veille", "Veille"),
        ("reglages", "Réglages"),
        ("recherche", "Recherche d'offres en ligne"),
        ("suivi", "Suivi des candidatures"),
        ("offres", "Offres d'emploi"),
    ],
)
def test_secondary_pages_render(seeded_data: Path, url_path: str, title: str) -> None:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, url_path)
    assert not at.exception
    assert at.title[0].value == title


def test_settings_page_lists_sources_without_secrets(seeded_data: Path) -> None:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "reglages")
    text = _messages(at.markdown)
    for source in ("Adzuna", "La Bonne Boîte", "France Travail", "Mailjet"):
        assert source in text


def test_applications_board_changes_status(seeded_data: Path) -> None:
    from src.services.applications import list_applications
    from src.services.send_queue import mark_offer_sent

    engine = create_database_engine(seeded_data / "assistant-candidatures.sqlite3")
    offer = list_offers(engine)[0]
    mark_offer_sent(engine, offer.id)
    application = list_applications(engine)[0]
    engine.dispose()

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "suivi")
    assert not at.exception
    selector = next(box for box in at.selectbox if box.key == f"status_{application.id}")
    selector.select("Entretien").run()
    assert not at.exception

    engine = create_database_engine(seeded_data / "assistant-candidatures.sqlite3")
    assert list_applications(engine)[0].status == "Entretien"
    engine.dispose()


def test_adding_an_offer_generates_an_adapted_cv_and_shows_keywords(seeded_data: Path) -> None:
    from src.services.tailored_resumes import get_tailored_resume

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "offres")
    assert not at.exception
    next(box for box in at.text_input if box.label == "Intitulé du poste").set_value(
        "Développeur Symfony H/F"
    )
    next(box for box in at.text_input if box.label == "Entreprise (facultatif)").set_value(
        "Nouvelle Société"
    )
    next(box for box in at.text_area if box.label == "Description de l'annonce").set_value(
        "Nous cherchons un développeur PHP et Symfony. API REST et SQL requis."
    )
    next(button for button in at.button if button.label == "Enregistrer l'offre").click().run()
    assert not at.exception
    assert any("CV adapté" in str(success.value) for success in at.success)
    assert any("Mots de l'annonce repris" in str(md.value) for md in at.markdown)

    engine = create_database_engine(seeded_data / "assistant-candidatures.sqlite3")
    created = next(offer for offer in list_offers(engine) if offer.company == "Nouvelle Société")
    assert get_tailored_resume(engine, created.id) is not None
    assert created.cv_state == "À soigner"
    assert created.origin == "Manuel"
    engine.dispose()


def test_offer_can_be_marked_to_curate_and_shows_in_queue_and_home(seeded_data: Path) -> None:
    from src.services.cv_curation import CV_TO_CURATE, list_offers_to_curate

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "offres")
    next(button for button in at.button if button.label == "Marquer le CV à soigner").click().run()
    assert not at.exception

    engine = create_database_engine(seeded_data / "assistant-candidatures.sqlite3")
    assert [offer.cv_state for offer in list_offers_to_curate(engine)] == [CV_TO_CURATE]
    engine.dispose()

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert any("CV à soigner" in str(info.value) for info in at.info)
    _open_page(at, "file-d-envoi")
    assert "CV à soigner" in _messages(at.markdown)


def test_queue_separates_my_offers_from_imported_ones(seeded_data: Path) -> None:
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "offres")
    next(box for box in at.text_input if box.label == "Intitulé du poste").set_value(
        "Développeur Symfony H/F"
    )
    next(box for box in at.text_area if box.label == "Description de l'annonce").set_value(
        "Nous cherchons un développeur PHP et Symfony. API REST et SQL requis."
    )
    next(button for button in at.button if button.label == "Enregistrer l'offre").click().run()

    _open_page(at, "file-d-envoi")
    assert not at.exception
    assert [tab.label for tab in at.tabs][:4] == [
        "Ajoutées par moi",
        "Importées par l'application",
        "Himalayas (candidature payante)",
        "Candidatures spontanées",
    ]
    manual_buttons = [button.key for button in at.tabs[0].button if button.key]
    imported_buttons = [button.key for button in at.tabs[1].button if button.key]
    assert any(key.startswith("queue_open_") for key in manual_buttons)
    assert any(key.startswith("queue_open_") for key in imported_buttons)
    assert not set(manual_buttons) & set(imported_buttons)


def test_unreadable_link_is_stored_for_later_reading(
    seeded_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services import offer_import
    from src.services.offer_reading import list_offers_to_read

    def unreadable(url: str):
        raise offer_import.OfferUnreadable("Le site a refusé (HTTP 403).", url, "www.apec.fr")

    monkeypatch.setattr(offer_import, "import_offer_from_url", unreadable)
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "offres")
    next(box for box in at.text_input if box.label == "Lien de l'annonce").set_value(
        "https://www.apec.fr/candidat/detail-offre/1?utm_source=x"
    )
    next(button for button in at.button if button.label == "Lire l'annonce").click().run()
    assert not at.exception
    assert any("Annonces à lire" in str(success.value) for success in at.success)

    engine = create_database_engine(seeded_data / "assistant-candidatures.sqlite3")
    [stored] = list_offers_to_read(engine)
    assert stored.url == "https://www.apec.fr/candidat/detail-offre/1"
    engine.dispose()

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    assert any("annonce(s) à lire" in str(info.value) for info in at.info)


def test_link_already_saved_is_reported_without_reading_it(
    seeded_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services import offer_import

    def must_not_read(url: str):
        raise AssertionError("un lien déjà enregistré ne doit pas être relu")

    monkeypatch.setattr(offer_import, "import_offer_from_url", must_not_read)
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "offres")
    next(box for box in at.text_input if box.label == "Lien de l'annonce").set_value(
        "http://www.example.com/offre/1/?utm_source=partage"
    )
    next(button for button in at.button if button.label == "Lire l'annonce").click().run()

    assert not at.exception
    assert any("déjà enregistrée" in str(warning.value) for warning in at.warning)


def test_offer_is_kept_and_page_stays_usable_when_dossier_generation_crashes(
    seeded_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.ui.pages import offres

    def crash(*_args, **_kwargs):
        raise KeyError("experience")

    # On remplace la fonction là où la page l'utilise (module importé une seule fois).
    monkeypatch.setattr(offres, "prepare_dossier_for_offer", crash)
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "offres")
    next(box for box in at.text_input if box.label == "Intitulé du poste").set_value(
        "Développeur Symfony H/F"
    )
    next(box for box in at.text_area if box.label == "Description de l'annonce").set_value(
        "Nous cherchons un développeur PHP et Symfony. API REST et SQL requis."
    )
    next(button for button in at.button if button.label == "Enregistrer l'offre").click().run()

    assert not at.exception
    assert any("Dossier non préparé" in str(success.value) for success in at.success)
    engine = create_database_engine(seeded_data / "assistant-candidatures.sqlite3")
    assert any(offer.title == "Développeur Symfony H/F" for offer in list_offers(engine))
    engine.dispose()


def test_offers_page_shows_a_first_page_of_offers_and_can_show_all(seeded_data: Path) -> None:
    engine = create_database_engine(seeded_data / "assistant-candidatures.sqlite3")
    for number in range(30):
        create_offer(
            engine,
            JobOfferData(
                title=f"Développeur PHP numéro {number}",
                company="Atelier Tech",
                location="Bourg-en-Bresse",
                url=f"https://example.com/lot/{number}",
                description="PHP et Symfony pour notre équipe.",
            ),
        )
    engine.dispose()

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "offres")
    assert not at.exception
    offer_expanders = [box for box in at.expander if "Développeur PHP" in box.label]
    assert len(offer_expanders) == 25
    assert any("non affichée" in str(caption.value) for caption in at.caption)

    next(box for box in at.selectbox if box.key == "offers_page_size").select(0).run()
    assert not any("non affichée" in str(caption.value) for caption in at.caption)


def test_discovery_page_proposes_the_watcher_departments_first(
    seeded_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La zone vient de la configuration locale de veille, jamais du code."""
    from src.services.job_watcher import WatcherConfig, save_watcher_config

    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_ID", "id-de-test")
    monkeypatch.setenv("FRANCE_TRAVAIL_CLIENT_SECRET", "secret-de-test")
    save_watcher_config(WatcherConfig(france_travail_departments=["38", "01"]))

    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    _open_page(at, "decouvrir")
    assert not at.exception
    assert at.title[0].value == "Découvrir des entreprises"
    assert at.multiselect[0].value == ["38", "01"]
    assert at.selectbox[0].options[:2] == ["38 — Isère", "01 — Ain"]


ALL_PAGES = [
    "aujourdhui", "profil", "cv", "offres", "recherche", "veille", "decouvrir", "entreprises",
    "file-d-envoi", "cv-par-offre", "spontanee", "email", "suivi", "reglages",
]


@pytest.mark.parametrize("url_path", ALL_PAGES)
def test_every_page_renders_without_exception(seeded_data: Path, url_path: str) -> None:
    """Chaque module de `src/ui/pages` est chargé et rendu au moins une fois."""
    at = AppTest.from_file(APP_PATH, default_timeout=60).run()
    if url_path != "aujourdhui":  # la page par défaut est déjà ouverte (chemin vide)
        _open_page(at, url_path)
    assert not at.exception
    assert at.title, f"page {url_path} sans titre"
