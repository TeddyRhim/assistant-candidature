"""Repère les offres déjà visées par une candidature envoyée récemment.

Une même annonce republiée sur un autre site change d'URL et parfois de titre : on compare donc
l'entreprise et le poste (sans ville, sans « H/F »), pas l'adresse.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import Application, Company
from src.services.job_titles import extract_job_role

RECENT_DAYS = 7
SENT_STATUSES = ("Envoyée", "Entretien", "Refusée")
MIN_PARTIAL_LENGTH = 5

RecentKeys = frozenset[tuple[str, str]]


def _normalize(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\W+", " ", text.casefold()).strip()


def _same_text(first: str, second: str) -> bool:
    """Égalité, ou inclusion d'un texte assez long dans l'autre (« Astek » et « Astek SA »)."""
    if not first or not second:
        return False
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    return len(shorter) >= MIN_PARTIAL_LENGTH and f" {shorter} " in f" {longer} "


def recent_application_keys(
    engine: Engine, *, today: date | None = None, days: int = RECENT_DAYS
) -> RecentKeys:
    """(entreprise, poste) normalisés des candidatures envoyées dans les `days` derniers jours."""
    limit = ((today or date.today()) - timedelta(days=days)).isoformat()
    keys: set[tuple[str, str]] = set()
    with Session(engine) as session:
        rows = session.execute(
            select(Application.role, Application.applied_on, Application.created_at, Company.name)
            .join(Company, Company.id == Application.company_id)
            .where(Application.status.in_(SENT_STATUSES))
        )
        for role, applied_on, created_at, company_name in rows:
            sent_on = applied_on or created_at[:10]
            if sent_on >= limit:
                keys.add((_normalize(company_name), _normalize(extract_job_role(role))))
    return frozenset(keys)


def was_recently_applied(keys: RecentKeys, title: str, company: str | None) -> bool:
    """Vrai si une candidature récente vise la même entreprise pour le même poste."""
    company_key = _normalize(company)
    if not company_key or not keys:
        return False
    role_key = _normalize(extract_job_role(title))
    return any(
        _same_text(known_company, company_key) and _same_text(known_role, role_key)
        for known_company, known_role in keys
    )
