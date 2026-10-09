from __future__ import annotations

import json
from io import BytesIO

import pytest
from pypdf import PdfReader
from sqlalchemy.orm import Session

from src import db
from src.models import JobOfferData, ProfileData, ResumeVersion, SkillRating
from src.services.cv_renderer import load_base_cv_data
from src.services.job_offers import create_offer
from src.services.tailored_resumes import (
    TailoredResumeError,
    build_tailored_resume,
    get_tailored_resume,
    relocation_question_for_offer,
    render_tailored_resume_pdf,
    save_tailored_resume,
    validate_tailored_resume_json,
)


def test_tailored_resume_builds_editable_json_from_base_and_offer() -> None:
    profile = ProfileData(
        target_role="Développeur backend",
        experience_min_years=5,
        experience_max_years=7,
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
        ],
    )
    offer = JobOfferData(
        title="Développeur backend",
        company="Exemple",
        url="https://jobs.example.com/offre/123",
        description="PHP and REST API development.",
    )

    draft = json.loads(build_tailored_resume(offer, profile))

    assert draft["name"] == "Alexandre Martin"
    assert draft["target_company"] == "Exemple"
    assert draft["target_role"] == "Développeur backend"
    assert "location" not in draft
    assert "target_offer_url" not in draft
    assert draft["skills"][0]["label"] == "Langages"
    assert draft["contact"][0]["text"] == "06 00 00 00 00"
    assert draft["experience"][0]["company"] == "TechSolutions"
    assert draft["education"][0]["text"].startswith("Titre RNCP Développeur")


def test_tailored_resume_pdf_contains_customized_content() -> None:
    data = load_base_cv_data()
    data["profile"] = "Compétences PHP et API."
    data["target_company"] = "Exemple"
    pdf_bytes = render_tailored_resume_pdf(json.dumps(data, ensure_ascii=False))

    assert pdf_bytes.startswith(b"%PDF-")
    pages = PdfReader(BytesIO(pdf_bytes)).pages
    assert pages
    extracted_text = " ".join(pages[0].extract_text().casefold().split())
    assert "alexandre martin" in extracted_text
    # Le nom de l'entreprise visée ne figure jamais dans le PDF.
    assert "exemple" not in extracted_text.replace("example.com", "")
    assert "compétences php et api." in extracted_text


def test_tailored_resume_pdf_uses_json_skill_data() -> None:
    data = load_base_cv_data()
    data["skills"][0]["details"] = "Symfony"
    pdf_bytes = render_tailored_resume_pdf(json.dumps(data, ensure_ascii=False))
    extracted_text = " ".join(
        " ".join((page.extract_text() or "").casefold().split())
        for page in PdfReader(BytesIO(pdf_bytes)).pages
    )

    assert "symfony" in extracted_text


def test_tailored_resume_pdf_omits_company_location_and_offer_link() -> None:
    offer = JobOfferData(
        title="Développeur backend",
        company="Atelier Exemple",
        location="Paris",
        url="https://jobs.example.com/offre/backend",
        description="PHP APIs.",
    )
    profile = ProfileData(
        skills=[SkillRating(name="PHP", category="Forte", level_min=8, level_max=8)]
    )
    draft = build_tailored_resume(offer, profile)
    draft_data = json.loads(draft)
    draft_data["name"] = "Alex Exemple"
    draft_data["contact"][1]["text"] = "alex@example.com"
    draft_data["contact"][1]["url"] = "mailto:alex@example.com"
    draft_data["experience"][0]["bullets"][0] = "Développement d'API PHP."

    pdf_bytes = render_tailored_resume_pdf(json.dumps(draft_data, ensure_ascii=False))
    pages = PdfReader(BytesIO(pdf_bytes)).pages
    extracted_text = " ".join(
        " ".join((page.extract_text() or "").casefold().split())
        for page in pages
    )

    assert "atelier exemple" not in extracted_text
    assert "paris" not in extracted_text
    assert "php" in extracted_text
    assert "développementd'apiphp." in extracted_text.replace(" ", "")
    assert "jobs.example.com" not in extracted_text
    for page in pages:
        annotations = page.get("/Annots", [])
        for annotation in annotations:
            annotation_object = annotation.get_object()
            if "/A" in annotation_object:
                action = annotation_object["/A"].get_object()
                assert action.get("/URI") != "https://jobs.example.com/offre/backend"


def test_tailored_resume_omits_legacy_offer_link_from_rendered_pdf() -> None:
    data = load_base_cv_data()
    data["target_company"] = "Atelier Exemple · Paris"
    data["target_offer_url"] = "javascript:alert(1)"

    normalized = validate_tailored_resume_json(json.dumps(data, ensure_ascii=False))
    normalized_data = json.loads(normalized)
    assert "target_offer_url" not in normalized_data
    assert normalized_data["target_company"] == "Atelier Exemple"


def test_tailored_resume_pdf_keeps_all_long_content() -> None:
    data = load_base_cv_data()
    data["experience"][0]["bullets"] = [
        f"Expérience avec PHP et API REST pour projet {index}." for index in range(70)
    ]

    pages = PdfReader(
        BytesIO(render_tailored_resume_pdf(json.dumps(data, ensure_ascii=False)))
    ).pages

    assert pages
    extracted_text = " ".join(
        " ".join((page.extract_text() or "").casefold().split()) for page in pages
    )
    assert "expérience avec php et api rest pour projet 69." in extracted_text


def test_tailored_resume_pdf_rejects_excessively_large_content() -> None:
    with pytest.raises(TailoredResumeError, match="trop volumineux"):
        render_tailored_resume_pdf("x" * 12_001)


def test_tailored_resume_is_saved_and_updated_per_offer(tmp_path) -> None:
    engine = db.create_database_engine(tmp_path / "tailored.sqlite3")
    db.initialize_database(engine)
    offer = create_offer(
        engine,
        JobOfferData(title="Backend developer", description="Build APIs."),
    )
    with Session(engine) as session, session.begin():
        session.add(
            ResumeVersion(
                id="resume-id",
                original_filename="reference.docx",
                stored_filename="resume-id.docx",
                reviewed_text="Verified source CV text.",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )

    valid_content = json.dumps(load_base_cv_data(), ensure_ascii=False)
    first = save_tailored_resume(engine, offer.id, "resume-id", valid_content)
    updated_data = load_base_cv_data()
    updated_data["target_company"] = "Updated editable draft"
    updated = save_tailored_resume(
        engine, offer.id, "resume-id", json.dumps(updated_data, ensure_ascii=False)
    )

    assert first.id == updated.id
    assert json.loads(get_tailored_resume(engine, offer.id).content)["target_company"] == (
        "Updated editable draft"
    )
    with Session(engine) as session:
        assert session.get(ResumeVersion, "resume-id").reviewed_text == "Verified source CV text."
    engine.dispose()


def test_tailored_resume_json_validation_reports_parse_errors_and_missing_fields() -> None:
    with pytest.raises(TailoredResumeError, match="JSON valide"):
        validate_tailored_resume_json('{"name":')
    with pytest.raises(TailoredResumeError, match="Champs JSON manquants"):
        validate_tailored_resume_json('{"name": "Alex"}')


def test_tailored_resume_uses_json_data_instead_of_reparsing_source_text() -> None:
    offer = JobOfferData(title="Backend developer", description="Build APIs.")
    tailored_data = json.loads(build_tailored_resume(offer, ProfileData()))
    tailored_data["name"] = "JSON Editable"
    normalized = validate_tailored_resume_json(json.dumps(tailored_data))

    assert json.loads(normalized)["name"] == "JSON Editable"


def test_relocation_question_is_only_suggested_for_paris_or_international_offers() -> None:
    paris_offer = JobOfferData(
        title="Backend developer",
        location="Paris",
        description="Description.",
    )
    international_offer = JobOfferData(
        title="Backend developer",
        location="Berlin",
        source="Adzuna (DE)",
        description="Description.",
    )
    local_offer = JobOfferData(
        title="Backend developer",
        location="Nice",
        source="Adzuna (FR)",
        description="Description.",
    )

    assert "déplacement ou l'installation" in relocation_question_for_offer(paris_offer)
    assert "à l'étranger" in relocation_question_for_offer(international_offer)
    assert relocation_question_for_offer(local_offer) is None
