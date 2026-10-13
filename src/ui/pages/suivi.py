"""Page « Suivi des candidatures »."""

from __future__ import annotations

from datetime import date

import streamlit as st
from pydantic import ValidationError
from sqlalchemy import Engine

from src.models import ApplicationData, ApplicationStatus
from src.services.applications import (
    ApplicationNotFound,
    ApplicationReferenceNotFound,
    create_application,
    delete_application,
    list_applications,
    set_application_status,
    update_application,
)
from src.services.companies import list_companies
from src.services.job_offers import list_offers

APPLICATION_STATUSES: list[ApplicationStatus] = [
    "À préparer",
    "Prête à envoyer",
    "Envoyée",
    "Entretien",
    "Refusée",
    "Retirée",
]


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


BOARD_STATUSES: list[ApplicationStatus] = ["À préparer", "Prête à envoyer", "Envoyée", "Entretien"]


CLOSED_STATUSES: list[ApplicationStatus] = ["Refusée", "Retirée"]


def _change_application_status(engine: Engine, application_id: str) -> None:
    status = st.session_state[f"status_{application_id}"]
    try:
        set_application_status(engine, application_id, status)
    except ApplicationNotFound as error:
        st.session_state["queue_flash"] = str(error)
    else:
        st.session_state["queue_flash"] = f"Statut mis à jour : {status}."


def _show_application_card(
    engine: Engine,
    application,
    company_label: str,
    company_options: dict[str, str],
    offer_options: dict[str, str],
) -> None:
    with st.container(border=True):
        text_column, status_column, details_column = st.columns(
            [6, 2, 1.5], vertical_alignment="center"
        )
        details = []
        if application.applied_on:
            details.append(f"Envoyée le {application.applied_on}")
        if application.next_action_on and application.status == "Envoyée":
            overdue = application.next_action_on <= date.today().isoformat()
            badge = ":red-badge[Relance due]" if overdue else ":gray-badge[Relance prévue]"
            details.append(f"{badge} {application.next_action_on}")
        lines = [f"**{application.role}** — {company_label}"]
        if details:
            lines.append(" · ".join(details))
        if application.next_action:
            due_on = f" ({application.next_action_on})" if application.next_action_on else ""
            if application.status != "Envoyée":
                lines.append(f":gray[À faire : {application.next_action}{due_on}]")
            else:
                lines.append(f":gray[{application.next_action}]")
        text_column.markdown("  \n".join(lines))
        status_column.selectbox(
            "Statut",
            options=APPLICATION_STATUSES,
            index=APPLICATION_STATUSES.index(application.status)
            if application.status in APPLICATION_STATUSES
            else 0,
            key=f"status_{application.id}",
            label_visibility="collapsed",
            on_change=_change_application_status,
            args=(engine, application.id),
        )
        with details_column.popover("Détails", width="stretch"):
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
                    st.session_state["queue_flash"] = "Candidature mise à jour."
                    st.rerun()
            except ValidationError as error:
                for issue in error.errors():
                    st.error(issue["msg"])
            except (ApplicationNotFound, ApplicationReferenceNotFound) as error:
                st.error(str(error))
            if st.button(
                "Supprimer cette candidature", key=f"delete_application_{application.id}"
            ):
                try:
                    delete_application(engine, application.id)
                except ApplicationNotFound as error:
                    st.error(str(error))
                else:
                    st.session_state["queue_flash"] = "Candidature supprimée."
                    st.rerun()


def show_applications_page(engine: Engine) -> None:
    st.title("Suivi des candidatures")
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
    applications = list_applications(engine)
    with st.expander("Ajouter une candidature", expanded=not applications):
        try:
            data = _application_form("new_application", company_options, offer_options)
            if data is not None:
                create_application(engine, data)
                st.session_state["queue_flash"] = "Candidature enregistrée."
                st.rerun()
        except ValidationError as error:
            for issue in error.errors():
                st.error(issue["msg"])
        except ApplicationReferenceNotFound as error:
            st.error(str(error))

    if not applications:
        st.info("Aucune candidature suivie pour le moment.")
        return

    companies_by_id = {company.id: company for company in companies}

    def label_for(application) -> str:
        company = companies_by_id.get(application.company_id)
        return company.name if company else "Entreprise supprimée"

    today = date.today().isoformat()
    groups: list[tuple[str, list]] = [
        (status, [a for a in applications if a.status == status]) for status in BOARD_STATUSES
    ]
    groups.append(("Clôturées", [a for a in applications if a.status in CLOSED_STATUSES]))
    labels = []
    for name, items in groups:
        label = f"{name} ({len(items)})"
        overdue = sum(
            1
            for a in items
            if a.status == "Envoyée" and a.next_action_on and a.next_action_on <= today
        )
        if overdue:
            label += f" · {overdue} à relancer"
        labels.append(label)

    for tab, (name, items) in zip(st.tabs(labels), groups, strict=True):
        with tab:
            if not items:
                st.caption(f"Aucune candidature dans « {name} ».")
                continue
            # Les relances échues d'abord, puis par date d'action ; sans date en dernier.
            for application in sorted(items, key=lambda a: a.next_action_on or "9999-12-31"):
                _show_application_card(
                    engine, application, label_for(application), company_options, offer_options
                )
