"""Éléments partagés par plusieurs pages : constantes, base de données, aides d'affichage."""

from __future__ import annotations

import streamlit as st
from sqlalchemy import Engine

from src.db import create_database_engine, initialize_database
from src.services.applications import ApplicationNotFound
from src.services.companies import list_companies
from src.services.data_repair import repair_legacy_data
from src.services.matching import OfferMatch
from src.services.offer_keywords import KeywordReport
from src.services.send_queue import FOLLOW_UP_DAYS, due_follow_ups, mark_followed_up

CONTRACT_TYPES = ["CDI", "CDD", "Freelance", "Stage", "Alternance", "Autre"]


@st.cache_resource
def get_engine() -> Engine:
    engine = create_database_engine()
    initialize_database(engine)
    repair_legacy_data(engine)
    return engine


def _format_duration(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest:02d} s" if minutes else f"{rest} s"


def _score_badge(score: int) -> str:
    color = "green" if score >= 70 else "orange" if score >= 50 else "gray"
    return f":{color}-badge[Score {score} %]"


def _show_keyword_report(report: KeywordReport) -> None:
    """Mots de l'annonce rapportés à ce que le CV dit déjà (aucun mot n'est jamais ajouté)."""
    total = len(report.found) + len(report.missing) + len(report.profile_only)
    if not total:
        st.caption("Aucun terme technique reconnu dans cette annonce.")
        return
    st.markdown(f"**Mots de l'annonce repris dans ton CV : {len(report.found)}/{total}**")
    if report.found:
        st.write(", ".join(report.found))
    if report.profile_only:
        st.caption(
            "Demandés et présents dans ton profil, mais pas dans ton CV : "
            + ", ".join(report.profile_only)
            + ". Ajoute-les à ton CV de référence si tu les as vraiment pratiqués."
        )
    if report.missing:
        st.caption(
            "Demandés et absents de ton CV : "
            + ", ".join(report.missing)
            + ". Ils ne sont jamais ajoutés automatiquement."
        )


def _required_technologies_text(match: OfferMatch) -> str | None:
    """Technologies demandées par l'annonce et niveau du profil, pour expliquer le score."""
    if not match.required_technologies:
        return None
    parts = [
        f"{item.label} ({round(item.level * 10)}/10)"
        if item.level > 0
        else f"{item.label} (hors profil)"
        for item in match.required_technologies
    ]
    return "Technologies demandées et ton niveau : " + ", ".join(parts)


def _france_travail_secrets() -> dict[str, object]:
    return {
        "FRANCE_TRAVAIL_CLIENT_ID": st.secrets.get("FRANCE_TRAVAIL_CLIENT_ID", ""),
        "FRANCE_TRAVAIL_CLIENT_SECRET": st.secrets.get("FRANCE_TRAVAIL_CLIENT_SECRET", ""),
    }


def _show_follow_ups(engine: Engine, *, limit: int | None = None, heading: bool = True) -> None:
    due = due_follow_ups(engine)
    if not due:
        return
    companies_by_id = {company.id: company for company in list_companies(engine)}
    if heading:
        st.subheader(f"À relancer ({len(due)})")
    for application in due[:limit]:
        company = companies_by_id.get(application.company_id)
        company_label = company.name if company else "Entreprise supprimée"
        columns = st.columns([5, 2], vertical_alignment="center")
        columns[0].write(
            f"**{application.role}** — {company_label}  \n"
            f"Envoyée le {application.applied_on or '?'} · relance prévue le "
            f"{application.next_action_on}"
        )
        if columns[1].button(
            f"Relance faite (+{FOLLOW_UP_DAYS} j)",
            key=f"followed_up_{application.id}",
        ):
            try:
                mark_followed_up(engine, application.id)
            except ApplicationNotFound as error:
                st.error(str(error))
            else:
                st.session_state["queue_flash"] = "Relance enregistrée."
                st.rerun()
    if limit is not None and len(due) > limit:
        st.caption(f"{len(due) - limit} autre(s) relance(s) dans la file d'envoi.")
    if heading:
        st.markdown("---")


def _show_job_board_help() -> None:
    """Explique en clair ce qu'est un « tableau » Greenhouse ou Lever et comment le trouver."""
    with st.expander("C'est quoi un tableau d'offres Greenhouse ou Lever ?"):
        st.markdown(
            """
**Greenhouse** et **Lever** ne sont pas des sites d'emploi comme Indeed : ce sont des
**logiciels de recrutement** que des entreprises (surtout tech et startups, souvent
internationales) utilisent pour publier leurs offres. Chaque entreprise y a sa propre page
carrière, son **tableau d'offres** (« board »).

Tu ne peux donc pas y *chercher* des offres : tu **choisis une entreprise** et l'application
lit ses offres publiées.

**Comment trouver le tableau d'une entreprise**
1. Va sur la page « Carrières » ou « Jobs » du site de l'entreprise et ouvre une offre.
2. Regarde l'adresse de la page. Si elle commence par `boards.greenhouse.io/…` ou
   `jobs.lever.co/…` (ou `jobs.eu.lever.co/…`), l'entreprise utilise cet outil.
3. Copie l'adresse complète (ou seulement le nom qui suit, par exemple `ateliertech`) dans le
   champ : l'application en extrait l'identifiant.

Si l'adresse n'a rien à voir avec ces deux noms, l'entreprise utilise un autre outil : il n'y a
rien à ajouter. Beaucoup de PME françaises sont dans ce cas.
            """
        )
