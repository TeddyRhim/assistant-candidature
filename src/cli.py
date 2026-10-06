from __future__ import annotations

import argparse
import logging
import sys

from src.db import create_database_engine, initialize_database, load_or_seed_profile
from src.services.daily_run import load_secrets_file, run_daily


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.cli")
    parser.add_argument(
        "command", choices=["daily"], help="daily : collecte des annonces puis dossiers prêts"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.command == "daily":
        return _daily()
    return 2


if __name__ == "__main__":
    sys.exit(main())
