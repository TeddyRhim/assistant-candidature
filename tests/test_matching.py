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
    search_languages,
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
    # JavaScript (ECMAScript) et Symfony sont demandés, tous deux au niveau 8/10 du profil.
    assert result.match_percentage == 80


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
    # L'annonce ne demande que PHP (niveau 8/10) : une annonce courte n'est plus pénalisée.
    assert result.match_percentage == 80
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

    assert result.skills.match_percentage == 80
    assert result.role_percentage == 100
    assert result.contract_compatible is True
    assert result.location_compatible is True
    assert result.location_reason == "hors zone, mais le télétravail est mentionné"
    assert result.overall_percentage == 88
    assert result.evaluated_criteria == (
        "compétences",
        "intitulé",
        "contrat",
        "télétravail",
    )


def _stack_profile() -> ProfileData:
    return ProfileData(
        target_role="Développeur backend / full-stack à dominante backend",
        skills=[
            SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
            SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
            SkillRating(name="SQL", category="Forte", level_min=7, level_max=7),
            SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
        ],
    )


def test_role_score_rewards_strong_stack_skills_cited_in_title() -> None:
    profile = _stack_profile()

    def role(title: str) -> int | None:
        return assess_offer_fit(JobOfferData(title=title, description="x"), profile).role_percentage

    # Une compétence forte dans le titre : 70 % au lieu de 20 % (seul « développeur » recoupe).
    assert role("Développeur PHP") == 70
    # Deux compétences fortes : correspondance complète.
    assert role("Développeur PHP Symfony (IT)") == 100
    # Langage accepté (niveau 5 ou plus, ici Python) : bonus intermédiaire de 50 %.
    assert role("Développeur Python") == 50
    # Langage absent du profil ou compétence générique (SQL) : pas de bonus de pile.
    assert role("Développeur Java") == 20
    assert role("Consultant DBA SQL Server") == 0
    # Le recoupement avec l'intitulé cible reste prioritaire quand il est supérieur.
    assert role("Développeur backend full stack") == 80


def _stack_skills() -> list[SkillRating]:
    return [
        SkillRating(name="PHP", category="Forte", level_min=8, level_max=8),
        SkillRating(name="Symfony", category="Forte", level_min=8, level_max=8),
        SkillRating(name="SQL", category="Forte", level_min=7, level_max=7),
        SkillRating(name="Docker", category="Intermédiaire", level_min=5, level_max=5),
        SkillRating(name="Python", category="Intermédiaire", level_min=5, level_max=5),
        SkillRating(name="JavaScript", category="Intermédiaire", level_min=5, level_max=5),
        SkillRating(name="Java", category="En développement", level_min=4, level_max=4),
    ]


def _coverage(title: str, description: str, skills: list[SkillRating] | None = None) -> int | None:
    profile = ProfileData(skills=skills or _stack_skills())
    offer = JobOfferData(title=title, description=description)
    return compare_offer_to_profile(offer, profile).match_percentage


def test_score_measures_what_the_offer_requires_not_the_whole_profile() -> None:
    # PHP 8 + Symfony 8 + SQL 7 + Docker 5 -> moyenne (0,8 + 0,8 + 0,7 + 0,5) / 4 = 70 %.
    assert _coverage("Développeur", "PHP, Symfony, SQL et Docker.") == 70
    # Une annonce Python n'est plus écrasée par la force du profil en PHP :
    # Python (titre, poids 2) 0,5 et SQL 0,7 -> (2 x 0,5 + 0,7) / 3 = 57 %.
    assert _coverage("Développeur Python", "Python et SQL.") == 57
    # Une stack éloignée du profil (Java 4/10 ; Angular dérivé de JavaScript) reste basse.
    assert _coverage("Développeur Java Angular", "Java, Spring Boot et Angular.") < 40


def test_title_technologies_weigh_double() -> None:
    in_title = _coverage("Développeur PHP", "Docker aussi.")
    in_body = _coverage("Développeur", "PHP et Docker.")

    assert in_title == 70  # (2 x 0,8 + 0,5) / 3
    assert in_body == 65


def test_related_framework_counts_for_a_fraction_of_its_language() -> None:
    # Laravel n'est pas au profil : 0,6 x niveau de PHP (8/10) = 0,48 -> 48 %.
    assert _coverage("Développeur", "Framework Laravel.") == 48
    # Avec PHP (0,8) en plus et Laravel dans le titre (poids 2) : (2 x 0,48 + 0,8) / 3 = 59 %.
    assert _coverage("Développeur Laravel", "Poste en PHP et Laravel.") == 59


def test_node_and_vue_js_do_not_count_as_plain_javascript() -> None:
    profile = ProfileData(skills=_stack_skills())
    offer = JobOfferData(title="Développeur", description="Node.js et Vue.js.")

    technologies = {
        item.label for item in compare_offer_to_profile(offer, profile).required_technologies
    }

    assert technologies == {"Node.js", "Vue.js"}


def test_score_falls_back_to_profile_coverage_when_no_known_technology() -> None:
    skills = [SkillRating(name="Gestion de projets", category="Forte", level_min=8, level_max=8)]
    offer = JobOfferData(title="Chef de projet", description="Gestion de projets.")
    result = compare_offer_to_profile(offer, ProfileData(skills=skills))

    assert result.required_technologies == ()
    assert result.requirements_percentage is None
    assert result.match_percentage == 100


def test_search_languages_follow_declared_levels() -> None:
    profile = ProfileData(skills=_stack_skills())

    # Java (niveau 4) est exclu ; SQL et Docker ne sont pas des langages.
    assert search_languages(profile) == ("PHP", "Python", "JavaScript")

    java_expert = SkillRating(name="Java", category="Forte", level_min=9, level_max=9)
    stronger_java = ProfileData(skills=[*_stack_skills()[:-1], java_expert])
    assert search_languages(stronger_java)[0] == "Java"


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
