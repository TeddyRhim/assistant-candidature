from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIRECTORY_ENV = "ASSISTANT_CANDIDATURES_DATA_DIR"
ADZUNA_APP_ID_ENV = "ADZUNA_APP_ID"
ADZUNA_APP_KEY_ENV = "ADZUNA_APP_KEY"
FRANCE_TRAVAIL_CLIENT_ID_ENV = "FRANCE_TRAVAIL_CLIENT_ID"
FRANCE_TRAVAIL_CLIENT_SECRET_ENV = "FRANCE_TRAVAIL_CLIENT_SECRET"
MAILJET_API_KEY_ENV = "MAILJET_API_KEY"
MAILJET_API_SECRET_ENV = "MAILJET_API_SECRET"
MAILJET_FROM_EMAIL_ENV = "MAILJET_FROM_EMAIL"
MAILJET_FROM_NAME_ENV = "MAILJET_FROM_NAME"
MAX_RESUME_SIZE_BYTES = 10 * 1024 * 1024


def get_data_dir() -> Path:
    configured_path = os.environ.get(DATA_DIRECTORY_ENV)
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return PROJECT_ROOT / "data"


def get_database_path() -> Path:
    return get_data_dir() / "assistant-candidatures.sqlite3"


def get_profile_seed_path() -> Path:
    return get_data_dir() / "profile_seed.json"


def get_adzuna_credentials(
    secrets: Mapping[str, object] | None = None,
) -> tuple[str, str]:
    configured = secrets or {}
    app_id = os.environ.get(ADZUNA_APP_ID_ENV, "") or configured.get(
        ADZUNA_APP_ID_ENV, ""
    )
    app_key = os.environ.get(ADZUNA_APP_KEY_ENV, "") or configured.get(
        ADZUNA_APP_KEY_ENV, ""
    )
    return str(app_id).strip(), str(app_key).strip()


def get_france_travail_credentials(
    secrets: Mapping[str, object] | None = None,
) -> tuple[str, str]:
    configured = secrets or {}
    client_id = os.environ.get(FRANCE_TRAVAIL_CLIENT_ID_ENV, "") or configured.get(
        FRANCE_TRAVAIL_CLIENT_ID_ENV, ""
    )
    client_secret = os.environ.get(FRANCE_TRAVAIL_CLIENT_SECRET_ENV, "") or configured.get(
        FRANCE_TRAVAIL_CLIENT_SECRET_ENV, ""
    )
    return str(client_id).strip(), str(client_secret).strip()


def get_mailjet_settings(
    secrets: Mapping[str, object] | None = None,
) -> tuple[str, str, str, str]:
    configured = secrets or {}
    settings = []
    for name in (
        MAILJET_API_KEY_ENV,
        MAILJET_API_SECRET_ENV,
        MAILJET_FROM_EMAIL_ENV,
        MAILJET_FROM_NAME_ENV,
    ):
        settings.append(
            str(os.environ.get(name, "") or configured.get(name, "")).strip()
        )
    return settings[0], settings[1], settings[2], settings[3]
