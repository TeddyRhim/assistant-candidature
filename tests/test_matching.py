from __future__ import annotations

from src.models import (
    Company,
    CompanyCandidate,
    CompanyCandidateData,
    JobOfferData,
    ProfileData,
    SkillRating,
)
from src.services.matching import (
    assess_company_fit,
    assess_offer_fit,
    compare_offer_to_profile,
)


def test_comparison_reports_mentions_with_evidence_and_others_for_review() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
        ]
    )
    offer = JobOfferData(
        title="Développeur backend",
        description="Nous recherchons un développeur Symfony.\n"
        "Une bonne connaissance de PHP est indispensable.",
    )

    result = compare_offer_to_profile(offer, profile)

    assert result.mentioned_count == 2
    assert result.needs_review_count == 1
    assert [match.skill.name for match in result.matches if match.mentioned] == [
        "PHP",
        "Symfony",
    ]
    assert result.matches[0].evidence == "Une bonne connaissance de PHP est indispensable."
    assert result.matches[2].evidence is None


def test_skill_match_uses_whole_terms_and_ignores_case() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="API REST", category="Forte", level_min=8, level_max=8),
        ]
    )
    offer = JobOfferData(
        title="Développeur",
        description="Nous utilisons phpunit et concevons des api rest.",
    )

    result = compare_offer_to_profile(offer, profile)

    assert [match.mentioned for match in result.matches] == [False, True]


def test_skill_aliases_and_requirement_priority_improve_match_score() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="JavaScript", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
        ]
    )
    offer = JobOfferData(
        title="Développeur backend",
        description=(
            "ECMAScript est indispensable pour le poste. Symfony serait un plus."
        ),
    )

    result = compare_offer_to_profile(offer, profile)

    assert [match.mentioned for match in result.matches] == [True, True, False]
    assert result.matches[0].priority == "requise dans l'annonce"
    assert result.matches[1].priority == "bonus dans l'annonce"
    assert result.matches[2].priority == "mentionnée"
    assert result.match_percentage == 70


def test_match_percentage_does_not_penalize_rich_candidate_profile() -> None:
    skills = [
        SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
        SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        SkillRating(name="API REST", category="Forte", level_min=8, level_max=8),
        SkillRating(name="SQL", category="Forte", level_min=7, level_max=7),
        SkillRating(name="Docker", category="Intermédiaire", level_min=5, level_max=5),
        SkillRating(name="Git", category="Forte", level_min=7, level_max=7),
        SkillRating(name="Kubernetes", category="En développement", level_min=3, level_max=3),
        SkillRating(name="FastAPI", category="En développement", level_min=4, level_max=4),
        SkillRating(name="Vue.js", category="Intermédiaire", level_min=5, level_max=5),
        SkillRating(name="Spring Boot", category="En développement", level_min=4, level_max=4),
        SkillRating(name="CI/CD", category="Intermédiaire", level_min=6, level_max=6),
        SkillRating(name="Linux", category="Intermédiaire", level_min=6, level_max=6),
        SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
        SkillRating(name="Java", category="En développement", level_min=4, level_max=4),
        SkillRating(name="Microservices", category="En développement", level_min=4, level_max=4),
    ]
    profile = ProfileData(skills=skills)
    offer = JobOfferData(
        title="Développeur PHP / Symfony",
        description=(
            "Nous recherchons un développeur backend PHP et Symfony.\n"
            "Maîtrise d'API REST et de SQL requise."
        ),
    )
    result = compare_offer_to_profile(offer, profile)
    assert result.match_percentage is not None
    assert result.match_percentage >= 75


def test_empty_profile_returns_empty_comparison() -> None:
    result = compare_offer_to_profile(
        JobOfferData(title="Développeur", description="Annonce."),
        ProfileData(),
    )

    assert result.matches == ()
    assert result.mentioned_count == 0
    assert result.needs_review_count == 0
    assert result.match_percentage is None


def test_match_percentage_counts_all_profile_skills_and_includes_title() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
            SkillRating(name="PostgreSQL", category="Intermédiaire", level_min=5, level_max=5),
        ]
    )
    offer = JobOfferData(title="Développeur PHP", description="Stack backend.")

    result = compare_offer_to_profile(offer, profile)

    assert result.mentioned_count == 1
    assert result.match_percentage == 38
    assert result.matches[0].evidence == "Développeur PHP"


def test_offer_fit_weights_skills_and_adds_target_role_contract_and_location() -> None:
    profile = ProfileData(
        target_role="Développeur backend",
        preferred_contracts=["CDI"],
        local_locations=["Nice"],
        remote_only_outside_local_area=True,
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
        ],
    )
    offer = JobOfferData(
        title="Développeur backend PHP",
        location="Paris — remote",
        contract_type="CDI",
        description="Poste fully remote avec PHP.",
    )

    result = assess_offer_fit(offer, profile)

    assert result.skills.match_percentage == 62
    assert result.role_percentage == 100
    assert result.contract_compatible is True
    assert result.location_compatible is True
    assert result.location_reason == "hors zone, mais le télétravail est mentionné"
    assert result.overall_percentage == 77
    assert result.evaluated_criteria == (
        "compétences",
        "intitulé",
        "contrat",
        "télétravail",
    )


def test_hybrid_work_does_not_satisfy_remote_only_preference() -> None:
    profile = ProfileData(
        target_role="Développeur",
        local_locations=["Nice"],
        remote_only_outside_local_area=True,
    )
    offer = JobOfferData(
        title="Développeur",
        location="Paris",
        description="Poste hybride avec deux jours de télétravail par semaine.",
    )

    result = assess_offer_fit(offer, profile)

    assert result.location_compatible is False
    assert result.location_reason == "hors zone renseignée"


def test_company_fit_labels_registered_software_activity_without_inventing_team_match() -> None:
    profile = ProfileData(
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
        ]
    )
    company = Company(
        name="Studio Exemple",
        location="Nice",
        development_evidence=(
            "Activité déclarée 62.01Z (Programmation informatique). "
            "La présence d'une équipe reste à vérifier."
        ),
        notes="",
    )

    result = assess_company_fit(company, profile)

    assert result.activity_is_software is True
    assert "62.01Z" in result.activity_label
    assert result.mentioned_skills == ()
    assert [skill.name for skill in result.unknown_skills] == ["PHP"]


def test_saved_registry_candidate_is_identified_as_software_activity() -> None:
    candidate = CompanyCandidate(
        siren="123456789",
        name="Studio Exemple",
        location="Nice",
        department="06",
        activity_code="62.01Z",
        employee_range=None,
        source_url="https://annuaire-entreprises.data.gouv.fr/entreprise/123456789",
        activity_api_url="https://recherche-entreprises.api.gouv.fr/search",
        discovered_at="2026-01-01T00:00:00+00:00",
    )

    result = assess_company_fit(candidate, ProfileData())

    assert result.activity_is_software is True
    assert result.activity_label.endswith("(62.01Z)")


def test_search_registry_candidate_data_can_be_assessed_before_saving() -> None:
    candidate = CompanyCandidateData(
        siren="123456789",
        name="Studio Exemple",
        location="Nice",
        department="06",
        activity_code="62.01Z",
        source_url="https://annuaire-entreprises.data.gouv.fr/entreprise/123456789",
        activity_api_url="https://recherche-entreprises.api.gouv.fr/search",
    )

    result = assess_company_fit(candidate, ProfileData())

    assert result.activity_is_software is True
    assert result.activity_label.endswith("(62.01Z)")
