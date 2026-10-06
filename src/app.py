from __future__ import annotations

import base64
import hashlib
import json

import streamlit as st
import streamlit.components.v1 as components
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from streamlit.errors import StreamlitSecretNotFoundError

from src.config import (
    get_adzuna_credentials,
    get_france_travail_credentials,
    get_mailjet_settings,
)
from src.db import (
    create_database_engine,
    initialize_database,
    load_or_seed_profile,
    save_profile,
)
from src.models import (
    ApplicationData,
    ApplicationStatus,
    Company,
    CompanyCandidate,
    CompanyCandidateData,
    CompanyData,
    JobOfferData,
    OfferStatus,
    ProfileData,
    SkillCategory,
    SkillRating,
    normalize_job_title,
)
from src.services.applications import (
    ApplicationNotFound,
    ApplicationReferenceNotFound,
    create_application,
    delete_application,
    list_applications,
    update_application,
)
from src.services.companies import (
    CompanyInUse,
    CompanyNotFound,
    DuplicateCompany,
    create_company,
    delete_company,
    list_companies,
    update_company,
)
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
from src.services.cover_letters import (
    CoverLetterError,
    build_cover_letter,
    get_cover_letter,
    render_cover_letter_pdf,
    save_cover_letter,
    validate_cover_letter_content,
)
from src.services.cv_renderer import (
    load_base_cv_data,
    render_cv_html,
    render_cv_pdf,
    save_base_cv_json,
    tailored_cv_filename,
    validate_base_cv_json,
)
from src.services.dossier_generator import prepare_dossier_for_offer, prepare_pending_dossiers
from src.services.email_delivery import EmailDeliveryError, send_application_email
from src.services.job_offers import (
    DuplicateOfferURL,
    OfferNotFound,
    create_offer,
    delete_offer,
    list_offers,
    update_offer,
)
from src.services.job_sources.adzuna import (
    ADZUNA_MARKETS,
    LOCAL_SEARCH_LOCATIONS,
    PROFILE_SEARCH_SKILL_LIMIT,
    JobSourceError,
    get_profile_search_status,
    listing_title_with_location,
    ordered_profile_terms,
    profile_search_keywords,
    rank_adzuna_results,
    start_profile_search,
)
from src.services.job_sources.base import SourceListing
from src.services.job_sources.bonne_boite import (
    DEFAULT_DEPARTMENTS as BONNE_BOITE_DEFAULT_DEPARTMENTS,
)
from src.services.job_sources.bonne_boite import (
    DEFAULT_ROME_CODES as BONNE_BOITE_DEFAULT_ROME_CODES,
)
from src.services.job_sources.bonne_boite import (
    ROME_CODES as BONNE_BOITE_ROME_CODES,
)
from src.services.job_sources.bonne_boite import (
    SUPPORTED_DEPARTMENTS as BONNE_BOITE_SUPPORTED_DEPARTMENTS,
)
from src.services.job_sources.bonne_boite import (
    BonneBoiteCompany,
    promote_bonne_boite_company,
    search_bonne_boite,
)
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
from src.services.job_watcher import (
    MonitoredTarget,
    find_excluded_keyword,
    get_global_watcher,
    load_watcher_config,
    run_watcher_cycle,
    save_watcher_config,
)
from src.services.matching import (
    OfferMatch,
    assess_company_fit,
    assess_offer_fit,
    compare_offer_to_profile,
    search_languages,
)
from src.services.offer_import import OfferImportError, import_offer_from_url
from src.services.resume_import import (
    ResumeImportError,
    extract_resume_text,
    list_resume_versions,
    save_resume_version,
)
from src.services.send_queue import (
    FOLLOW_UP_DAYS,
    OfferAlreadySent,
    QueueItem,
    SpontaneousItem,
    build_send_queue,
    build_spontaneous_queue,
    contact_search_links,
    due_follow_ups,
    mark_followed_up,
    mark_offer_sent,
    mark_spontaneous_sent,
    offer_to_data,
    set_offer_status,
)
from src.services.tailored_resumes import (
    TailoredResumeError,
    build_tailored_resume,
    get_tailored_resume,
    relocation_question_for_offer,
    render_tailored_resume_pdf,
    save_tailored_resume,
    validate_tailored_resume_json,
)

st.set_page_config(page_title="Assistant candidatures", page_icon="🧭", layout="wide")

CONTRACT_TYPES = ["CDI", "CDD", "Freelance", "Stage", "Alternance", "Autre"]
SKILL_CATEGORIES: list[SkillCategory] = [
    "Forte",
    "Intermédiaire",
    "En développement",
    "IA et nouvelles technologies",
]
OFFER_STATUSES: list[OfferStatus] = [
    "À examiner",
    "Intéressante",
    "Écartée",
    "Candidature liée",
]
APPLICATION_STATUSES: list[ApplicationStatus] = [
    "À préparer",
    "Prête à envoyer",
    "Envoyée",
    "Entretien",
    "Refusée",
    "Retirée",
]


@st.cache_resource
def get_engine() -> Engine:
    engine = create_database_engine()
    initialize_database(engine)
    return engine


def show_home(profile: ProfileData) -> None:
    st.title("Assistant candidatures")
    st.write(
        "Un espace local pour organiser votre recherche d'emploi, analyser des offres "
        "et préparer des candidatures adaptées."
    )
    st.info(
        "Vos données de profil sont enregistrées localement. Aucun CV ni renseignement "
        "personnel n'est envoyé à un service distant."
    )
    offer_count = len(list_offers(get_engine()))
    company_count = len(list_companies(get_engine()))
    company_candidate_count = len(list_company_candidates(get_engine()))
    application_count = len(list_applications(get_engine()))
    metric_columns = st.columns(4)
    metric_columns[0].metric("Offres enregistrées", offer_count)
    metric_columns[1].metric("Fiches entreprises", company_count)
    metric_columns[2].metric("Pistes registre à vérifier", company_candidate_count)
    metric_columns[3].metric("Candidatures suivies", application_count)
    if offer_count == 0:
        st.info(
            "Aucune offre n'est préchargée : ajoute-en une manuellement ou connecte une "
            "source autorisée."
        )

    if not profile.target_role:
        st.warning("Commencez par compléter votre profil dans la rubrique Profil.")
        return

    st.subheader("Profil de recherche")
    st.write(profile.target_role)
    first, second, third = st.columns(3)
    with first:
        years = "Non renseigné"
        if (
            profile.experience_min_years is not None
            and profile.experience_max_years is not None
        ):
            years = f"{profile.experience_min_years:g}–{profile.experience_max_years:g} ans"
        st.metric("Expérience", years)
    with second:
        st.metric("Compétences suivies", len(profile.skills))
    with third:
        st.metric(
            "Contrat privilégié",
            ", ".join(profile.preferred_contracts) or "Non renseigné",
        )
    st.write(f"**Zone :** {', '.join(profile.local_locations) or 'À définir'}")
    if profile.remote_only_outside_local_area:
        st.caption(
            "Hors de la zone indiquée, seuls les postes entièrement en télétravail sont visés."
        )
    st.subheader("Prochaines étapes")
    st.write(
        "Ajoutez vos premières offres manuellement, puis nous pourrons construire le "
        "comparatif explicable entre une annonce et votre profil."
    )


def show_profile_form(engine: Engine, profile: ProfileData) -> None:
    st.title("Profil de recherche")
    st.caption(
        "Les niveaux de compétences sont vos auto-évaluations ; ils servent à organiser "
        "la recherche et ne constituent pas des évaluations objectives."
    )
    errors: list[str] = []
    with st.form("profile_form"):
        target_role = st.text_input("Intitulé ou profil recherché", value=profile.target_role)
        first, second = st.columns(2)
        with first:
            experience_min = st.number_input(
                "Expérience minimum (années)",
                min_value=0.0,
                max_value=60.0,
                step=0.5,
                value=profile.experience_min_years or 0.0,
            )
        with second:
            experience_max = st.number_input(
                "Expérience maximum (années)",
                min_value=0.0,
                max_value=60.0,
                step=0.5,
                value=profile.experience_max_years or 0.0,
            )

        preferred_contracts = st.multiselect(
            "Types de contrat",
            options=CONTRACT_TYPES,
            default=profile.preferred_contracts,
        )
        locations = st.text_input(
            "Zone géographique locale (séparée par des virgules)",
            value=", ".join(profile.local_locations),
            help="Par exemple : Nice, Cannes, Var",
        )
        remote_only_outside_local_area = st.checkbox(
            "Hors de cette zone, ne retenir que les postes entièrement en télétravail",
            value=profile.remote_only_outside_local_area,
        )

        skill_rows = [
            {
                "Compétence": skill.name,
                "Catégorie": skill.category,
                "Niveau min": skill.level_min,
                "Niveau max": skill.level_max,
            }
            for skill in profile.skills
        ]
        if not skill_rows:
            skill_rows = [
                {
                    "Compétence": "",
                    "Catégorie": "Forte",
                    "Niveau min": 0.0,
                    "Niveau max": 0.0,
                }
            ]
        edited_skills = st.data_editor(
            skill_rows,
            num_rows="dynamic",
            use_container_width=True,
            key="profile_skills",
            column_config={
                "Compétence": st.column_config.TextColumn("Compétence"),
                "Catégorie": st.column_config.SelectboxColumn(
                    "Catégorie", options=SKILL_CATEGORIES, required=True
                ),
                "Niveau min": st.column_config.NumberColumn(
                    "Niveau min", min_value=0.0, max_value=10.0, step=0.5
                ),
                "Niveau max": st.column_config.NumberColumn(
                    "Niveau max", min_value=0.0, max_value=10.0, step=0.5
                ),
            },
        )
        st.caption(
            "Les compétences déjà enregistrées sont chargées automatiquement. La catégorie "
            "et le niveau servent à définir leur priorité ; elles seront utilisées dans les "
            "recherches et le classement des offres."
        )
        submitted = st.form_submit_button("Enregistrer le profil", type="primary")

    if submitted:
        try:
            skill_data = []
            for row in _data_editor_records(edited_skills):
                name = str(row.get("Compétence", "")).strip()
                if not name:
                    continue
                skill_data.append(
                    SkillRating(
                        name=name,
                        category=row["Catégorie"],
                        level_min=row["Niveau min"],
                        level_max=row["Niveau max"],
                    )
                )
            profile_data = ProfileData(
                target_role=target_role,
                experience_min_years=experience_min,
                experience_max_years=experience_max,
                preferred_contracts=preferred_contracts,
                local_locations=[location.strip() for location in locations.split(",")],
                remote_only_outside_local_area=remote_only_outside_local_area,
                skills=skill_data,
            )
            save_profile(engine, profile_data)
        except ValidationError as error:
            errors = [issue["msg"] for issue in error.errors()]
        except ValueError as error:
            errors = [str(error)]
        else:
            st.success("Profil enregistré localement.")
            st.rerun()

    for error in errors:
        st.error(error)


def _data_editor_records(value: object) -> list[dict[str, object]]:
    if isinstance(value, list):
        rows: object = value
    else:
        to_dict = getattr(value, "to_dict", None)
        if not callable(to_dict):
            raise ValueError("Le tableau des compétences a renvoyé un format inattendu.")
        rows = to_dict(orient="records")

    if not isinstance(rows, list):
        raise ValueError("Les lignes du tableau des compétences sont invalides.")
    records: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Une ligne du tableau des compétences est invalide.")
        records.append(row)
    return records


def show_resume_page(engine: Engine) -> None:
    st.title("CV de référence")
    st.write(
        "Sélectionnez vous-même un fichier DOCX ou PDF textuel. Le document et le texte "
        "vérifié seront enregistrés uniquement dans le dossier local de l'application."
    )
    st.caption(
        "Taille maximale : 10 Mo. Les PDF scannés, protégés par mot de passe et l'OCR "
        "ne sont pas pris en charge."
    )
    st.subheader("CV structuré de base")
    st.caption(
        "Ce contenu est la base des nouveaux CV personnalisés. Le modifier ne change pas "
        "les brouillons déjà enregistrés pour des offres."
    )
    if "base_cv_json_editor" not in st.session_state:
        st.session_state["base_cv_json_editor"] = json.dumps(
            load_base_cv_data(), ensure_ascii=False, indent=2
        )
    edited_base_json = st.text_area(
        "Données du CV de base (JSON)",
        key="base_cv_json_editor",
        height=460,
        help=(
            "Modifie le profil, les contacts, les expériences (role, company, period, bullets), "
            "la formation ou les compétences. Pour ajouter une expérience, ajoute un objet "
            "dans la liste experience."
        ),
    )
    save_base_cv = st.button("Enregistrer le CV de base", type="primary")
    try:
        normalized_base_json = validate_base_cv_json(edited_base_json)
    except ValueError as error:
        st.error(str(error))
    else:
        if save_base_cv:
            try:
                normalized_base_json = save_base_cv_json(edited_base_json)
            except (OSError, ValueError) as error:
                st.error(f"Impossible d'enregistrer le CV de base : {error}")
            else:
                st.success(
                    "CV de base enregistré. Les nouveaux brouillons utiliseront ces données."
                )
                st.rerun()
        preview_data = json.loads(normalized_base_json)
        preview_html = None
        preview_pdf = None
        try:
            preview_html = render_cv_html(preview_data)
        except (ValueError, KeyError) as error:
            st.error(f"Impossible de préparer l'aperçu du CV : {error}")

        try:
            preview_pdf = render_cv_pdf(preview_data)
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            st.warning(f"Génération du PDF (WeasyPrint) non disponible : {error}")

        if preview_html or preview_pdf:
            st.subheader("Aperçu du CV")
            if preview_pdf:
                st.download_button(
                    "Télécharger le PDF d'aperçu",
                    data=preview_pdf,
                    file_name=tailored_cv_filename(preview_data),
                    mime="application/pdf",
                    key="preview_base_cv_pdf_dl",
                )

            tab_html, tab_pdf = st.tabs(["Aperçu direct (HTML)", "Aperçu PDF intégré"])
            with tab_html:
                if preview_html:
                    components.html(preview_html, height=860, scrolling=True)
                else:
                    st.info("Aperçu visuel non disponible.")
            with tab_pdf:
                if preview_pdf:
                    pdf_base64 = base64.b64encode(preview_pdf).decode("ascii")
                    components.html(
                        f'<object data="data:application/pdf;base64,{pdf_base64}" '
                        'type="application/pdf" width="100%" height="850px">'
                        '<iframe title="Aperçu PDF du CV" '
                        f'src="data:application/pdf;base64,{pdf_base64}" '
                        'width="100%" height="850px" style="border: 0;">'
                        "<p>Votre navigateur ne prend pas en charge l'affichage direct des PDF. "
                        "Veuillez utiliser le bouton de téléchargement ci-dessus.</p>"
                        "</iframe></object>",
                        height=860,
                        scrolling=True,
                    )
                else:
                    st.info("Aperçu PDF non disponible (WeasyPrint / GTK3).")

    uploaded_file = st.file_uploader(
        "Choisir un CV",
        type=["pdf", "docx"],
        help="Le fichier n'est lu qu'après votre sélection et reste sur cet ordinateur.",
    )
    if uploaded_file is not None:
        content = uploaded_file.getvalue()
        digest = hashlib.sha256(content).hexdigest()
        upload_identity = f"{uploaded_file.name}:{digest}"
        if st.session_state.get("resume_upload_identity") != upload_identity:
            st.session_state["resume_upload_identity"] = upload_identity
            st.session_state["resume_extracted_identity"] = ""
            st.session_state["resume_review_text"] = ""

        if st.button("Extraire le texte pour vérification"):
            try:
                preview = extract_resume_text(uploaded_file.name, content)
            except ResumeImportError as error:
                st.error(str(error))
            else:
                st.session_state["resume_review_text"] = preview
                st.session_state["resume_extracted_identity"] = upload_identity
                st.success(
                    "Extraction terminée. Relisez et corrigez le texte avant l'enregistrement."
                )

        if st.session_state.get("resume_extracted_identity") == upload_identity:
            st.text_area(
                "Texte extrait — modifiable avant enregistrement",
                key="resume_review_text",
                height=360,
            )
            if st.button("Enregistrer cette version du CV", type="primary"):
                try:
                    version = save_resume_version(
                        engine,
                        uploaded_file.name,
                        content,
                        st.session_state["resume_review_text"],
                    )
                except ResumeImportError as error:
                    st.error(str(error))
                except (OSError, SQLAlchemyError) as error:
                    st.error(f"Impossible d'enregistrer le CV localement : {error}")
                else:
                    st.success(f"Version enregistrée ({version.original_filename}).")
                    st.session_state["resume_upload_identity"] = ""
                    st.rerun()

    versions = list_resume_versions(engine)
    st.subheader("Versions enregistrées")
    if not versions:
        st.info("Aucun CV n'a encore été enregistré.")
        return

    for version in versions:
        with st.expander(f"{version.original_filename} — {version.created_at[:10]}"):
            st.text_area(
                "Texte vérifié enregistré",
                value=version.reviewed_text,
                height=240,
                key=f"saved_resume_{version.id}",
                disabled=True,
            )
            st.caption("Le fichier original est conservé dans le dossier local data/resumes.")


def _offer_form(
    key: str,
    offer: JobOfferData | None = None,
) -> JobOfferData | None:
    with st.form(key):
        title = st.text_input("Intitulé du poste", value=offer.title if offer else "")
        company = st.text_input("Entreprise (facultatif)", value=offer.company if offer else "")
        location = st.text_input("Lieu / modalité", value=offer.location if offer else "")
        contract_options = [None, *CONTRACT_TYPES]
        current_contract = offer.contract_type if offer else None
        contract_index = (
            contract_options.index(current_contract) if current_contract in contract_options else 0
        )
        contract_type = st.selectbox(
            "Type de contrat",
            options=contract_options,
            index=contract_index,
            format_func=lambda value: value or "Non précisé",
        )
        url = st.text_input(
            "URL de l'annonce (facultatif)",
            value=offer.url if offer and offer.url else "",
        )
        source = st.text_input("Source", value=offer.source if offer else "Saisie manuelle")
        status_options = OFFER_STATUSES
        current_status = offer.status if offer else "À examiner"
        status = st.selectbox(
            "Statut",
            options=status_options,
            index=status_options.index(current_status)
            if current_status in status_options
            else 0,
        )
        description = st.text_area(
            "Description de l'annonce",
            value=offer.description if offer else "",
            height=240,
        )
        submitted = st.form_submit_button(
            "Enregistrer l'offre" if offer is None else "Enregistrer les modifications",
            type="primary",
        )

    if not submitted:
        return None
    return JobOfferData(
        title=title,
        company=company,
        location=location,
        contract_type=contract_type,
        url=url or None,
        source=source,
        status=status,
        description=description,
    )


def _offer_link_import_ui(engine: Engine) -> None:
    st.subheader("Importer depuis le lien d'une annonce")
    st.caption(
        "L'application lit uniquement l'URL que tu soumets, extrait les informations "
        "disponibles publiquement, puis te laisse les vérifier avant l'enregistrement."
    )
    with st.form("offer_link_import"):
        url = st.text_input(
            "Lien de l'annonce",
            placeholder="https://exemple.fr/offre/...",
            key="offer_link_import_url",
        )
        read_submitted = st.form_submit_button("Lire l'annonce")

    if read_submitted:
        try:
            with st.spinner("Lecture de l'annonce…"):
                imported_offer = import_offer_from_url(url)
        except OfferImportError as error:
            st.error(str(error))
        else:
            for key in (
                "offer_import_title",
                "offer_import_company",
                "offer_import_location",
                "offer_import_url",
                "offer_import_source",
                "offer_import_description",
            ):
                st.session_state.pop(key, None)
            st.session_state["offer_link_draft"] = imported_offer.model_dump(mode="json")
            st.rerun()

    draft = st.session_state.get("offer_link_draft")
    if not isinstance(draft, dict):
        return

    st.info(
        "Vérifie et corrige les champs extraits : certains sites empêchent la lecture "
        "automatique ou ne fournissent qu'un extrait."
    )
    with st.form("review_imported_offer"):
        title = st.text_input(
            "Intitulé du poste",
            value=draft["title"],
            key="offer_import_title",
        )
        company = st.text_input(
            "Entreprise (facultatif)",
            value=draft["company"],
            key="offer_import_company",
        )
        location = st.text_input(
            "Lieu / modalité",
            value=draft["location"],
            key="offer_import_location",
        )
        imported_url = st.text_input(
            "URL source",
            value=draft["url"] or "",
            key="offer_import_url",
        )
        source = st.text_input(
            "Source",
            value=draft["source"],
            key="offer_import_source",
        )
        description = st.text_area(
            "Description extraite — à vérifier",
            value=draft["description"],
            height=260,
            key="offer_import_description",
        )
        save_imported = st.form_submit_button(
            "Enregistrer l'offre vérifiée",
            type="primary",
        )

    if not save_imported:
        return
    try:
        offer_data = JobOfferData(
            title=title,
            company=company,
            location=location,
            url=imported_url or None,
            source=source,
            description=description,
        )
        create_offer(engine, offer_data)
    except ValidationError as error:
        for issue in error.errors():
            st.error(issue["msg"])
    except DuplicateOfferURL as error:
        st.error(str(error))
    else:
        st.session_state.pop("offer_link_draft", None)
        st.success(
            "Offre vérifiée et enregistrée. Elle est maintenant disponible dans CV par offre."
        )
        st.rerun()


def show_offers_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Offres d'emploi")
    st.write(
        "Ajoute une annonce manuellement, colle son lien pour en extraire les informations, "
        "ou importe les résultats de recherche disponibles."
    )
    _offer_link_import_ui(engine)
    st.subheader("Ajouter une offre")
    try:
        new_offer = _offer_form("new_offer")
        if new_offer is not None:
            create_offer(engine, new_offer)
            st.success("Offre enregistrée localement.")
            st.rerun()
    except ValidationError as error:
        for issue in error.errors():
            st.error(issue["msg"])
    except DuplicateOfferURL as error:
        st.error(str(error))

    offers = list_offers(engine)
    st.subheader(f"Offres enregistrées ({len(offers)})")
    if not offers:
        st.info("Aucune offre pour le moment. Tu peux commencer par coller une annonce ci-dessus.")
        return

    scored_offers = []
    for offer in offers:
        offer_data = JobOfferData(
            title=offer.title,
            company=offer.company,
            location=offer.location,
            contract_type=offer.contract_type,
            url=offer.url,
            source=offer.source,
            status=offer.status,
            description=offer.description,
        )
        scored_offers.append((offer, offer_data, assess_offer_fit(offer_data, profile)))
    scored_offers.sort(
        key=lambda item: (
            item[2].overall_percentage is not None,
            item[2].overall_percentage or 0,
        ),
        reverse=True,
    )
    for offer, offer_data, fit in scored_offers:
        heading = " — ".join(part for part in (offer.title, offer.company) if part)
        score_label = (
            f"{fit.overall_percentage}% · "
            if fit.overall_percentage is not None
            else ""
        )
        with st.expander(f"{score_label}{heading} · {offer.status}"):
            st.caption(
                f"Ajoutée le {offer.collected_at[:10]} · Source : {offer.source}"
            )
            if offer.url:
                st.markdown(f"[Ouvrir l'annonce]({offer.url})")
            if fit.overall_percentage is not None:
                st.progress(fit.overall_percentage / 100)
                st.caption(
                    "Correspondance indicative (compétences 60 %, rôle 20 %, contrat 10 %, "
                    "zone/télétravail 10 % ; seuls les critères évaluables sont comptés)."
                )
            comparison = fit.skills
            if comparison.matches:
                st.markdown(
                    f"**Compétences mentionnées :** {comparison.mentioned_count}/"
                    f"{len(comparison.matches)} "
                    f"(score compétences {comparison.match_percentage} %)"
                )
                technologies_caption = _required_technologies_text(comparison)
                if technologies_caption:
                    st.caption(technologies_caption)
                for match in comparison.matches:
                    if match.mentioned:
                        st.write(
                            f"- **{match.skill.name}** — {match.priority} · "
                            f"« {match.evidence} »"
                        )
                not_mentioned = [
                    match.skill.name for match in comparison.matches if not match.mentioned
                ]
                if not_mentioned:
                    st.caption(
                        "Non mentionnées dans l'annonce — à vérifier, pas nécessairement "
                        "absentes du profil : " + ", ".join(not_mentioned)
                    )
            elif profile.skills:
                st.info("Aucune compétence renseignée dans le profil n'est citée dans l'annonce.")
            if fit.role_percentage is not None:
                st.write(f"**Proximité du rôle visé :** {fit.role_percentage} %")
            if fit.contract_compatible is not None:
                st.write(
                    "**Contrat :** "
                    + (
                        "compatible avec tes préférences"
                        if fit.contract_compatible
                        else "différent de tes préférences"
                    )
                )
            if fit.location_reason:
                st.write(f"**Zone :** {fit.location_reason}")
            try:
                edited_offer = _offer_form(
                    f"edit_offer_{offer.id}",
                    JobOfferData(
                        title=offer.title,
                        company=offer.company,
                        location=offer.location,
                        contract_type=offer.contract_type,
                        url=offer.url,
                        source=offer.source,
                        status=offer.status,
                        description=offer.description,
                    ),
                )
                if edited_offer is not None:
                    update_offer(engine, offer.id, edited_offer)
                    st.success("Offre mise à jour.")
                    st.rerun()
            except ValidationError as error:
                for issue in error.errors():
                    st.error(issue["msg"])
            except (DuplicateOfferURL, OfferNotFound) as error:
                st.error(str(error))

            if st.button("Supprimer cette offre", key=f"delete_offer_{offer.id}"):
                try:
                    delete_offer(engine, offer.id)
                except OfferNotFound as error:
                    st.error(str(error))
                else:
                    st.success("Offre supprimée.")
                    st.rerun()


def show_job_search_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Recherche d'offres en ligne")
    adzuna_tab, france_travail_tab, targeted_tab, watcher_tab = st.tabs(
        [
            "Adzuna (recherche par mots-clés)",
            "France Travail",
            "Greenhouse & Lever (collecte ciblée)",
            "Veille automatique planifiée",
        ]
    )
    with adzuna_tab:
        _show_adzuna_search_tab(engine, profile)
    with france_travail_tab:
        _show_france_travail_tab(engine, profile)
    with targeted_tab:
        _show_targeted_job_boards_tab(engine, profile)
    with watcher_tab:
        _show_job_watcher_tab(engine, profile)


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
            st.caption(
                "La recherche couvre Nice, Cannes, Mougins, Sophia Antipolis, Antibes, "
                "Grasse, Cagnes-sur-Mer, Menton, le Var, Toulon, Marseille et Paris. "
                "Les annonces des zones hors Paris seront classées avant celles de Paris ; "
                "le score de correspondance les départage dans chaque groupe."
            )
            selected_countries = ["fr"]
            selected_locations = list(LOCAL_SEARCH_LOCATIONS)
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
        and not find_excluded_keyword(item.title, watcher_cfg.excluded_title_keywords)
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
        "Collecte les annonces publiées directement depuis le tableau de recrutement d'une "
        "entreprise via les API publiques officielles Greenhouse ou Lever."
    )
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
                    f"✈️ **Mention de relocalisation détectée :** "
                    f"{listing.relocation_evidence}"
                )
            if listing.public_contact_email:
                st.info(
                    f"✉️ **Contact public extrait :** {listing.public_contact_email}"
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


def _show_job_watcher_tab(engine: Engine, profile: ProfileData) -> None:
    st.write(
        "La veille automatique planifiée surveille périodiquement vos sources ciblées "
        "(Greenhouse, Lever et Adzuna), évalue la correspondance avec votre profil de compétences, "
        "et importe directement les opportunités pertinentes dans votre suivi sous le statut "
        "**À examiner**."
    )
    watcher = get_global_watcher()
    cfg = load_watcher_config()

    col_status, col_actions = st.columns([1, 1])
    with col_status:
        if watcher.is_active:
            st.success("🟢 **Veille automatique en tâche de fond : ACTIVE**")
        else:
            st.info("⚪ **Veille automatique en tâche de fond : INACTIVE**")

        last_res = watcher.last_result
        if last_res:
            st.caption(f"Dernier cycle : {last_res.started_at[:19]} UTC")
            st.markdown(
                f"- **Annonces analysées** : {last_res.total_listings_found}\n"
                f"- **Nouvelles offres importées** : {last_res.new_offers_imported}\n"
                f"- **Dossiers pré-générés (CV + Lettre)** : {last_res.dossiers_prepared}\n"
                f"- **Doublons ignorés** : {last_res.duplicates_skipped}\n"
                f"- **Score insuffisant (< {cfg.min_match_percentage}%)** : "
                f"{last_res.low_match_skipped}\n"
                f"- **Titres exclus (stage, freelance…)** : {last_res.excluded_skipped}"
            )
            if last_res.imported_offer_titles:
                with st.expander("Dernières offres importées automatiquement"):
                    for item in last_res.imported_offer_titles:
                        st.write(f"- {item}")
            if last_res.errors:
                with st.expander("Avertissements du dernier cycle"):
                    for err in last_res.errors:
                        st.caption(f"⚠️ {err}")

    with col_actions:
        try:
            secrets = {
                "ADZUNA_APP_ID": st.secrets.get("ADZUNA_APP_ID", ""),
                "ADZUNA_APP_KEY": st.secrets.get("ADZUNA_APP_KEY", ""),
                **_france_travail_secrets(),
            }
        except StreamlitSecretNotFoundError:
            secrets = {}

        if st.button("Lancer un cycle de veille maintenant", type="primary"):
            with st.spinner("Exécution du cycle de veille..."):
                cycle_res = run_watcher_cycle(engine, profile, cfg, secrets)
                st.session_state["manual_watcher_result"] = cycle_res.to_dict()
                st.rerun()

        if st.button("Pré-générer les dossiers manquants en base"):
            with st.spinner("Génération des CV et lettres manquants..."):
                batch_res = prepare_pending_dossiers(
                    engine, profile, min_score=cfg.min_match_percentage
                )
                st.session_state["manual_dossiers_result"] = {
                    "eligible": batch_res.total_eligible,
                    "generated": batch_res.generated_count,
                    "skipped": batch_res.skipped_existing,
                    "errors": batch_res.errors,
                }
                st.rerun()

        if not watcher.is_active:
            if st.button("Démarrer la veille automatique périodique"):
                watcher.start(engine, profile, None, secrets)
                st.success("Veille démarrée en tâche de fond !")
                st.rerun()
        else:
            if st.button("Arrêter la veille automatique"):
                watcher.stop()
                st.warning("Veille arrêtée.")
                st.rerun()

    manual_res = st.session_state.get("manual_watcher_result")
    if manual_res:
        st.success(
            f"Cycle ponctuel terminé : {manual_res.get('new_offers_imported', 0)} offre(s) "
            f"importée(s), {manual_res.get('dossiers_prepared', 0)} dossier(s) préparé(s), "
            f"{manual_res.get('duplicates_skipped', 0)} doublon(s) ignoré(s)."
        )
        if manual_res.get("imported_offer_titles"):
            for title in manual_res["imported_offer_titles"]:
                st.markdown(f"- ✅ **{title}**")

    manual_dossiers = st.session_state.get("manual_dossiers_result")
    if manual_dossiers:
        st.success(
            f"Pré-génération terminée : {manual_dossiers.get('generated', 0)} dossier(s) "
            f"créé(s), {manual_dossiers.get('skipped', 0)} déjà complet(s) sur "
            f"{manual_dossiers.get('eligible', 0)} offre(s) éligible(s)."
        )
        if manual_dossiers.get("errors"):
            with st.expander("Erreurs rencontrées lors de la pré-génération"):
                for err in manual_dossiers["errors"]:
                    st.caption(f"⚠️ {err}")

    st.markdown("---")
    st.subheader("Configuration de la veille")
    with st.form("watcher_config_form"):
        min_score = st.slider(
            "Seuil minimal de correspondance (%)",
            min_value=20,
            max_value=90,
            value=cfg.min_match_percentage,
            step=5,
            help=(
                "Score global affiché dans les offres (compétences, intitulé, contrat, lieu). "
                "Seules les annonces à ce score ou au-dessus sont importées."
            ),
        )
        excluded_raw = st.text_input(
            "Mots exclus du titre (séparés par des virgules)",
            value=", ".join(cfg.excluded_title_keywords),
            help="Une annonce dont le titre contient l'un de ces mots entiers est ignorée.",
        )
        freq_options = [1, 2, 4, 8, 24]
        cur_hours = max(1, cfg.interval_seconds // 3600)
        cur_index = freq_options.index(cur_hours) if cur_hours in freq_options else 0
        interval_hours = st.selectbox(
            "Fréquence de passage en arrière-plan",
            options=freq_options,
            index=cur_index,
            format_func=lambda h: f"Toutes les {h} heure{'s' if h > 1 else ''}",
        )
        enable_adzuna = st.checkbox(
            "Inclure la recherche par profil Adzuna",
            value=cfg.enable_adzuna,
        )
        enable_france_travail = st.checkbox(
            "Inclure la recherche France Travail (identifiants requis)",
            value=cfg.enable_france_travail,
        )
        ft_departments = st.multiselect(
            "Départements France Travail",
            options=list(FRANCE_TRAVAIL_DEPARTMENT_NAMES),
            default=[
                code for code in cfg.france_travail_departments
                if code in FRANCE_TRAVAIL_DEPARTMENT_NAMES
            ],
            format_func=lambda code: f"{code} — {FRANCE_TRAVAIL_DEPARTMENT_NAMES[code]}",
        )
        ft_cdi_only = st.checkbox(
            "France Travail : CDI uniquement",
            value=cfg.france_travail_cdi_only,
        )
        auto_import = st.checkbox(
            "Importer automatiquement en base les offres retenues",
            value=cfg.auto_import,
        )
        auto_prepare_dossier = st.checkbox(
            "Pré-générer automatiquement le dossier (CV ciblé + lettre) pour chaque offre retenue",
            value=cfg.auto_prepare_dossier,
        )

        submitted = st.form_submit_button("Enregistrer la configuration")
        if submitted:
            cfg.min_match_percentage = min_score
            cfg.excluded_title_keywords = [
                word.strip() for word in excluded_raw.split(",") if word.strip()
            ]
            cfg.interval_seconds = interval_hours * 3600
            cfg.enable_adzuna = enable_adzuna
            cfg.enable_france_travail = enable_france_travail
            cfg.france_travail_departments = ft_departments
            cfg.france_travail_cdi_only = ft_cdi_only
            cfg.auto_import = auto_import
            cfg.auto_prepare_dossier = auto_prepare_dossier
            save_watcher_config(cfg)
            st.success("Configuration de la veille enregistrée !")
            st.rerun()

    st.subheader("Tableaux employeurs surveillés (Greenhouse & Lever)")
    if not cfg.targets:
        st.info("Aucun tableau employeur configuré pour le moment.")
    else:
        for idx, target in enumerate(cfg.targets):
            cols = st.columns([3, 2, 2, 1])
            with cols[0]:
                display = target.display_name or target.target
                st.write(f"**{display}** ({target.platform.title()})")
            with cols[1]:
                slug_info = f"Slug: `{target.target}`" + (" (EU)" if target.is_eu else "")
                st.caption(slug_info)
            with cols[2]:
                active_str = "Actif" if target.enabled else "Suspendu"
                st.write(f"Statut : {active_str}")
            with cols[3]:
                if st.button("Supprimer", key=f"del_target_{idx}"):
                    cfg.targets.pop(idx)
                    save_watcher_config(cfg)
                    st.rerun()

    with st.expander("Ajouter un tableau employeur à surveiller"):
        with st.form("add_target_form"):
            new_platform = st.selectbox("Plateforme", ["greenhouse", "lever"])
            new_target = st.text_input(
                "Identifiant / Slug / URL du board", placeholder="ex: ateliertech"
            )
            new_name = st.text_input(
                "Nom de l'entreprise (optionnel)", placeholder="ex: Atelier Tech"
            )
            new_eu = st.checkbox("Endpoint Europe (pour Lever uniquement)", value=False)
            add_sub = st.form_submit_button("Ajouter à la surveillance")
            if add_sub and new_target.strip():
                clean_target = (
                    extract_greenhouse_board_token(new_target)
                    if new_platform == "greenhouse"
                    else extract_lever_site_slug(new_target)
                )
                cfg.targets.append(
                    MonitoredTarget(
                        platform=new_platform,
                        target=clean_target,
                        display_name=new_name.strip() or clean_target,
                        is_eu=new_eu,
                        enabled=True,
                    )
                )
                save_watcher_config(cfg)
                st.success(f"Tableau {clean_target} ajouté à la veille !")
                st.rerun()


def show_tailored_resume_page(engine: Engine, profile: ProfileData) -> None:
    single_tab, batch_tab = st.tabs(["Personnaliser une offre", "Batch de CV"])
    with single_tab:
        _show_single_tailored_resume_page(engine, profile)
    with batch_tab:
        _show_tailored_resume_batch(engine, profile)


def _set_tailored_batch_selection(offer_ids: tuple[str, ...]) -> None:
    selected = st.session_state["tailored_batch_select_all"]
    for offer_id in offer_ids:
        st.session_state[f"tailored_batch_selected_{offer_id}"] = selected


def _sync_tailored_batch_select_all(offer_ids: tuple[str, ...]) -> None:
    st.session_state["tailored_batch_select_all"] = all(
        st.session_state.get(f"tailored_batch_selected_{offer_id}", False)
        for offer_id in offer_ids
    )


def _show_tailored_resume_batch(engine: Engine, profile: ProfileData) -> None:
    st.subheader("Préparer plusieurs CV personnalisés")
    st.caption(
        "Sélectionne les annonces à traiter. Chaque brouillon JSON est enregistré avec son "
        "offre ; le PDF exporté ne contient ni le lieu ni le lien de l'annonce."
    )
    offers = list_offers(engine)
    if not offers:
        st.info("Aucune annonce enregistrée. Ajoute ou importe d'abord des offres.")
        return
    resumes = list_resume_versions(engine)
    resume_by_id = {resume.id: resume for resume in resumes}
    selected_resume_id: str | None = None
    if resumes:
        selected_resume_id = st.selectbox(
            "CV de référence associé aux nouveaux brouillons",
            options=list(resume_by_id),
            format_func=lambda resume_id: resume_by_id[resume_id].original_filename,
            key="tailored_batch_source_resume",
        )
    else:
        st.warning(
            "Enregistre d'abord un CV vérifié dans « CV de référence » pour créer les "
            "brouillons du batch."
        )

    saved_drafts = {offer.id: get_tailored_resume(engine, offer.id) for offer in offers}
    offer_ids = tuple(offer.id for offer in offers)
    for offer in offers:
        selection_key = f"tailored_batch_selected_{offer.id}"
        if selection_key not in st.session_state:
            st.session_state[selection_key] = saved_drafts[offer.id] is not None
    all_selected = all(
        st.session_state.get(f"tailored_batch_selected_{offer_id}", False)
        for offer_id in offer_ids
    )
    st.session_state["tailored_batch_select_all"] = all_selected
    st.checkbox(
        "Tout sélectionner",
        key="tailored_batch_select_all",
        on_change=_set_tailored_batch_selection,
        args=(offer_ids,),
    )
    st.markdown(
        """
        <style>
        div[data-testid="stVerticalBlockBorderWrapper"] {
            height: 340px;
            overflow-y: auto;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] > div {
            height: 100%;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    offer_columns = st.columns(4)
    for index, offer in enumerate(offers):
        with offer_columns[index % 4]:
            with st.container(border=True):
                st.markdown(f"**{normalize_job_title(offer.title)}**")
                st.caption(
                    " · ".join(part for part in (offer.company, offer.location) if part)
                    or "Entreprise / lieu non précisé"
                )
                if offer.url:
                    st.markdown(f"[Ouvrir l'annonce]({offer.url})")
                else:
                    st.caption("Aucun lien d'annonce")
                draft = saved_drafts[offer.id]
                if draft:
                    st.success(f"CV sauvegardé · {draft.updated_at[:10]}")
                else:
                    st.caption("Pas encore de CV personnalisé")
                st.checkbox(
                    "Inclure dans le batch",
                    key=f"tailored_batch_selected_{offer.id}",
                    on_change=_sync_tailored_batch_select_all,
                    args=(offer_ids,),
                )
                if draft:
                    try:
                        pdf_bytes = render_tailored_resume_pdf(draft.content)
                    except TailoredResumeError as error:
                        st.error(f"PDF indisponible : {error}")
                    else:
                        draft_data = json.loads(draft.content)
                        st.download_button(
                            "Télécharger le CV PDF",
                            data=pdf_bytes,
                            file_name=tailored_cv_filename(
                                draft_data,
                                target_role=draft_data.get("target_role", offer.title),
                                target_company=offer.company,
                            ),
                            mime="application/pdf",
                            key=f"tailored_batch_download_{offer.id}",
                            use_container_width=True,
                        )
                else:
                    st.button(
                        "PDF non généré",
                        key=f"tailored_batch_pdf_unavailable_{offer.id}",
                        disabled=True,
                        use_container_width=True,
                    )

    selected_offers = [
        offer
        for offer in offers
        if st.session_state.get(f"tailored_batch_selected_{offer.id}", False)
    ]
    if not selected_offers:
        st.info("Coche une ou plusieurs annonces pour sélectionner le lot.")
        return
    if st.button(
        f"Générer et enregistrer {len(selected_offers)} CV",
        type="primary",
        disabled=selected_resume_id is None,
        key="generate_tailored_resume_batch",
    ):
        if selected_resume_id is None:
            st.error("Sélectionne un CV de référence avant de créer le batch.")
            return
        generated_count = 0
        for offer in selected_offers:
            offer_data = JobOfferData(
                title=offer.title,
                company=offer.company,
                location=offer.location,
                contract_type=offer.contract_type,
                url=offer.url,
                source=offer.source,
                status=offer.status,
                description=offer.description,
            )
            try:
                draft_json = build_tailored_resume(offer_data, profile)
                save_tailored_resume(
                    engine,
                    offer.id,
                    selected_resume_id,
                    draft_json,
                )
            except (TailoredResumeError, SQLAlchemyError) as error:
                st.error(f"{offer.title} : {error}")
            else:
                generated_count += 1
        if generated_count:
            st.success(
                f"{generated_count} CV personnalisé(s) enregistré(s). "
                "Les PDF aux noms descriptifs sont disponibles sur chaque carte."
            )
            st.rerun()


def _show_single_tailored_resume_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Préparer un CV pour une offre")
    st.write(
        "Crée une version personnalisée distincte pour une offre à partir des variables "
        "structurées du modèle JSON. Le CV de référence reste inchangé et sert de référence "
        "associée à cette version."
    )
    offers = list_offers(engine)
    resumes = list_resume_versions(engine)
    if not offers:
        st.info("Enregistre d'abord une offre dans « Offres » ou importe-la depuis la recherche.")
        return

    offer_options = {
        offer.id: (
            f"{normalize_job_title(offer.title)} — "
            f"{offer.company or offer.location or 'Entreprise non précisée'}"
        )
        for offer in offers
    }
    offer_id = st.selectbox(
        "Offre à cibler",
        options=list(offer_options),
        format_func=offer_options.__getitem__,
    )
    offer = next(offer for offer in offers if offer.id == offer_id)
    offer_data = JobOfferData(
        title=offer.title,
        company=offer.company,
        location=offer.location,
        contract_type=offer.contract_type,
        url=offer.url,
        source=offer.source,
        status=offer.status,
        description=offer.description,
    )
    fit = assess_offer_fit(offer_data, profile)
    if fit.overall_percentage is not None:
        st.metric(
            "Correspondance indicative avec l'offre",
            f"{fit.overall_percentage} %",
        )
    if fit.skills.matches:
        matched_skills = [
            match for match in fit.skills.matches if match.mentioned
        ]
        if matched_skills:
            st.caption(
                "Compétences citées (priorité détectée dans l'annonce) : "
                + ", ".join(
                    f"{match.skill.name} — {match.priority}"
                    for match in matched_skills
                )
            )
    relocation_question = relocation_question_for_offer(offer_data)
    if relocation_question:
        st.subheader("Question à reprendre dans la lettre ou le mail")
        st.text_area(
            "Modalités de déplacement / installation",
            value=relocation_question,
            height=120,
            key=f"relocation_question_{offer_id}",
        )

    show_cover_letter_page(
        engine,
        profile,
        selected_offer_id=offer_id,
        selected_offer=offer_data,
    )

    if not resumes:
        st.info("Enregistre d'abord ton CV vérifié dans « CV de référence ».")
        return

    resume_by_id = {resume.id: resume for resume in resumes}
    existing_draft = get_tailored_resume(engine, offer_id)
    resume_ids = list(resume_by_id)
    default_resume_id = (
        existing_draft.source_resume_id
        if existing_draft and existing_draft.source_resume_id in resume_by_id
        else resume_ids[0]
    )
    selected_resume_id = st.selectbox(
        "CV de référence associé au suivi",
        options=resume_ids,
        index=resume_ids.index(default_resume_id),
        format_func=lambda resume_id: resume_by_id[resume_id].original_filename,
        key=f"tailored_resume_source_{offer_id}",
    )
    content_key = f"tailored_resume_content_{offer_id}"
    legacy_draft = False
    if content_key not in st.session_state:
        if existing_draft:
            try:
                st.session_state[content_key] = validate_tailored_resume_json(
                    existing_draft.content
                )
            except TailoredResumeError:
                st.session_state[content_key] = ""
                legacy_draft = True
        else:
            st.session_state[content_key] = ""

    generate_label = (
        "Régénérer les données JSON du CV personnalisé"
        if st.session_state[content_key]
        else "Créer le CV personnalisé depuis les données JSON"
    )
    if st.button(generate_label, type="primary"):
        try:
            st.session_state[content_key] = build_tailored_resume(offer_data, profile)
        except (OSError, ValueError) as error:
            st.error(f"Impossible de créer les données JSON du CV : {error}")
            return

    if not st.session_state[content_key]:
        if legacy_draft:
            st.warning(
                "Le brouillon enregistré est dans l'ancien format texte et ne peut pas être "
                "converti fidèlement en variables JSON. Génère un nouveau brouillon ; le CV "
                "source reste conservé."
            )
        st.info("Génère un brouillon automatique, puis modifie-le avant de l'enregistrer.")
        return

    st.caption(
        "Le brouillon part de data/cv_base.json, au format ATS à une colonne. Le CV de "
        "référence sélectionné est associé au suivi ; les données affichées et rendues viennent "
        "du JSON modifiable ci-dessous. L'en-tête du PDF n'affiche que le nom de l'entreprise, "
        "sans ville ni lien vers l'annonce."
    )
    edited_json = st.text_area(
        "Données du CV personnalisé (JSON)",
        key=content_key,
        height=500,
        help=(
            "Modifie les valeurs JSON (nom, contacts, profil, compétences, expériences et "
            "formation). Garde une syntaxe JSON valide."
        ),
    )
    try:
        normalized_json = validate_tailored_resume_json(edited_json)
    except TailoredResumeError as error:
        st.error(str(error))
        return
    st.download_button(
        "Télécharger les données du CV (JSON)",
        data=normalized_json,
        file_name=tailored_cv_filename(
            json.loads(normalized_json),
            target_role=offer.title,
            target_company=offer.company,
        ).removesuffix(".pdf")
        + ".json",
        mime="application/json",
    )
    if st.button("Enregistrer les modifications du CV"):
        try:
            saved_resume = save_tailored_resume(
                engine,
                offer_id,
                selected_resume_id,
                normalized_json,
            )
        except TailoredResumeError as error:
            st.error(str(error))
        except SQLAlchemyError as error:
            st.error(f"Impossible d'enregistrer le brouillon CV localement : {error}")
        else:
            updated_date = saved_resume.updated_at[:10]
            st.success(
                f"Brouillon enregistré · dernière modification {updated_date}."
            )

    try:
        pdf_bytes = render_tailored_resume_pdf(normalized_json)
    except TailoredResumeError as error:
        st.warning(str(error))
    else:
        normalized_data = json.loads(normalized_json)
        st.download_button(
            "Télécharger le CV personnalisé en PDF",
            data=pdf_bytes,
            file_name=tailored_cv_filename(
                normalized_data,
                target_role=offer.title,
                target_company=offer.company,
            ),
            mime="application/pdf",
            help="Le PDF reprend le texte affiché ci-dessus, y compris les modifications en cours.",
        )
    st.caption(
        "Le fichier PDF téléchargé est une copie personnalisée à joindre à ton e-mail ; "
        "le CV de référence enregistré reste inchangé."
    )


def show_cover_letter_page(
    engine: Engine,
    profile: ProfileData,
    *,
    selected_offer_id: str | None = None,
    selected_offer: JobOfferData | None = None,
) -> None:
    integrated_offer = selected_offer_id is not None
    if integrated_offer:
        st.divider()
        st.subheader("Lettre de motivation")
        st.caption(
            "Prépare une lettre associée à cette offre. Le texte est modifiable et "
            "reprend les faits déjà présents dans le CV."
        )
    else:
        st.title("Candidature spontanée")
        st.write(
            "Prépare une lettre en français pour une entreprise sans offre sélectionnée. "
            "Choisis une entreprise parmi les pistes enregistrées, ou saisis un nom. "
            "Le texte reste modifiable et s'appuie sur les faits déjà présents dans le CV."
        )
    job_offer_id: str | None = None
    company_id: str | None = None
    target_company = ""
    offer_data: JobOfferData | None = None

    if integrated_offer:
        job_offer_id = selected_offer_id
        offer_data = selected_offer
        target_company = selected_offer.company if selected_offer else ""
        if offer_data is None:
            st.error("Impossible de charger l'offre sélectionnée.")
            return
    else:
        companies = list_companies(engine)
        company_options: list[str | None] = [None, *(company.id for company in companies)]
        company_by_id = {company.id: company for company in companies}
        company_id = st.selectbox(
            "Entreprise prospectée (facultatif)",
            options=company_options,
            format_func=lambda value: (
                "Saisir un autre nom"
                if value is None
                else f"{company_by_id[value].name} — {company_by_id[value].location}"
            ),
            key="cover_letter_company",
        )
        selected_company = company_by_id.get(company_id) if company_id else None
        target_company = st.text_input(
            "Nom de l'entreprise",
            value=selected_company.name if selected_company else "",
            key=f"cover_letter_target_{company_id or 'custom'}",
        ).strip()
        if not target_company:
            st.info("Renseigne le nom de l'entreprise pour créer une candidature spontanée.")
            return

    scope_label = (
        f"offer:{job_offer_id}"
        if job_offer_id
        else f"company:{company_id or target_company.casefold()}"
    )
    scope_token = hashlib.sha256(scope_label.encode("utf-8")).hexdigest()[:16]
    content_key = f"cover_letter_content_{scope_token}"
    if content_key not in st.session_state:
        saved_letter = get_cover_letter(
            engine,
            job_offer_id=job_offer_id,
            company_id=company_id,
            target_company=target_company,
        )
        st.session_state[content_key] = saved_letter.content if saved_letter else ""

    if st.button(
        "Générer la lettre" if not st.session_state[content_key] else "Régénérer la lettre",
        type="primary",
        key=f"generate_cover_letter_{scope_token}",
    ):
        try:
            st.session_state[content_key] = build_cover_letter(
                offer_data,
                profile,
                target_company=target_company,
            )
        except (OSError, ValueError) as error:
            st.error(f"Impossible de générer la lettre : {error}")
            return

    if not st.session_state[content_key]:
        st.info("Génère une lettre pour afficher le brouillon modifiable.")
        return

    st.caption(
        "Le modèle reprend des faits du CV JSON de base et, pour une réponse à une offre, "
        "des compétences correspondantes. Relis et personnalise le texte avant de l'utiliser."
    )
    edited_content = st.text_area(
        "Brouillon modifiable",
        key=content_key,
        height=420,
    )
    try:
        letter_content = validate_cover_letter_content(edited_content)
    except CoverLetterError as error:
        st.error(str(error))
        return

    if st.button("Enregistrer la lettre", key=f"save_cover_letter_{scope_token}"):
        try:
            saved_letter = save_cover_letter(
                engine,
                letter_content,
                job_offer_id=job_offer_id,
                company_id=company_id,
                target_company=target_company,
            )
        except CoverLetterError as error:
            st.error(str(error))
        except SQLAlchemyError as error:
            st.error(f"Impossible d'enregistrer la lettre localement : {error}")
        else:
            st.success(
                f"Lettre enregistrée · dernière modification "
                f"{saved_letter.updated_at[:10]}."
            )

    try:
        pdf_bytes = render_cover_letter_pdf(letter_content)
    except CoverLetterError as error:
        st.error(str(error))
    else:
        st.download_button(
            "Télécharger la lettre en PDF",
            data=pdf_bytes,
            file_name=f"lettre-candidature-{scope_token}.pdf",
            mime="application/pdf",
            key=f"download_cover_letter_{scope_token}",
        )


def _company_form(
    key: str,
    company: CompanyData | None = None,
) -> CompanyData | None:
    with st.form(key):
        name = st.text_input("Nom de l'entreprise", value=company.name if company else "")
        location = st.text_input(
            "Ville / zone",
            value=company.location if company else "",
            help="Exemples : Nice, Cannes, Toulon, Var",
        )
        website_url = st.text_input(
            "Site internet (facultatif)",
            value=company.website_url if company and company.website_url else "",
        )
        source_url = st.text_input(
            "Lien justifiant l'activité ou l'équipe de développement",
            value=company.source_url if company else "",
        )
        development_evidence = st.text_area(
            "Éléments vérifiés sur l'activité / l'équipe tech",
            value=company.development_evidence if company else "",
            help="Note la preuve consultée (ex. page équipe, offre tech, activité logicielle).",
        )
        public_contact_email = st.text_input(
            "E-mail de contact public (facultatif)",
            value=company.public_contact_email if company and company.public_contact_email else "",
        )
        contact_source_url = st.text_input(
            "Lien source de l'e-mail (obligatoire si e-mail saisi)",
            value=company.contact_source_url if company and company.contact_source_url else "",
        )
        notes = st.text_area("Notes (facultatif)", value=company.notes if company else "")
        submitted = st.form_submit_button(
            "Enregistrer l'entreprise" if company is None else "Mettre à jour l'entreprise",
            type="primary",
        )

    if not submitted:
        return None
    return CompanyData(
        name=name,
        location=location,
        website_url=website_url or None,
        source_url=source_url,
        development_evidence=development_evidence,
        public_contact_email=public_contact_email or None,
        contact_source_url=contact_source_url or None,
        notes=notes,
    )


def show_companies_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Entreprises à prospecter")
    st.write(
        "Constituez une liste sourcée d'entreprises de votre zone ayant une activité ou une "
        "équipe de développement. Une fiche n'est pas une confirmation qu'elles recrutent."
    )
    st.caption(
        f"Zone de recherche du profil : {', '.join(profile.local_locations) or 'à définir'}. "
        "Les entreprises doivent être ajoutées après vérification de leurs sources."
    )
    with st.expander("Ajouter une entreprise", expanded=not list_companies(engine)):
        try:
            company_data = _company_form("new_company")
            if company_data is not None:
                create_company(engine, company_data)
                st.success("Entreprise ajoutée à la liste de prospection.")
                st.rerun()
        except ValidationError as error:
            for issue in error.errors():
                st.error(issue["msg"])
        except DuplicateCompany as error:
            st.error(str(error))

    filter_location = st.text_input(
        "Filtrer par ville ou zone",
        value="",
        placeholder="Nice, Cannes, Var…",
    )
    companies = list_companies(engine, filter_location or None)
    st.subheader(f"Entreprises enregistrées ({len(companies)})")
    if not companies:
        st.info(
            "Aucune entreprise n'est préchargée. Ajoute uniquement des entreprises vérifiées "
            "et conserve le lien de la source consultée."
        )
        return

    for company in companies:
        with st.expander(f"{company.name} — {company.location}"):
            fit = assess_company_fit(company, profile)
            st.write(f"**Activité / adéquation documentée :** {fit.activity_label}")
            st.caption(
                f"Fiche mise à jour le {company.verified_at[:10]} · "
                "Vérifie l'activité, l'équipe tech et les recrutements avant de candidater."
            )
            st.markdown(f"[Voir la source]({company.source_url})")
            if company.website_url:
                st.markdown(f"[Site de l'entreprise]({company.website_url})")
            st.write(company.development_evidence)
            if fit.mentioned_skills:
                st.write("**Compétences du profil mentionnées dans les informations :**")
                for match in fit.mentioned_skills:
                    st.write(f"- {match.skill.name} — « {match.evidence} »")
            elif profile.skills:
                st.caption(
                    "Aucune compétence du profil n'est documentée dans cette fiche ; "
                    "cela ne permet pas de conclure que l'entreprise ne l'utilise pas."
                )
            if company.public_contact_email:
                st.write(f"**Contact public :** {company.public_contact_email}")
                st.caption(f"Source du contact : {company.contact_source_url}")
            if company.notes:
                st.write(company.notes)
            try:
                edited_company = _company_form(
                    f"edit_company_{company.id}",
                    CompanyData(
                        name=company.name,
                        location=company.location,
                        website_url=company.website_url,
                        source_url=company.source_url,
                        development_evidence=company.development_evidence,
                        public_contact_email=company.public_contact_email,
                        contact_source_url=company.contact_source_url,
                        notes=company.notes,
                    ),
                )
                if edited_company is not None:
                    update_company(engine, company.id, edited_company)
                    st.success("Fiche entreprise mise à jour.")
                    st.rerun()
            except ValidationError as error:
                for issue in error.errors():
                    st.error(issue["msg"])
            except (CompanyInUse, CompanyNotFound, DuplicateCompany) as error:
                st.error(str(error))

            if st.button("Supprimer cette entreprise", key=f"delete_company_{company.id}"):
                try:
                    delete_company(engine, company.id)
                except (CompanyInUse, CompanyNotFound) as error:
                    st.error(str(error))
                else:
                    st.success("Entreprise supprimée.")
                    st.rerun()


def show_company_discovery_page(engine: Engine) -> None:
    st.title("Découvrir des entreprises")
    bonne_boite_tab, registry_tab = st.tabs(
        ["La Bonne Boîte (potentiel d'embauche)", "Registre public (activité déclarée)"]
    )
    with bonne_boite_tab:
        _show_bonne_boite_tab(engine)
    with registry_tab:
        _show_registry_discovery_tab(engine)


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
            default=list(BONNE_BOITE_DEFAULT_DEPARTMENTS),
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


def _show_registry_discovery_tab(engine: Engine) -> None:
    st.write(
        "Recherche des sociétés actives dans les Alpes-Maritimes (06), à Paris (75) ou "
        "dans le Var (83) à partir de l'API publique Recherche d'entreprises. Les pistes "
        "ajoutées ici deviennent sélectionnables pour une candidature spontanée."
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
        department = st.selectbox(
            "Département",
            options=["06", "75", "83"],
            format_func=lambda value: {
                "06": "Alpes-Maritimes — Nice, Cannes, Antibes…",
                "75": "Paris",
                "83": "Var — Toulon, Draguignan, Fréjus…",
            }[value],
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


def show_profile_fit_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Correspondance avec mon profil")
    st.write(
        "Classe les offres et les entreprises enregistrées selon les informations réellement "
        "disponibles. Les scores sont indicatifs et chaque critère est expliqué."
    )
    st.caption(
        "Les offres sont évaluées selon compétences, intitulé, contrat et zone quand ces "
        "informations existent dans ton profil. Pour les entreprises, le registre ne renseigne "
        "pas les technologies des équipes : leur activité déclarée n'est pas assimilée à une "
        "correspondance de compétences."
    )

    offer_tab, company_tab = st.tabs(["Offres", "Entreprises"])
    offers = list_offers(engine)
    with offer_tab:
        st.subheader(f"Offres évaluées ({len(offers)})")
        if not offers:
            st.info("Enregistre ou importe des offres pour les comparer à ton profil.")
        elif not profile.skills and not profile.target_role:
            st.warning(
                "Renseigne au moins un rôle cible ou des compétences dans « Profil » "
                "pour obtenir un classement utile."
            )
        else:
            assessments = []
            for offer in offers:
                offer_data = JobOfferData(
                    title=offer.title,
                    company=offer.company,
                    location=offer.location,
                    contract_type=offer.contract_type,
                    url=offer.url,
                    source=offer.source,
                    status=offer.status,
                    description=offer.description,
                )
                assessments.append(
                    (offer, assess_offer_fit(offer_data, profile))
                )
            assessments.sort(
                key=lambda item: (
                    item[1].overall_percentage is not None,
                    item[1].overall_percentage or 0,
                ),
                reverse=True,
            )
            for offer, assessment in assessments:
                percentage = assessment.overall_percentage
                score_label = f"{percentage}% indicatif" if percentage is not None else "À évaluer"
                with st.expander(
                    f"{score_label} · {offer.title} — "
                    f"{offer.company or offer.location or 'Entreprise non précisée'}"
                ):
                    if percentage is not None:
                        st.progress(percentage / 100)
                        st.caption(
                            "Score pondéré parmi les critères renseignés : compétences "
                            "(60 %), intitulé (20 %), contrat et lieu (10 % chacun). "
                            "Les critères non renseignés sont exclus du calcul."
                        )
                    skill_match = assessment.skills
                    if skill_match.matches:
                        st.write(
                            f"**Compétences citées :** {skill_match.mentioned_count}/"
                            f"{len(skill_match.matches)} "
                            f"(score compétences : {skill_match.match_percentage} %)"
                        )
                        technologies_caption = _required_technologies_text(skill_match)
                        if technologies_caption:
                            st.caption(technologies_caption)
                        for match in skill_match.matches:
                            if match.mentioned:
                                st.write(
                                    f"- **{match.skill.name}** — « {match.evidence} »"
                                )
                        missing_skills = [
                            match.skill.name
                            for match in skill_match.matches
                            if not match.mentioned
                        ]
                        if missing_skills:
                            st.caption(
                                "Compétences non mentionnées dans l'annonce — "
                                "à vérifier, pas nécessairement absentes du profil : "
                                + ", ".join(missing_skills)
                            )
                    elif profile.skills:
                        st.info("Aucune compétence du profil n'est citée dans l'annonce.")
                    if assessment.role_percentage is not None:
                        st.write(
                            f"**Proximité de l'intitulé avec le rôle cible :** "
                            f"{assessment.role_percentage} %"
                        )
                    if assessment.contract_compatible is not None:
                        st.write(
                            "**Contrat :** "
                            + (
                                "correspond aux préférences"
                                if assessment.contract_compatible
                                else "différent des préférences renseignées"
                            )
                        )
                    if assessment.location_compatible is not None:
                        st.write(f"**Localisation :** {assessment.location_reason}")
                    st.write(f"**Statut :** {offer.status}")
                    if offer.url:
                        st.markdown(f"[Ouvrir l'offre]({offer.url})")

    companies = list_companies(engine)
    candidates = list_company_candidates(engine)
    with company_tab:
        st.subheader(
            f"Entreprises à prospecter ({len(companies)}) · pistes à vérifier ({len(candidates)})"
        )
        if not companies and not candidates:
            st.info(
                "Ajoute des entreprises à prospecter ou conserve des pistes dans "
                "« Découvrir des entreprises » pour les examiner."
            )
        company_assessments = [
            (company.name, company.location, company, assess_company_fit(company, profile))
            for company in companies
        ]
        candidate_assessments = [
            (
                candidate.name,
                candidate.location,
                candidate,
                assess_company_fit(candidate, profile),
            )
            for candidate in candidates
        ]
        all_company_assessments = company_assessments + candidate_assessments
        all_company_assessments.sort(
            key=lambda item: (
                item[3].activity_is_software,
                len(item[3].mentioned_skills),
                item[0].casefold(),
            ),
            reverse=True,
        )
        for name, location, company_record, assessment in all_company_assessments:
            record_type = (
                "Piste du registre à vérifier"
                if isinstance(company_record, CompanyCandidate)
                else "Entreprise à prospecter"
            )
            with st.expander(f"{name} — {location} · {record_type}"):
                st.write(f"**Indice d'activité :** {assessment.activity_label}")
                st.caption(
                    "Cet indice décrit les données de registre ou les notes de l'entreprise, "
                    "pas les technologies réellement utilisées par une équipe."
                )
                if assessment.mentioned_skills:
                    st.write("**Compétences explicitement citées dans les informations :**")
                    for match in assessment.mentioned_skills:
                        st.write(f"- **{match.skill.name}** — « {match.evidence} »")
                elif profile.skills:
                    st.write(
                        "**Compétences du profil citées :** aucune dans les informations "
                        "enregistrées. Cela ne signifie pas que l'entreprise ne possède pas "
                        "d'équipe utilisant ces technologies."
                    )
                if isinstance(company_record, CompanyCandidate):
                    st.markdown(
                        f"[Fiche officielle]({company_record.source_url})"
                    )
                else:
                    st.markdown(f"[Source de l'entreprise]({company_record.source_url})")


def show_email_page(engine: Engine) -> None:
    st.title("Envoyer une candidature par e-mail")
    st.write(
        "Prépare un message individuel, vérifie le destinataire et envoie-le via l'API "
        "transactionnelle Mailjet. Aucun message n'est envoyé automatiquement."
    )
    companies = [
        company
        for company in list_companies(engine)
        if company.public_contact_email
    ]
    if not companies:
        st.info(
            "Ajoute d'abord une entreprise vérifiée avec un e-mail public et sa source dans "
            "« Entreprises à prospecter ». Les résultats bruts du registre ne fournissent "
            "pas de contact e-mail vérifié."
        )
        return

    try:
        secrets = {
            key: st.secrets.get(key, "")
            for key in (
                "MAILJET_API_KEY",
                "MAILJET_API_SECRET",
                "MAILJET_FROM_EMAIL",
                "MAILJET_FROM_NAME",
            )
        }
    except StreamlitSecretNotFoundError:
        secrets = {}
    api_key, api_secret, sender_email, sender_name = get_mailjet_settings(secrets)
    if not all((api_key, api_secret, sender_email, sender_name)):
        st.warning(
            "Configure les quatre valeurs Mailjet ci-dessous dans "
            "`.streamlit/secrets.toml` ou comme variables d'environnement locales. "
            "L'adresse expéditrice doit être vérifiée et active dans Mailjet."
        )
        st.code(
            'MAILJET_API_KEY = "clé publique"\n'
            'MAILJET_API_SECRET = "clé privée"\n'
            'MAILJET_FROM_EMAIL = "toi@ton-domaine.fr"\n'
            'MAILJET_FROM_NAME = "Prénom Nom"',
            language="toml",
        )

    company_by_id = {company.id: company for company in companies}
    company_id = st.selectbox(
        "Entreprise destinataire",
        options=list(company_by_id),
        format_func=lambda item_id: (
            f"{company_by_id[item_id].name} — "
            f"{company_by_id[item_id].public_contact_email}"
        ),
    )
    company = company_by_id[company_id]
    st.caption(f"Source du contact : {company.contact_source_url}")

    offers = list_offers(engine)
    offer_options: list[str | None] = [None, *(offer.id for offer in offers)]
    offer_by_id = {offer.id: offer for offer in offers}
    offer_id = st.selectbox(
        "Offre associée (facultatif)",
        options=offer_options,
        format_func=lambda item_id: (
            "Candidature spontanée"
            if item_id is None
            else f"{offer_by_id[item_id].title} — "
            f"{offer_by_id[item_id].company or offer_by_id[item_id].location}"
        ),
        key=f"email_offer_{company_id}",
    )
    tailored_resume = get_tailored_resume(engine, offer_id) if offer_id else None
    attach_resume = False
    if tailored_resume:
        attach_resume = st.checkbox(
            "Joindre le CV personnalisé PDF enregistré pour cette offre",
            value=True,
            key=f"email_attach_resume_{company_id}_{offer_id}",
        )
    elif offer_id:
        st.info(
            "Aucun CV personnalisé n'est encore enregistré pour cette offre. "
            "Tu peux préparer et enregistrer un CV dans « CV par offre »."
        )

    suggested_subject = (
        f"Candidature — {offer_by_id[offer_id].title}"
        if offer_id
        else "Candidature spontanée"
    )
    subject = st.text_input(
        "Objet",
        value=suggested_subject,
        key=f"email_subject_{company_id}_{offer_id or 'spontaneous'}",
    )
    message_body = st.text_area(
        "Message",
        value=(
            "Bonjour,\n\n"
            "Je vous contacte au sujet de votre entreprise et souhaite vous proposer "
            "ma candidature"
            + (
                f" pour le poste de {offer_by_id[offer_id].title}."
                if offer_id
                else " spontanée."
            )
            + "\n\nJe serais ravi d'échanger avec vous.\n\nCordialement,"
        ),
        height=240,
        key=f"email_body_{company_id}_{offer_id or 'spontaneous'}",
    )
    st.warning(
        f"Envoi immédiat à **{company.public_contact_email}** depuis "
        f"**{sender_email or 'l’adresse expéditrice Mailjet configurée'}**. "
        "Relis le message et la pièce jointe avant de confirmer."
    )
    message_signature = hashlib.sha256(
        "\0".join(
            (
                company_id,
                str(company.public_contact_email),
                offer_id or "",
                subject,
                message_body,
                tailored_resume.content if attach_resume and tailored_resume else "",
            )
        ).encode("utf-8")
    ).hexdigest()
    confirmation_key = f"email_confirm_{message_signature}"
    sent_signatures = st.session_state.get("sent_email_signatures", [])
    was_sent = message_signature in sent_signatures
    if was_sent:
        st.info("Ce message a déjà été accepté par Mailjet pendant cette session.")
    confirmed = st.checkbox(
        f"Je confirme l'envoi de cet e-mail à {company.public_contact_email}.",
        key=confirmation_key,
        disabled=was_sent,
    )
    if st.button(
        "Envoyer via Mailjet",
        type="primary",
        disabled=(
            not confirmed
            or was_sent
            or not all((api_key, api_secret, sender_email, sender_name))
        ),
    ):
        attachment = None
        attachment_filename = None
        if attach_resume and tailored_resume:
            try:
                attachment = render_tailored_resume_pdf(tailored_resume.content)
            except TailoredResumeError as error:
                st.error(f"Impossible de préparer la pièce jointe : {error}")
                return
            attachment_filename = tailored_cv_filename(
                json.loads(tailored_resume.content),
                target_role=offer_by_id[offer_id].title,
                target_company=company.name,
            )
        try:
            message_id = send_application_email(
                api_key=api_key,
                api_secret=api_secret,
                sender_email=sender_email,
                sender_name=sender_name,
                recipient_email=company.public_contact_email,
                subject=subject,
                text_body=message_body,
                attachment=attachment,
                attachment_filename=attachment_filename,
            )
        except EmailDeliveryError as error:
            st.error(str(error))
        else:
            st.session_state["sent_email_signatures"] = [
                *sent_signatures,
                message_signature,
            ]
            st.success(
                f"Mailjet a accepté l'envoi à {company.public_contact_email} "
                f"(identifiant de message : {message_id})."
            )


def _application_form(
    key: str,
    company_options: dict[str, str],
    offer_options: dict[str, str],
    application: ApplicationData | None = None,
) -> ApplicationData | None:
    company_ids = list(company_options)
    with st.form(key):
        company_id = st.selectbox(
            "Entreprise",
            options=company_ids,
            index=company_ids.index(application.company_id)
            if application and application.company_id in company_ids
            else 0,
            format_func=company_options.__getitem__,
        )
        offer_ids: list[str | None] = [None, *offer_options]
        current_offer_id = application.job_offer_id if application else None
        offer_id = st.selectbox(
            "Offre (laisser vide pour une candidature spontanée)",
            options=offer_ids,
            index=offer_ids.index(current_offer_id)
            if current_offer_id in offer_ids
            else 0,
            format_func=lambda value: offer_options[value] if value else "Candidature spontanée",
        )
        role = st.text_input("Poste visé", value=application.role if application else "")
        current_status = application.status if application else "À préparer"
        status = st.selectbox(
            "Statut",
            options=APPLICATION_STATUSES,
            index=APPLICATION_STATUSES.index(current_status)
            if current_status in APPLICATION_STATUSES
            else 0,
        )
        applied_on = st.text_input(
            "Date d'envoi (AAAA-MM-JJ, facultative)",
            value=application.applied_on if application and application.applied_on else "",
        )
        next_action = st.text_input(
            "Prochaine action",
            value=application.next_action if application else "",
        )
        next_action_on = st.text_input(
            "Date de prochaine action (AAAA-MM-JJ, facultative)",
            value=application.next_action_on if application and application.next_action_on else "",
        )
        notes = st.text_area("Notes", value=application.notes if application else "")
        submitted = st.form_submit_button(
            "Enregistrer la candidature" if application is None else "Mettre à jour",
            type="primary",
        )

    if not submitted:
        return None
    return ApplicationData(
        company_id=company_id,
        job_offer_id=offer_id,
        role=role,
        status=status,
        applied_on=applied_on or None,
        next_action=next_action,
        next_action_on=next_action_on or None,
        notes=notes,
    )


def show_applications_page(engine: Engine) -> None:
    st.title("Suivi des candidatures")
    st.write(
        "Enregistre les candidatures liées à une offre ou les démarches spontanées, "
        "avec leur statut et prochaine action."
    )
    companies = list_companies(engine)
    offers = list_offers(engine)
    if not companies:
        st.info("Ajoute d'abord une entreprise vérifiée dans « Entreprises à prospecter ».")
        return

    company_options = {company.id: f"{company.name} — {company.location}" for company in companies}
    offer_options = {
        offer.id: f"{offer.title} — {offer.company or offer.location or 'Offre'}"
        for offer in offers
    }
    with st.expander("Ajouter une candidature", expanded=not list_applications(engine)):
        try:
            data = _application_form("new_application", company_options, offer_options)
            if data is not None:
                create_application(engine, data)
                st.success("Candidature enregistrée.")
                st.rerun()
        except ValidationError as error:
            for issue in error.errors():
                st.error(issue["msg"])
        except ApplicationReferenceNotFound as error:
            st.error(str(error))

    applications = list_applications(engine)
    st.subheader(f"Candidatures enregistrées ({len(applications)})")
    if not applications:
        st.info("Aucune candidature suivie pour le moment.")
        return

    companies_by_id = {company.id: company for company in companies}
    for application in applications:
        company = companies_by_id.get(application.company_id)
        company_label = company.name if company else "Entreprise supprimée"
        with st.expander(f"{application.role} — {company_label} · {application.status}"):
            if application.job_offer_id and application.job_offer_id in offer_options:
                st.caption(f"Offre : {offer_options[application.job_offer_id]}")
            else:
                st.caption("Candidature spontanée")
            if application.next_action:
                due = f" — {application.next_action_on}" if application.next_action_on else ""
                st.write(f"**Prochaine action :** {application.next_action}{due}")
            try:
                data = _application_form(
                    f"edit_application_{application.id}",
                    company_options,
                    offer_options,
                    ApplicationData(
                        company_id=application.company_id,
                        job_offer_id=application.job_offer_id,
                        role=application.role,
                        status=application.status,
                        applied_on=application.applied_on,
                        next_action=application.next_action,
                        next_action_on=application.next_action_on,
                        notes=application.notes,
                    ),
                )
                if data is not None:
                    update_application(engine, application.id, data)
                    st.success("Candidature mise à jour.")
                    st.rerun()
            except ValidationError as error:
                for issue in error.errors():
                    st.error(issue["msg"])
            except (ApplicationNotFound, ApplicationReferenceNotFound) as error:
                st.error(str(error))

            if st.button(
                "Supprimer cette candidature",
                key=f"delete_application_{application.id}",
            ):
                try:
                    delete_application(engine, application.id)
                except ApplicationNotFound as error:
                    st.error(str(error))
                else:
                    st.success("Candidature supprimée.")
                    st.rerun()


def _build_queue_documents(engine: Engine, offer) -> dict[str, object]:
    """Génère à la demande les PDF du CV ciblé et de la lettre déjà enregistrés."""
    documents: dict[str, object] = {"cv": None, "letter": None, "errors": []}
    errors: list[str] = documents["errors"]  # type: ignore[assignment]
    resume = get_tailored_resume(engine, offer.id)
    if resume is not None:
        try:
            normalized = validate_tailored_resume_json(resume.content)
            documents["cv"] = (
                render_tailored_resume_pdf(normalized),
                tailored_cv_filename(
                    json.loads(normalized),
                    target_role=offer.title,
                    target_company=offer.company,
                ),
            )
        except TailoredResumeError as error:
            errors.append(f"CV : {error}")
    letter = get_cover_letter(engine, job_offer_id=offer.id)
    if letter is not None:
        try:
            letter_name = tailored_cv_filename(
                load_base_cv_data(), target_role=offer.title, target_company=offer.company
            ).replace("-cv-", "-lettre-", 1)
            documents["letter"] = (render_cover_letter_pdf(letter.content), letter_name)
        except CoverLetterError as error:
            errors.append(f"Lettre : {error}")
    return documents


def _show_follow_ups(engine: Engine) -> None:
    due = due_follow_ups(engine)
    if not due:
        return
    companies_by_id = {company.id: company for company in list_companies(engine)}
    st.subheader(f"À relancer ({len(due)})")
    for application in due:
        company = companies_by_id.get(application.company_id)
        company_label = company.name if company else "Entreprise supprimée"
        columns = st.columns([5, 2])
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
    st.markdown("---")


def _show_queue_card(engine: Engine, profile: ProfileData, item: QueueItem) -> None:
    offer = item.offer
    docs_key = f"queue_docs_{offer.id}"
    with st.container(border=True):
        st.markdown(
            f"**{normalize_job_title(offer.title)}** — "
            f"{offer.company or 'Entreprise non précisée'}"
        )
        details = [f"Score {item.score} %", offer.location or "Lieu non précisé", offer.source]
        if offer.contract_type:
            details.insert(2, offer.contract_type)
        if offer.status == "Intéressante":
            details.insert(0, "⭐")
        st.caption(" · ".join(details))
        st.caption(
            f"{'✅' if item.has_resume else '⚠️'} CV ciblé · "
            f"{'✅' if item.has_letter else '⚠️'} Lettre"
        )
        if offer.source.startswith("Adzuna") and len(offer.description) >= 480:
            st.caption(
                "Texte d'annonce tronqué par Adzuna : lis l'annonce d'origine avant d'envoyer."
            )

        actions = st.columns(4)
        if offer.url:
            actions[0].link_button("1. Ouvrir l'annonce", offer.url)
        else:
            actions[0].caption("Aucun lien enregistré")
        if item.is_ready:
            if actions[1].button("2. Préparer les PDF", key=f"queue_pdf_{offer.id}"):
                st.session_state[docs_key] = _build_queue_documents(engine, offer)
        elif actions[1].button("Préparer le dossier", key=f"queue_prepare_{offer.id}"):
            result = prepare_dossier_for_offer(engine, offer.id, offer_to_data(offer), profile)
            st.session_state["queue_flash"] = (
                f"Dossier incomplet : {result.error}" if result.error else "Dossier préparé."
            )
            st.rerun()
        if offer.status != "Intéressante" and actions[2].button(
            "⭐ Intéressante", key=f"queue_star_{offer.id}"
        ):
            set_offer_status(engine, offer.id, "Intéressante")
            st.rerun()
        if actions[3].button("Écarter", key=f"queue_discard_{offer.id}"):
            set_offer_status(engine, offer.id, "Écartée")
            st.session_state["queue_flash"] = "Offre écartée de la file."
            st.rerun()

        documents = st.session_state.get(docs_key)
        if documents:
            downloads = st.columns(2)
            for column, key, label in (
                (downloads[0], "cv", "Télécharger le CV (PDF)"),
                (downloads[1], "letter", "Télécharger la lettre (PDF)"),
            ):
                if documents[key]:
                    data, file_name = documents[key]
                    column.download_button(
                        label,
                        data=data,
                        file_name=file_name,
                        mime="application/pdf",
                        key=f"queue_download_{key}_{offer.id}",
                    )
            for message in documents["errors"]:
                st.warning(message)

        if item.has_letter:
            letter = get_cover_letter(engine, job_offer_id=offer.id)
            if letter is not None:
                with st.expander("Relire la lettre"):
                    st.text(letter.content)

        st.caption(
            "Relis le CV et la lettre, envoie-les toi-même sur le site de l'employeur, "
            "puis enregistre l'envoi."
        )
        if st.button(
            "3. ✅ J'ai envoyé ma candidature",
            key=f"queue_sent_{offer.id}",
            type="primary",
            help=f"Crée la candidature « Envoyée » avec une relance dans {FOLLOW_UP_DAYS} jours.",
        ):
            try:
                mark_offer_sent(engine, offer.id)
            except (OfferNotFound, OfferAlreadySent) as error:
                st.error(str(error))
            else:
                st.session_state.pop(docs_key, None)
                st.session_state["queue_flash"] = (
                    f"Envoi enregistré. Relance prévue dans {FOLLOW_UP_DAYS} jours."
                )
                st.rerun()


def show_send_queue_page(engine: Engine, profile: ProfileData) -> None:
    st.title("File d'envoi")
    st.write(
        "Les offres les plus pertinentes et les entreprises à contacter, avec leur CV et leur "
        "lettre prêts. L'envoi reste manuel : relis les documents, candidate sur le site ou "
        "par e-mail, puis enregistre l'envoi pour déclencher le suivi et la relance."
    )
    flash = st.session_state.pop("queue_flash", None)
    if flash:
        st.success(flash)

    _show_follow_ups(engine)

    offers_tab, spontaneous_tab = st.tabs(["Offres", "Candidatures spontanées"])
    with offers_tab:
        _show_offers_queue(engine, profile)
    with spontaneous_tab:
        _show_spontaneous_queue(engine, profile)


def _show_offers_queue(engine: Engine, profile: ProfileData) -> None:
    controls = st.columns(2)
    min_score = controls[0].slider(
        "Score minimal",
        min_value=0,
        max_value=90,
        value=load_watcher_config().min_match_percentage,
        step=5,
        help="Les offres marquées « Intéressante » restent affichées sous ce seuil.",
    )
    limit = controls[1].selectbox("Offres affichées", options=[10, 20, 50], index=0)
    queue = build_send_queue(engine, profile, min_score)

    ready_count = sum(item.is_ready for item in queue)
    metrics = st.columns(2)
    metrics[0].metric("Offres à traiter", len(queue))
    metrics[1].metric("Dossiers complets", ready_count)

    if not queue:
        st.info(
            "Aucune offre à traiter à ce seuil. Lance la veille ou baisse le score minimal "
            "(les offres déjà envoyées ou écartées n'apparaissent pas)."
        )
        return
    if ready_count < len(queue):
        st.caption(
            "Les dossiers incomplets se préparent offre par offre, ou en lot depuis "
            "« Recherche en ligne → Veille automatique planifiée »."
        )
    for item in queue[:limit]:
        _show_queue_card(engine, profile, item)
    if len(queue) > limit:
        st.caption(f"{len(queue) - limit} autre(s) offre(s) non affichée(s).")


def _build_spontaneous_documents(company: Company, letter_content: str) -> dict[str, object]:
    """Génère à la demande le CV de base et la lettre spontanée en PDF."""
    documents: dict[str, object] = {"cv": None, "letter": None, "errors": []}
    errors: list[str] = documents["errors"]  # type: ignore[assignment]
    base_cv = load_base_cv_data()
    cv_name = tailored_cv_filename(base_cv, target_company=company.name)
    try:
        documents["cv"] = (render_cv_pdf(base_cv), cv_name)
    except (OSError, ValueError, KeyError) as error:
        errors.append(f"CV : {error}")
    try:
        documents["letter"] = (
            render_cover_letter_pdf(letter_content),
            cv_name.replace("-cv-", "-lettre-", 1),
        )
    except CoverLetterError as error:
        errors.append(f"Lettre : {error}")
    return documents


def _show_spontaneous_card(engine: Engine, profile: ProfileData, item: SpontaneousItem) -> None:
    company = item.company
    docs_key = f"queue_spontaneous_docs_{company.id}"
    letter = get_cover_letter(engine, company_id=company.id)
    with st.container(border=True):
        st.markdown(f"**{company.name}** — {company.location}")
        st.caption(company.development_evidence[:260])
        if company.public_contact_email:
            st.write(f"**Contact public :** {company.public_contact_email}")
        else:
            st.caption(
                "Aucun contact enregistré : trouve l'adresse ou le formulaire de candidature "
                "sur le site de l'entreprise."
            )
        links = st.columns(3)
        for column, (label, url) in zip(links, contact_search_links(company), strict=False):
            column.link_button(label, url)
        if company.source_url.startswith(("http://", "https://")):
            links[2].link_button("Fiche officielle", company.source_url)

        if letter is None:
            if st.button("1. Préparer la lettre", key=f"spont_prepare_{company.id}"):
                try:
                    save_cover_letter(
                        engine,
                        build_cover_letter(None, profile, company.name),
                        company_id=company.id,
                        target_company=company.name,
                    )
                except CoverLetterError as error:
                    st.error(str(error))
                else:
                    st.session_state["queue_flash"] = "Lettre préparée : relis-la avant l'envoi."
                    st.rerun()
        else:
            with st.expander("Relire et modifier la lettre"):
                edited = st.text_area(
                    "Lettre",
                    value=letter.content,
                    height=320,
                    key=f"spont_letter_{company.id}",
                    label_visibility="collapsed",
                )
                if st.button("Enregistrer la lettre", key=f"spont_save_letter_{company.id}"):
                    try:
                        save_cover_letter(
                            engine, edited, company_id=company.id, target_company=company.name
                        )
                    except CoverLetterError as error:
                        st.error(str(error))
                    else:
                        st.session_state.pop(docs_key, None)
                        st.session_state["queue_flash"] = "Lettre enregistrée."
                        st.rerun()
            if st.button("2. Préparer les PDF", key=f"spont_pdf_{company.id}"):
                st.session_state[docs_key] = _build_spontaneous_documents(
                    company, letter.content
                )

        documents = st.session_state.get(docs_key)
        if documents:
            downloads = st.columns(2)
            for column, key, label in (
                (downloads[0], "cv", "Télécharger le CV (PDF)"),
                (downloads[1], "letter", "Télécharger la lettre (PDF)"),
            ):
                if documents[key]:
                    data, file_name = documents[key]
                    column.download_button(
                        label,
                        data=data,
                        file_name=file_name,
                        mime="application/pdf",
                        key=f"spont_download_{key}_{company.id}",
                    )
            for message in documents["errors"]:
                st.warning(message)

        st.caption(
            "Envoie ta candidature toi-même (e-mail, formulaire du site, LinkedIn), puis "
            "enregistre l'envoi. Pour un e-mail depuis l'application, utilise « Envoyer un "
            "e-mail » (contact public sourcé requis)."
        )
        role = st.text_input(
            "Poste visé",
            value=profile.target_role or "Développeur",
            key=f"spont_role_{company.id}",
        )
        if st.button(
            "3. ✅ J'ai envoyé ma candidature",
            key=f"spont_sent_{company.id}",
            type="primary",
            help=f"Crée la candidature « Envoyée » avec une relance dans {FOLLOW_UP_DAYS} jours.",
        ):
            try:
                mark_spontaneous_sent(engine, company.id, role=role)
            except (ValueError, CompanyNotFound) as error:
                st.error(str(error))
            else:
                st.session_state.pop(docs_key, None)
                st.session_state["queue_flash"] = (
                    f"Envoi enregistré pour {company.name}. Relance prévue dans "
                    f"{FOLLOW_UP_DAYS} jours."
                )
                st.rerun()


def _show_spontaneous_queue(engine: Engine, profile: ProfileData) -> None:
    st.write(
        "Les entreprises à prospecter (par exemple issues de La Bonne Boîte) qui n'ont pas "
        "encore reçu de candidature. Prépare la lettre, trouve le contact, envoie toi-même "
        "puis enregistre l'envoi pour déclencher la relance."
    )
    queue = build_spontaneous_queue(engine)
    metrics = st.columns(2)
    metrics[0].metric("Entreprises à contacter", len(queue))
    metrics[1].metric("Lettres prêtes", sum(item.has_letter for item in queue))
    if not queue:
        st.info(
            "Aucune entreprise à contacter. Ajoute des pistes depuis « Recherche → Découvrir "
            "des entreprises » (onglet La Bonne Boîte)."
        )
        return
    limit = st.selectbox("Entreprises affichées", options=[10, 20, 50], index=0)
    for item in queue[:limit]:
        _show_spontaneous_card(engine, profile, item)
    if len(queue) > limit:
        st.caption(f"{len(queue) - limit} autre(s) entreprise(s) non affichée(s).")


engine = get_engine()
profile = load_or_seed_profile(engine)
st.markdown(
    """
    <style>
    [data-testid="stSidebar"] {
        border-right: 1px solid rgba(120, 130, 150, 0.18);
    }
    [data-testid="stSidebar"] [role="radiogroup"] {
        gap: 0.25rem;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label {
        padding: 0.35rem 0.55rem;
        border-radius: 0.5rem;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label:hover {
        background: rgba(100, 120, 150, 0.10);
    }
    </style>
    """,
    unsafe_allow_html=True,
)
with st.sidebar:
    st.title("Candidatures")
    st.caption("Recherche · préparation · suivi")
    section = st.radio(
        "Rubriques",
        ["Vue d'ensemble", "Recherche", "Candidatures"],
        label_visibility="collapsed",
    )
    page_groups = {
        "Vue d'ensemble": ["Accueil", "Profil", "CV de référence"],
        "Recherche": [
            "Offres",
            "Recherche en ligne",
            "Découvrir des entreprises",
            "Entreprises à prospecter",
        ],
        "Candidatures": [
            "File d'envoi",
            "CV par offre",
            "Candidature spontanée",
            "Envoyer un e-mail",
            "Candidatures",
        ],
    }
    st.markdown("---")
    page = st.radio(
        section,
        page_groups[section],
        label_visibility="collapsed",
        key=f"navigation_{section}",
    )
if page == "Profil":
    show_profile_form(engine, profile)
elif page == "CV de référence":
    show_resume_page(engine)
elif page == "Offres":
    show_offers_page(engine, profile)
elif page == "Recherche en ligne":
    show_job_search_page(engine, profile)
elif page == "File d'envoi":
    show_send_queue_page(engine, profile)
elif page == "CV par offre":
    show_tailored_resume_page(engine, profile)
elif page == "Candidature spontanée":
    show_cover_letter_page(engine, profile)
elif page == "Découvrir des entreprises":
    show_company_discovery_page(engine)
elif page == "Entreprises à prospecter":
    show_companies_page(engine, profile)
elif page == "Envoyer un e-mail":
    show_email_page(engine)
elif page == "Candidatures":
    show_applications_page(engine)
else:
    show_home(profile)

st.caption("Application locale — les bases, CV importés et exports restent sur cet ordinateur.")
