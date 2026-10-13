"""Page « Envoyer un e-mail »."""

from __future__ import annotations

import hashlib
import json

import streamlit as st
from sqlalchemy import Engine
from streamlit.errors import StreamlitSecretNotFoundError

from src.config import get_mailjet_settings
from src.services.companies import list_companies
from src.services.cv_renderer import tailored_cv_filename
from src.services.email_delivery import EmailDeliveryError, send_application_email
from src.services.job_offers import list_offers
from src.services.tailored_resumes import (
    TailoredResumeError,
    get_tailored_resume,
    render_tailored_resume_pdf,
)


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
