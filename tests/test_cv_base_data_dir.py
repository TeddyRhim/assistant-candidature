"""Le CV de base suit ASSISTANT_CANDIDATURES_DATA_DIR en lecture comme en écriture."""

from __future__ import annotations

import json
from pathlib import Path

from src.config import DATA_DIRECTORY_ENV, PROJECT_ROOT
from src.services import cv_renderer


def _sample_cv() -> dict:
    return json.loads(cv_renderer.get_base_cv_example_path().read_text(encoding="utf-8"))


def test_base_cv_paths_follow_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))

    assert cv_renderer.get_base_cv_path() == tmp_path.resolve() / "cv_base.json"

    other = tmp_path / "autre"
    other.mkdir()
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(other))
    assert cv_renderer.get_base_cv_path() == other.resolve() / "cv_base.json"


def test_base_cv_defaults_to_project_data_without_variable(monkeypatch) -> None:
    monkeypatch.delenv(DATA_DIRECTORY_ENV, raising=False)

    assert cv_renderer.get_base_cv_path() == PROJECT_ROOT / "data" / "cv_base.json"


def test_base_cv_read_and_write_stay_in_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(tmp_path))
    real_cv = PROJECT_ROOT / "data" / "cv_base.json"
    real_before = real_cv.read_bytes() if real_cv.exists() else None

    # Lecture sans fichier : repli sur l'exemple fictif, rien n'est créé.
    assert cv_renderer.load_base_cv_data()["name"]
    assert not (tmp_path / "cv_base.json").exists()

    data = _sample_cv()
    data["profile"] = "Profil modifié pour le test."
    cv_renderer.save_base_cv_json(json.dumps(data, ensure_ascii=False))

    saved = tmp_path / "cv_base.json"
    assert json.loads(saved.read_text(encoding="utf-8"))["profile"] == data["profile"]
    assert cv_renderer.load_base_cv_data()["profile"] == data["profile"]
    assert not saved.with_suffix(".json.tmp").exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cv_base.json"]

    real_after = real_cv.read_bytes() if real_cv.exists() else None
    assert real_after == real_before
