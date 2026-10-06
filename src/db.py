from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import URL, Engine, create_engine, event
from sqlalchemy.orm import Session

from src.config import get_database_path, get_profile_seed_path
from src.models import Base, ProfileData, UserProfile


def create_database_engine(database_path: Path | None = None) -> Engine:
    path = database_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    url = URL.create("sqlite", database=str(path))
    engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _migrate_database(engine)


def _migrate_database(engine: Engine) -> None:
    with engine.connect() as connection:
        raw_connection = connection.connection
        cursor = raw_connection.cursor()
        cursor.execute("PRAGMA table_info(job_offers)")
        columns = [row[1] for row in cursor.fetchall()]
        if columns and "company_id" not in columns:
            cursor.execute(
                "ALTER TABLE job_offers ADD COLUMN company_id VARCHAR(32) "
                "REFERENCES companies(id) ON DELETE SET NULL"
            )
            raw_connection.commit()
        cursor.execute("PRAGMA table_info(applications)")
        application_columns = [row[1] for row in cursor.fetchall()]
        if application_columns and "prep_seconds" not in application_columns:
            cursor.execute("ALTER TABLE applications ADD COLUMN prep_seconds INTEGER")
            raw_connection.commit()
        cursor.close()


def load_profile(engine: Engine) -> ProfileData:
    with Session(engine) as session:
        profile = session.get(UserProfile, 1)
        if profile is None:
            return ProfileData()
        return ProfileData.model_validate(profile.profile_data)


def load_or_seed_profile(engine: Engine) -> ProfileData:
    with Session(engine) as session, session.begin():
        profile = session.get(UserProfile, 1)
        if profile is not None:
            return ProfileData.model_validate(profile.profile_data)

        seed_path = get_profile_seed_path()
        if not seed_path.is_file():
            return ProfileData()

        profile_data = ProfileData.model_validate(
            json.loads(seed_path.read_text(encoding="utf-8"))
        )
        session.add(UserProfile(id=1, profile_data=profile_data.model_dump(mode="json")))
        return profile_data


def save_profile(engine: Engine, profile_data: ProfileData) -> None:
    with Session(engine) as session, session.begin():
        profile = session.get(UserProfile, 1)
        if profile is None:
            profile = UserProfile(id=1, profile_data=profile_data.model_dump(mode="json"))
            session.add(profile)
        else:
            profile.profile_data = profile_data.model_dump(mode="json")
