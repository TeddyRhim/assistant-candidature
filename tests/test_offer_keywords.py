from __future__ import annotations

from src.models import JobOfferData, ProfileData, SkillRating
from src.services.offer_keywords import (
    extract_offer_keywords,
    keyword_report,
    tailor_cv_data,
)

OFFER = JobOfferData(
    title="Développeur PHP Symfony H/F",
    company="Atelier Tech",
    location="Bourg-en-Bresse",
    description=(
        "Vous maîtrisez PHP 8 et Symfony. API REST obligatoire, SQL. "
        "Tests unitaires appréciés. Kubernetes est un plus."
    ),
)


def _cv() -> dict:
    return {
        "name": "Alex Martin",
        "profile": "Développeur backend, expert Symfony et bases SQL.",
        "experience": [
            {
                "role": "Développeur",
                "company": "Studio A",
                "period": "2020 - 2024",
                "bullets": [
                    "Animation d'ateliers internes.",
                    "Gestion des sauvegardes serveur.",
                    "Conception d'**API REST** en PHP avec Symfony.",
                    "Optimisation de requêtes SQL.",
                    "Formation des nouveaux arrivants.",
                    "Veille technologique.",
                    "Rédaction de documentation.",
                    "Support utilisateurs.",
                ],
            }
        ],
        "skills": [
            {"label": "Outils", "details": "Jira, Docker"},
            {"label": "Back-end", "details": "Twig, Symfony, API REST (JSON, XML), PHP"},
        ],
    }


def test_keywords_come_from_the_offer_with_title_terms_first() -> None:
    labels = [keyword.label for keyword in extract_offer_keywords(OFFER)]

    assert labels[:2] == ["PHP", "Symfony"]
    assert {"API REST", "SQL", "Tests automatisés", "Kubernetes"} <= set(labels)
    assert labels.count("API REST") == 1


def test_report_separates_what_the_cv_says_from_what_it_does_not() -> None:
    profile = ProfileData(
        target_role="Dev",
        skills=[
            SkillRating(name="Kubernetes", category="En développement", level_min=2, level_max=2)
        ],
    )

    report = keyword_report(OFFER, _cv(), profile)

    assert {"PHP", "Symfony", "API REST", "SQL"} <= set(report.found)
    assert report.profile_only == ["Kubernetes"]
    assert report.missing == ["Tests automatisés"]
    assert report.coverage_percentage == 67


def test_tailoring_puts_relevant_bullets_first_and_trims_the_rest() -> None:
    tailored = tailor_cv_data(_cv(), OFFER)

    bullets = tailored["experience"][0]["bullets"]
    assert len(bullets) == 4
    assert "API REST" in bullets[0]
    assert "Support utilisateurs." not in bullets
    assert "Optimisation de requêtes SQL." in bullets[:3] or "**SQL**" in " ".join(bullets[:3])


def test_tailoring_never_adds_a_term_the_cv_does_not_contain() -> None:
    tailored = tailor_cv_data(_cv(), OFFER)

    text = str(tailored).casefold()
    assert "kubernetes" not in text
    assert "phpunit" not in text
    assert "tests unitaires" not in text


def test_tailoring_bolds_terms_without_doubling_existing_bold() -> None:
    tailored = tailor_cv_data(_cv(), OFFER)

    bullets = tailored["experience"][0]["bullets"]
    assert bullets[0].count("**") == 4 or "**API REST**" in bullets[0]
    assert "****" not in "".join(bullets)
    assert "**" in tailored["profile"]


def test_tailoring_moves_matching_skill_lines_and_items_first() -> None:
    tailored = tailor_cv_data(_cv(), OFFER)

    assert tailored["skills"][0]["label"] == "Back-end"
    details = tailored["skills"][0]["details"]
    assert details.startswith("Symfony") or details.startswith("PHP")
    assert "API REST (JSON, XML)" in details


def test_original_cv_is_not_mutated() -> None:
    original = _cv()
    tailor_cv_data(original, OFFER)

    assert original == _cv()


def test_offer_without_known_terms_leaves_the_cv_as_is() -> None:
    offer = JobOfferData(title="Comptable", company="X", description="Tenue de la comptabilité.")

    assert tailor_cv_data(_cv(), offer) == _cv()
