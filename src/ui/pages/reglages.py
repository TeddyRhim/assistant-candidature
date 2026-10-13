"""Page « Réglages »."""

from __future__ import annotations

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from src.config import (
    get_adzuna_credentials,
    get_france_travail_credentials,
    get_jooble_api_key,
    get_mailjet_settings,
)
from src.services.job_sources.adzuna import JobSourceError
from src.services.job_sources.bonne_boite import BONNE_BOITE_SCOPES
from src.services.job_sources.france_travail import (
    FRANCE_TRAVAIL_FALLBACK_SCOPE,
    FRANCE_TRAVAIL_SCOPE,
)
from src.services.job_sources.france_travail import get_access_token as get_france_travail_token
from src.services.job_sources.jooble import load_usage as load_jooble_usage
from src.services.job_sources.jooble import reset_usage as reset_jooble_usage
from src.ui.commun import _france_travail_secrets


def _source_secrets() -> dict[str, object]:
    try:
        return {
            "ADZUNA_APP_ID": st.secrets.get("ADZUNA_APP_ID", ""),
            "ADZUNA_APP_KEY": st.secrets.get("ADZUNA_APP_KEY", ""),
            "JOOBLE_API_KEY": st.secrets.get("JOOBLE_API_KEY", ""),
            **_france_travail_secrets(),
        }
    except StreamlitSecretNotFoundError:
        return {}


def _check_france_travail_api(scopes: tuple[str, ...], api_label: str) -> tuple[bool, str]:
    client_id, client_secret = get_france_travail_credentials(_source_secrets())
    try:
        get_france_travail_token(client_id, client_secret, scopes=scopes, api_label=api_label)
    except JobSourceError as error:
        return False, str(error)
    return True, "Connexion réussie."


def show_settings_page() -> None:
    st.title("Réglages")
    st.caption(
        "État des sources de données. Les clés se renseignent dans `.streamlit/secrets.toml` "
        "(fichier local, jamais publié) ou dans des variables d'environnement."
    )
    secrets = _source_secrets()
    adzuna_id, adzuna_key = get_adzuna_credentials(secrets)
    ft_id, ft_secret = get_france_travail_credentials(secrets)
    ft_ready = bool(ft_id and ft_secret)
    mailjet_ready = all(get_mailjet_settings(secrets)[:3])

    sources = [
        (
            "Adzuna",
            "adzuna",
            bool(adzuna_id and adzuna_key),
            None,
            "Recherche d'offres par mots-clés.",
        ),
        (
            "La Bonne Boîte",
            "lbb",
            ft_ready,
            BONNE_BOITE_SCOPES,
            "Entreprises à fort potentiel d'embauche (identifiants France Travail).",
        ),
        (
            "France Travail, offres d'emploi",
            "ft",
            ft_ready,
            (FRANCE_TRAVAIL_SCOPE, FRANCE_TRAVAIL_FALLBACK_SCOPE),
            "Offres avec texte complet (l'accès à l'API se demande sur francetravail.io).",
        ),
        (
            "Jooble",
            "jooble",
            bool(get_jooble_api_key(secrets)),
            None,
            "Agrégateur d'offres (HelloWork, Free-Work…), clé limitée à 500 requêtes.",
        ),
        ("Mailjet", "mailjet", mailjet_ready, None, "Envoi d'e-mails depuis l'application."),
    ]
    for label, key, configured, scopes, description in sources:
        with st.container(border=True):
            columns = st.columns([4, 2, 2], vertical_alignment="center")
            columns[0].markdown(f"**{label}**  \n:gray[{description}]")
            columns[1].markdown(
                ":green-badge[Clés présentes]" if configured else ":red-badge[Clés manquantes]"
            )
            if scopes is not None and configured:
                if columns[2].button("Tester la connexion", key=f"check_{key}"):
                    st.session_state[f"check_result_{key}"] = _check_france_travail_api(
                        scopes, label
                    )
            result = st.session_state.get(f"check_result_{key}")
            if result is not None:
                (st.success if result[0] else st.warning)(result[1])
            if key == "jooble" and configured:
                usage = load_jooble_usage()
                st.caption(
                    f"{usage.used} / {usage.limit} requêtes comptées, dont une réserve de "
                    f"{usage.reserve} jamais utilisée par la veille. Il en reste "
                    f"{usage.remaining} pour elle."
                )
                if st.button(
                    "Remettre le compteur à zéro",
                    key="reset_jooble_usage",
                    help="À faire seulement si Jooble remet le quota à zéro.",
                ):
                    reset_jooble_usage()
                    st.rerun()
    st.caption(
        "Les valeurs des clés ne sont jamais affichées. Le test de connexion demande seulement "
        "un jeton d'accès, sans lancer de recherche."
    )
