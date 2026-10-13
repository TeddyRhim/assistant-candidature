"""Page « Entreprises à prospecter »."""

from __future__ import annotations

import streamlit as st
from pydantic import ValidationError
from sqlalchemy import Engine

from src.models import CompanyData, ProfileData
from src.services.companies import (
    CompanyInUse,
    CompanyNotFound,
    DuplicateCompany,
    create_company,
    delete_company,
    list_companies,
    update_company,
)
from src.services.matching import assess_company_fit


def _company_form(
    key: str,
    company: CompanyData | None = None,
) -> CompanyData | None:
    with st.form(key):
        name = st.text_input("Nom de l'entreprise", value=company.name if company else "")
        location = st.text_input(
            "Ville / zone",
            value=company.location if company else "",
            help="Ville, département ou région.",
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
        placeholder="Ville, département…",
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
