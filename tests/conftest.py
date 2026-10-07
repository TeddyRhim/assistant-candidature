"""Fixtures partagées : les tests n'utilisent jamais le vrai ``data/cv_base.json``."""

from __future__ import annotations

import pytest

from src.services import cv_renderer


@pytest.fixture(autouse=True)
def _use_example_base_cv(monkeypatch: pytest.MonkeyPatch, tmp_path_factory) -> None:
    """Pointe le CV de base vers un fichier temporaire copié de l'exemple fictif."""
    destination = tmp_path_factory.mktemp("base_cv") / "cv_base.json"
    destination.write_text(
        cv_renderer.BASE_CV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(cv_renderer, "BASE_CV_PATH", destination)


@pytest.fixture(autouse=True)
def _no_remote_board_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """La veille n'appelle jamais Himalayas ni Remote OK pendant les tests, sauf mock explicite."""
    from src.services import job_watcher
    from src.services.job_sources.remote_common import RemoteBoardResult

    monkeypatch.setattr(job_watcher, "search_himalayas", lambda terms: RemoteBoardResult())
    monkeypatch.setattr(job_watcher, "search_remoteok", lambda terms: RemoteBoardResult())
