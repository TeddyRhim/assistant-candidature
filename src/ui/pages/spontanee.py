"""Page « Candidature spontanée »."""

from __future__ import annotations

import hashlib

import streamlit as st
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.models import JobOfferData, ProfileData
from src.services.companies import list_companies
from src.services.cover_letters import (
    CoverLetterError,
    build_cover_letter,
    get_cover_letter,
    render_cover_letter_pdf,
    save_cover_letter,
    validate_cover_letter_content,
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
