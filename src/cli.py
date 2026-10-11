from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.db import create_database_engine, initialize_database, load_or_seed_profile
from src.services.cv_curation import (
    CV_STATES,
    CurationError,
    curation_brief,
    list_offers_to_curate,
    resolve_offer,
    save_curated_cv,
    set_cv_state,
)
from src.services.daily_run import load_secrets_file, run_daily
from src.services.job_titles import extract_job_role


def _daily() -> int:
    engine = create_database_engine()
    initialize_database(engine)
    profile = load_or_seed_profile(engine)
    if not profile.target_role:
        print("Profil incomplet : renseigne-le dans l'application avant la veille.")
        return 1
    result = run_daily(engine, profile, secrets=load_secrets_file())
    print(result.summary())
    for item in result.imported_offer_titles:
        print(f"  + {item}")
    for error in result.errors:
        print(f"  ! {error}")
    return 0


def _open_engine():
    engine = create_database_engine()
    initialize_database(engine)
    return engine


def _soigner(args: argparse.Namespace) -> int:
    """File « à soigner » : lister, lire, enregistrer, changer l'état."""
    sys.stdout.reconfigure(encoding="utf-8")
    engine = _open_engine()
    try:
        if args.action == "list":
            offers = list_offers_to_curate(engine)
            if not offers:
                print("Aucune offre à soigner.")
            for offer in offers:
                role = extract_job_role(offer.title, offer.location)
                print(f"{offer.id[:8]}  {role}  |  {offer.company or '?'}  |  {offer.location}")
            return 0
        offer = resolve_offer(engine, args.offer)
        if args.action == "show":
            brief = curation_brief(engine, offer, load_or_seed_profile(engine))
            print(json.dumps(brief, ensure_ascii=False, indent=2))
            return 0
        if args.action == "save":
            result = save_curated_cv(engine, offer, Path(args.file).read_text(encoding="utf-8"))
            if not result.accepted:
                print("CV refusé :")
                for error in result.errors:
                    print(f"  - {error}")
                return 1
            print(f"CV enregistré pour {offer.id[:8]} : état Soigné.")
            return 0
        set_cv_state(engine, offer.id, args.state)
        print(f"État de {offer.id[:8]} : {args.state}.")
        return 0
    except (CurationError, OSError) as error:
        print(f"Erreur : {error}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("daily", help="collecte des annonces puis dossiers prêts")
    curate = commands.add_parser("soigner", help="file des CV à soigner")
    actions = curate.add_subparsers(dest="action", required=True)
    actions.add_parser("list", help="offres dont le CV est à soigner")
    show = actions.add_parser("show", help="annonce, CV de base et CV actuel d'une offre")
    show.add_argument("offer", help="identifiant (ou début d'identifiant) de l'offre")
    save = actions.add_parser("save", help="enregistre le CV soigné (JSON) après contrôle")
    save.add_argument("offer")
    save.add_argument("file", help="fichier JSON du CV soigné")
    state = actions.add_parser("state", help="change l'état du CV d'une offre")
    state.add_argument("offer")
    state.add_argument("state", choices=CV_STATES)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.command == "daily":
        return _daily()
    return _soigner(args)


if __name__ == "__main__":
    sys.exit(main())
