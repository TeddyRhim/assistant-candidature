"""Fixtures partagées : les tests n'utilisent jamais le vrai dossier ``data/``."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_remote_board_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """La veille n'appelle jamais Himalayas ni Remote OK pendant les tests, sauf mock explicite."""
    from src.services import job_watcher
    from src.services.job_sources.remote_common import RemoteBoardResult

    monkeypatch.setattr(job_watcher, "search_himalayas", lambda terms: RemoteBoardResult())
    monkeypatch.setattr(job_watcher, "search_remoteok", lambda terms: RemoteBoardResult())


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Les tests ne lisent ni n'écrivent jamais le vrai dossier data/ (réglages, compteurs, CV).

    Le CV de base est résolu dans ce dossier temporaire, avec repli sur l'exemple fictif.
    """
    monkeypatch.setenv(
        "ASSISTANT_CANDIDATURES_DATA_DIR", str(tmp_path_factory.mktemp("data_dir"))
    )
    monkeypatch.delenv("JOOBLE_API_KEY", raising=False)
