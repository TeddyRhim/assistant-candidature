"""Mots-clés d'une annonce et adaptation honnête du CV.

On ne réécrit jamais le CV : on met en avant ce qui y figure déjà et que l'annonce demande
(ordre des puces, termes en gras, compétences remontées). Un mot demandé mais absent du CV est
seulement signalé, jamais ajouté : tu ne dois le mentionner que si tu l'as réellement pratiqué.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from src.models import JobOfferData, ProfileData
from src.services.matching import (
    TECHNOLOGIES,
    Technology,
    _find_technology_evidence,
    _priority_for_aliases,
)

MAX_BULLETS_PER_EXPERIENCE = 6
MIN_BULLETS_PER_EXPERIENCE = 4
MAX_BOLD_KEYWORDS = 10
MAX_BOLD_IN_PROFILE = 4
MAX_BOLD_IN_BULLET = 2

# Pratiques et outils courants des annonces, en plus des langages et frameworks du catalogue.
EXTRA_KEYWORDS: tuple[Technology, ...] = (
    Technology("graphql", "GraphQL", ("graphql",)),
    Technology("soap", "SOAP", ("soap",)),
    Technology("api_platform", "API Platform", ("api platform",), "symfony"),
    Technology("doctrine", "Doctrine", ("doctrine",), "php"),
    Technology("twig", "Twig", ("twig",), "php"),
    Technology("composer", "Composer", ("composer",), "php"),
    Technology("phpunit", "PHPUnit", ("phpunit",), "php"),
    Technology(
        "tests",
        "Tests automatisés",
        ("tests unitaires", "tests automatisés", "tests d'intégration", "unit tests", "tdd"),
    ),
    Technology("ddd", "DDD", ("ddd", "domain driven design", "domain-driven design")),
    Technology(
        "cicd",
        "CI/CD",
        ("ci/cd", "ci cd", "intégration continue", "gitlab ci", "github actions", "jenkins"),
    ),
    Technology("git", "Git", ("git", "gitlab", "github")),
    Technology("linux", "Linux", ("linux",)),
    Technology("redis", "Redis", ("redis",)),
    Technology("rabbitmq", "RabbitMQ", ("rabbitmq",)),
    Technology("elasticsearch", "Elasticsearch", ("elasticsearch", "elastic search")),
    Technology("mongodb", "MongoDB", ("mongodb", "mongo db")),
    Technology("microservices", "Microservices", ("microservices", "micro-services")),
    Technology("agile", "Agile", ("agile", "scrum", "kanban")),
    Technology(
        "architecture",
        "Architecture logicielle",
        ("clean architecture", "architecture hexagonale", "solid"),
    ),
    Technology("terraform", "Terraform", ("terraform",)),
    Technology("ansible", "Ansible", ("ansible",)),
    Technology("nginx", "Nginx", ("nginx",)),
    Technology("html_css", "HTML / CSS", ("html", "css", "html5", "css3")),
    Technology("jquery", "jQuery", ("jquery",)),
    Technology("llm", "LLM", ("llm", "llms", "rag")),
)

_EXTRA_KEYS = {technology.key for technology in EXTRA_KEYWORDS}
KEYWORD_CATALOG: tuple[Technology, ...] = (
    *(technology for technology in TECHNOLOGIES if technology.key not in _EXTRA_KEYS),
    *EXTRA_KEYWORDS,
)


@dataclass(frozen=True)
class OfferKeyword:
    label: str
    aliases: tuple[str, ...]
    weight: float
    in_title: bool


@dataclass
class KeywordReport:
    """Ce que l'annonce demande, rapporté à ce que le CV dit déjà."""

    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    profile_only: list[str] = field(default_factory=list)

    @property
    def coverage_percentage(self) -> int | None:
        total = len(self.found) + len(self.missing) + len(self.profile_only)
        return round(100 * len(self.found) / total) if total else None


def extract_offer_keywords(offer: JobOfferData) -> list[OfferKeyword]:
    """Termes techniques de l'annonce : titre d'abord, puis les plus exigés, puis les autres."""
    title = offer.title
    text = f"{title}\n{offer.description}"
    keywords: list[tuple[int, OfferKeyword]] = []
    for technology in KEYWORD_CATALOG:
        evidence = _find_technology_evidence(text, technology)
        if evidence is None:
            continue
        _priority, weight = _priority_for_aliases(evidence, technology.aliases)
        in_title = _find_technology_evidence(title, technology) is not None
        position = min(
            (
                match.start()
                for alias in technology.aliases
                if (
                    match := re.search(
                        rf"(?<![\w.#+]){re.escape(alias)}(?![\w#+])", text, re.IGNORECASE
                    )
                )
            ),
            default=len(text),
        )
        keywords.append(
            (position, OfferKeyword(technology.label, technology.aliases, weight, in_title))
        )
    keywords.sort(key=lambda item: (not item[1].in_title, -item[1].weight, item[0]))
    return [keyword for _position, keyword in keywords]


def _alias_pattern(aliases: tuple[str, ...]) -> re.Pattern[str]:
    phrases = sorted(
        (r"\s+".join(re.escape(part) for part in alias.split()) for alias in aliases),
        key=len,
        reverse=True,
    )
    return re.compile(rf"(?<![\w.#+])(?:{'|'.join(phrases)})(?![\w#+])", re.IGNORECASE)


def cv_text(data: dict[str, Any]) -> str:
    """Tout le texte du CV susceptible de prouver qu'un terme a été pratiqué."""
    parts = [str(data.get("profile", ""))]
    for job in data.get("experience", []):
        parts.append(f"{job.get('role', '')} {job.get('company', '')}")
        parts.extend(str(bullet) for bullet in job.get("bullets", []))
    for project in data.get("projects", []):
        parts.append(str(project))
    parts.extend(
        f"{skill.get('label', '')} {skill.get('details', '')}" for skill in data.get("skills", [])
    )
    return "\n".join(part.replace("**", "") for part in parts)


def _in_text(text: str, keyword: OfferKeyword) -> bool:
    return _alias_pattern(keyword.aliases).search(text) is not None


def keyword_report(
    offer: JobOfferData, data: dict[str, Any], profile: ProfileData | None = None
) -> KeywordReport:
    """Mots de l'annonce présents dans le CV, absents du CV, ou seulement dans le profil."""
    text = cv_text(data)
    profile_names = (
        {skill.name.casefold() for skill in profile.skills} if profile is not None else set()
    )
    report = KeywordReport()
    for keyword in extract_offer_keywords(offer):
        if _in_text(text, keyword):
            report.found.append(keyword.label)
        elif keyword.label.casefold() in profile_names or any(
            alias.casefold() in profile_names for alias in keyword.aliases
        ):
            report.profile_only.append(keyword.label)
        else:
            report.missing.append(keyword.label)
    return report


def _score(text: str, keywords: list[OfferKeyword]) -> int:
    """Plus un passage cite de termes importants de l'annonce (titre d'abord), plus il monte."""
    total = len(keywords)
    return sum(total - rank for rank, keyword in enumerate(keywords) if _in_text(text, keyword))


def _bold_keywords(text: str, keywords: list[OfferKeyword], limit: int) -> str:
    """Met en gras la première occurrence de `limit` mots-clés au plus, hors des passages déjà
    en gras (un terme déjà mis en valeur dans le texte n'est pas doublé)."""
    pieces = re.split(r"(\*\*.+?\*\*)", text)
    bolded = 0
    for keyword in keywords:
        if bolded >= limit:
            break
        if any(_in_text(piece, keyword) for piece in pieces if piece.startswith("**")):
            continue
        pattern = _alias_pattern(keyword.aliases)
        for index, piece in enumerate(pieces):
            if piece.startswith("**"):
                continue
            replaced, count = pattern.subn(lambda m: f"**{m.group(0)}**", piece, count=1)
            if count:
                pieces[index] = replaced
                bolded += 1
                break
    return "".join(pieces)


def _split_details(details: str) -> list[str]:
    """Découpe « A, B (x, y), C » sur les virgules hors parenthèses."""
    items, depth, current = [], 0, ""
    for char in details:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            items.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        items.append(current.strip())
    return items


def tailor_cv_data(data: dict[str, Any], offer: JobOfferData) -> dict[str, Any]:
    """Copie du CV où ce que l'annonce demande, et que le CV contient déjà, passe devant."""
    tailored = copy.deepcopy(data)
    text = cv_text(tailored)
    keywords = [keyword for keyword in extract_offer_keywords(offer) if _in_text(text, keyword)]
    if not keywords:
        return tailored

    for job in tailored.get("experience", []):
        bullets = list(job.get("bullets", []))
        ranked = sorted(bullets, key=lambda bullet: -_score(bullet.replace("**", ""), keywords))
        if len(ranked) > MAX_BULLETS_PER_EXPERIENCE:
            relevant = [b for b in ranked if _score(b.replace("**", ""), keywords) > 0]
            kept = ranked[: max(len(relevant), MIN_BULLETS_PER_EXPERIENCE)]
            ranked = kept[:MAX_BULLETS_PER_EXPERIENCE]
        job["bullets"] = ranked

    for skill in tailored.get("skills", []):
        items = _split_details(str(skill.get("details", "")))
        if len(items) > 1:
            items.sort(key=lambda item: -_score(item, keywords))
            skill["details"] = ", ".join(items)
    tailored["skills"] = sorted(
        tailored.get("skills", []),
        key=lambda skill: -_score(f"{skill.get('label', '')} {skill.get('details', '')}", keywords),
    )

    highlighted = keywords[:MAX_BOLD_KEYWORDS]
    tailored["profile"] = _bold_keywords(
        str(tailored.get("profile", "")), highlighted, MAX_BOLD_IN_PROFILE
    )
    for job in tailored.get("experience", []):
        job["bullets"] = [
            _bold_keywords(bullet, highlighted, MAX_BOLD_IN_BULLET) for bullet in job["bullets"]
        ]
    return tailored
