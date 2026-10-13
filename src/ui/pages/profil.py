"""Pages « Mon profil » et « CV de référence »."""

from __future__ import annotations

import base64
import hashlib
import json

import streamlit as st
import streamlit.components.v1 as components
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from src.db import save_profile
from src.models import ProfileData, SkillCategory, SkillRating
from src.services.cv_renderer import (
    load_base_cv_data,
    render_cv_html,
    render_cv_pdf,
    save_base_cv_json,
    tailored_cv_filename,
    validate_base_cv_json,
)
from src.services.resume_import import (
    ResumeImportError,
    extract_resume_text,
    list_resume_versions,
    save_resume_version,
)
from src.ui.commun import CONTRACT_TYPES

SKILL_CATEGORIES: list[SkillCategory] = [
    "Forte",
    "Intermédiaire",
    "En développement",
    "IA et nouvelles technologies",
]


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
            help="Villes, départements ou régions, séparés par des virgules.",
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
            width="stretch",
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
