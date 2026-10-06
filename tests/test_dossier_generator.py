from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from src.db import create_database_engine, initialize_database
from src.models import (
    JobOfferData,
    ProfileData,
    ResumeVersion,
    SkillRating,
)
from src.services.dossier_generator import (
    prepare_dossier_for_offer,
    prepare_pending_dossiers,
)
from src.services.job_offers import create_offer


def _sample_profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur Symfony",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
            SkillRating(name="API REST", category="Forte", level_min=8, level_max=8),
            SkillRating(name="SQL", category="Forte", level_min=7, level_max=7),
            SkillRating(name="Docker", category="Intermédiaire", level_min=5, level_max=5),
        ],
    )


def test_prepare_dossier_without_resume_returns_clean_error(tmp_path: Path) -> None:
    db_path = tmp_path / "test_dossier_no_resume.sqlite3"
    engine = create_database_engine(db_path)
    initialize_database(engine)
    profile = _sample_profile()

    offer_data = JobOfferData(
        title="Développeur Symfony",
        company="TechCorp",
        description="Besoin développeur Symfony et API REST.",
    )
    offer = create_offer(engine, offer_data)

    result = prepare_dossier_for_offer(engine, offer.id, offer_data, profile)
    assert result.tailored_resume_id is None
    assert result.cover_letter_id is None
    assert "Aucun CV de référence" in (result.error or "")


def test_prepare_dossier_and_batch_pending(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test_dossier.sqlite3"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ASSISTANT_CANDIDATURES_DATA_DIR", str(data_dir))

    engine = create_database_engine(db_path)
    initialize_database(engine)
    profile = _sample_profile()

    with Session(engine) as session, session.begin():
        session.add(
            ResumeVersion(
                id="resume-id",
                original_filename="cv_ref.pdf",
                stored_filename="cv_ref.pdf",
                reviewed_text="Alexandre Martin Développeur PHP Symfony API REST SQL",
                created_at="2026-01-01T00:00:00+00:00",
            )
        )

    # Créer 2 offres : une qui matche, une qui ne matche pas
    offer_match_data = JobOfferData(
        title="Développeur PHP Symfony Backend",
        company="TechSolutions",
        description="Nous recherchons un développeur PHP Symfony maîtrisant les API REST.",
    )
    offer_match = create_offer(engine, offer_match_data)

    offer_low_data = JobOfferData(
        title="Directeur Marketing Digital",
        company="BrandAgency",
        description="Gestion de campagnes SEO et SEA sans développement.",
    )
    create_offer(engine, offer_low_data)

    # 1. Test unitaire prepare_dossier_for_offer
    dossier_single = prepare_dossier_for_offer(
        engine=engine,
        job_offer_id=offer_match.id,
        offer_data=offer_match_data,
        profile=profile,
        source_resume_id="resume-id",
    )
    assert dossier_single.error is None
    assert dossier_single.tailored_resume_id is not None
    assert dossier_single.cover_letter_id is not None

    # 2. Test batch prepare_pending_dossiers
    # offer_match a déjà ses documents, offer_low a un score faible (< 40%)
    batch_res1 = prepare_pending_dossiers(engine, profile, min_score=40)
    assert batch_res1.total_eligible == 1
    assert batch_res1.skipped_existing == 1
    assert batch_res1.generated_count == 0

    # Créer une 3ème offre éligible sans dossier
    offer_match2_data = JobOfferData(
        title="Lead Developer PHP / Symfony",
        company="WebStudio",
        description="Expertise PHP, Symfony et PostgreSQL demandée.",
    )
    create_offer(engine, offer_match2_data)

    batch_res2 = prepare_pending_dossiers(engine, profile, min_score=40)
    assert batch_res2.total_eligible == 2
    assert batch_res2.skipped_existing == 1
    assert batch_res2.generated_count == 1
    assert len(batch_res2.errors) == 0
