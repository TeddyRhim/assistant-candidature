from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from src.models import ContractType

RelocationSignal = Literal["not_mentioned", "mentioned"]


class JobSourceError(RuntimeError):
    """A job source could not return a valid search response."""


class JobSearchQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    keywords: list[str] = Field(default_factory=list, max_length=20)
    countries: list[str] = Field(default_factory=list, max_length=10)
    locations: list[str] = Field(default_factory=list, max_length=20)
    contracts: list[ContractType] = Field(default_factory=list)
    remote_only: bool = False


class SourceListing(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: str = Field(min_length=1, max_length=100)
    source_name: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=250)
    company: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    contract_type: ContractType | None = None
    original_url: AnyHttpUrl
    description: str = Field(default="", max_length=100_000)
    public_contact_email: str | None = Field(default=None, max_length=254)
    contact_source_url: AnyHttpUrl | None = None
    relocation_signal: RelocationSignal = "not_mentioned"
    relocation_evidence: str | None = Field(default=None, max_length=500)

    @field_validator("country_code")
    @classmethod
    def normalize_country_code(cls, country_code: str | None) -> str | None:
        return country_code.upper() if country_code else None

    @field_validator("public_contact_email")
    @classmethod
    def validate_public_email(cls, email: str | None) -> str | None:
        if email is None:
            return None
        normalized_email = email.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized_email):
            raise ValueError("L'adresse de contact doit être un e-mail public valide.")
        return normalized_email

    @model_validator(mode="after")
    def validate_evidence_provenance(self) -> SourceListing:
        if self.public_contact_email and self.contact_source_url is None:
            raise ValueError("La provenance du contact e-mail doit être conservée.")
        if self.relocation_signal == "mentioned" and not self.relocation_evidence:
            raise ValueError("Une mention de relocalisation doit conserver son extrait source.")
        return self


@dataclass(frozen=True)
class JobSourceDescriptor:
    source_id: str
    display_name: str
    access_method: Literal["official_api", "rss", "manual"]
    documentation_url: str
    enabled: bool = False


class JobSource(Protocol):
    descriptor: JobSourceDescriptor

    def search(self, query: JobSearchQuery) -> list[SourceListing]:
        """Return published listings through an approved and configured access method."""


_RELOCATION_PATTERNS = [
    re.compile(
        r"([^.\n]*\brelocation\s+(?:assistance|package|support|bonus|allowance)[^.\n]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([^.\n]*\b(?:aide|accompagnement)\s+(?:à\s+la\s+relocalisation|au\s+déménagement)[^.\n]*)",
        re.IGNORECASE,
    ),
    re.compile(
        r"([^.\n]*\brelocation\b[^.\n]*\b(?:included|offered|available|provided)[^.\n]*)",
        re.IGNORECASE,
    ),
]

_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

_COUNTRY_KEYWORDS: dict[str, str] = {
    "france": "FR",
    "paris": "FR",
    "germany": "DE",
    "deutschland": "DE",
    "berlin": "DE",
    "united kingdom": "GB",
    "uk": "GB",
    "london": "GB",
    "united states": "US",
    "usa": "US",
    "canada": "CA",
    "belgium": "BE",
    "belgique": "BE",
    "brussels": "BE",
    "switzerland": "CH",
    "suisse": "CH",
    "spain": "ES",
    "espagne": "ES",
    "madrid": "ES",
    "barcelona": "ES",
    "italy": "IT",
    "italie": "IT",
    "netherlands": "NL",
    "pays-bas": "NL",
    "amsterdam": "NL",
    "poland": "PL",
    "pologne": "PL",
    "austria": "AT",
    "autriche": "AT",
    "ireland": "IE",
    "irlande": "IE",
    "dublin": "IE",
}


def clean_html_to_text(raw_html: str) -> str:
    """Strip HTML tags and convert structure into clean readable plain text."""
    if not raw_html:
        return ""
    text = re.sub(r"<(?:br|br\s*/)>", "\n", raw_html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\n\1\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def detect_relocation(text: str) -> tuple[RelocationSignal, str | None]:
    """Detect explicit relocation assistance in job text with a snippet."""
    if not text:
        return "not_mentioned", None
    for pattern in _RELOCATION_PATTERNS:
        match = pattern.search(text)
        if match:
            snippet = match.group(1).strip()
            if len(snippet) > 300:
                snippet = snippet[:297] + "..."
            return "mentioned", snippet
    return "not_mentioned", None


def extract_explicit_email(text: str) -> str | None:
    """Extract a publicly published contact email address from job description text."""
    if not text:
        return None
    for match in _EMAIL_PATTERN.finditer(text):
        candidate = match.group(0).strip().lower()
        if not candidate.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            if not any(
                candidate.startswith(prefix)
                for prefix in ("noreply@", "no-reply@", "example@", "test@")
            ):
                return candidate
    return None


def detect_country_code(location: str) -> str | None:
    """Attempt to detect a 2-letter country code from a location string."""
    if not location:
        return None
    cleaned = location.casefold()
    for keyword, code in _COUNTRY_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", cleaned):
            return code
    return None
