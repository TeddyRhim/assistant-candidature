"""File « à soigner » : des CV que Claude ajuste à la main, contrôlés par le code.

L'application ne gère que l'état (Automatique, À soigner, Soigné). Le travail de rédaction se
fait en dehors, via le skill `soigner-cv` : il lit l'offre et le CV de base, écrit le JSON du CV
et l'enregistre par `python -m src.cli soigner save`. Ce module vérifie que rien n'est inventé.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import JobOffer, JobOfferData, ProfileData
from src.services.cv_renderer import load_base_cv_data
from src.services.job_titles import extract_job_role
from src.services.offer_keywords import (
    KEYWORD_CATALOG,
    _alias_pattern,
    cv_text,
    keyword_report,
)
from src.services.resume_import import list_resume_versions
from src.services.tailored_resumes import (
    TailoredResumeError,
    get_tailored_resume,
    save_tailored_resume,
    validate_tailored_resume_json,
)

CV_AUTOMATIC = "Automatique"
CV_TO_CURATE = "À soigner"
CV_CURATED = "Soigné"
CV_STATES = (CV_AUTOMATIC, CV_TO_CURATE, CV_CURATED)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
FIXED_EXPERIENCE_FIELDS = ("role", "company", "period")


class CurationError(ValueError):
    """Le CV soigné est refusé ou l'offre est introuvable."""


@dataclass
class CurationResult:
    offer_id: str
    errors: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return not self.errors


def set_cv_state(engine: Engine, offer_id: str, state: str) -> None:
    if state not in CV_STATES:
        raise CurationError(f"État inconnu : {state}. Valeurs : {', '.join(CV_STATES)}.")
    with Session(engine) as session, session.begin():
        offer = session.get(JobOffer, offer_id)
        if offer is None:
            raise CurationError("Cette offre n'existe plus.")
        offer.cv_state = state


def list_offers_to_curate(engine: Engine) -> list[JobOffer]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(JobOffer)
                .where(JobOffer.cv_state == CV_TO_CURATE)
                .order_by(JobOffer.collected_at)
            )
        )


def resolve_offer(engine: Engine, offer_ref: str) -> JobOffer:
    """Retrouve une offre par identifiant complet ou par préfixe non ambigu."""
    ref = offer_ref.strip()
    if not ref:
        raise CurationError("Indique l'identifiant de l'offre.")
    with Session(engine) as session:
        matches = list(session.scalars(select(JobOffer).where(JobOffer.id.startswith(ref))))
    if not matches:
        raise CurationError(f"Aucune offre ne correspond à « {offer_ref} ».")
    if len(matches) > 1:
        raise CurationError(f"« {offer_ref} » correspond à plusieurs offres : précise.")
    return matches[0]


def _offer_data(offer: JobOffer) -> JobOfferData:
    return JobOfferData(
        title=offer.title,
        company=offer.company,
        location=offer.location,
        contract_type=offer.contract_type,  # type: ignore[arg-type]
        url=offer.url,
        source=offer.source,
        description=offer.description,
    )


def curation_brief(
    engine: Engine, offer: JobOffer, profile: ProfileData | None = None
) -> dict[str, Any]:
    """Tout ce qu'il faut pour soigner le CV d'une offre : annonce, CV de base, CV actuel."""
    base_cv = load_base_cv_data()
    report = keyword_report(_offer_data(offer), base_cv, profile)
    saved = get_tailored_resume(engine, offer.id)
    return {
        "offer": {
            "id": offer.id,
            "title": offer.title,
            "role": extract_job_role(offer.title, offer.location),
            "company": offer.company,
            "location": offer.location,
            "contract_type": offer.contract_type,
            "url": offer.url,
            "source": offer.source,
            "description": offer.description,
        },
        "keywords": {
            "found_in_base_cv": report.found,
            "in_profile_only": report.profile_only,
            "absent_do_not_add": report.missing,
        },
        "base_cv": base_cv,
        "current_cv": json.loads(saved.content) if saved else None,
    }


def validate_curated_cv(base: dict[str, Any], curated: dict[str, Any]) -> list[str]:
    """Erreurs si le CV soigné invente un terme technique, un chiffre ou change un fait."""
    errors: list[str] = []
    base_text = cv_text(base)
    curated_text = cv_text(curated)

    for technology in KEYWORD_CATALOG:
        pattern = _alias_pattern(technology.aliases)
        if pattern.search(curated_text) and not pattern.search(base_text):
            errors.append(f"Terme absent du CV de base : {technology.label}.")

    unknown_numbers = sorted(set(_NUMBER.findall(curated_text)) - set(_NUMBER.findall(base_text)))
    if unknown_numbers:
        errors.append("Chiffres absents du CV de base : " + ", ".join(unknown_numbers) + ".")

    for key in ("name", "contact", "education"):
        if curated.get(key) != base.get(key):
            errors.append(f"La section « {key} » ne doit pas changer.")

    base_jobs = base.get("experience", [])
    curated_jobs = curated.get("experience", [])
    if len(base_jobs) != len(curated_jobs):
        errors.append(
            f"Le nombre d'expériences doit rester {len(base_jobs)} (reçu {len(curated_jobs)})."
        )
    else:
        for position, (before, after) in enumerate(zip(base_jobs, curated_jobs, strict=True), 1):
            for key in FIXED_EXPERIENCE_FIELDS:
                if before.get(key) != after.get(key):
                    errors.append(f"Expérience {position} : « {key} » ne doit pas changer.")
            if not after.get("bullets"):
                errors.append(f"Expérience {position} : au moins une puce est nécessaire.")
    return errors


def save_curated_cv(engine: Engine, offer: JobOffer, content: str) -> CurationResult:
    """Enregistre le CV soigné si le contrôle passe, puis passe l'offre à « Soigné »."""
    result = CurationResult(offer_id=offer.id)
    try:
        normalized = validate_tailored_resume_json(content)
    except TailoredResumeError as error:
        result.errors.append(str(error))
        return result
    result.errors = validate_curated_cv(load_base_cv_data(), json.loads(normalized))
    if result.errors:
        return result
    existing = get_tailored_resume(engine, offer.id)
    if existing is not None:
        source_resume_id = existing.source_resume_id
    else:
        versions = list_resume_versions(engine)
        source_resume_id = versions[0].id if versions else None
    if source_resume_id is None:
        result.errors.append("Aucun CV de référence importé : importe-le dans l'application.")
        return result
    save_tailored_resume(engine, offer.id, source_resume_id, normalized)
    set_cv_state(engine, offer.id, CV_CURATED)
    return result
