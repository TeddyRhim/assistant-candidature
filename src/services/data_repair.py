"""Réparations idempotentes des données enregistrées par d'anciennes versions.

Appelées au démarrage : une base déjà propre n'est pas modifiée.
- entités HTML laissées dans les offres importées (« d&#39;Or » au lieu de « d'Or ») ;
- champ `target_role` des CV ciblés générés avant le nettoyage des titres d'annonces.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from src.models import JobOffer, TailoredResume
from src.services.job_titles import extract_job_role

_ENTITY = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,8});")
_OFFER_TEXT_FIELDS = ("title", "company", "location", "description")


@dataclass(frozen=True)
class RepairReport:
    offers_decoded: int = 0
    resumes_fixed: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.offers_decoded or self.resumes_fixed)


def repair_legacy_data(engine: Engine) -> RepairReport:
    """Décode les entités HTML des offres, puis aligne `target_role` des CV sur le poste seul."""
    offers_decoded = 0
    resumes_fixed = 0
    with Session(engine) as session, session.begin():
        offers = {offer.id: offer for offer in session.scalars(select(JobOffer))}
        for offer in offers.values():
            changed = False
            for field in _OFFER_TEXT_FIELDS:
                value = getattr(offer, field) or ""
                if _ENTITY.search(value):
                    setattr(offer, field, html.unescape(value))
                    changed = True
            offers_decoded += changed

        for resume in session.scalars(select(TailoredResume)):
            offer = offers.get(resume.job_offer_id)
            if offer is None:
                continue
            try:
                data = json.loads(resume.content)
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            role = extract_job_role(offer.title, offer.location)
            if data.get("target_role") != role:
                data["target_role"] = role
                resume.content = json.dumps(data, ensure_ascii=False, indent=2)
                resumes_fixed += 1
    return RepairReport(offers_decoded=offers_decoded, resumes_fixed=resumes_fixed)
