from __future__ import annotations

import re
from dataclasses import dataclass

from src.models import (
    Company,
    CompanyCandidate,
    CompanyCandidateData,
    JobOfferData,
    ProfileData,
    SkillRating,
)


@dataclass(frozen=True)
class SkillMatch:
    skill: SkillRating
    mentioned: bool
    evidence: str | None
    priority: str = "mentionnée"
    weight: float = 1.0


BENCHMARK_STACK_SIZE = 5
# Une technologie voisine d'un langage maîtrisé (framework du même langage) compte pour une
# fraction de ce niveau : un développeur PHP apprend Laravel plus vite qu'un novice.
RELATED_TECHNOLOGY_FACTOR = 0.6
# Niveau minimal (sur 10) d'un langage du profil pour qu'il serve à la recherche d'offres.
SEARCH_LANGUAGE_MIN_LEVEL = 5
ACCEPTED_LANGUAGE_TITLE_SCORE = 50


@dataclass(frozen=True)
class Technology:
    key: str
    label: str
    aliases: tuple[str, ...]
    parent: str | None = None
    is_language: bool = False


# Technologies que les annonces peuvent exiger, avec le langage dont elles dépendent.
TECHNOLOGIES: tuple[Technology, ...] = (
    Technology("php", "PHP", ("php", "php8", "php 8", "php7"), is_language=True),
    Technology("python", "Python", ("python", "python3"), is_language=True),
    Technology("java", "Java", ("java",), is_language=True),
    Technology("javascript", "JavaScript", ("javascript", "ecmascript", "js"), is_language=True),
    Technology("typescript", "TypeScript", ("typescript",), "javascript", True),
    Technology("kotlin", "Kotlin", ("kotlin",), is_language=True),
    Technology("csharp", "C#", ("c#", "c sharp"), is_language=True),
    Technology("ruby", "Ruby", ("ruby",), is_language=True),
    Technology("go", "Go", ("golang",), is_language=True),
    Technology("rust", "Rust", ("rust",), is_language=True),
    Technology("symfony", "Symfony", ("symfony", "symfony 6", "symfony 7"), "php"),
    Technology("laravel", "Laravel", ("laravel",), "php"),
    Technology("wordpress", "WordPress", ("wordpress",), "php"),
    Technology("drupal", "Drupal", ("drupal",), "php"),
    Technology("django", "Django", ("django",), "python"),
    Technology("flask", "Flask", ("flask",), "python"),
    Technology("fastapi", "FastAPI", ("fastapi", "fast api"), "python"),
    Technology("spring", "Spring", ("spring boot", "spring framework", "spring"), "java"),
    Technology("node", "Node.js", ("node.js", "nodejs", "node js"), "javascript"),
    Technology("react", "React", ("react", "reactjs", "react.js"), "javascript"),
    Technology("angular", "Angular", ("angular", "angularjs"), "typescript"),
    Technology("vue", "Vue.js", ("vue.js", "vuejs", "vue 3", "vue 2"), "javascript"),
    Technology("rails", "Ruby on Rails", ("ruby on rails", "rails"), "ruby"),
    Technology("dotnet", ".NET", (".net", "dotnet"), "csharp"),
    Technology("sql", "SQL", ("sql", "mysql", "mariadb", "postgresql", "postgres")),
    Technology("docker", "Docker", ("docker",)),
    Technology("kubernetes", "Kubernetes", ("kubernetes", "k8s")),
    Technology("aws", "AWS", ("aws", "amazon web services")),
    Technology("azure", "Azure", ("azure",)),
    Technology(
        "apirest",
        "API REST",
        ("api rest", "rest api", "restful", "apis rest", "api restful"),
    ),
)
TECHNOLOGY_BY_KEY = {technology.key: technology for technology in TECHNOLOGIES}


@dataclass(frozen=True)
class RequiredTechnology:
    """Technologie demandée par l'annonce, avec le niveau du profil (0 à 1)."""

    label: str
    level: float
    in_title: bool = False


@dataclass(frozen=True)
class OfferMatch:
    matches: tuple[SkillMatch, ...]
    required_technologies: tuple[RequiredTechnology, ...] = ()
    requirements_percentage: int | None = None

    @property
    def mentioned_count(self) -> int:
        return sum(match.mentioned for match in self.matches)

    @property
    def needs_review_count(self) -> int:
        return len(self.matches) - self.mentioned_count

    @property
    def match_percentage(self) -> int | None:
        """Maîtrise moyenne des technologies que l'annonce demande, pondérée par leur poids.

        Quand aucune technologie connue n'est détectée, on retombe sur l'ancien calcul : part
        des meilleures compétences du profil citées dans l'annonce.
        """
        if not self.matches:
            return None
        if self.requirements_percentage is not None:
            return self.requirements_percentage
        benchmark_count = min(len(self.matches), BENCHMARK_STACK_SIZE)
        top_skills = sorted(
            self.matches,
            key=lambda m: (m.skill.level_max, m.weight),
            reverse=True,
        )[:benchmark_count]
        benchmark_weight = sum(max(m.skill.level_max, 1) for m in top_skills)
        if benchmark_weight <= 0:
            return 0
        matched_weight = sum(
            max(match.skill.level_max, 1) * match.weight
            for match in self.matches
            if match.mentioned
        )
        return min(100, round(matched_weight / benchmark_weight * 100))


@dataclass(frozen=True)
class OfferFitAssessment:
    skills: OfferMatch
    overall_percentage: int | None
    role_percentage: int | None
    contract_compatible: bool | None
    location_compatible: bool | None
    location_reason: str | None
    evaluated_criteria: tuple[str, ...]


@dataclass(frozen=True)
class CompanyFitAssessment:
    activity_label: str
    activity_is_software: bool
    mentioned_skills: tuple[SkillMatch, ...]
    unknown_skills: tuple[SkillRating, ...]


ROLE_STOP_WORDS = {
    "de",
    "du",
    "des",
    "en",
    "et",
    "la",
    "le",
    "les",
    "un",
    "une",
    "the",
    "and",
    "for",
    "senior",
    "junior",
}
STRONG_SKILL_LEVEL = 7
# Compétences trop génériques pour caractériser un intitulé de poste.
GENERIC_TITLE_SKILLS = frozenset({"sql", "git", "curl", "jwt", "linux"})
SOFTWARE_ACTIVITY_CODE_PATTERN = re.compile(r"\b62\.(?:01Z|02A|02B|03Z|09Z)\b", re.I)
SOFTWARE_ACTIVITY_TERMS = (
    "programmation informatique",
    "conseil en systèmes et logiciels",
    "logiciel",
    "développement informatique",
    "équipe de développement",
    "équipe tech",
)


def compare_offer_to_profile(offer: JobOfferData, profile: ProfileData) -> OfferMatch:
    matches = []
    searchable_text = f"{offer.title}\n{offer.description}"
    for skill in profile.skills:
        evidence = _find_evidence(searchable_text, skill.name)
        priority, weight = _evidence_priority(evidence or "", skill.name)
        matches.append(
            SkillMatch(
                skill=skill,
                mentioned=evidence is not None,
                evidence=evidence,
                priority=priority,
                weight=weight,
            )
        )
    required, percentage = _requirements_coverage(offer, profile)
    return OfferMatch(
        matches=tuple(matches),
        required_technologies=required,
        requirements_percentage=percentage,
    )


def search_languages(profile: ProfileData) -> tuple[str, ...]:
    """Langages du profil assez maîtrisés pour chercher des offres, du plus fort au plus faible.

    Le niveau déclaré pilote la recherche : monter ou baisser le niveau d'un langage dans le
    profil l'ajoute ou le retire des requêtes.
    """
    languages: dict[str, tuple[str, int]] = {}
    for technology in TECHNOLOGIES:
        if not technology.is_language:
            continue
        for skill in profile.skills:
            if skill.level_max < SEARCH_LANGUAGE_MIN_LEVEL:
                continue
            if _skill_matches_technology(skill.name, technology):
                known = languages.get(technology.key)
                if known is None or skill.level_max > known[1]:
                    languages[technology.key] = (skill.name, skill.level_max)
    return tuple(
        name for name, _level in sorted(languages.values(), key=lambda item: -item[1])
    )


def _skill_matches_technology(skill_name: str, technology: Technology) -> bool:
    normalized = _normalize_skill_name(skill_name)
    names = {_normalize_skill_name(technology.key), _normalize_skill_name(technology.label)}
    names.update(_normalize_skill_name(alias) for alias in technology.aliases)
    return normalized in names


def _technology_level(
    technology: Technology,
    profile: ProfileData,
    _depth: int = 0,
) -> float:
    """Niveau (0 à 1) du profil pour une technologie, ou une fraction de celui de son parent."""
    direct = [
        skill.level_max
        for skill in profile.skills
        if _skill_matches_technology(skill.name, technology)
    ]
    if direct:
        return max(direct) / 10
    parent = TECHNOLOGY_BY_KEY.get(technology.parent or "")
    if parent is None or _depth >= 3:
        return 0.0
    return _technology_level(parent, profile, _depth + 1) * RELATED_TECHNOLOGY_FACTOR


def _find_technology_evidence(text: str, technology: Technology) -> str | None:
    for line in text.splitlines():
        for alias in technology.aliases:
            phrase = r"\s+".join(re.escape(part) for part in alias.split())
            # « node.js » ne doit pas compter comme « js » : on exclut les points collés.
            match = re.search(rf"(?<![\w.#+]){phrase}(?![\w#+])", line, re.IGNORECASE)
            if match is not None:
                start = max(0, match.start() - 50)
                end = min(len(line), match.end() + 50)
                return line[start:end].strip()
    return None


def _requirements_coverage(
    offer: JobOfferData,
    profile: ProfileData,
) -> tuple[tuple[RequiredTechnology, ...], int | None]:
    """Maîtrise moyenne des technologies exigées par l'annonce (titre = poids double)."""
    text = f"{offer.title}\n{offer.description}"
    required = []
    total_weight = 0.0
    achieved = 0.0
    for technology in TECHNOLOGIES:
        evidence = _find_technology_evidence(text, technology)
        if evidence is None:
            continue
        _priority, priority_weight = _priority_for_aliases(evidence, technology.aliases)
        in_title = _find_technology_evidence(offer.title, technology) is not None
        weight = priority_weight * (2.0 if in_title else 1.0)
        level = _technology_level(technology, profile)
        total_weight += weight
        achieved += weight * level
        required.append(RequiredTechnology(technology.label, level, in_title))
    if not required:
        return (), None
    return tuple(required), round(achieved / total_weight * 100)


def assess_offer_fit(offer: JobOfferData, profile: ProfileData) -> OfferFitAssessment:
    skills = compare_offer_to_profile(offer, profile)
    weighted_components: list[tuple[int, int]] = []
    evaluated_criteria = []

    if skills.match_percentage is not None:
        weighted_components.append((skills.match_percentage, 60))
        evaluated_criteria.append("compétences")

    role_percentage = _role_similarity(offer.title, profile)
    if role_percentage is not None:
        weighted_components.append((role_percentage, 20))
        evaluated_criteria.append("intitulé")

    contract_compatible: bool | None = None
    if profile.preferred_contracts and offer.contract_type is not None:
        contract_compatible = offer.contract_type in profile.preferred_contracts
        weighted_components.append((100 if contract_compatible else 0, 10))
        evaluated_criteria.append("contrat")

    location_compatible: bool | None = None
    location_reason: str | None = None
    if profile.local_locations and offer.location.strip():
        normalized_location = offer.location.casefold()
        location_compatible = any(
            location.casefold() in normalized_location
            or normalized_location in location.casefold()
            for location in profile.local_locations
            if location.strip()
        )
        if location_compatible:
            weighted_components.append((100, 10))
            evaluated_criteria.append("lieu")
            location_reason = "dans la zone renseignée"
        elif profile.remote_only_outside_local_area and _is_remote_offer(offer):
            weighted_components.append((100, 10))
            evaluated_criteria.append("télétravail")
            location_compatible = True
            location_reason = "hors zone, mais le télétravail est mentionné"
        else:
            weighted_components.append((0, 10))
            evaluated_criteria.append("lieu")
            location_reason = "hors zone renseignée"

    overall_percentage = (
        round(
            sum(score * weight for score, weight in weighted_components)
            / sum(weight for _, weight in weighted_components)
        )
        if weighted_components
        else None
    )
    return OfferFitAssessment(
        skills=skills,
        overall_percentage=overall_percentage,
        role_percentage=role_percentage,
        contract_compatible=contract_compatible,
        location_compatible=location_compatible,
        location_reason=location_reason,
        evaluated_criteria=tuple(evaluated_criteria),
    )


def assess_company_fit(
    company: Company | CompanyCandidate | CompanyCandidateData,
    profile: ProfileData,
) -> CompanyFitAssessment:
    if isinstance(company, (CompanyCandidate, CompanyCandidateData)):
        company_text = "\n".join(
            (company.name, company.activity_code, company.location)
        )
        activity_code_value = company.activity_code
    else:
        company_text = "\n".join(
            (company.name, company.development_evidence, company.notes)
        )
        activity_code_value = None
    searchable_text = company_text
    normalized_text = searchable_text.casefold()
    activity_code = SOFTWARE_ACTIVITY_CODE_PATTERN.search(
        activity_code_value or searchable_text
    )
    activity_term = next(
        (term for term in SOFTWARE_ACTIVITY_TERMS if term in normalized_text),
        None,
    )
    matches = []
    for skill in profile.skills:
        evidence = find_skill_evidence(searchable_text, skill.name)
        if evidence is not None:
            matches.append(SkillMatch(skill=skill, mentioned=True, evidence=evidence))
    mentioned_skills = tuple(matches)
    unknown_skills = tuple(
        skill
        for skill in profile.skills
        if not any(match.skill.name == skill.name for match in mentioned_skills)
    )
    is_software_activity = activity_code is not None or activity_term is not None
    activity_label = (
        f"Activité informatique déclarée ({activity_code.group(0).upper()})"
        if activity_code
        else "Activité logicielle mentionnée dans les notes"
        if activity_term
        else "Activité tech / équipe de développement non établie"
    )
    return CompanyFitAssessment(
        activity_label=activity_label,
        activity_is_software=is_software_activity,
        mentioned_skills=mentioned_skills,
        unknown_skills=unknown_skills,
    )


def _role_similarity(title: str, profile: ProfileData) -> int | None:
    """Proximité du titre avec le poste visé, ou avec la stack forte du profil.

    Un titre comme « Développeur PHP Symfony » partage peu de mots avec un intitulé cible
    générique (« Développeur backend / full-stack ») : le recoupement des mots est donc
    complété par les compétences fortes du profil citées dans le titre.
    """
    title_terms = _meaningful_terms(title)
    target_terms = _meaningful_terms(profile.target_role)
    if not title_terms or not target_terms:
        return None
    overlap = round(len(title_terms & target_terms) / len(target_terms) * 100)
    return max(overlap, _title_stack_score(title, profile))


def _title_stack_score(title: str, profile: ProfileData) -> int:
    """70 % si le titre cite une compétence forte du profil, 100 % s'il en cite deux.

    Un titre qui cite seulement un langage accepté du profil (niveau moyen, par exemple
    Python) obtient 50 %.
    """
    cited = {
        _normalize_skill_name(skill.name)
        for skill in profile.skills
        if skill.level_max >= STRONG_SKILL_LEVEL
        and _normalize_skill_name(skill.name) in SKILL_ALIASES
        and _normalize_skill_name(skill.name) not in GENERIC_TITLE_SKILLS
        and _find_evidence(title, skill.name) is not None
    }
    if cited:
        return {1: 70}.get(len(cited), 100)
    if any(_find_evidence(title, language) is not None for language in search_languages(profile)):
        return ACCEPTED_LANGUAGE_TITLE_SCORE
    return 0


def _meaningful_terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[\w+#.]+", value.casefold())
        if len(term) > 1 and term not in ROLE_STOP_WORDS
    }


def _is_remote_offer(offer: JobOfferData) -> bool:
    return bool(
        re.search(
            r"\b(?:fully remote|full[- ]remote|100\s*%\s*(?:en\s*)?remote|"
            r"télétravail(?: intégral| complet| à 100\s*%)|entièrement à distance)\b",
            f"{offer.title}\n{offer.location}\n{offer.description}",
            re.IGNORECASE,
        )
    )


def _find_evidence(description: str, skill_name: str) -> str | None:
    aliases = SKILL_ALIASES.get(_normalize_skill_name(skill_name), (skill_name,))
    for line in description.splitlines():
        for alias in aliases:
            phrase = r"\s+".join(re.escape(part) for part in alias.split())
            pattern = re.compile(rf"(?<!\w){phrase}(?!\w)", re.IGNORECASE)
            match = pattern.search(line)
            if match is not None:
                start = max(0, match.start() - 50)
                end = min(len(line), match.end() + 50)
                excerpt = line[start:end].strip()
                if start:
                    excerpt = f"…{excerpt}"
                if end < len(line):
                    excerpt = f"{excerpt}…"
                return excerpt
    return None


def find_skill_evidence(description: str, skill_name: str) -> str | None:
    return _find_evidence(description, skill_name)


def _normalize_skill_name(value: str) -> str:
    return re.sub(r"[^a-z0-9+#.]", "", value.casefold())


SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "javascript": ("javascript", "js", "ecmascript"),
    "typescript": ("typescript", "ts"),
    "postgresql": ("postgresql", "postgres"),
    "postgres": ("postgres", "postgresql"),
    "c#": ("c#", "c sharp"),
    ".net": (".net", "dotnet"),
    "dotnet": (".net", "dotnet"),
    "restapi": ("rest api", "restful api", "api rest", "apis rest", "rest"),
    "apirest": ("api rest", "rest api", "restful api", "apis rest", "rest"),
    "apisrestsoap": ("api rest", "rest api", "soap", "api soap", "rest", "apis"),
    "soap": ("soap", "api soap", "apis soap"),
    "vue.js": ("vue.js", "vuejs", "vue", "vue 3", "vue 2"),
    "vuejs": ("vue.js", "vuejs", "vue", "vue 3", "vue 2"),
    "react.js": ("react.js", "reactjs", "react"),
    "reactjs": ("react.js", "reactjs", "react"),
    "aws": ("aws", "amazon web services"),
    "fastapi": ("fastapi", "fast api"),
    "springboot": ("spring boot", "springboot", "spring"),
    "docker": ("docker", "conteneur", "containerization"),
    "kubernetes": ("kubernetes", "k8s"),
    "cicd": ("ci/cd", "ci-cd", "ci cd", "continuous integration", "intégration continue"),
    "shellbashscripting": ("bash", "shell", "sh", "scripting", "scripts bash"),
    "linux": ("linux", "unix", "debian", "ubuntu"),
    "nginx": ("nginx",),
    "oauth2": ("oauth2", "oauth 2", "oauth"),
    "jwt": ("jwt", "json web token"),
    "sql": ("sql", "mysql", "mariadb", "postgresql", "postgres"),
    "doctrine": ("doctrine", "doctrine orm", "orm doctrine"),
    "agilescrum": ("agile", "scrum", "agilité", "méthode agile"),
    "testing": ("testing", "tests", "tests unitaires", "phpunit", "pytest", "tdd"),
    "optimisation": ("optimisation", "performance", "tuning", "optimisation sql"),
    "gestiondeprojets": ("gestion de projet", "gestion de projets", "project management"),
    "microservices": ("microservices", "micro-services", "micro services"),
    "designpatterns": ("design patterns", "design pattern", "patrons de conception"),
    "apiversioning": ("api versioning", "versioning d'api", "versioning"),
    "webscraping": ("web scraping", "scraping", "scraper"),
    "curl": ("curl", "c-url"),
    "kotlin": ("kotlin",),
    "lua": ("lua",),
    "python": ("python", "python3"),
    "php": ("php", "php8", "php 8", "php7"),
    "symfony": ("symfony", "symfony 6", "symfony 7", "sf"),
}


def _evidence_priority(evidence: str, skill_name: str) -> tuple[str, float]:
    aliases = SKILL_ALIASES.get(_normalize_skill_name(skill_name), (skill_name,))
    return _priority_for_aliases(evidence, aliases)


def _priority_for_aliases(evidence: str, aliases: tuple[str, ...]) -> tuple[str, float]:
    segments = re.split(r"(?<=[.!?;])\s+", evidence)

    def segment_mentions_skill(segment: str) -> bool:
        for alias in aliases:
            phrase = r"\s+".join(re.escape(part) for part in alias.split())
            if re.search(rf"(?<!\w){phrase}(?!\w)", segment, re.IGNORECASE):
                return True
        return False

    relevant_segment = next(
        (segment for segment in segments if segment_mentions_skill(segment)),
        evidence,
    )
    normalized = relevant_segment.casefold()
    if re.search(
        r"\b(obligatoire|indispensable|exigé|exigée|requis|requise|"
        r"must[- ]have|required|essential|mandatory)\b",
        normalized,
    ):
        return "requise dans l'annonce", 1.5
    if re.search(
        r"\b(atout|bonus|apprécié|appréciée|souhaité|souhaitée|"
        r"nice[- ]to[- ]have|would be a plus|plus)\b",
        normalized,
    ):
        return "bonus dans l'annonce", 0.6
    return "mentionnée", 1.0
