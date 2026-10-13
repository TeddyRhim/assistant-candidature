"""Page « Offres » : formulaire, import par lien, dossier adapté."""

from __future__ import annotations

import json

import streamlit as st
from pydantic import ValidationError
from sqlalchemy import Engine

from src.models import JobOfferData, OfferStatus, ProfileData
from src.services.cv_curation import CV_AUTOMATIC, CV_TO_CURATE, set_cv_state
from src.services.cv_renderer import load_base_cv_data
from src.services.dossier_generator import prepare_dossier_for_offer
from src.services.job_offers import (
    ORIGIN_MANUAL,
    DuplicateOfferURL,
    OfferNotFound,
    create_offer,
    delete_offer,
    find_offer_by_url,
    list_offers,
    update_offer,
)
from src.services.matching import assess_offer_fit
from src.services.offer_import import OfferImportError, OfferUnreadable, import_offer_from_url
from src.services.offer_keywords import keyword_report
from src.services.offer_reading import store_unread_offer
from src.services.tailored_resumes import get_tailored_resume
from src.ui.commun import CONTRACT_TYPES, _required_technologies_text, _show_keyword_report

OFFER_STATUSES: list[OfferStatus] = [
    "À examiner",
    "Intéressante",
    "Écartée",
    "Candidature liée",
]


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


def _show_cv_state_control(engine: Engine, offer) -> None:
    """État du CV (Automatique, À soigner, Soigné) et bascule vers la file à soigner."""
    columns = st.columns([3, 2], vertical_alignment="center")
    if offer.read_state != "Complète":
        columns[0].caption(f"Annonce : {offer.read_state.lower()} · CV : {offer.cv_state}")
    else:
        columns[0].caption(f"CV : {offer.cv_state}")
    if offer.cv_state == CV_TO_CURATE:
        label, target = "Retirer de la file à soigner", CV_AUTOMATIC
    else:
        label, target = "Marquer le CV à soigner", CV_TO_CURATE
    if columns[1].button(label, key=f"cv_state_{offer.id}"):
        set_cv_state(engine, offer.id, target)
        st.rerun()


def _prepare_adapted_dossier(
    engine: Engine,
    profile: ProfileData,
    offer_id: str,
    offer_data: JobOfferData,
    *,
    to_curate: bool = False,
) -> dict[str, object]:
    """Génère le CV adapté et la lettre d'une offre qu'on vient d'ajouter, et le rapport.

    L'offre est déjà enregistrée : une erreur de génération est rapportée, jamais propagée.
    """
    try:
        result = prepare_dossier_for_offer(engine, offer_id, offer_data, profile)
    except Exception as error:  # noqa: BLE001
        if to_curate:
            set_cv_state(engine, offer_id, CV_TO_CURATE)
        return {
            "message": "Offre enregistrée. Dossier non préparé "
            f"({type(error).__name__}) : prépare-le depuis « CV par offre »."
        }
    if result.error:
        return {"message": f"Offre enregistrée. Dossier non préparé : {result.error}"}
    resume = get_tailored_resume(engine, offer_id)
    report = None
    if resume is not None:
        try:
            report = keyword_report(offer_data, json.loads(resume.content), profile)
        except (ValueError, TypeError):
            report = None
    message = (
        "Offre enregistrée. CV adapté à l'annonce et lettre générés (voir « File "
        "d'envoi » ou « CV par offre »)."
    )
    if to_curate:
        set_cv_state(engine, offer_id, CV_TO_CURATE)
        message += " Le CV est marqué « à soigner » : lance le skill soigner-cv dans Claude Code."
    return {"message": message, "report": report}


def _offer_link_import_ui(engine: Engine, profile: ProfileData) -> None:
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

    existing = find_offer_by_url(engine, url) if read_submitted and url.strip() else None
    if existing is not None:
        st.warning(
            f"Cette annonce est déjà enregistrée : « {existing.title} » ({existing.status}). "
            "Rien n'a été ajouté."
        )
    elif read_submitted:
        try:
            with st.spinner("Lecture de l'annonce…"):
                imported_offer = import_offer_from_url(url)
        except OfferUnreadable as error:
            try:
                store_unread_offer(engine, error.url, error.host, str(error))
            except DuplicateOfferURL as duplicate:
                st.error(str(duplicate))
            else:
                st.session_state["offer_flash"] = {
                    "message": "Le site n'a pas pu être lu automatiquement : le lien est "
                    "gardé dans « Annonces à lire ». Lance le skill lire-annonces dans "
                    "Claude Code pour que ton Chrome la lise, puis soigner-cv."
                }
                st.rerun()
        except OfferImportError as error:
            st.error(str(error))
        except Exception as error:  # noqa: BLE001
            st.error(f"Lecture impossible ({type(error).__name__}). Réessaie ou saisis l'annonce.")
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
        created = create_offer(engine, offer_data, origin=ORIGIN_MANUAL)
    except ValidationError as error:
        for issue in error.errors():
            st.error(issue["msg"])
    except DuplicateOfferURL as error:
        st.error(str(error))
    else:
        st.session_state.pop("offer_link_draft", None)
        st.session_state["offer_flash"] = _prepare_adapted_dossier(
            engine, profile, created.id, offer_data, to_curate=True
        )
        st.rerun()


OFFERS_PAGE_SIZES = [25, 50, 100, 0]


def show_offers_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Offres d'emploi")
    st.write(
        "Ajoute une annonce manuellement, colle son lien pour en extraire les informations, "
        "ou importe les résultats de recherche disponibles."
    )
    flash = st.session_state.pop("offer_flash", None)
    if flash:
        st.success(flash["message"])
        if flash.get("report") is not None:
            _show_keyword_report(flash["report"])
    _offer_link_import_ui(engine, profile)
    st.subheader("Ajouter une offre")
    try:
        new_offer = _offer_form("new_offer")
        if new_offer is not None:
            created = create_offer(engine, new_offer, origin=ORIGIN_MANUAL)
            st.session_state["offer_flash"] = _prepare_adapted_dossier(
                engine, profile, created.id, new_offer, to_curate=True
            )
            st.rerun()
    except ValidationError as error:
        for issue in error.errors():
            st.error(issue["msg"])
    except DuplicateOfferURL as error:
        st.error(str(error))

    offers = list_offers(engine)
    if not offers:
        st.subheader("Offres enregistrées (0)")
        st.info("Aucune offre pour le moment. Tu peux commencer par coller une annonce ci-dessus.")
        return
    manual_count = sum(offer.origin == ORIGIN_MANUAL for offer in offers)
    origin_filter = st.radio(
        "Origine",
        options=["Toutes", "Ajoutées par moi", "Importées par l'application"],
        format_func=lambda value: {
            "Toutes": f"Toutes ({len(offers)})",
            "Ajoutées par moi": f"Ajoutées par moi ({manual_count})",
            "Importées par l'application": (
                f"Importées par l'application ({len(offers) - manual_count})"
            ),
        }[value],
        horizontal=True,
        key="offers_origin_filter",
    )
    if origin_filter == "Ajoutées par moi":
        offers = [offer for offer in offers if offer.origin == ORIGIN_MANUAL]
    elif origin_filter == "Importées par l'application":
        offers = [offer for offer in offers if offer.origin != ORIGIN_MANUAL]
    st.subheader(f"Offres enregistrées ({len(offers)})")
    if not offers:
        st.info("Aucune offre dans cette catégorie.")
        return

    try:
        base_cv_data = load_base_cv_data()
    except (OSError, ValueError):
        base_cv_data = {}
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
    page_size = st.selectbox(
        "Offres affichées",
        options=OFFERS_PAGE_SIZES,
        index=0,
        format_func=lambda size: "Toutes" if size == 0 else str(size),
        key="offers_page_size",
        help="Afficher moins d'offres rend la page plus rapide.",
    )
    shown_offers = scored_offers[:page_size] if page_size else scored_offers
    if len(shown_offers) < len(scored_offers):
        st.caption(
            f"{len(scored_offers) - len(shown_offers)} offre(s) moins bien classée(s) non "
            "affichée(s) : choisis « Toutes » pour les voir."
        )
    for offer, offer_data, fit in shown_offers:
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
            _show_cv_state_control(engine, offer)
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
                _show_keyword_report(keyword_report(offer_data, base_cv_data, profile))
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
