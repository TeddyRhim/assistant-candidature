from __future__ import annotations

import json
import logging
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine

from src.config import PROJECT_ROOT, get_data_dir
from src.models import ProfileData
from src.services.dossier_generator import prepare_pending_dossiers
from src.services.job_watcher import WatcherConfig, load_watcher_config, run_watcher_cycle

logger = logging.getLogger(__name__)

STREAMLIT_SECRETS_PATH = PROJECT_ROOT / ".streamlit" / "secrets.toml"


@dataclass
class DailyRunResult:
    started_at: str
    completed_at: str = ""
    listings_found: int = 0
    new_offers: int = 0
    dossiers_prepared: int = 0
    duplicates_skipped: int = 0
    low_match_skipped: int = 0
    excluded_skipped: int = 0
    imported_offer_titles: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"{self.new_offers} nouvelle(s) offre(s) sur {self.listings_found} annonce(s) "
            f"analysée(s), {self.dossiers_prepared} dossier(s) prêt(s) "
            f"({self.duplicates_skipped} doublon(s), {self.low_match_skipped} score "
            f"insuffisant, {self.excluded_skipped} titre(s) exclu(s))."
        )


def get_last_run_path() -> Path:
    return get_data_dir() / "last_daily_run.json"


def load_last_run() -> DailyRunResult | None:
    path = get_last_run_path()
    if not path.is_file():
        return None
    try:
        return DailyRunResult(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError) as error:
        logger.warning("Lecture de last_daily_run.json impossible : %s", error)
        return None


def _save_last_run(result: DailyRunResult) -> None:
    path = get_last_run_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def load_secrets_file(path: Path = STREAMLIT_SECRETS_PATH) -> dict[str, object]:
    """Lit `.streamlit/secrets.toml` hors Streamlit (planificateur, ligne de commande)."""
    if not path.is_file():
        return {}
    try:
        return dict(tomllib.loads(path.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError) as error:
        logger.warning("Lecture de %s impossible : %s", path.name, error)
        return {}


def run_daily(
    engine: Engine,
    profile: ProfileData,
    config: WatcherConfig | None = None,
    secrets: Mapping[str, object] | None = None,
) -> DailyRunResult:
    """Collecte les annonces puis prépare CV et lettre pour tout ce qui est éligible."""
    cfg = config or load_watcher_config()
    result = DailyRunResult(started_at=datetime.now(UTC).isoformat())

    cycle = run_watcher_cycle(engine, profile, cfg, secrets)
    result.listings_found = cycle.total_listings_found
    result.new_offers = cycle.new_offers_imported
    result.dossiers_prepared = cycle.dossiers_prepared
    result.duplicates_skipped = cycle.duplicates_skipped
    result.low_match_skipped = cycle.low_match_skipped
    result.excluded_skipped = cycle.excluded_skipped
    result.imported_offer_titles = list(cycle.imported_offer_titles)
    result.errors = list(cycle.errors)

    # Rattrape les offres déjà en base dont le dossier manque (échec ou CV importé après coup).
    try:
        batch = prepare_pending_dossiers(engine, profile, min_score=cfg.min_match_percentage)
        result.dossiers_prepared += batch.generated_count
        result.errors.extend(batch.errors)
    except Exception as error:
        logger.warning("Préparation des dossiers manquants impossible : %s", error)
        result.errors.append(f"Dossiers manquants : {error}")

    result.completed_at = datetime.now(UTC).isoformat()
    _save_last_run(result)
    return result
