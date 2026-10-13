"""Page « CV par offre »."""

from __future__ import annotations

import json

import streamlit as st
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.models import JobOfferData, ProfileData, normalize_job_title
from src.services.cv_renderer import load_base_cv_data, tailored_cv_filename
from src.services.job_offers import list_offers
from src.services.matching import assess_offer_fit
from src.services.offer_keywords import keyword_report
from src.services.resume_import import list_resume_versions
from src.services.tailored_resumes import (
    TailoredResumeError,
    build_tailored_resume,
    get_tailored_resume,
    relocation_question_for_offer,
    render_tailored_resume_pdf,
    save_tailored_resume,
    validate_tailored_resume_json,
)
from src.ui.commun import _show_keyword_report
from src.ui.pages.spontanee import show_cover_letter_page


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
                            width="stretch",
                        )
                else:
                    st.button(
                        "PDF non généré",
                        key=f"tailored_batch_pdf_unavailable_{offer.id}",
                        disabled=True,
                        width="stretch",
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
    try:
        _show_keyword_report(keyword_report(offer_data, load_base_cv_data(), profile))
    except (OSError, ValueError):
        pass
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
        "Le brouillon part de cv_base.json (dossier data/ par défaut), au format ATS à une "
        "colonne. Le CV de "
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
