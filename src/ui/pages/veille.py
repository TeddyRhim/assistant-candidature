"""Page « Veille »."""

from __future__ import annotations

import streamlit as st
from sqlalchemy import Engine
from streamlit.errors import StreamlitSecretNotFoundError

from src.models import ProfileData
from src.services.daily_run import load_last_run, run_daily
from src.services.dossier_generator import prepare_pending_dossiers
from src.services.job_sources.france_travail import (
    DEPARTMENT_NAMES as FRANCE_TRAVAIL_DEPARTMENT_NAMES,
)
from src.services.job_sources.greenhouse import extract_greenhouse_board_token
from src.services.job_sources.lever import extract_lever_site_slug
from src.services.job_watcher import (
    MonitoredTarget,
    get_global_watcher,
    load_watcher_config,
    run_watcher_cycle,
    save_watcher_config,
)
from src.ui.commun import _france_travail_secrets, _show_job_board_help


def show_watcher_page(engine: Engine, profile: ProfileData) -> None:
    st.title("Veille")
    _show_job_watcher_tab(engine, profile)


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
            st.success("**Veille automatique : active**")
        else:
            st.info("**Veille automatique : inactive**")

        last_res = watcher.last_result
        if last_res:
            st.caption(f"Dernier cycle : {last_res.started_at[:19]} UTC")
            st.markdown(
                f"- **Annonces analysées** : {last_res.total_listings_found}\n"
                f"- **Nouvelles offres importées** : {last_res.new_offers_imported}\n"
                f"- **Dossiers pré-générés (CV + Lettre)** : {last_res.dossiers_prepared}\n"
                f"- **Doublons ignorés** : {last_res.duplicates_skipped}\n"
                f"- **Déjà postulées cette semaine** : {last_res.recently_applied_skipped}\n"
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
                        st.caption(err)

    with col_actions:
        try:
            secrets = {
                "ADZUNA_APP_ID": st.secrets.get("ADZUNA_APP_ID", ""),
                "ADZUNA_APP_KEY": st.secrets.get("ADZUNA_APP_KEY", ""),
                "JOOBLE_API_KEY": st.secrets.get("JOOBLE_API_KEY", ""),
                **_france_travail_secrets(),
            }
        except StreamlitSecretNotFoundError:
            secrets = {}

        if st.button(
            "Récupérer les annonces et préparer les dossiers",
            type="primary",
            help="Interroge toutes les sources, importe les offres pertinentes puis génère "
            "CV et lettre. Même traitement que la tâche quotidienne planifiée.",
        ):
            with st.spinner("Collecte des annonces et préparation des dossiers..."):
                daily_res = run_daily(engine, profile, cfg, secrets)
                st.session_state["manual_daily_result"] = daily_res.to_dict()
                st.rerun()

        if st.button("Lancer un cycle de veille maintenant"):
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

    daily_res = st.session_state.get("manual_daily_result")
    if daily_res:
        st.success(
            f"{daily_res['new_offers']} nouvelle(s) offre(s) sur {daily_res['listings_found']} "
            f"annonce(s) analysée(s), {daily_res['dossiers_prepared']} dossier(s) prêt(s)."
        )
        for title in daily_res["imported_offer_titles"]:
            st.markdown(f"- **{title}**")
        if daily_res["errors"]:
            with st.expander("Avertissements de la récupération"):
                for err in daily_res["errors"]:
                    st.caption(err)
    else:
        last_daily = load_last_run()
        if last_daily:
            st.caption(
                f"Dernière récupération complète : {last_daily.started_at[:16].replace('T', ' ')} "
                f"UTC — {last_daily.summary()}"
            )

    manual_res = st.session_state.get("manual_watcher_result")
    if manual_res:
        st.success(
            f"Cycle ponctuel terminé : {manual_res.get('new_offers_imported', 0)} offre(s) "
            f"importée(s), {manual_res.get('dossiers_prepared', 0)} dossier(s) préparé(s), "
            f"{manual_res.get('duplicates_skipped', 0)} doublon(s) ignoré(s)."
        )
        if manual_res.get("imported_offer_titles"):
            for title in manual_res["imported_offer_titles"]:
                st.markdown(f"- **{title}**")

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
                    st.caption(err)

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
        non_dev_raw = st.text_input(
            "Rôles hors développement à écarter (séparés par des virgules)",
            value=", ".join(cfg.non_dev_title_keywords),
            help="Écartés sauf si le titre contient aussi « développeur », « developer », "
            "« backend »… Un * final accepte toute fin de mot (technicien*).",
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
        adzuna_zones_raw = st.text_input(
            "Zones Adzuna (séparées par des virgules)",
            value=", ".join(cfg.adzuna_locations),
            help="Villes ou départements interrogés un par un. Vide : zone de ton profil.",
        )
        priority_raw = st.text_input(
            "Zones prioritaires dans la file d'envoi (séparées par des virgules)",
            value=", ".join(cfg.priority_areas),
            help="Les offres de ces zones, et celles en full remote, passent en tête de la "
            "file. Vide : zone de ton profil.",
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
        enable_jooble = st.checkbox(
            "Inclure Jooble (clé limitée à 500 requêtes)",
            value=cfg.enable_jooble,
        )
        jooble_zones_raw = st.text_input(
            "Zones Jooble (séparées par des virgules)",
            value=", ".join(cfg.jooble_locations),
            help="Une requête par technologie et par zone. Vide : zone de ton profil.",
        )
        jooble_per_run = st.number_input(
            "Jooble : requêtes maximum par passage",
            min_value=1,
            max_value=50,
            value=cfg.jooble_max_requests_per_run,
            help="Chaque passage ne dépasse jamais ce nombre ; le quota total est suivi à part.",
        )
        enable_himalayas = st.checkbox(
            "Inclure Himalayas (offres en télétravail, en anglais)",
            value=cfg.enable_himalayas,
        )
        enable_remoteok = st.checkbox(
            "Inclure Remote OK (offres en télétravail, en anglais)",
            value=cfg.enable_remoteok,
        )
        excluded_contracts = st.multiselect(
            "Types de contrat écartés",
            options=["Stage", "Alternance", "Freelance", "CDD"],
            default=[
                c
                for c in cfg.excluded_contract_types
                if c in ("Stage", "Alternance", "Freelance", "CDD")
            ],
            help="Appliqué quand la source indique le type de contrat. Les mots exclus du "
            "titre complètent ce filtre.",
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
            cfg.non_dev_title_keywords = [
                word.strip() for word in non_dev_raw.split(",") if word.strip()
            ]
            cfg.interval_seconds = interval_hours * 3600
            cfg.enable_adzuna = enable_adzuna
            cfg.enable_france_travail = enable_france_travail
            cfg.adzuna_locations = [z.strip() for z in adzuna_zones_raw.split(",") if z.strip()]
            cfg.priority_areas = [z.strip() for z in priority_raw.split(",") if z.strip()]
            cfg.france_travail_departments = ft_departments
            cfg.enable_jooble = enable_jooble
            cfg.jooble_locations = [z.strip() for z in jooble_zones_raw.split(",") if z.strip()]
            cfg.jooble_max_requests_per_run = int(jooble_per_run)
            cfg.enable_himalayas = enable_himalayas
            cfg.enable_remoteok = enable_remoteok
            cfg.excluded_contract_types = excluded_contracts
            cfg.france_travail_cdi_only = ft_cdi_only
            cfg.auto_import = auto_import
            cfg.auto_prepare_dossier = auto_prepare_dossier
            save_watcher_config(cfg)
            st.success("Configuration de la veille enregistrée !")
            st.rerun()

    st.subheader("Entreprises surveillées (Greenhouse & Lever)")
    st.caption(
        "Les offres de ces entreprises sont relues à chaque cycle de veille. Ajoute une "
        "entreprise seulement si sa page carrière utilise Greenhouse ou Lever."
    )
    _show_job_board_help()
    if not cfg.targets:
        st.info("Aucune entreprise surveillée pour le moment.")
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

    with st.expander("Ajouter une entreprise à surveiller"):
        with st.form("add_target_form"):
            new_platform = st.selectbox("Plateforme", ["greenhouse", "lever"])
            new_target = st.text_input(
                "Identifiant ou adresse de sa page carrière",
                placeholder="ex: ateliertech ou https://boards.greenhouse.io/ateliertech",
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
