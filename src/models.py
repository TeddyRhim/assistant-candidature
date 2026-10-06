from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SkillCategory = Literal[
    "Forte",
    "Intermédiaire",
    "En développement",
    "IA et nouvelles technologies",
]
ContractType = Literal["CDI", "CDD", "Freelance", "Stage", "Alternance", "Autre"]
OfferStatus = Literal["À examiner", "Intéressante", "Écartée", "Candidature liée"]
CompanyDepartment = Literal["06", "75", "83"]
ApplicationStatus = Literal[
    "À préparer",
    "Prête à envoyer",
    "Envoyée",
    "Entretien",
    "Refusée",
    "Retirée",
]


def normalize_job_title(title: str) -> str:
    """Remove inclusive-gender tags commonly appended to job titles."""
    normalized_title = re.sub(
        r"\s*(?:[-–—|]\s*)?[\(\[]?\s*m\s*/\s*[fw]\s*/\s*d\s*[\)\]]?\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    ).strip()
    if not normalized_title:
        raise ValueError("Le titre de l'offre ne peut pas être vide.")
    return normalized_title


class SkillRating(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    category: SkillCategory
    level_min: float = Field(ge=0, le=10)
    level_max: float = Field(ge=0, le=10)

    @model_validator(mode="after")
    def validate_level_range(self) -> SkillRating:
        if self.level_min > self.level_max:
            raise ValueError("Le niveau minimum doit être inférieur ou égal au niveau maximum.")
        return self


class ProfileData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    target_role: str = Field(default="", max_length=200)
    experience_min_years: float | None = Field(default=None, ge=0, le=60)
    experience_max_years: float | None = Field(default=None, ge=0, le=60)
    preferred_contracts: list[ContractType] = Field(default_factory=list)
    local_locations: list[str] = Field(default_factory=list)
    remote_only_outside_local_area: bool = True
    skills: list[SkillRating] = Field(default_factory=list)

    @field_validator("local_locations")
    @classmethod
    def clean_locations(cls, locations: list[str]) -> list[str]:
        return list(dict.fromkeys(location.strip() for location in locations if location.strip()))

    @model_validator(mode="after")
    def validate_experience_range(self) -> ProfileData:
        if (
            self.experience_min_years is not None
            and self.experience_max_years is not None
            and self.experience_min_years > self.experience_max_years
        ):
            raise ValueError("L'expérience minimum doit être inférieure ou égale au maximum.")
        return self


class Base(DeclarativeBase):
    pass


class UserProfile(Base):
    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_data: Mapped[dict] = mapped_column(JSON, nullable=False)


class JobOfferData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=250)
    company: str = Field(default="", max_length=200)
    company_id: str | None = Field(default=None, min_length=32, max_length=32)
    location: str = Field(default="", max_length=200)
    contract_type: ContractType | None = None
    url: str | None = Field(default=None, max_length=2048)
    source: str = Field(default="Saisie manuelle", min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=100_000)
    status: OfferStatus = "À examiner"

    @field_validator("title")
    @classmethod
    def clean_title(cls, title: str) -> str:
        return normalize_job_title(title)

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, url: str | None) -> str | None:
        if url is None or not str(url).strip():
            return None
        return str(AnyHttpUrl(str(url).strip()))


class JobOffer(Base):
    __tablename__ = "job_offers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    company_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    location: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    contract_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    collected_at: Mapped[str] = mapped_column(String(32), nullable=False)


class CompanyData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    website_url: str | None = Field(default=None, max_length=2048)
    source_url: str = Field(min_length=1, max_length=2048)
    development_evidence: str = Field(min_length=1, max_length=2000)
    public_contact_email: str | None = Field(default=None, max_length=254)
    contact_source_url: str | None = Field(default=None, max_length=2048)
    notes: str = Field(default="", max_length=5000)

    @field_validator("website_url", "source_url", "contact_source_url", mode="before")
    @classmethod
    def validate_http_url(cls, url: str | None) -> str | None:
        if url is None or not str(url).strip():
            return None
        return str(AnyHttpUrl(str(url).strip()))

    @field_validator("public_contact_email")
    @classmethod
    def validate_public_contact_email(cls, email: str | None) -> str | None:
        if email is None or not email.strip():
            return None
        normalized_email = email.strip()
        if "@" not in normalized_email or "." not in normalized_email.rsplit("@", 1)[-1]:
            raise ValueError("L'adresse doit être une adresse e-mail publique valide.")
        return normalized_email

    @model_validator(mode="after")
    def validate_contact_source(self) -> CompanyData:
        if self.public_contact_email and self.contact_source_url is None:
            raise ValueError("La source de l'adresse e-mail publique est obligatoire.")
        return self


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    development_evidence: Mapped[str] = mapped_column(Text, nullable=False)
    public_contact_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    contact_source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_at: Mapped[str] = mapped_column(String(32), nullable=False)


class CompanyCandidateData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    siren: str = Field(pattern=r"^\d{9}$")
    name: str = Field(min_length=1, max_length=200)
    location: str = Field(min_length=1, max_length=200)
    department: CompanyDepartment
    activity_code: str = Field(min_length=1, max_length=20)
    employee_range: str | None = Field(default=None, max_length=50)
    source_url: str = Field(min_length=1, max_length=2048)
    activity_api_url: str = Field(min_length=1, max_length=2048)

    @field_validator("source_url", "activity_api_url", mode="before")
    @classmethod
    def validate_http_url(cls, url: str) -> str:
        return str(AnyHttpUrl(str(url).strip()))


class CompanyCandidate(Base):
    __tablename__ = "company_candidates"

    siren: Mapped[str] = mapped_column(String(9), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    department: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    activity_code: Mapped[str] = mapped_column(String(20), nullable=False)
    employee_range: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    activity_api_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    discovered_at: Mapped[str] = mapped_column(String(32), nullable=False)


class ApplicationData(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company_id: str = Field(min_length=32, max_length=32)
    job_offer_id: str | None = Field(default=None, min_length=32, max_length=32)
    role: str = Field(min_length=1, max_length=250)
    status: ApplicationStatus = "À préparer"
    applied_on: str | None = None
    next_action: str = Field(default="", max_length=500)
    next_action_on: str | None = None
    notes: str = Field(default="", max_length=5000)

    @field_validator("applied_on", "next_action_on")
    @classmethod
    def validate_iso_date(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as error:
            raise ValueError("La date doit être au format AAAA-MM-JJ.") from error


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    company_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_offer_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("job_offers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(250), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    applied_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    next_action: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    next_action_on: Mapped[str | None] = mapped_column(String(10), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    prep_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ResumeVersion(Base):
    __tablename__ = "resume_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    reviewed_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class TailoredResume(Base):
    __tablename__ = "tailored_resumes"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_offer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("job_offers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    source_resume_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("resume_versions.id", ondelete="RESTRICT"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class CoverLetter(Base):
    __tablename__ = "cover_letters"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    job_offer_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("job_offers.id", ondelete="CASCADE"), nullable=True
    )
    company_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    target_company: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
