"""Page « File d'envoi » : offres, candidatures spontanées et fenêtre « Candidater »."""

from __future__ import annotations

import json
import time

import streamlit as st
from sqlalchemy import Engine

from src.models import Company, ProfileData
from src.services.companies import CompanyNotFound
from src.services.cover_letters import (
    CoverLetterError,
    build_cover_letter,
    get_cover_letter,
    render_cover_letter_pdf,
    save_cover_letter,
)
from src.services.cv_curation import CV_CURATED, CV_TO_CURATE
from src.services.cv_renderer import load_base_cv_data, render_cv_pdf, tailored_cv_filename
from src.services.dossier_generator import prepare_dossier_for_offer
from src.services.job_offers import OfferNotFound, get_offer
from src.services.job_titles import extract_job_role
from src.services.job_watcher import load_watcher_config
from src.services.offer_reading import list_offers_to_read
from src.services.send_queue import (
    FOLLOW_UP_DAYS,
    OfferAlreadySent,
    QueueItem,
    SpontaneousItem,
    build_send_queue,
    build_spontaneous_queue,
    clamp_prep_seconds,
    complete_offer_description,
    contact_search_links,
    group_queue,
    is_probably_truncated,
    mark_offer_sent,
    mark_spontaneous_sent,
    offer_to_data,
    set_offer_status,
)
from src.services.tailored_resumes import (
    TailoredResumeError,
    get_tailored_resume,
    render_tailored_resume_pdf,
    validate_tailored_resume_json,
)
from src.ui.commun import _format_duration, _score_badge, _show_follow_ups


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


def _show_complete_description_form(engine: Engine, profile: ProfileData, offer) -> None:
    st.caption(
        "Ouvre l'annonce d'origine, copie son texte complet et colle-le ici. Le score est "
        "recalculé avec les technologies réellement demandées."
    )
    pasted = st.text_area(
        "Texte complet de l'annonce",
        height=220,
        key=f"queue_full_text_{offer.id}",
        placeholder="Colle ici le texte complet de l'annonce…",
    )
    regenerate = st.checkbox(
        "Régénérer le CV ciblé et la lettre avec ce texte",
        value=True,
        key=f"queue_full_regen_{offer.id}",
        help="Écrase le CV ciblé et la lettre déjà enregistrés pour cette offre, y compris "
        "tes modifications. Décoche pour ne changer que le texte et le score.",
    )
    if st.button("Enregistrer le texte complet", key=f"queue_full_save_{offer.id}"):
        try:
            update = complete_offer_description(
                engine, offer.id, pasted, profile, regenerate=regenerate
            )
        except (ValueError, OfferNotFound) as error:
            st.error(str(error))
            return
        message = f"Texte enregistré : score {update.score_before} % → {update.score_after} %."
        if update.dossier_regenerated:
            message += " CV ciblé et lettre régénérés."
        if update.dossier_error:
            message += f" Dossier non régénéré : {update.dossier_error}"
        for key in (f"queue_docs_{offer.id}", f"queue_full_text_{offer.id}"):
            st.session_state.pop(key, None)
        st.session_state["queue_flash"] = message
        st.rerun()


@st.dialog("Candidater", width="large")
def _application_dialog(engine: Engine, profile: ProfileData, offer_id: str) -> None:
    offer = get_offer(engine, offer_id)
    if offer is None:
        st.error("Cette offre n'existe plus.")
        return
    docs_key = f"queue_docs_{offer.id}"
    st.markdown(
        f"### {extract_job_role(offer.title, offer.location)}\n"
        f"{offer.company or 'Entreprise non précisée'} · {offer.location or 'Lieu non précisé'}"
    )
    if docs_key not in st.session_state:
        with st.spinner("Préparation des PDF…"):
            st.session_state[docs_key] = _build_queue_documents(engine, offer)
    documents = st.session_state[docs_key]

    st.markdown("**1. Ouvre l'annonce et récupère tes documents**")
    columns = st.columns(3)
    if offer.url:
        columns[0].link_button("Ouvrir l'annonce", offer.url, width="stretch")
    else:
        columns[0].caption("Aucun lien enregistré")
    for column, key, label in (
        (columns[1], "cv", "CV (PDF)"),
        (columns[2], "letter", "Lettre (PDF)"),
    ):
        if documents[key]:
            data, file_name = documents[key]
            column.download_button(
                label,
                data=data,
                file_name=file_name,
                mime="application/pdf",
                key=f"queue_download_{key}_{offer.id}",
                icon=":material/download:",
                width="stretch",
            )
        else:
            column.caption(f"{label} indisponible")
    for message in documents["errors"]:
        st.warning(message)

    letter = get_cover_letter(engine, job_offer_id=offer.id)
    truncated = is_probably_truncated(offer)
    letter_tab, text_tab = st.tabs(
        ["Lettre à relire", "Texte de l'annonce" + (" (tronqué)" if truncated else "")]
    )
    with letter_tab:
        if letter is not None:
            with st.container(height=280):
                st.text(letter.content)
        else:
            st.info("Aucune lettre enregistrée : prépare d'abord le dossier.")
    with text_tab:
        if truncated:
            st.warning(
                "Texte tronqué par Adzuna : colle le texte complet pour un score et un "
                "dossier fidèles à l'annonce."
            )
        _show_complete_description_form(engine, profile, offer)

    st.markdown("**2. Une fois envoyée, enregistre l'envoi**")
    if st.button(
        "J'ai envoyé ma candidature",
        key=f"queue_sent_{offer.id}",
        type="primary",
        icon=":material/send:",
        width="stretch",
        help=f"Crée la candidature « Envoyée » avec une relance dans {FOLLOW_UP_DAYS} jours.",
    ):
        started = st.session_state.pop(f"queue_started_{offer.id}", None)
        prep = clamp_prep_seconds(time.time() - started) if started else None
        try:
            mark_offer_sent(engine, offer.id, prep_seconds=prep)
        except (OfferNotFound, OfferAlreadySent) as error:
            st.error(str(error))
        else:
            st.session_state.pop(docs_key, None)
            duration = f" en {_format_duration(prep)}" if prep is not None else ""
            st.session_state["queue_flash"] = (
                f"Candidature enregistrée{duration}. Relance prévue dans {FOLLOW_UP_DAYS} jours."
            )
            st.rerun()


def _show_queue_card(engine: Engine, profile: ProfileData, item: QueueItem) -> None:
    offer = item.offer
    with st.container(border=True):
        columns = st.columns([6, 3], vertical_alignment="center")
        badges = [_score_badge(item.score)]
        if offer.status == "Intéressante":
            badges.append(":yellow-badge[Intéressante]")
        badges.append(
            ":green-badge[Dossier prêt]" if item.is_ready else ":gray-badge[Dossier à préparer]"
        )
        if is_probably_truncated(offer):
            badges.append(":orange-badge[Annonce tronquée]")
        if offer.cv_state == CV_TO_CURATE:
            badges.append(":blue-badge[CV à soigner]")
        elif offer.cv_state == CV_CURATED:
            badges.append(":green-badge[CV soigné]")
        info = " · ".join(
            part for part in (offer.contract_type, offer.location, offer.source) if part
        )
        columns[0].markdown(
            f"**{extract_job_role(offer.title, offer.location)}** — "
            f"{offer.company or 'Entreprise non précisée'}  \n{' '.join(badges)}  \n"
            f":gray[{info}]"
        )
        with columns[1]:
            actions = st.columns([4, 1, 1])
            if item.is_ready:
                if actions[0].button(
                    "Candidater",
                    key=f"queue_open_{offer.id}",
                    type="primary",
                    icon=":material/play_arrow:",
                    width="stretch",
                ):
                    st.session_state[f"queue_started_{offer.id}"] = time.time()
                    _application_dialog(engine, profile, offer.id)
            elif actions[0].button(
                "Préparer",
                key=f"queue_prepare_{offer.id}",
                icon=":material/auto_fix_high:",
                width="stretch",
            ):
                result = prepare_dossier_for_offer(
                    engine, offer.id, offer_to_data(offer), profile
                )
                st.session_state["queue_flash"] = (
                    f"Dossier incomplet : {result.error}" if result.error else "Dossier préparé."
                )
                st.rerun()
            if offer.status != "Intéressante" and actions[1].button(
                "",
                key=f"queue_star_{offer.id}",
                icon=":material/star:",
                help="Marquer comme intéressante",
            ):
                set_offer_status(engine, offer.id, "Intéressante")
                st.rerun()
            if actions[2].button(
                "",
                key=f"queue_discard_{offer.id}",
                icon=":material/close:",
                help="Écarter cette offre de la file",
            ):
                set_offer_status(engine, offer.id, "Écartée")
                st.session_state["queue_flash"] = "Offre écartée de la file."
                st.rerun()


def show_send_queue_page(engine: Engine, profile: ProfileData) -> None:
    st.title("File d'envoi")
    st.caption(
        "L'envoi reste manuel : « Candidater » ouvre le dossier (annonce, CV, lettre), tu "
        "envoies toi-même, puis tu enregistres l'envoi pour lancer le suivi."
    )
    _show_follow_ups(engine)

    manual_tab, imported_tab, paid_tab, spontaneous_tab = st.tabs(
        [
            "Ajoutées par moi",
            "Importées par l'application",
            "Himalayas (candidature payante)",
            "Candidatures spontanées",
        ]
    )
    with manual_tab:
        _show_offers_queue(engine, profile, group="manual")
    with imported_tab:
        _show_offers_queue(engine, profile, group="automatic")
    with paid_tab:
        _show_offers_queue(engine, profile, group="paid")
    with spontaneous_tab:
        _show_spontaneous_queue(engine, profile)


def _show_offers_queue(engine: Engine, profile: ProfileData, *, group: str) -> None:
    key = group
    if group == "paid":
        st.caption(
            "Himalayas fait payer la candidature : ces offres sont séparées de la file "
            "principale. Garde-les pour plus tard ou écarte-les."
        )
    elif group == "manual":
        st.caption(
            "Les offres que tu as ajoutées toi-même, par formulaire ou par lien. Elles sont "
            "toutes affichées, sans seuil de score."
        )
        unread = list_offers_to_read(engine)
        if unread:
            with st.expander(f"Annonces à lire ({len(unread)})", expanded=True):
                st.caption(
                    "Ces liens n'ont pas pu être lus automatiquement. Lance le skill "
                    "lire-annonces dans Claude Code : ton Chrome les lit et complète les offres."
                )
                for offer in unread:
                    st.markdown(f"- [{offer.url}]({offer.url})")
    else:
        st.caption("Les offres trouvées par la veille et les recherches de l'application.")
    controls = st.columns(2)
    min_score = controls[0].slider(
        "Score minimal",
        min_value=0,
        max_value=90,
        value=0 if group == "manual" else load_watcher_config().min_match_percentage,
        step=5,
        help="Les offres marquées « Intéressante » restent affichées sous ce seuil.",
        key=f"queue_min_score_{key}",
    )
    limit = controls[1].selectbox(
        "Offres affichées", options=[10, 20, 50], index=0, key=f"queue_limit_{key}"
    )
    groups = group_queue(build_send_queue(engine, profile, min_score))
    queue = getattr(groups, group)

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
            "la page « Veille »."
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
            "3. J'ai envoyé ma candidature",
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
            "Aucune entreprise à contacter. Ajoute des pistes depuis la page « Découvrir des "
            "entreprises » (onglet La Bonne Boîte)."
        )
        return
    limit = st.selectbox("Entreprises affichées", options=[10, 20, 50], index=0)
    for item in queue[:limit]:
        _show_spontaneous_card(engine, profile, item)
    if len(queue) > limit:
        st.caption(f"{len(queue) - limit} autre(s) entreprise(s) non affichée(s).")
