from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.db import create_database_engine, initialize_database
from src.models import ProfileData
from src.services.daily_run import (
    DailyRunResult,
    load_last_run,
    load_secrets_file,
    run_daily,
)
from src.services.dossier_generator import DossierBatchResult
from src.services.job_watcher import WatcherConfig, WatcherCycleResult


def _engine(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_CANDIDATURES_DATA_DIR", str(tmp_path))
    engine = create_database_engine(tmp_path / "db.sqlite3")
    initialize_database(engine)
    return engine


def test_run_daily_combines_cycle_and_pending_dossiers(tmp_path: Path, monkeypatch) -> None:
    engine = _engine(tmp_path, monkeypatch)
    cycle = WatcherCycleResult(
        started_at="2026-10-07T08:00:00+00:00",
        total_listings_found=20,
        new_offers_imported=4,
        dossiers_prepared=3,
        duplicates_skipped=10,
        imported_offer_titles=["Dev PHP - Acme (80%)"],
        errors=["Lever (x): timeout"],
    )
    batch = DossierBatchResult(generated_count=1, errors=["Offre B: erreur"])

    with (
        patch("src.services.daily_run.run_watcher_cycle", return_value=cycle),
        patch("src.services.daily_run.prepare_pending_dossiers", return_value=batch),
    ):
        result = run_daily(engine, ProfileData(), WatcherConfig())

    assert result.new_offers == 4
    assert result.dossiers_prepared == 4
    assert result.errors == ["Lever (x): timeout", "Offre B: erreur"]
    assert "4 nouvelle(s) offre(s) sur 20" in result.summary()
    saved = load_last_run()
    assert isinstance(saved, DailyRunResult)
    assert saved.dossiers_prepared == 4


def test_run_daily_survives_dossier_failure(tmp_path: Path, monkeypatch) -> None:
    engine = _engine(tmp_path, monkeypatch)
    cycle = WatcherCycleResult(started_at="2026-10-07T08:00:00+00:00")

    with (
        patch("src.services.daily_run.run_watcher_cycle", return_value=cycle),
        patch("src.services.daily_run.prepare_pending_dossiers", side_effect=RuntimeError("boom")),
    ):
        result = run_daily(engine, ProfileData(), WatcherConfig())

    assert result.completed_at
    assert any("boom" in error for error in result.errors)


def test_load_last_run_returns_none_without_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ASSISTANT_CANDIDATURES_DATA_DIR", str(tmp_path))
    assert load_last_run() is None


def test_load_secrets_file_reads_toml_and_tolerates_missing(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('ADZUNA_APP_ID = "abc"\n', encoding="utf-8")
    assert load_secrets_file(secrets) == {"ADZUNA_APP_ID": "abc"}
    assert load_secrets_file(tmp_path / "absent.toml") == {}
