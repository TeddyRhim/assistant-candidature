from __future__ import annotations

import pytest

from src.models import JobOfferData, ProfileData
from src.services.cover_letters import build_cover_letter
from src.services.cv_renderer import tailored_cv_filename
from src.services.job_titles import extract_job_role
from src.services.tailored_resumes import build_tailored_resume

# Titres réels d'annonces (Adzuna, LinkedIn, France Travail) et le poste attendu.
REAL_TITLES = [
    ("- Ingénieurs Full Stack en CDI H/F", "Ingénieurs Full Stack"),
    ("Architecte / Lead PHP - CDI - Paris", "Architecte / Lead PHP"),
    ("Backend Developer with PHP and Symfony -NL Based", "Backend Developer with PHP and Symfony"),
    ("Backend Engineer PHP H/F", "Backend Engineer PHP"),
    ("CDI - Data Engineer F/H", "Data Engineer"),
    ("DEVELOPPEUR PHP - PARIS", "Développeur PHP"),
    ("Developpeur SQL Server F/H", "Développeur SQL Server"),
    (
        "Stage - Ingénieur.e Développement IA & Web - 6 mois H/F",
        "Ingénieur Développement IA & Web",
    ),
    (
        "\u2705 Backend Developer PHP/Symfony – Open Source & AI Features",
        "Backend Developer PHP/Symfony",
    ),
    (
        "Technicien Systèmes Numériques - Développeur Informatique H/F",
        "Technicien Systèmes Numériques - Développeur Informatique",
    ),
    ("Data engineer python / airflow / sql f/h (CDI)", "Data engineer python / airflow / sql"),
    (
        "Développeur Back-End PHP H/F at Synanto — Marseille, Provence-Alpes-Côte d&#39;Azur, "
        "France | LinkedIn Jobs",
        "Développeur Back-End PHP",
    ),
    ("Développeur C# ASP.NET / T-SQL / API REST (IT)", "Développeur C# ASP.NET / T-SQL / API REST"),
    ("Développeur Full-Stack (JavaScript) H/F", "Développeur Full-Stack (JavaScript)"),
    (
        "Développeur ou développeuse Full Stack Java/Angular",
        "Développeur Full Stack Java/Angular",
    ),
    (
        "Développeur principal ou développeuse principale Fullstack Java/Angular",
        "Développeur principal Fullstack Java/Angular",
    ),
    (
        "Développeur Node.js & Automatisation des Tests (H/F) - CDI",
        "Développeur Node.js & Automatisation des Tests",
    ),
    ("Développeur PHP - Symfony (H-F)", "Développeur PHP - Symfony"),
    ("Développeur PHP / IA H/F (IT)", "Développeur PHP / IA"),
    ("Développeur PHP/Symfony Freelance – IA - Freelance", "Développeur PHP/Symfony"),
    ("Développeur Python API – H/F", "Développeur Python API"),
    (
        "Développeur Python confirmé — Migration ETL vers Python, données de référence (IT)",
        "Développeur Python confirmé",
    ),
    (
        "Développeur Symfony Senior - Agence Digitale Innovante - Marseille (H/F)",
        "Développeur Symfony Senior",
    ),
    (
        "Développeur Vue.js/Cesium - Société Innovante en Cartographie 3D - Biot (H/F) Full remote",
        "Développeur Vue.js/Cesium",
    ),
    ("Développeur(se) Python Data F/H", "Développeur Python Data"),
    ("Ingénieur(e) Développement Go & Python F/H", "Ingénieur Développement Go & Python"),
    ("Ingénieur Softwear C++ - Python F/H", "Ingénieur Softwear C++ - Python"),
    (
        "Ingénieur en Développement de Tests Automatisés - Python F/H (CDI)",
        "Ingénieur en Développement de Tests Automatisés - Python",
    ),
    (
        "Ingénieur Testeur QA – Stratégies de Tests et Automatisation – Secteur des Télécoms – "
        "Nice (H/F)",
        "Ingénieur Testeur QA",
    ),
    ("INGÉNIEUR QA AUTOMATISATION F/H", "Ingénieur QA Automatisation"),
    ("Lead Dev SYMFONY Senior (IT)", "Lead Dev SYMFONY Senior"),
    ("Lead Développeur PHP / Symfony H/F - Paris", "Lead Développeur PHP / Symfony"),
    ("PHP Symfony Developer (m/f/d)", "PHP Symfony Developer"),
    ("Programatori PHP - Symfony/Drupal Paris", "Programatori PHP - Symfony/Drupal"),
    (
        "Senior PHP Software Engineer (Pimcore) @ Branchspace sp. z o.o.",
        "Senior PHP Software Engineer (Pimcore)",
    ),
    (
        "Développeur Back-End Symfony / Expert eCommerce",
        "Développeur Back-End Symfony / Expert eCommerce",
    ),
]


@pytest.mark.parametrize(("title", "expected"), REAL_TITLES)
def test_real_titles_are_reduced_to_the_role(title: str, expected: str) -> None:
    assert extract_job_role(title) == expected


def test_clean_roles_are_left_untouched() -> None:
    for role in ("Développeur PHP / Symfony", "Développeur backend", "Senior PHP Engineer"):
        assert extract_job_role(role) == role


def test_offer_location_is_removed_from_the_end_of_the_title() -> None:
    assert extract_job_role("Développeur PHP Cagnes-sur-Mer", "Cagnes-sur-Mer (06)") == (
        "Développeur PHP"
    )
    assert extract_job_role("Développeur PHP - Pantin", "Pantin, Bobigny") == "Développeur PHP"


def test_fallback_when_nothing_usable_remains() -> None:
    assert extract_job_role("H/F") == "H/F"
    assert extract_job_role("   ") == ""


def test_cover_letter_uses_the_role_not_the_whole_title() -> None:
    offer = JobOfferData(
        title="Développeur PHP Symfony (IT) H/F - CDI - Paris",
        company="Atelier Exemple",
        location="Paris",
        description="PHP et Symfony.",
    )

    letter = build_cover_letter(offer, ProfileData())

    assert "Objet : Candidature au poste de Développeur PHP Symfony chez Atelier Exemple" in letter
    assert "Votre annonce pour le poste de Développeur PHP Symfony a retenu" in letter
    assert "CDI" not in letter.split("\n\n")[0]


def test_tailored_resume_and_filename_use_the_role() -> None:
    offer = JobOfferData(
        title="DEVELOPPEUR PHP - PARIS",
        company="Atelier Exemple",
        description="PHP.",
    )

    import json

    draft = json.loads(build_tailored_resume(offer, ProfileData()))
    filename = tailored_cv_filename(
        {"name": "Alexandre Martin"},
        target_role="Développeur PHP Symfony (IT) H/F - CDI - Paris",
        target_company="Atelier Exemple",
    )

    assert draft["target_role"] == "Développeur PHP"
    assert filename == "alexandre-martin-cv-developpeur-php-symfony.pdf"
