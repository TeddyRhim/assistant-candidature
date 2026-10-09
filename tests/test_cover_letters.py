from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from src import db
from src.models import JobOfferData, ProfileData, SkillRating
from src.services.cover_letters import (
    CoverLetterError,
    build_cover_letter,
    get_cover_letter,
    render_cover_letter_pdf,
    save_cover_letter,
)
from src.services.cv_renderer import load_base_cv_data
from src.services.job_offers import create_offer


def test_offer_cover_letter_uses_offer_and_verified_cv_facts() -> None:
    offer = JobOfferData(
        title="Développeur PHP (m/f/d)",
        company="Atelier Exemple",
        description="Nous cherchons une expérience PHP pour créer des API REST.",
    )
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="API REST", category="Forte", level_min=8, level_max=8),
        ]
    )

    letter = build_cover_letter(offer, profile)

    assert "Objet : Candidature au poste de Développeur PHP chez Atelier Exemple" in letter
    assert "Chez TechSolutions" in letter
    assert "PHP" in letter
    assert "Symfony" in letter
    assert "API REST" in letter
    assert "m/f/d" not in letter.casefold()
    assert "Alexandre Martin" in letter


def test_spontaneous_cover_letter_requires_company_and_names_it() -> None:
    letter = build_cover_letter(None, ProfileData(target_role="Développeur backend"), "Exemple")

    assert "Candidature spontanée" in letter
    assert "au sein de Exemple" in letter
    with pytest.raises(CoverLetterError, match="nom de l'entreprise"):
        build_cover_letter(None, ProfileData())


def test_cover_letter_pdf_uses_edited_text() -> None:
    data = load_base_cv_data()
    edited = (
        "Objet : Candidature personnalisée\n\n"
        "Madame, Monsieur,\n\n"
        "Texte modifié spécifiquement pour cette candidature.\n\n"
        "Cordialement,\n\n"
        f"{data['name']}"
    )

    pdf = render_cover_letter_pdf(edited)
    pages = PdfReader(BytesIO(pdf)).pages
    extracted_text = " ".join(
        " ".join((page.extract_text() or "").casefold().split()) for page in pages
    )

    assert pdf.startswith(b"%PDF-")
    assert "candidature personnalisée" in extracted_text
    # Sans espaces : l'extraction PDF peut couper un mot selon la police (Linux).
    assert "textemodifiéspécifiquement" in extracted_text.replace(" ", "")


def test_cover_letter_is_saved_and_updated_for_offer(tmp_path: Path) -> None:
    engine = db.create_database_engine(tmp_path / "letters.sqlite3")
    db.initialize_database(engine)
    offer = create_offer(
        engine,
        JobOfferData(title="Développeur backend", description="Développer des API."),
    )

    first = save_cover_letter(
        engine,
        "Première version.",
        job_offer_id=offer.id,
        target_company="Exemple",
    )
    updated = save_cover_letter(
        engine,
        "Version modifiée.",
        job_offer_id=offer.id,
        target_company="Exemple",
    )

    assert first.id == updated.id
    assert get_cover_letter(engine, job_offer_id=offer.id).content == "Version modifiée."
    engine.dispose()
