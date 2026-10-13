"""Page « Aujourd'hui »."""

from __future__ import annotations

from datetime import date

import streamlit as st
from sqlalchemy import Engine

from src.models import ProfileData
from src.services.applications import list_applications
from src.services.cv_curation import list_offers_to_curate
from src.services.job_titles import extract_job_role
from src.services.job_watcher import get_global_watcher, load_watcher_config
from src.services.offer_reading import list_offers_to_read
from src.services.send_queue import (
    average_prep_seconds,
    build_send_queue,
    due_follow_ups,
    group_queue,
)
from src.ui.commun import _format_duration, _score_badge, _show_follow_ups


def show_home(engine: Engine, profile: ProfileData, pages: dict[str, st.Page]) -> None:
    st.title("Aujourd'hui")
    if not profile.target_role:
        st.warning("Commence par compléter ton profil (menu « Mon profil »).")
        return

    min_score = load_watcher_config().min_match_percentage
    queue = group_queue(build_send_queue(engine, profile, min_score)).sendable
    due = due_follow_ups(engine)
    sent_count = sum(a.status != "À préparer" for a in list_applications(engine))
    average = average_prep_seconds(engine)

    metrics = st.columns(4)
    metrics[0].metric("Relances à faire", len(due))
    metrics[1].metric("Offres à traiter", len(queue))
    metrics[2].metric("Dossiers prêts", sum(item.is_ready for item in queue))
    metrics[3].metric(
        "Temps moyen / candidature",
        _format_duration(average) if average is not None else "—",
        help="Mesuré entre l'ouverture du dossier dans la file d'envoi et l'enregistrement "
        f"de l'envoi. {sent_count} candidature(s) suivie(s) au total.",
    )

    if due:
        st.subheader(f"À relancer ({len(due)})")
        _show_follow_ups(engine, limit=3, heading=False)

    to_read = list_offers_to_read(engine)
    if to_read:
        st.info(
            f"{len(to_read)} annonce(s) à lire. Dans Claude Code, lance le skill "
            "lire-annonces : ton Chrome lit les pages, puis lance soigner-cv."
        )
    to_curate = list_offers_to_curate(engine)
    if to_curate:
        st.info(
            f"{len(to_curate)} CV à soigner. Dans Claude Code, lance le skill soigner-cv : "
            "il rédige le CV de chaque offre, puis l'état passe à « Soigné »."
        )

    st.subheader("À envoyer maintenant")
    if not queue:
        st.info(
            "Aucune offre à traiter pour l'instant. Lance la veille ou baisse le score minimal "
            "dans la file d'envoi."
        )
    for item in queue[:3]:
        with st.container(border=True):
            columns = st.columns([5, 2], vertical_alignment="center")
            offer = item.offer
            ready = ":green-badge[Dossier prêt]" if item.is_ready else ":gray-badge[À préparer]"
            columns[0].markdown(
                f"**{extract_job_role(offer.title, offer.location)}** — "
                f"{offer.company or 'Entreprise non précisée'}  \n"
                f"{_score_badge(item.score)} {ready}"
            )
            columns[1].page_link(
                pages["queue"], label="Ouvrir la file d'envoi", icon=":material/arrow_forward:"
            )
    if len(queue) > 3:
        st.caption(f"{len(queue) - 3} autre(s) offre(s) dans la file d'envoi.")

    watcher = get_global_watcher()
    last = watcher.last_result
    with st.container(border=True):
        columns = st.columns([5, 2], vertical_alignment="center")
        status = "Veille active" if watcher.is_active else "Veille inactive"
        detail = (
            f"dernier cycle {last.started_at[:16].replace('T', ' ')} UTC · "
            f"{last.new_offers_imported} nouvelle(s) offre(s)"
            if last
            else "aucun cycle lancé depuis le démarrage de l'application"
        )
        columns[0].markdown(f"**{status}** — {detail}")
        columns[1].page_link(
            pages["watcher"], label="Gérer la veille", icon=":material/arrow_forward:"
        )

    with st.expander("Profil de recherche"):
        st.write(profile.target_role)
        st.write(f"**Zone :** {', '.join(profile.local_locations) or 'À définir'}")
        st.write(f"**Contrat privilégié :** {', '.join(profile.preferred_contracts) or '—'}")
        if profile.remote_only_outside_local_area:
            st.caption(
                "Hors de la zone indiquée, seuls les postes entièrement en télétravail sont "
                "visés."
            )
    st.caption(f"Nous sommes le {date.today().strftime('%d/%m/%Y')}.")
