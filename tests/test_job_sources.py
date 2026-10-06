from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.services.job_sources.base import (
    JobSearchQuery,
    JobSourceDescriptor,
    SourceListing,
)


def test_source_listing_preserves_original_source_and_public_contact() -> None:
    listing = SourceListing(
        source_id="company-123",
        source_name="Employer Board",
        title="Backend Developer",
        country_code="fr",
        original_url="https://jobs.example.com/backend",
        public_contact_email="recruitment@example.com",
        contact_source_url="https://jobs.example.com/backend",
        relocation_signal="mentioned",
        relocation_evidence="Relocation support may be available.",
    )

    assert listing.country_code == "FR"
    assert str(listing.original_url) == "https://jobs.example.com/backend"
    assert listing.public_contact_email == "recruitment@example.com"
    assert listing.relocation_signal == "mentioned"
    assert listing.relocation_evidence == "Relocation support may be available."


def test_source_listing_requires_evidence_for_relocation_mention() -> None:
    with pytest.raises(ValidationError, match="extrait source"):
        SourceListing(
            source_id="company-123",
            source_name="Employer Board",
            title="Backend Developer",
            original_url="https://jobs.example.com/backend",
            relocation_signal="mentioned",
        )


def test_source_listing_rejects_invalid_contact_email_and_non_http_url() -> None:
    with pytest.raises(ValidationError, match="e-mail public valide"):
        SourceListing(
            source_id="company-123",
            source_name="Employer Board",
            title="Backend Developer",
            original_url="https://jobs.example.com/backend",
            public_contact_email="not-an-email",
        )
    with pytest.raises(ValidationError, match="provenance du contact"):
        SourceListing(
            source_id="company-123",
            source_name="Employer Board",
            title="Backend Developer",
            original_url="https://jobs.example.com/backend",
            public_contact_email="recruitment@example.com",
        )
    with pytest.raises(ValidationError):
        SourceListing(
            source_id="company-123",
            source_name="Employer Board",
            title="Backend Developer",
            original_url="javascript:alert(1)",
        )


def test_job_source_descriptor_defaults_to_disabled() -> None:
    source = JobSourceDescriptor(
        source_id="greenhouse",
        display_name="Greenhouse",
        access_method="official_api",
        documentation_url="https://docs.example.com/api",
    )

    assert source.enabled is False


def test_search_query_defaults_to_explicit_empty_filters() -> None:
    query = JobSearchQuery(remote_only=True)

    assert query.keywords == []
    assert query.countries == []
    assert query.locations == []
    assert query.contracts == []
    assert query.remote_only is True


def test_job_sources_package_exports() -> None:
    import src.services.job_sources as js

    assert hasattr(js, "fetch_greenhouse_listings")
    assert hasattr(js, "fetch_lever_listings")
    assert hasattr(js, "GREENHOUSE_DESCRIPTOR")
    assert hasattr(js, "LEVER_DESCRIPTOR")
    assert js.GREENHOUSE_DESCRIPTOR.enabled is True
    assert js.LEVER_DESCRIPTOR.enabled is True
