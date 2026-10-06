from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src import db
from src.config import DATA_DIRECTORY_ENV, get_data_dir, get_database_path
from src.models import ProfileData, SkillRating


def test_data_directory_can_be_configured(tmp_path, monkeypatch) -> None:
    configured = tmp_path / "private-data"
    monkeypatch.setenv(DATA_DIRECTORY_ENV, str(configured))

    assert get_data_dir() == configured.resolve()
    assert get_database_path() == configured.resolve() / "assistant-candidatures.sqlite3"


def test_profile_persists_and_updates_in_sqlite(tmp_path) -> None:
    database_path = tmp_path / "nested" / "profile.sqlite3"
    engine = db.create_database_engine(database_path)
    db.initialize_database(engine)

    initial = ProfileData(
        target_role="Développeur backend",
        experience_min_years=5,
        experience_max_years=7,
        preferred_contracts=["CDI"],
        local_locations=["Nice", "Var"],
        skills=[
            SkillRating(
                name="Symfony",
                category="Forte",
                level_min=8,
                level_max=8,
            )
        ],
    )
    db.save_profile(engine, initial)
    assert db.load_profile(engine) == initial

    updated = initial.model_copy(update={"target_role": "Développeur Symfony"})
    db.save_profile(engine, updated)
    assert db.load_profile(engine) == updated
    engine.dispose()


def test_profile_seed_is_imported_only_when_no_profile_exists(tmp_path, monkeypatch) -> None:
    seed_path = tmp_path / "profile_seed.json"
    seed_data = {"target_role": "Développeur backend", "preferred_contracts": ["CDI"]}
    seed_path.write_text(json.dumps(seed_data), encoding="utf-8")
    monkeypatch.setattr(db, "get_profile_seed_path", lambda: seed_path)

    engine = db.create_database_engine(tmp_path / "profile.sqlite3")
    db.initialize_database(engine)
    assert db.load_or_seed_profile(engine).target_role == "Développeur backend"

    seed_path.write_text(json.dumps({"target_role": "Ne pas réimporter"}), encoding="utf-8")
    assert db.load_or_seed_profile(engine).target_role == "Développeur backend"
    engine.dispose()


def test_skill_rating_rejects_invalid_range() -> None:
    with pytest.raises(ValidationError):
        SkillRating(name="Python", category="Forte", level_min=8, level_max=6)


def test_profile_rejects_reversed_experience_range() -> None:
    with pytest.raises(ValidationError):
        ProfileData(experience_min_years=7, experience_max_years=5)
