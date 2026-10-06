from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import (
    CoverLetter,
    JobOffer,
    JobOfferData,
    ProfileData,
    TailoredResume,
)
from src.services.cover_letters import build_cover_letter, save_cover_letter
from src.services.matching import assess_offer_fit
from src.services.resume_import import list_resume_versions
from src.services.tailored_resumes import (
    TailoredResumeError,
    build_tailored_resume,
    save_tailored_resume,
)

logger = logging.getLogger(__name__)


@dataclass
class DossierResult:
    job_offer_id: str
    offer_title: str
    company: str
    tailored_resume_id: str | None = None
    cover_letter_id: str | None = None
    error: str | None = None


@dataclass
class DossierBatchResult:
    total_eligible: int = 0
    generated_count: int = 0
    skipped_existing: int = 0
    errors: list[str] = field(default_factory=list)


def prepare_dossier_for_offer(
    engine: Engine,
    job_offer_id: str,
    offer_data: JobOfferData,
    profile: ProfileData,
    source_resume_id: str | None = None,
) -> DossierResult:
    """Génère et sauvegarde automatiquement le CV ciblé et la lettre pour une offre."""
    result = DossierResult(
        job_offer_id=job_offer_id,
        offer_title=offer_data.title,
        company=offer_data.company,
    )

    # 1. Résolution du CV de référence source
    resolved_resume_id = source_resume_id
    if not resolved_resume_id:
        resumes = list_resume_versions(engine)
        if not resumes:
            result.error = "Aucun CV de référence importé en base pour générer le CV ciblé."
            return result
        resolved_resume_id = resumes[0].id

    # 2. Génération et sauvegarde du CV ciblé
    try:
        tailored_content = build_tailored_resume(offer_data, profile)
        saved_resume = save_tailored_resume(
            engine=engine,
            job_offer_id=job_offer_id,
            source_resume_id=resolved_resume_id,
            content=tailored_content,
        )
        result.tailored_resume_id = saved_resume.id
    except TailoredResumeError as err:
        logger.warning("Erreur CV ciblé pour l'offre %s: %s", job_offer_id, err)
        result.error = f"Erreur CV ciblé: {err}"
        return result

    # 3. Génération et sauvegarde de la lettre de motivation
    try:
        letter_content = build_cover_letter(offer_data, profile)
        saved_letter = save_cover_letter(
            engine=engine,
            content=letter_content,
            job_offer_id=job_offer_id,
        )
        result.cover_letter_id = saved_letter.id
    except Exception as err:
        logger.warning("Erreur lettre pour l'offre %s: %s", job_offer_id, err)
        result.error = f"Erreur lettre de motivation: {err}"

    return result


def prepare_pending_dossiers(
    engine: Engine,
    profile: ProfileData,
    min_score: int = 40,
    source_resume_id: str | None = None,
) -> DossierBatchResult:
    """Génère les dossiers manquants pour les offres existantes dépassant le score minimal."""
    batch_result = DossierBatchResult()

    with Session(engine) as session:
        offers = list(session.scalars(select(JobOffer).order_by(JobOffer.collected_at.desc())))

    for offer in offers:
        offer_data = JobOfferData(
            title=offer.title,
            company=offer.company,
            location=offer.location,
            contract_type=offer.contract_type,  # type: ignore[arg-type]
            url=offer.url,
            source=offer.source,
            description=offer.description,
            status=offer.status,  # type: ignore[arg-type]
        )
        score = assess_offer_fit(offer_data, profile).overall_percentage or 0

        if score < min_score:
            continue

        batch_result.total_eligible += 1

        # Vérifier si les deux documents existent déjà
        with Session(engine) as session:
            has_resume = (
                session.scalar(
                    select(TailoredResume.id).where(TailoredResume.job_offer_id == offer.id)
                )
                is not None
            )
            has_letter = (
                session.scalar(
                    select(CoverLetter.id).where(CoverLetter.job_offer_id == offer.id)
                )
                is not None
            )

        if has_resume and has_letter:
            batch_result.skipped_existing += 1
            continue

        # Préparation automatique du dossier
        dossier_res = prepare_dossier_for_offer(
            engine=engine,
            job_offer_id=offer.id,
            offer_data=offer_data,
            profile=profile,
            source_resume_id=source_resume_id,
        )

        if dossier_res.error:
            batch_result.errors.append(f"{offer.title} ({offer.company}): {dossier_res.error}")
        else:
            batch_result.generated_count += 1

    return batch_result
