"""Page « Recherche en ligne » : Adzuna, France Travail et sites ciblés."""

from __future__ import annotations

import streamlit as st
from sqlalchemy import Engine
from streamlit.errors import StreamlitSecretNotFoundError

from src.config import get_adzuna_credentials, get_france_travail_credentials
from src.models import JobOfferData, ProfileData
from src.services.companies import list_companies
from src.services.job_offers import DuplicateOfferURL, create_offer, list_offers
from src.services.job_sources.adzuna import (
    ADZUNA_MARKETS,
    PROFILE_SEARCH_SKILL_LIMIT,
    JobSourceError,
    get_profile_search_status,
    listing_title_with_location,
    ordered_profile_terms,
    profile_search_keywords,
    rank_adzuna_results,
    resolve_search_locations,
    start_profile_search,
)
from src.services.job_sources.base import SourceListing
from src.services.job_sources.france_travail import (
    DEPARTMENT_NAMES as FRANCE_TRAVAIL_DEPARTMENT_NAMES,
)
from src.services.job_sources.france_travail import (
    PUBLISHED_SINCE_CHOICES as FRANCE_TRAVAIL_PUBLISHED_SINCE_CHOICES,
)
from src.services.job_sources.france_travail import search_france_travail_for_profile
from src.services.job_sources.greenhouse import (
    GREENHOUSE_DESCRIPTOR,
    extract_greenhouse_board_token,
    fetch_greenhouse_listings,
)
from src.services.job_sources.lever import (
    LEVER_DESCRIPTOR,
    extract_lever_site_slug,
    fetch_lever_listings,
)
from src.services.job_watcher import is_title_excluded, load_watcher_config
from src.services.matching import assess_offer_fit, compare_offer_to_profile, search_languages
from src.ui.commun import _france_travail_secrets, _required_technologies_text, _show_job_board_help


def show_job_search_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Recherche d'offres en ligne")
    adzuna_tab, france_travail_tab, targeted_tab = st.tabs(
        [
            "Adzuna (recherche par mots-clés)",
            "France Travail",
            "Greenhouse & Lever (collecte ciblée)",
        ]
    )
    with adzuna_tab:
        _show_adzuna_search_tab(engine, profile)
    with france_travail_tab:
        _show_france_travail_tab(engine, profile)
    with targeted_tab:
        _show_targeted_job_boards_tab(engine, profile)


def _show_adzuna_search_tab(engine: Engine, profile: ProfileData) -> None:
    st.write(
        "Lance une recherche ponctuelle via l'API Adzuna. Les résultats ne sont enregistrés "
        "dans ta base que si tu choisis explicitement de les importer."
    )
    st.caption(
        "Adzuna renvoie un extrait et un lien de redirection, pas toujours l'annonce complète. "
        "Vérifie les conditions, tarifs et quotas de ton compte avant les recherches."
    )
    try:
        secrets = {
            "ADZUNA_APP_ID": st.secrets.get("ADZUNA_APP_ID", ""),
            "ADZUNA_APP_KEY": st.secrets.get("ADZUNA_APP_KEY", ""),
        }
    except StreamlitSecretNotFoundError:
        secrets = {}
    app_id, app_key = get_adzuna_credentials(secrets)
    if not app_id or not app_key:
        st.warning(
            "Configure ADZUNA_APP_ID et ADZUNA_APP_KEY dans `.streamlit/secrets.toml` "
            "ou dans les variables d'environnement locales avant de rechercher. "
            "Ne colle pas ces valeurs dans le dépôt ou dans la conversation."
        )
        return

    with st.form("adzuna_search"):
        search_scope = st.radio(
            "Périmètre",
            ["Zone de recherche locale", "Europe — choisir les pays"],
            horizontal=True,
        )
        if search_scope == "Zone de recherche locale":
            selected_locations = resolve_search_locations(
                profile, load_watcher_config().adzuna_locations
            )
            zones = ", ".join(selected_locations) or "toute la France"
            st.caption(
                f"Zones interrogées : {zones}. Elles se règlent dans la page Veille ou dans "
                "la zone de ton profil. Les annonces de Paris sont classées après les autres ; "
                "le score de correspondance les départage dans chaque groupe."
            )
            selected_countries = ["fr"]
        else:
            st.caption(
                "Adzuna ne propose pas une recherche continentale unique : sélectionne les "
                "marchés européens ou le Canada. Les annonces dont la langue détectée n'est "
                "pas le français ou l'anglais (ou dont la langue est indéterminée) sont écartées."
            )
            selected_countries = st.multiselect(
                "Pays (une requête par pays)",
                options=list(ADZUNA_MARKETS),
                default=[
                    "at",
                    "be",
                    "ca",
                    "ch",
                    "de",
                    "es",
                    "fr",
                    "gb",
                    "ie",
                    "it",
                    "nl",
                    "pl",
                ],
                format_func=ADZUNA_MARKETS.__getitem__,
            )
            selected_locations = [""]
        page_number = st.number_input(
            "Page de résultats",
            min_value=1,
            max_value=50,
            value=1,
            step=1,
        )
        search_submitted = st.form_submit_button("Rechercher sur Adzuna", type="primary")

    st.caption(
        f"Jusqu'à {PROFILE_SEARCH_SKILL_LIMIT} compétences prioritaires et le poste visé font "
        "chacun l'objet d'une "
        "requête séparée ; elles ne sont donc pas exigées simultanément dans une annonce. "
        "Les langages que tu maîtrises assez (niveau 5 ou plus au profil) sont cherchés en "
        "premier. Le classement mesure ta maîtrise des technologies que chaque annonce "
        "demande. Les requêtes s'exécutent en parallèle avec au plus quatre appels actifs."
    )
    if profile.skills:
        ordered_terms = ordered_profile_terms(profile)
        st.write("**Compétences classées par priorité :** " + ", ".join(ordered_terms))
        languages = search_languages(profile)
        if languages:
            st.write("**Langages cherchés en premier :** " + ", ".join(languages))
    else:
        st.warning("Ajoute tes compétences au profil pour obtenir un classement personnalisé.")

    if search_submitted:
        targets = [
            (country, location)
            for country in selected_countries
            for location in selected_locations
        ]
        query_count = sum(
            len(profile_search_keywords(profile, country))
            for country, _location in targets
        )
        try:
            job_id = start_profile_search(
                profile,
                targets,
                app_id,
                app_key,
                int(page_number),
            )
        except JobSourceError as error:
            st.error(str(error))
            return
        st.session_state["adzuna_search_job_id"] = job_id
        st.session_state["adzuna_pending_scope"] = search_scope
        st.session_state["adzuna_pending_query_count"] = query_count

    pending_job_id = st.session_state.get("adzuna_search_job_id")
    if pending_job_id:
        if st.button("Actualiser l'état de la recherche", key="refresh_adzuna_search"):
            st.rerun()
        try:
            job_status = get_profile_search_status(pending_job_id)
        except JobSourceError as error:
            st.error(str(error))
            st.session_state.pop("adzuna_search_job_id", None)
        else:
            if job_status.is_running:
                st.info(
                    "Recherche en cours en arrière-plan — jusqu'à "
                    f"{st.session_state.get('adzuna_pending_query_count', 0)} appels, "
                    "avec quatre requêtes maximum simultanées. Tu peux naviguer ailleurs "
                    "et revenir ici ; utilise « Actualiser » pour vérifier l'avancement."
                )
            elif job_status.error:
                st.error(job_status.error)
                st.session_state.pop("adzuna_search_job_id", None)
            elif job_status.result is not None:
                search_result = job_status.result
                st.session_state["adzuna_results"] = [
                    listing.model_dump(mode="json") for listing in search_result.listings
                ]
                st.session_state["adzuna_search_scope"] = st.session_state.pop(
                    "adzuna_pending_scope",
                    "",
                )
                st.session_state["adzuna_failed_targets"] = list(
                    search_result.failed_targets
                )
                st.session_state["adzuna_searched_targets"] = list(
                    search_result.searched_targets
                )
                st.session_state["adzuna_search_terms"] = list(search_result.search_terms)
                st.session_state["adzuna_filtered_language_count"] = (
                    search_result.filtered_language_count
                )
                st.session_state.pop("adzuna_search_job_id", None)
                st.session_state.pop("adzuna_pending_query_count", None)
                st.rerun()

    results_data = st.session_state.get("adzuna_results", [])
    failed_targets = st.session_state.get("adzuna_failed_targets", [])
    filtered_language_count = st.session_state.get("adzuna_filtered_language_count", 0)
    if filtered_language_count:
        st.info(
            f"{filtered_language_count} annonce(s) écartée(s), car leur langue détectée "
            "n'était ni le français ni l'anglais."
        )
    for target, message in failed_targets:
        st.warning(f"Recherche pour « {target} » non aboutie : {message}")
    if not results_data:
        if search_submitted:
            st.info(
                "Adzuna n'a retourné aucune annonce pour ces zones et cette recherche. "
                "Essaie une autre page, vérifie les termes envoyés ou consulte le tableau de "
                "bord Adzuna pour confirmer que ce marché est activé sur ton compte."
            )
        return
    results = [SourceListing.model_validate(item) for item in results_data]
    saved_urls = {offer.url for offer in list_offers(engine) if offer.url}
    already_saved_count = sum(
        str(listing.original_url) in saved_urls for listing in results
    )
    results = [
        listing for listing in results if str(listing.original_url) not in saved_urls
    ]
    if already_saved_count:
        st.info(
            f"{already_saved_count} annonce(s) déjà enregistrée(s) masquée(s) des résultats. "
            "Adzuna les a tout de même renvoyées ; elles sont filtrées ici, après l'appel API."
        )
    if not results:
        st.info("Toutes les annonces trouvées sont déjà enregistrées localement.")
        return
    result_scope = st.session_state.get("adzuna_search_scope", "")
    searched_targets = st.session_state.get("adzuna_searched_targets", [])
    st.subheader(f"{len(results)} résultats Adzuna")
    st.caption("Zones interrogées : " + ", ".join(searched_targets))
    ranked_results = rank_adzuna_results(
        results,
        profile,
        prioritize_local_areas=result_scope == "Zone de recherche locale",
    )
    if result_scope == "Europe — choisir les pays":
        st.warning(
            "Une annonce à l'étranger ne garantit ni le télétravail intégral ni une aide au "
            "déménagement. Vérifie le lieu de travail, les conditions de mobilité et le droit "
            "au travail sur l'annonce d'origine."
        )

    for index, listing in enumerate(ranked_results):
        match = compare_offer_to_profile(
            JobOfferData(
                title=listing.title,
                description=listing.description or "Aucun extrait fourni.",
            ),
            profile,
        )
        score_label = (
            f"{match.match_percentage}%"
            if match.match_percentage is not None
            else "non calculé"
        )
        display_title = listing_title_with_location(listing)
        with st.expander(
            f"{score_label} de correspondance · {display_title} — "
            f"{listing.company or 'Employeur non précisé'}"
        ):
            if match.match_percentage is not None:
                st.progress(match.match_percentage / 100)
                st.caption(
                    f"{match.mentioned_count} compétence(s) sur {len(match.matches)} "
                    "mentionnée(s) dans le titre ou l'extrait Adzuna. Le score est ta maîtrise "
                    "moyenne des technologies demandées (un extrait tronqué en révèle peu) ; "
                    "il ne mesure pas l'adéquation globale au poste."
                )
                technologies_caption = _required_technologies_text(match)
                if technologies_caption:
                    st.caption(technologies_caption)
                for skill_match in match.matches:
                    if skill_match.mentioned:
                        st.write(
                            f"- **{skill_match.skill.name}** — {skill_match.priority} · "
                            f"{skill_match.evidence}"
                        )
            st.write(f"**Lieu :** {listing.location or 'Non précisé'}")
            if listing.contract_type:
                st.write(f"**Contrat estimé :** {listing.contract_type}")
            st.write(listing.description or "Aucun extrait fourni par la source.")
            st.markdown(f"[Ouvrir l'annonce originale]({listing.original_url})")
            if str(listing.original_url) in saved_urls:
                st.caption("Cette URL est déjà enregistrée dans tes offres.")
            elif st.button("Importer cette offre localement", key=f"import_adzuna_{index}"):
                try:
                    create_offer(
                        engine,
                        JobOfferData(
                            title=listing.title,
                            company=listing.company,
                            location=listing.location,
                            contract_type=listing.contract_type,
                            url=str(listing.original_url),
                            source=f"Adzuna ({listing.country_code})",
                            description=listing.description
                            or "Adzuna n'a pas fourni d'extrait pour cette annonce.",
                        ),
                    )
                except DuplicateOfferURL:
                    st.info("Cette annonce a déjà été importée.")
                else:
                    st.success("Annonce enregistrée localement avec son lien d'origine.")
                    st.rerun()


def _france_travail_offer_data(listing: SourceListing) -> JobOfferData:
    description = listing.description or "France Travail n'a pas fourni de texte pour cette offre."
    if listing.public_contact_email:
        description = f"Contact public : {listing.public_contact_email}\n\n{description}"
    return JobOfferData(
        title=listing.title,
        company=listing.company,
        location=listing.location,
        contract_type=listing.contract_type,
        url=str(listing.original_url),
        source="France Travail",
        description=description,
    )


def _show_france_travail_tab(engine: Engine, profile: ProfileData) -> None:
    st.write(
        "Recherche les offres publiées sur France Travail via son API officielle. Les "
        "annonces comportent le texte complet (contrairement aux extraits Adzuna) ; elles ne "
        "sont enregistrées que si tu les importes."
    )
    try:
        secrets = _france_travail_secrets()
    except StreamlitSecretNotFoundError:
        secrets = {}
    client_id, client_secret = get_france_travail_credentials(secrets)
    if not client_id or not client_secret:
        st.warning(
            "Crée un compte sur [francetravail.io](https://francetravail.io/inscription), "
            "déclare une application abonnée à l'API « Offres d'emploi », puis ajoute "
            "FRANCE_TRAVAIL_CLIENT_ID et FRANCE_TRAVAIL_CLIENT_SECRET dans "
            "`.streamlit/secrets.toml` ou dans les variables d'environnement locales. "
            "Ne colle pas ces valeurs dans le dépôt ou dans la conversation."
        )
        return

    flash = st.session_state.pop("ft_flash", None)
    if flash:
        st.success(flash)
    watcher_cfg = load_watcher_config()
    with st.form("france_travail_search"):
        departments = st.multiselect(
            "Départements",
            options=list(FRANCE_TRAVAIL_DEPARTMENT_NAMES),
            default=[
                code
                for code in watcher_cfg.france_travail_departments
                if code in FRANCE_TRAVAIL_DEPARTMENT_NAMES
            ],
            format_func=lambda code: f"{code} — {FRANCE_TRAVAIL_DEPARTMENT_NAMES[code]}",
        )
        published_since = st.selectbox(
            "Offres publiées depuis",
            options=list(FRANCE_TRAVAIL_PUBLISHED_SINCE_CHOICES),
            index=list(FRANCE_TRAVAIL_PUBLISHED_SINCE_CHOICES).index(14),
            format_func=lambda days: f"{days} jour{'s' if days > 1 else ''}",
        )
        cdi_only = st.checkbox("CDI uniquement", value=watcher_cfg.france_travail_cdi_only)
        submitted = st.form_submit_button("Rechercher sur France Travail", type="primary")

    if profile.skills:
        st.caption(
            "Une requête par compétence prioritaire et par département ; elles ne sont pas "
            "exigées simultanément. Termes : " + ", ".join(ordered_profile_terms(profile))
        )

    if submitted:
        with st.spinner("Interrogation de France Travail..."):
            try:
                result = search_france_travail_for_profile(
                    profile,
                    client_id,
                    client_secret,
                    departments=tuple(departments),
                    contract_codes=("CDI",) if cdi_only else (),
                    published_since_days=published_since,
                )
            except JobSourceError as error:
                st.error(str(error))
                return
        st.session_state["ft_results"] = [
            listing.model_dump(mode="json") for listing in result.listings
        ]
        st.session_state["ft_failed"] = list(result.failed_targets)
        st.session_state["ft_searched"] = list(result.searched_targets)

    for target, message in st.session_state.get("ft_failed", []):
        st.warning(f"Recherche pour « {target} » non aboutie : {message}")
    results_data = st.session_state.get("ft_results")
    if results_data is None:
        return

    saved_urls = {offer.url for offer in list_offers(engine) if offer.url}
    listings = [SourceListing.model_validate(item) for item in results_data]
    new_listings = [item for item in listings if str(item.original_url) not in saved_urls]
    if len(new_listings) < len(listings):
        st.info(f"{len(listings) - len(new_listings)} offre(s) déjà enregistrée(s) masquée(s).")
    if not new_listings:
        st.info("Aucune nouvelle offre pour ces critères.")
        return

    scored = sorted(
        (
            (
                assess_offer_fit(_france_travail_offer_data(item), profile).overall_percentage
                or 0,
                item,
            )
            for item in new_listings
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    st.subheader(f"{len(scored)} nouvelle(s) offre(s) France Travail")
    st.caption("Zones : " + ", ".join(st.session_state.get("ft_searched", [])))
    importable = [
        item
        for score, item in scored
        if score >= watcher_cfg.min_match_percentage
        and not is_title_excluded(item.title, watcher_cfg)
    ]
    if importable and st.button(
        f"Importer les {len(importable)} offre(s) au-dessus du seuil "
        f"({watcher_cfg.min_match_percentage} %, hors titres exclus)"
    ):
        imported = 0
        for item in importable:
            try:
                create_offer(engine, _france_travail_offer_data(item))
                imported += 1
            except DuplicateOfferURL:
                continue
        st.session_state["ft_results"] = [
            row for row in results_data if SourceListing.model_validate(row) not in importable
        ]
        st.session_state["ft_flash"] = (
            f"{imported} offre(s) importée(s). Retrouve-les dans la file d'envoi."
        )
        st.rerun()

    for index, (score, item) in enumerate(scored):
        with st.expander(
            f"{score} % · {item.title} — {item.company or 'Employeur non précisé'} "
            f"· {item.location or 'Lieu non précisé'}"
        ):
            st.progress(score / 100)
            if item.contract_type:
                st.write(f"**Contrat :** {item.contract_type}")
            if item.public_contact_email:
                st.write(f"**Contact public :** {item.public_contact_email}")
            st.write(item.description or "Aucun texte fourni par la source.")
            st.markdown(f"[Ouvrir l'annonce originale]({item.original_url})")
            st.caption("Source : France Travail.")
            if st.button("Importer cette offre localement", key=f"import_ft_{index}"):
                try:
                    create_offer(engine, _france_travail_offer_data(item))
                except DuplicateOfferURL:
                    st.info("Cette annonce a déjà été importée.")
                else:
                    st.success("Annonce enregistrée localement avec son lien d'origine.")
                    st.rerun()


def _show_targeted_job_boards_tab(engine: Engine, profile: ProfileData) -> None:
    st.write(
        "Lis les offres publiées par **une entreprise précise** sur sa page carrière "
        "Greenhouse ou Lever, via leurs API publiques officielles."
    )
    _show_job_board_help()
    st.caption(
        f"Connecteurs officiels en lecture seule : [{GREENHOUSE_DESCRIPTOR.display_name}]"
        f"({GREENHOUSE_DESCRIPTOR.documentation_url}) et [{LEVER_DESCRIPTOR.display_name}]"
        f"({LEVER_DESCRIPTOR.documentation_url}). "
        "Sans clé API ni scraping. Les offres ne sont enregistrées que sur action explicite."
    )

    registered_companies = list_companies(engine)
    company_choices = ["(Déduire automatiquement de l'identifiant)"] + [
        c.name for c in registered_companies
    ]

    with st.form("targeted_ats_search"):
        platform = st.radio(
            "Plateforme de recrutement",
            ["Greenhouse", "Lever"],
            horizontal=True,
            key="targeted_platform_choice",
        )
        col1, col2 = st.columns(2)
        with col1:
            if platform == "Greenhouse":
                board_input = st.text_input(
                    "Identifiant ou URL du tableau Greenhouse",
                    placeholder="Ex. ateliertech ou https://boards.greenhouse.io/ateliertech",
                    help="Indique le slug ou l'URL complète du tableau d'offres Greenhouse.",
                )
            else:
                board_input = st.text_input(
                    "Identifiant ou URL du site Lever",
                    placeholder="Ex. exampleco ou https://jobs.lever.co/exampleco",
                    help="Indique le slug ou l'URL du site d'offres Lever.",
                )
        with col2:
            chosen_company = st.selectbox(
                "Associer à une entreprise enregistrée",
                options=company_choices,
                help=(
                    "Sélectionne une entreprise existante pour lier automatiquement "
                    "les offres importées."
                ),
            )
            if chosen_company == "(Déduire automatiquement de l'identifiant)":
                custom_company_name = st.text_input(
                    "Nom de l'entreprise (facultatif si non enregistrée)",
                    placeholder="Ex. Atelier Tech",
                )
            else:
                custom_company_name = ""

        use_eu = False
        if platform == "Lever":
            use_eu = st.checkbox(
                "Hébergement européen Lever (api.eu.lever.co)",
                help=(
                    "À cocher si le site Lever est hébergé en Europe "
                    "(détecté aussi si tu colles une URL en eu.lever.co)."
                ),
            )

        filter_kw = st.text_input(
            "Filtrer par mot-clé dans les résultats (facultatif)",
            placeholder="Ex. Symfony, backend, remote, CDI...",
            help="Ne conserve que les offres dont le titre ou le texte contient ce mot-clé.",
        )

        search_submitted = st.form_submit_button(
            "Collecter les annonces", type="primary"
        )

    if search_submitted:
        clean_input = board_input.strip()
        if not clean_input:
            st.error(f"Veuillez renseigner un identifiant ou une URL pour {platform}.")
            return

        company_name_to_use = (
            chosen_company
            if chosen_company != "(Déduire automatiquement de l'identifiant)"
            else custom_company_name.strip()
        )

        if platform == "Greenhouse":
            target_id = extract_greenhouse_board_token(clean_input)
            with st.spinner(f"Interrogation de l'API publique Greenhouse pour « {target_id} »..."):
                try:
                    listings = fetch_greenhouse_listings(
                        target_id, company_name=company_name_to_use
                    )
                except JobSourceError as error:
                    st.error(str(error))
                    return
        else:
            target_id = extract_lever_site_slug(clean_input)
            with st.spinner(f"Interrogation de l'API publique Lever pour « {target_id} »..."):
                try:
                    listings = fetch_lever_listings(
                        target_id,
                        company_name=company_name_to_use,
                        use_eu_endpoint=use_eu,
                    )
                except JobSourceError as error:
                    st.error(str(error))
                    return

        st.session_state["targeted_listings"] = [
            listing.model_dump(mode="json") for listing in listings
        ]
        st.session_state["targeted_platform"] = platform
        st.session_state["targeted_target_id"] = target_id
        st.session_state["targeted_company"] = (
            company_name_to_use or target_id.replace("-", " ").title()
        )
        st.session_state["targeted_filter_kw"] = filter_kw.strip()
        st.rerun()

    raw_listings = st.session_state.get("targeted_listings")
    if raw_listings is None:
        return

    all_listings = [SourceListing.model_validate(item) for item in raw_listings]
    platform_name = st.session_state.get("targeted_platform", "ATS")
    target_id = st.session_state.get("targeted_target_id", "")
    active_filter_kw = st.session_state.get("targeted_filter_kw", "")

    if active_filter_kw:
        listings = [
            item
            for item in all_listings
            if active_filter_kw.casefold()
            in f"{item.title}\n{item.description}\n{item.location}".casefold()
        ]
    else:
        listings = all_listings

    if not listings:
        st.info(
            f"Aucune offre active ne correspond pour « {target_id} »"
            + (f" avec le filtre « {active_filter_kw} »." if active_filter_kw else ".")
        )
        return

    saved_urls = {offer.url for offer in list_offers(engine) if offer.url}
    already_saved_count = sum(
        str(listing.original_url) in saved_urls for listing in listings
    )

    def sort_targeted(item: SourceListing) -> int:
        match = compare_offer_to_profile(
            JobOfferData(
                title=item.title,
                description=item.description or "Aucun extrait fourni.",
            ),
            profile,
        )
        return match.match_percentage or 0

    ranked_listings = sorted(listings, key=sort_targeted, reverse=True)

    st.subheader(
        f"{len(ranked_listings)} offre(s) publiée(s) sur {platform_name} ({target_id})"
    )
    if already_saved_count > 0:
        st.info(f"{already_saved_count} offre(s) déjà enregistrée(s) dans votre base locale.")

    unsaved_listings = [
        item for item in ranked_listings if str(item.original_url) not in saved_urls
    ]
    if unsaved_listings:
        if st.button(
            f"Importer toutes les offres non enregistrées ({len(unsaved_listings)})",
            key="import_all_targeted_btn",
        ):
            imported_count = 0
            for item in unsaved_listings:
                try:
                    create_offer(
                        engine,
                        JobOfferData(
                            title=item.title,
                            company=item.company,
                            location=item.location,
                            contract_type=item.contract_type,
                            url=str(item.original_url),
                            source=f"{platform_name} ({target_id})",
                            description=item.description
                            or f"{platform_name} n'a pas fourni de description.",
                        ),
                    )
                    imported_count += 1
                except DuplicateOfferURL:
                    pass
            st.success(
                f"{imported_count} offre(s) importée(s) avec succès dans votre base !"
            )
            st.rerun()

    for index, listing in enumerate(ranked_listings):
        match = compare_offer_to_profile(
            JobOfferData(
                title=listing.title,
                description=listing.description or "Aucun extrait fourni.",
            ),
            profile,
        )
        score_label = (
            f"{match.match_percentage}%"
            if match.match_percentage is not None
            else "non calculé"
        )
        is_saved = str(listing.original_url) in saved_urls
        status_suffix = " · [Déjà importée]" if is_saved else ""
        with st.expander(
            f"{score_label} de correspondance · {listing.title} — "
            f"{listing.company or target_id}{status_suffix}"
        ):
            if match.match_percentage is not None:
                st.progress(match.match_percentage / 100)
                st.caption(
                    f"{match.mentioned_count} compétence(s) sur {len(match.matches)} "
                    "mentionnée(s) dans le descriptif."
                )
                technologies_caption = _required_technologies_text(match)
                if technologies_caption:
                    st.caption(technologies_caption)
                for skill_match in match.matches:
                    if skill_match.mentioned:
                        st.write(
                            f"- **{skill_match.skill.name}** — {skill_match.priority} · "
                            f"{skill_match.evidence}"
                        )
            st.write(f"**Lieu :** {listing.location or 'Non précisé'}")
            if listing.contract_type:
                st.write(f"**Contrat estimé :** {listing.contract_type}")
            if listing.relocation_signal == "mentioned":
                st.success(
                    f"**Mention de relocalisation détectée :** "
                    f"{listing.relocation_evidence}"
                )
            if listing.public_contact_email:
                st.info(
                    f"**Contact public extrait :** {listing.public_contact_email}"
                )
                if listing.contact_source_url:
                    st.caption(f"Source du contact : {listing.contact_source_url}")
            with st.expander("Voir le texte de l'annonce", expanded=False):
                st.text(listing.description)
            st.markdown(
                f"[Ouvrir l'annonce officielle sur {platform_name}]({listing.original_url})"
            )
            if is_saved:
                st.caption("Cette annonce est déjà enregistrée dans votre base d'offres.")
            elif st.button(
                "Importer cette offre localement", key=f"import_targeted_{index}"
            ):
                try:
                    create_offer(
                        engine,
                        JobOfferData(
                            title=listing.title,
                            company=listing.company,
                            location=listing.location,
                            contract_type=listing.contract_type,
                            url=str(listing.original_url),
                            source=f"{platform_name} ({target_id})",
                            description=listing.description
                            or f"{platform_name} n'a pas fourni de description.",
                        ),
                    )
                except DuplicateOfferURL:
                    st.info("Cette offre est déjà enregistrée.")
                else:
                    st.success("Offre enregistrée localement avec succès !")
                    st.rerun()
