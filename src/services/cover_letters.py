from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import (
    Company,
    CoverLetter,
    JobOffer,
    JobOfferData,
    ProfileData,
    normalize_job_title,
)
from src.services.cv_renderer import load_base_cv_data, render_template_pdf
from src.services.matching import compare_offer_to_profile

MAX_COVER_LETTER_CHARACTERS = 12_000


class CoverLetterError(ValueError):
    """A cover letter cannot be generated, rendered, or saved."""


def build_cover_letter(
    offer: JobOfferData | None,
    profile: ProfileData,
    target_company: str = "",
) -> str:
    data = load_base_cv_data()
    company = (offer.company if offer else target_company).strip()
    role = normalize_job_title(offer.title) if offer else profile.target_role
    if not role:
        role = "Développeur Full Stack"
    if not company and offer:
        company = "votre entreprise"
    elif not company:
        raise CoverLetterError(
            "Indique le nom de l'entreprise pour préparer une candidature spontanée."
        )

    highlights = _relevant_experience_highlights(data, offer, profile)
    profile_intro = str(data["profile"]).strip()
    signature = str(data["name"]).strip()
    if offer:
        subject = f"Objet : Candidature au poste de {role}"
        if offer.company:
            subject += f" chez {offer.company}"
        paragraphs = [
            subject,
            "Madame, Monsieur,",
            (
                f"Votre annonce pour le poste de {role} a retenu mon attention. "
                f"{profile_intro}"
            ),
            _experience_paragraph(data, highlights),
            (
                "Je serais heureux d'échanger avec vous sur les besoins de votre équipe "
                "et sur la manière dont mon expérience pourrait contribuer à vos projets."
            ),
            "Je vous remercie de l'attention portée à ma candidature.",
            "Cordialement,",
            signature,
        ]
    else:
        paragraphs = [
            f"Objet : Candidature spontanée — {role}",
            "Madame, Monsieur,",
            (
                f"Je souhaite vous proposer ma candidature spontanée au sein de {company}. "
                f"{profile_intro}"
            ),
            _experience_paragraph(data, highlights),
            (
                "Je serais heureux d'échanger avec vous sur vos besoins et sur les "
                "contributions que je pourrais apporter à vos projets."
            ),
            "Je vous remercie de l'attention portée à ma candidature.",
            "Cordialement,",
            signature,
        ]
    return "\n\n".join(paragraphs)


def _experience_paragraph(
    cv_data: dict[str, object],
    highlights: list[str],
) -> str:
    experience = cv_data["experience"]
    if not isinstance(experience, list) or not experience:
        return (
            "Mon parcours m'a permis de développer des compétences techniques et "
            "une approche rigoureuse du développement logiciel."
        )
    most_recent = experience[0]
    if not isinstance(most_recent, dict):
        raise CoverLetterError("Les données d'expérience du CV de base sont invalides.")
    company = str(most_recent.get("company", "")).strip()
    period = str(most_recent.get("period", "")).strip()
    details = highlights or [
        bullet
        for item in experience
        if isinstance(item, dict) and isinstance(item.get("bullets"), list)
        for bullet in item["bullets"]
        if isinstance(bullet, str)
    ]
    if details:
        intro = f"Chez {company}" if company else "Au cours de mon parcours"
        if period:
            intro += f" ({period})"
        cleaned_details = [
            re.sub(r"\*\*(.+?)\*\*", r"\1", detail).strip()
            for detail in details
            if detail
        ]
        lowercased_details = [
            detail[0].lower() + detail[1:] for detail in cleaned_details if detail
        ]
        return (
            f"{intro}, mes missions ont notamment porté sur "
            f"{'; '.join(lowercased_details)}."
        )
    return (
        "Mon expérience professionnelle m'a permis de participer au développement "
        "d'applications web et de services métier."
    )


def _relevant_experience_highlights(
    cv_data: dict[str, object],
    offer: JobOfferData | None,
    profile: ProfileData,
) -> list[str]:
    experience = cv_data.get("experience", [])
    if not isinstance(experience, list):
        return []
    all_bullets: list[str] = []
    for item in experience:
        if isinstance(item, dict) and isinstance(item.get("bullets"), list):
            all_bullets.extend(
                bullet for bullet in item["bullets"] if isinstance(bullet, str)
            )
    if not all_bullets:
        return []
    if offer is None:
        return all_bullets

    comparison = compare_offer_to_profile(offer, profile)
    matched_terms = [
        match.skill.name.casefold()
        for match in comparison.matches
        if match.mentioned
    ]
    relevant = [
        bullet
        for bullet in all_bullets
        if any(term in bullet.casefold() for term in matched_terms)
    ]
    return relevant or all_bullets


def _extract_cv_contacts(cv_data: dict[str, object]) -> tuple[str, str]:
    email = str(cv_data.get("email") or "").strip()
    phone = str(cv_data.get("phone") or "").strip()

    contacts = cv_data.get("contact", [])
    if isinstance(contacts, list):
        for item in contacts:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            url = str(item.get("url") or "").strip()
            if not email and ("@" in text or url.startswith("mailto:")):
                email = text
            elif not phone and any(char.isdigit() for char in text):
                if not any(
                    domain in text.casefold()
                    for domain in ("linkedin", "github", "http://", "https://")
                ):
                    phone = text
    return phone, email


def render_cover_letter_pdf(content: str) -> bytes:
    validate_cover_letter_content(content)
    cv_data = load_base_cv_data()
    phone, email = _extract_cv_contacts(cv_data)
    template_data = {
        "name": str(cv_data.get("name") or "").strip(),
        "phone": phone,
        "email": email,
        "paragraphs": [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", content.strip())
            if paragraph.strip()
        ],
    }
    try:
        return render_template_pdf(template_data, "cover_letter.html")
    except (ImportError, OSError, RuntimeError) as error:
        raise CoverLetterError(
            "Le moteur WeasyPrint ne peut pas générer la lettre PDF. "
            f"Détail technique : {error}"
        ) from error


def validate_cover_letter_content(content: str) -> str:
    normalized = content.strip()
    if not normalized:
        raise CoverLetterError("Le texte de la lettre ne peut pas être vide.")
    if len(normalized) > MAX_COVER_LETTER_CHARACTERS:
        raise CoverLetterError("Le texte de la lettre dépasse la taille maximale autorisée.")
    return normalized


def _scope_key(
    job_offer_id: str | None,
    company_id: str | None,
    target_company: str,
) -> str:
    if job_offer_id:
        return f"offer:{job_offer_id}"
    if company_id:
        return f"company:{company_id}"
    normalized_company = " ".join(target_company.casefold().split())
    digest = hashlib.sha256(normalized_company.encode("utf-8")).hexdigest()[:32]
    return f"spontaneous:{digest}"


def get_cover_letter(
    engine: Engine,
    *,
    job_offer_id: str | None = None,
    company_id: str | None = None,
    target_company: str = "",
) -> CoverLetter | None:
    if not (job_offer_id or company_id or target_company.strip()):
        return None
    scope_key = _scope_key(job_offer_id, company_id, target_company)
    with Session(engine) as session:
        return session.scalar(select(CoverLetter).where(CoverLetter.scope_key == scope_key))


def save_cover_letter(
    engine: Engine,
    content: str,
    *,
    job_offer_id: str | None = None,
    company_id: str | None = None,
    target_company: str = "",
) -> CoverLetter:
    normalized_content = validate_cover_letter_content(content)
    if not job_offer_id and not (company_id or target_company.strip()):
        raise CoverLetterError(
            "Associe la lettre à une offre ou indique le nom d'une entreprise."
        )
    if len(target_company) > 200:
        raise CoverLetterError("Le nom de l'entreprise dépasse 200 caractères.")
    scope_key = _scope_key(job_offer_id, company_id, target_company)
    now = datetime.now(UTC).isoformat()
    with Session(engine, expire_on_commit=False) as session, session.begin():
        if job_offer_id and session.get(JobOffer, job_offer_id) is None:
            raise CoverLetterError("L'offre sélectionnée n'existe plus.")
        if company_id and session.get(Company, company_id) is None:
            raise CoverLetterError("L'entreprise sélectionnée n'existe plus.")
        letter = session.scalar(
            select(CoverLetter).where(CoverLetter.scope_key == scope_key)
        )
        if letter is None:
            letter = CoverLetter(
                id=uuid.uuid4().hex,
                scope_key=scope_key,
                job_offer_id=job_offer_id,
                company_id=company_id,
                target_company=target_company.strip(),
                content=normalized_content,
                created_at=now,
                updated_at=now,
            )
            session.add(letter)
        else:
            letter.job_offer_id = job_offer_id
            letter.company_id = company_id
            letter.target_company = target_company.strip()
            letter.content = normalized_content
            letter.updated_at = now
    return letter
