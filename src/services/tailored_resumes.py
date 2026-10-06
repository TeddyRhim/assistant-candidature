from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import (
    JobOffer,
    JobOfferData,
    ProfileData,
    ResumeVersion,
    TailoredResume,
)
from src.services.cv_renderer import load_base_cv_data, prepare_cv_data, render_cv_pdf
from src.services.matching import compare_offer_to_profile


class TailoredResumeError(ValueError):
    """A tailored resume cannot be generated or saved."""


MAX_TAILORED_RESUME_CHARACTERS = 12_000


def render_tailored_resume_pdf(content: str) -> bytes:
    if not content.strip():
        raise TailoredResumeError("Les données JSON du CV ne peuvent pas être vides.")
    if len(content) > MAX_TAILORED_RESUME_CHARACTERS:
        raise TailoredResumeError(
            "Le contenu JSON du CV est trop volumineux pour être exporté en PDF."
        )
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise TailoredResumeError(
            f"Le brouillon n'est pas un JSON valide (ligne {error.lineno}, "
            f"colonne {error.colno}) : {error.msg}."
        ) from error
    if not isinstance(data, dict):
        raise TailoredResumeError("Les données du CV doivent être un objet JSON.")
    try:
        return render_cv_pdf(data)
    except ValueError as error:
        raise TailoredResumeError(str(error)) from error
    except (ImportError, OSError, RuntimeError) as error:
        raise TailoredResumeError(
            "Le moteur WeasyPrint ne peut pas générer le CV. Vérifie l'installation de "
            "WeasyPrint et de ses bibliothèques natives GTK/Pango sous Windows. "
            f"Détail technique : {error}"
        ) from error


def build_tailored_resume(
    offer: JobOfferData,
    profile: ProfileData,
) -> str:
    data = load_base_cv_data()
    comparison = compare_offer_to_profile(offer, profile)
    relevant_skills = sorted(
        (match.skill for match in comparison.matches if match.mentioned),
        key=lambda skill: (-skill.level_max, skill.name.casefold()),
    )
    other_skills = sorted(
        (match.skill for match in comparison.matches if not match.mentioned),
        key=lambda skill: (-skill.level_max, skill.name.casefold()),
    )
    data["target_company"] = offer.company
    data["target_role"] = offer.title
    prioritized_skills = [skill.name for skill in (*relevant_skills[:6], *other_skills[:6])]
    data["skills"] = _prioritize_base_skills(data["skills"], prioritized_skills)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _prioritize_base_skills(
    base_skills: list[dict[str, str]],
    prioritized_skill_names: list[str],
) -> list[dict[str, str]]:
    ranked_names = {name.casefold(): index for index, name in enumerate(prioritized_skill_names)}
    return sorted(
        base_skills,
        key=lambda skill: min(
            (
                index
                for name, index in ranked_names.items()
                if name in (skill["label"] + " " + skill["details"]).casefold()
            ),
            default=len(ranked_names),
        ),
    )


def validate_tailored_resume_json(content: str) -> str:
    if not content.strip():
        raise TailoredResumeError("Les données JSON du CV ne peuvent pas être vides.")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise TailoredResumeError(
            f"Le brouillon n'est pas un JSON valide (ligne {error.lineno}, "
            f"colonne {error.colno}) : {error.msg}."
        ) from error
    if not isinstance(data, dict):
        raise TailoredResumeError("Les données du CV doivent être un objet JSON.")
    try:
        prepared_data = prepare_cv_data(data)
    except ValueError as error:
        raise TailoredResumeError(str(error)) from error
    return json.dumps(prepared_data, ensure_ascii=False, indent=2)


def relocation_question_for_offer(offer: JobOfferData) -> str | None:
    source_match = re.search(r"\(([A-Z]{2})\)", offer.source)
    country_code = source_match.group(1) if source_match else None
    normalized_location = offer.location.casefold()
    is_paris = any(
        term in normalized_location
        for term in ("paris", "île-de-france", "ile-de-france")
    )
    is_international = country_code is not None and country_code != "FR"
    if not is_paris and not is_international:
        return None
    return (
        "Votre offre étant située "
        + ("à Paris" if is_paris else "à l'étranger")
        + ", pourriez-vous m'indiquer les modalités de présence sur site et de télétravail, "
        "ainsi que les éventuelles solutions d'accompagnement pour le déplacement ou "
        "l'installation ?"
    )


def save_tailored_resume(
    engine: Engine,
    job_offer_id: str,
    source_resume_id: str,
    content: str,
) -> TailoredResume:
    normalized_content = validate_tailored_resume_json(content)
    now = datetime.now(UTC).isoformat()
    with Session(engine, expire_on_commit=False) as session, session.begin():
        if session.get(JobOffer, job_offer_id) is None:
            raise TailoredResumeError("L'offre sélectionnée n'existe plus.")
        if session.get(ResumeVersion, source_resume_id) is None:
            raise TailoredResumeError("Le CV de référence sélectionné n'existe plus.")
        statement = select(TailoredResume).where(
            TailoredResume.job_offer_id == job_offer_id
        )
        tailored_resume = session.scalar(statement)
        if tailored_resume is None:
            tailored_resume = TailoredResume(
                id=uuid.uuid4().hex,
                job_offer_id=job_offer_id,
                source_resume_id=source_resume_id,
                content=normalized_content,
                created_at=now,
                updated_at=now,
            )
            session.add(tailored_resume)
        else:
            tailored_resume.source_resume_id = source_resume_id
            tailored_resume.content = normalized_content
            tailored_resume.updated_at = now
    return tailored_resume


def get_tailored_resume(engine: Engine, job_offer_id: str) -> TailoredResume | None:
    with Session(engine) as session:
        return session.scalar(
            select(TailoredResume).where(TailoredResume.job_offer_id == job_offer_id)
        )
