from __future__ import annotations

from src.config import get_adzuna_credentials


def test_adzuna_credentials_are_read_from_local_secrets(monkeypatch) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    assert get_adzuna_credentials(
        {"ADZUNA_APP_ID": " local-id ", "ADZUNA_APP_KEY": " local-key "}
    ) == ("local-id", "local-key")


def test_environment_credentials_override_local_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ADZUNA_APP_ID", "environment-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "environment-key")

    assert get_adzuna_credentials(
        {"ADZUNA_APP_ID": "local-id", "ADZUNA_APP_KEY": "local-key"}
    ) == ("environment-id", "environment-key")


def test_adzuna_credentials_default_to_empty_strings(monkeypatch) -> None:
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)

    assert get_adzuna_credentials() == ("", "")
