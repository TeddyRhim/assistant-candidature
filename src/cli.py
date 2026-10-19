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
from src.services.data_repair import repair_legacy_data
from src.services.job_titles import extract_job_role
from src.services.mail_offers import (
    DUPLICATE,
    FAILED,
    IMPORTED,
    TO_READ,
    import_mail_offers,
    last_search_date,
    record_search_date,
)
from src.services.offer_reading import (
    ReadingError,
    complete_read_offer,
    list_offers_to_read,
    mark_unreadable,
)


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
    repair_legacy_data(engine)
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


def _lire(args: argparse.Namespace) -> int:
    """Annonces à lire : lister, compléter avec ce que Chrome a lu, ou marquer illisible."""
    sys.stdout.reconfigure(encoding="utf-8")
    engine = _open_engine()
    try:
        if args.action == "list":
            offers = list_offers_to_read(engine)
            if not offers:
                print("Aucune annonce à lire.")
            for offer in offers:
                print(f"{offer.id[:8]}  {offer.url}")
            return 0
        offer = resolve_offer(engine, args.offer)
        if args.action == "unreadable":
            mark_unreadable(engine, offer.id, args.reason)
            print(f"{offer.id[:8]} : marquée illisible.")
            return 0
        details = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = complete_read_offer(engine, offer.id, details, load_or_seed_profile(engine))
        print(f"{offer.id[:8]} : annonce complétée, CV à soigner.")
        if result.error:
            print(f"Dossier incomplet : {result.error}")
        return 0
    except (CurationError, ReadingError, OSError, ValueError) as error:
        print(f"Erreur : {error}")
        return 1


MAIL_STATUSES = (IMPORTED, TO_READ, DUPLICATE, FAILED)


def _mails(args: argparse.Namespace) -> int:
    """Offres relevées dans les alertes e-mail : date de la dernière recherche et import en lot."""
    sys.stdout.reconfigure(encoding="utf-8")
    if args.action == "depuis":
        print(last_search_date() or "jamais")
        return 0
    if args.action == "noter":
        print(f"Recherche notée au {record_search_date()}.")
        return 0
    try:
        items = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"Erreur : {error}")
        return 1
    if not isinstance(items, list):
        print("Erreur : le fichier doit contenir une liste d'offres.")
        return 1
    engine = _open_engine()
    results = import_mail_offers(engine, load_or_seed_profile(engine), items)
    for result in results:
        prefix = f"{result.offer_id[:8]}  " if result.offer_id else ""
        print(f"[{result.status}] {prefix}{result.label} : {result.detail}")
    counts = {status: sum(r.status == status for r in results) for status in MAIL_STATUSES}
    print(", ".join(f"{count} {status}" for status, count in counts.items()))
    print(f"Recherche notée au {record_search_date()}.")
    return 0


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
    read = commands.add_parser("lire", help="annonces à lire avec le navigateur")
    read_actions = read.add_subparsers(dest="action", required=True)
    read_actions.add_parser("list", help="liens d'annonces non lus")
    read_save = read_actions.add_parser("save", help="complète une annonce avec ce qui a été lu")
    read_save.add_argument("offer")
    read_save.add_argument(
        "file", help="JSON : title, company, location, contract_type, description"
    )
    unreadable = read_actions.add_parser("unreadable", help="annonce impossible à lire")
    unreadable.add_argument("offer")
    unreadable.add_argument("reason")
    mails = commands.add_parser("mails", help="offres relevées dans les alertes e-mail")
    mail_actions = mails.add_subparsers(dest="action", required=True)
    mail_actions.add_parser("depuis", help="date de la dernière recherche (AAAA/MM/JJ)")
    mail_actions.add_parser("noter", help="note la recherche du jour sans rien importer")
    mail_import = mail_actions.add_parser("import", help="importe une liste d'offres (JSON)")
    mail_import.add_argument("file", help="JSON : liste de {title, company, location, url, source}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.command == "daily":
        return _daily()
    if args.command == "mails":
        return _mails(args)
    if args.command == "lire":
        return _lire(args)
    return _soigner(args)


if __name__ == "__main__":
    sys.exit(main())
