"""Point d'entrée Streamlit : configuration, pages et navigation.

Chaque page vit dans `src/ui/pages/` ; les éléments partagés dans `src/ui/commun.py`.
"""

from __future__ import annotations

import streamlit as st

from src.db import load_or_seed_profile
from src.ui.commun import get_engine
from src.ui.pages.accueil import show_home
from src.ui.pages.cv_par_offre import show_tailored_resume_page
from src.ui.pages.decouverte import show_company_discovery_page
from src.ui.pages.email import show_email_page
from src.ui.pages.entreprises import show_companies_page
from src.ui.pages.file_envoi import show_send_queue_page
from src.ui.pages.offres import show_offers_page
from src.ui.pages.profil import show_profile_form, show_resume_page
from src.ui.pages.recherche import show_job_search_page
from src.ui.pages.reglages import show_settings_page
from src.ui.pages.spontanee import show_cover_letter_page
from src.ui.pages.suivi import show_applications_page
from src.ui.pages.veille import show_watcher_page

st.set_page_config(page_title="Assistant candidatures", layout="wide")


engine = get_engine()
profile = load_or_seed_profile(engine)
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(120, 130, 150, 0.18);
    }
    </style>
    """,
    unsafe_allow_html=True,
)
def _page(callback, title: str, icon: str, url_path: str, *, default: bool = False) -> st.Page:
    return st.Page(
        callback, title=title, icon=f":material/{icon}:", url_path=url_path, default=default
    )


PAGES: dict[str, st.Page] = {
    "home": _page(
        lambda: show_home(engine, profile, PAGES), "Aujourd'hui", "home", "aujourdhui", default=True
    ),
    "profile": _page(lambda: show_profile_form(engine, profile), "Mon profil", "person", "profil"),
    "resume": _page(lambda: show_resume_page(engine), "CV de référence", "description", "cv"),
    "offers": _page(lambda: show_offers_page(engine, profile), "Offres", "list_alt", "offres"),
    "search": _page(
        lambda: show_job_search_page(engine, profile),
        "Recherche en ligne",
        "travel_explore",
        "recherche",
    ),
    "watcher": _page(lambda: show_watcher_page(engine, profile), "Veille", "radar", "veille"),
    "discovery": _page(
        lambda: show_company_discovery_page(engine, profile),
        "Découvrir des entreprises",
        "explore",
        "decouvrir",
    ),
    "companies": _page(
        lambda: show_companies_page(engine, profile),
        "Entreprises à prospecter",
        "business",
        "entreprises",
    ),
    "queue": _page(
        lambda: show_send_queue_page(engine, profile), "File d'envoi", "outbox", "file-d-envoi"
    ),
    "tailored": _page(
        lambda: show_tailored_resume_page(engine, profile),
        "CV par offre",
        "target",
        "cv-par-offre",
    ),
    "spontaneous": _page(
        lambda: show_cover_letter_page(engine, profile),
        "Candidature spontanée",
        "edit_note",
        "spontanee",
    ),
    "email": _page(lambda: show_email_page(engine), "Envoyer un e-mail", "mail", "email"),
    "applications": _page(
        lambda: show_applications_page(engine), "Suivi des candidatures", "view_kanban", "suivi"
    ),
    "settings": _page(show_settings_page, "Réglages", "tune", "reglages"),
}
navigation = st.navigation(
    {
        "Accueil": [PAGES["home"]],
        "Préparer": [PAGES["profile"], PAGES["resume"]],
        "Trouver": [
            PAGES["offers"],
            PAGES["search"],
            PAGES["watcher"],
            PAGES["discovery"],
            PAGES["companies"],
        ],
        "Candidater": [
            PAGES["queue"],
            PAGES["tailored"],
            PAGES["spontaneous"],
            PAGES["email"],
            PAGES["applications"],
        ],
        "Configuration": [PAGES["settings"]],
    }
)
flash = st.session_state.pop("queue_flash", None)
if flash:
    st.toast(flash)
navigation.run()

st.caption("Application locale — les bases, CV importés et exports restent sur cet ordinateur.")
