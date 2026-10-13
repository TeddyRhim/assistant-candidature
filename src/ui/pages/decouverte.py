"""Page « Découvrir des entreprises » : La Bonne Boîte et registre public."""

from __future__ import annotations

import streamlit as st
from sqlalchemy import Engine
from streamlit.errors import StreamlitSecretNotFoundError

from src.config import get_france_travail_credentials
from src.models import CompanyCandidateData, ProfileData
from src.services.companies import DuplicateCompany, list_companies
from src.services.company_registry import (
    COMPANY_DEPARTMENT_LABELS,
    SOFTWARE_ACTIVITY_CODES,
    CompanyRegistryError,
    DuplicateCompanyCandidate,
    delete_company_candidate,
    list_company_candidates,
    promote_company_candidate_to_prospect,
    save_company_candidate,
    search_company_registry,
)
from src.services.job_sources.adzuna import JobSourceError
from src.services.job_sources.bonne_boite import (
    DEFAULT_DEPARTMENTS as BONNE_BOITE_DEFAULT_DEPARTMENTS,
)
from src.services.job_sources.bonne_boite import (
    DEFAULT_ROME_CODES as BONNE_BOITE_DEFAULT_ROME_CODES,
)
from src.services.job_sources.bonne_boite import ROME_CODES as BONNE_BOITE_ROME_CODES
from src.services.job_sources.bonne_boite import (
    SUPPORTED_DEPARTMENTS as BONNE_BOITE_SUPPORTED_DEPARTMENTS,
)
from src.services.job_sources.bonne_boite import (
    BonneBoiteCompany,
    promote_bonne_boite_company,
    search_bonne_boite,
)
from src.services.job_watcher import load_watcher_config
from src.services.matching import assess_company_fit
from src.ui.commun import _france_travail_secrets


def show_company_discovery_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Découvrir des entreprises")
    bonne_boite_tab, registry_tab = st.tabs(
        ["La Bonne Boîte (potentiel d'embauche)", "Registre public (activité déclarée)"]
    )
    with bonne_boite_tab:
        _show_bonne_boite_tab(engine)
    with registry_tab:
        _show_registry_discovery_tab(engine, profile)


def _show_bonne_boite_tab(engine: Engine) -> None:
    st.write(
        "La Bonne Boîte (France Travail) classe les entreprises qui ont le plus de chances "
        "de recruter dans les 6 prochains mois pour un métier, y compris sans offre publiée : "
        "idéal pour les candidatures spontanées."
    )
    st.warning(
        "Le score est une estimation statistique relative (calculée sur les recrutements "
        "passés) : il ne prouve ni une offre ouverte ni une équipe de développement. L'API "
        "ne fournit aucun e-mail : elle indique seulement si une adresse est connue. Trouve "
        "le contact sur le site de l'entreprise avant de candidater."
    )
    try:
        secrets = _france_travail_secrets()
    except StreamlitSecretNotFoundError:
        secrets = {}
    client_id, client_secret = get_france_travail_credentials(secrets)
    if not client_id or not client_secret:
        st.warning(
            "Configure FRANCE_TRAVAIL_CLIENT_ID et FRANCE_TRAVAIL_CLIENT_SECRET (voir l'onglet "
            "France Travail de « Recherche en ligne ») et abonne l'application à l'API "
            "« La Bonne Boîte » sur francetravail.io."
        )
        return

    with st.form("bonne_boite_search"):
        departments = st.multiselect(
            "Départements entiers",
            options=list(BONNE_BOITE_SUPPORTED_DEPARTMENTS),
            # Par défaut, les départements choisis pour la veille (configuration locale).
            default=[
                code
                for code in load_watcher_config().france_travail_departments
                if code in BONNE_BOITE_SUPPORTED_DEPARTMENTS
            ]
            or list(BONNE_BOITE_DEFAULT_DEPARTMENTS),
            format_func=lambda code: f"{code} — {COMPANY_DEPARTMENT_LABELS[code]}",
        )
        rome_codes = st.multiselect(
            "Métiers (codes ROME)",
            options=list(BONNE_BOITE_ROME_CODES),
            default=list(BONNE_BOITE_DEFAULT_ROME_CODES),
            format_func=lambda code: f"{code} — {BONNE_BOITE_ROME_CODES[code]}",
        )
        software_only = st.checkbox(
            "Uniquement les sociétés d'informatique (activité NAF 62.xx)",
            value=False,
            help="Sans ce filtre, les entreprises d'autres secteurs qui recrutent aussi des "
            "développeurs sont affichées (leur activité est indiquée).",
        )
        submitted = st.form_submit_button("Rechercher avec La Bonne Boîte", type="primary")

    if submitted:
        with st.spinner("Interrogation de La Bonne Boîte…"):
            try:
                result = search_bonne_boite(
                    client_id,
                    client_secret,
                    rome_codes=tuple(rome_codes),
                    departments=tuple(departments),
                )
            except JobSourceError as error:
                st.error(str(error))
                return
        st.session_state["lbb_results"] = [
            company.model_dump(mode="json") for company in result.companies
        ]
        st.session_state["lbb_total_hits"] = result.total_hits
        st.session_state["lbb_truncated"] = result.truncated

    results_data = st.session_state.get("lbb_results")
    if results_data is None:
        return
    companies = [BonneBoiteCompany.model_validate(item) for item in results_data]
    if software_only:
        companies = [company for company in companies if company.is_software_activity]
    prospect_keys = {
        (company.name.casefold(), company.location.casefold())
        for company in list_companies(engine)
    }
    saved_sirens = {candidate.siren for candidate in list_company_candidates(engine)}

    flash = st.session_state.pop("lbb_flash", None)
    if flash:
        st.success(flash)
    st.subheader(f"{len(companies)} entreprise(s) classée(s) par potentiel d'embauche")
    if st.session_state.get("lbb_truncated"):
        st.info(
            f"{st.session_state.get('lbb_total_hits', 0)} entreprises correspondent ; seules les "
            "meilleures sont affichées. Restreins les départements ou le métier."
        )
    st.caption("Données La Bonne Boîte — France Travail (licence ouverte).")
    if not companies:
        st.info("Aucune entreprise pour ces critères.")
        return

    for index, company in enumerate(companies):
        email_label = {True: "oui", False: "non", None: "inconnu"}[company.has_known_email]
        with st.expander(
            f"{company.hiring_potential:.0f}/100 · {company.name} — {company.city} "
            f"({company.department})"
        ):
            st.progress(min(max(company.hiring_potential, 0.0), 100.0) / 100)
            st.write(f"**Activité :** {company.naf} — {company.naf_label or 'non précisée'}")
            if not company.is_software_activity:
                st.caption("Hors informatique : l'entreprise peut avoir une équipe interne.")
            st.write(f"**Effectif :** {company.employee_range or 'non renseigné'}")
            st.write(
                f"**Adresse de contact connue de La Bonne Boîte :** {email_label} "
                "(l'adresse n'est pas communiquée)"
            )
            st.markdown(f"[Fiche officielle de l'entreprise]({company.source_url})")
            prospect_key = (company.name.casefold(), company.city.casefold())
            if prospect_key in prospect_keys:
                st.info("Cette entreprise est déjà dans « Entreprises à prospecter ».")
            elif st.button(
                "Ajouter aux entreprises à prospecter",
                key=f"promote_lbb_{index}",
            ):
                try:
                    created = promote_bonne_boite_company(engine, company)
                except DuplicateCompany as error:
                    st.info(str(error))
                else:
                    st.session_state["lbb_flash"] = (
                        f"{created.name} ajoutée à « Entreprises à prospecter »."
                    )
                    st.rerun()
            if company.siren not in saved_sirens and st.button(
                "Garder comme piste à vérifier",
                key=f"save_lbb_{index}",
            ):
                try:
                    save_company_candidate(engine, company.to_candidate())
                except DuplicateCompanyCandidate as error:
                    st.info(str(error))
                else:
                    st.session_state["lbb_flash"] = "Piste enregistrée localement."
                    st.rerun()


def _show_registry_discovery_tab(engine: Engine, profile: ProfileData) -> None:
    st.write(
        "Recherche des sociétés actives d'un département à partir de l'API publique "
        "Recherche d'entreprises. Les pistes ajoutées ici deviennent sélectionnables pour "
        "une candidature spontanée."
    )
    st.warning(
        "Le registre décrit une activité administrative (NAF/APE) ; il ne confirme ni une "
        "équipe de développement en interne, ni un recrutement, ni un contact e-mail. "
        "Les résultats sont des pistes à vérifier avant une candidature spontanée."
    )
    st.caption(
        "Dans un résultat, choisis « Ajouter aux entreprises à prospecter » pour créer une "
        "fiche locale. Elle sera ensuite proposée dans « Candidature spontanée »."
    )
    with st.form("company_registry_search"):
        st.caption(
            "Par défaut, laisse les mots-clés vides : l'application interroge les activités "
            "informatiques (codes NAF/APE) et fusionne les résultats sans doublons."
        )
        query = st.text_input(
            "Mots-clés (facultatif)",
            value="",
            placeholder="Ex. informatique, éditeur logiciel",
            help="Un mot-clé restreint la recherche. Laisse ce champ vide pour parcourir "
            "automatiquement les activités logicielles.",
        )
        # Les départements de la veille sont proposés en premier ; la zone n'est pas dans le code.
        preferred = [
            code
            for code in load_watcher_config().france_travail_departments
            if code in COMPANY_DEPARTMENT_LABELS
        ]
        department_options = preferred + [
            code for code in COMPANY_DEPARTMENT_LABELS if code not in preferred
        ]
        department = st.selectbox(
            "Département",
            options=department_options,
            format_func=lambda code: f"{code} — {COMPANY_DEPARTMENT_LABELS[code]}",
        )
        activity_options: list[str | None] = [None, *SOFTWARE_ACTIVITY_CODES]
        activity_code = st.selectbox(
            "Activité logicielle",
            options=activity_options,
            format_func=lambda value: (
                "Toutes les activités informatiques (recommandé)"
                if value is None
                else f"{value} — {SOFTWARE_ACTIVITY_CODES[value]}"
            ),
        )
        page_number = st.number_input(
            "Page de résultats",
            min_value=1,
            max_value=20,
            value=1,
            step=1,
        )
        submitted = st.form_submit_button("Rechercher des entreprises", type="primary")

    if submitted:
        try:
            with st.spinner("Recherche dans le registre public des entreprises…"):
                candidates = search_company_registry(
                    query,
                    department,
                    activity_code,
                    int(page_number),
                )
        except CompanyRegistryError as error:
            st.error(str(error))
            return
        st.session_state["company_registry_results"] = [
            candidate.model_dump(mode="json") for candidate in candidates
        ]

    results_data = st.session_state.get("company_registry_results", [])
    saved_candidates = list_company_candidates(engine)
    saved_sirens = {candidate.siren for candidate in saved_candidates}
    prospect_companies = list_companies(engine)
    prospect_keys = {
        (company.name.casefold(), company.location.casefold())
        for company in prospect_companies
    }

    def add_candidate_to_prospects(candidate_data: CompanyCandidateData) -> None:
        try:
            company = promote_company_candidate_to_prospect(engine, candidate_data)
        except DuplicateCompany as error:
            st.info(str(error))
        else:
            st.success(
                f"{company.name} ajoutée à « Entreprises à prospecter ». "
                "La fiche précise que l'équipe tech reste à vérifier."
            )
            st.rerun()

    st.subheader(f"Pistes locales enregistrées ({len(saved_sirens)})")
    for candidate in saved_candidates:
        with st.expander(f"{candidate.name} — {candidate.location} · à vérifier"):
            st.write(f"Activité déclarée : {candidate.activity_code}")
            st.markdown(f"[Fiche officielle]({candidate.source_url})")
            st.caption(
                "La présence d'une équipe technique et le recrutement restent à vérifier."
            )
            candidate_data = CompanyCandidateData(
                siren=candidate.siren,
                name=candidate.name,
                location=candidate.location,
                department=candidate.department,
                activity_code=candidate.activity_code,
                employee_range=candidate.employee_range,
                source_url=candidate.source_url,
                activity_api_url=candidate.activity_api_url,
            )
            candidate_fit = assess_company_fit(candidate_data, profile)
            st.write(f"**Activité déclarée :** {candidate_fit.activity_label}")
            prospect_key = (candidate.name.casefold(), candidate.location.casefold())
            if prospect_key in prospect_keys:
                st.info("Cette entreprise est déjà dans « Entreprises à prospecter ».")
            elif st.button(
                "Ajouter aux entreprises à prospecter",
                key=f"promote_saved_candidate_{candidate.siren}",
            ):
                add_candidate_to_prospects(candidate_data)
            if st.button(
                "Retirer cette piste",
                key=f"delete_company_candidate_{candidate.siren}",
            ):
                try:
                    delete_company_candidate(engine, candidate.siren)
                except CompanyRegistryError as error:
                    st.error(str(error))
                else:
                    st.success("Piste retirée de la liste locale.")
                    st.rerun()

    results = [CompanyCandidateData.model_validate(item) for item in results_data]
    st.subheader(f"Résultats de la recherche ({len(results)})")
    if not results:
        if submitted:
            st.info(
                "Aucun résultat pour ces critères. Laisse les mots-clés vides pour lancer "
                "la recherche automatique par activités informatiques, ou essaie un autre "
                "département."
            )
        else:
            st.info("Lance une recherche pour afficher des résultats du registre.")

    for index, candidate in enumerate(results):
        with st.expander(f"{candidate.name} — {candidate.location} ({candidate.department})"):
            st.write(f"**Activité déclarée :** {candidate.activity_code}")
            fit = assess_company_fit(candidate, profile)
            st.caption(f"**Indice profil :** {fit.activity_label}")
            if candidate.employee_range:
                st.write(f"**Tranche d'effectif déclarée :** {candidate.employee_range}")
            else:
                st.write("**Effectif :** non renseigné")
            st.markdown(f"[Fiche officielle de l'entreprise]({candidate.source_url})")
            st.caption(f"Source de recherche : {candidate.activity_api_url}")
            if candidate.siren in saved_sirens:
                st.info("Cette entreprise est déjà conservée dans les pistes à vérifier.")
            prospect_key = (candidate.name.casefold(), candidate.location.casefold())
            if prospect_key in prospect_keys:
                st.info("Cette entreprise est déjà dans « Entreprises à prospecter ».")
            elif st.button(
                "Ajouter aux entreprises à prospecter",
                key=f"promote_company_candidate_{index}",
            ):
                add_candidate_to_prospects(candidate)
            if candidate.siren not in saved_sirens and st.button(
                "Garder comme piste à vérifier",
                key=f"save_company_candidate_{index}",
            ):
                try:
                    save_company_candidate(engine, candidate)
                except DuplicateCompanyCandidate as error:
                    st.info(str(error))
                else:
                    st.success("Piste enregistrée localement.")
                    st.rerun()
