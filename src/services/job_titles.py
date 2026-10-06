"""Réduit le titre d'une annonce au seul poste, pour les lettres, CV et noms de fichier."""

from __future__ import annotations

import html
import re

from src.services.matching import mentions_technology

_GENDER_TAG = re.compile(
    r"[\(\[]?\s*(?<!\w)"
    r"(?:h\s*[/.\-]\s*f|f\s*[/.\-]\s*h|[mfw]\s*[/.\-]\s*[mfwhd]\s*[/.\-]\s*[dxm])"
    r"(?!\w)\s*[\)\]]?",
    re.IGNORECASE,
)
_IT_TAG = re.compile(r"[\(\[]\s*IT\s*[\)\]]")
_CONTRACT_TAG = re.compile(
    r"[\(\[]?\s*(?<!\w)(?:cdi|cdd|freelance|int[ée]rim|temps\s+plein)(?!\w)\s*[\)\]]?",
    re.IGNORECASE,
)
_WORK_MODE_TAG = re.compile(
    r"(?<!\w)(?:full[- ]remote|100\s*%\s*remote|t[ée]l[ée]travail|hybride|remote)(?!\w)",
    re.IGNORECASE,
)
# « Ingénieur(e) », « Développeur(se) » : on garde la forme neutre de base.
_FEMININE_SUFFIX = re.compile(
    r"\((?:e|es|se|ne|euse|trice|rice)\)|(?<=\w)\.(?:e|es|se)(?!\w)", re.IGNORECASE
)
_BARE_CONTRACT_WORDS = {"stage", "alternance", "apprentissage", "stagiaire"}
_UNACCENTED_WORDS = {
    "developpeur": "développeur",
    "developpeurs": "développeurs",
    "developpement": "développement",
    "ingenieur": "ingénieur",
    "ingenieurs": "ingénieurs",
}
# « Développeur ou développeuse (principale) » : on garde la première forme.
_FEMININE_ALTERNATIVE = re.compile(
    r"\s+ou\s+[^\W\d_]*(?:euse|trice|ère|ingénieure|cheffe)(?:\s+[^\W\d_]+e)?(?!\w)",
    re.IGNORECASE,
)
_SEPARATORS = re.compile(r"\s+[-–—|]+\s*|\s*[–—|]+\s*|\s+@\s*|\s+(?:at|chez)\s+", re.IGNORECASE)
_TRAILING_FILLER = re.compile(r"(?:\s+(?:en|de|à|au|chez|pour|et|des|du))+\s*$", re.IGNORECASE)
_LEADING_PUNCTUATION = " \t,;:-–—|•*"
_COMMON_PLACES = (
    "paris|nice|marseille|lyon|cannes|antibes|sophia[- ]antipolis|toulouse|bordeaux|lille|"
    "nantes|strasbourg|montpellier|toulon|monaco|biot|valbonne|mougins|grasse|rennes|"
    "aix-en-provence|cagnes-sur-mer|menton|vallauris|île-de-france|ile-de-france"
)
_ROLE_WORDS = re.compile(
    r"(?<!\w)(?:d[ée]veloppeu\w*|developer|engineer|ing[ée]nieur\w*|architecte|lead|senior|"
    r"junior|confirm[ée]\w*|expert|full[- ]?stack|back[- ]?end|front[- ]?end|devops|software|"
    r"web|mobile|data|analyste|consultant|testeur|qa)(?!\w)",
    re.IGNORECASE,
)
_MAX_CONTINUATION_WORDS = 3


def extract_job_role(title: str, location: str = "") -> str:
    """Retourne le poste seul : sans genre (H/F), contrat, lieu, entreprise ni slogan.

    « Développeur PHP Symfony (IT) H/F - CDI - Paris » devient « Développeur PHP Symfony ».
    Les précisions techniques courtes après un tiret (« Développeur PHP - Symfony ») sont
    conservées. Retourne le titre d'origine nettoyé si rien d'exploitable ne reste.
    """
    text = html.unescape(title).replace("\xa0", " ").strip(_LEADING_PUNCTUATION)
    text = re.sub(r"^[\W_]+", "", text)  # emoji ou puces en tête
    places = _places_pattern(location)
    kept: list[str] = []
    segments = _SEPARATORS.split(text)
    for index, raw_segment in enumerate(segments):
        segment = _clean_segment(raw_segment, places)
        if not segment:
            continue
        if not kept and index < len(segments) - 1 and segment.casefold() in _BARE_CONTRACT_WORDS:
            continue  # « Stage - Ingénieur… » : le mot de contrat seul n'est pas le poste
        if not kept or _is_role_continuation(segment):
            kept.append(segment)
    role = " - ".join(kept) if kept else _clean_segment(text, places)
    role = _restore_accents(_normalize_case(role))
    return role or title.strip()


def _places_pattern(location: str) -> re.Pattern[str]:
    names = [part.strip() for part in re.split(r"[,(/]", location) if len(part.strip()) >= 3]
    alternatives = "|".join([_COMMON_PLACES, *(re.escape(name) for name in names)])
    return re.compile(
        rf"(?:[\s,\-–—]+|^)(?:{alternatives})(?:\s*\(\d{{2,3}}\))?\s*$", re.IGNORECASE
    )


def _clean_segment(segment: str, places: re.Pattern[str]) -> str:
    cleaned = segment
    for pattern in (
        _GENDER_TAG,
        _IT_TAG,
        _CONTRACT_TAG,
        _WORK_MODE_TAG,
        _FEMININE_SUFFIX,
        _FEMININE_ALTERNATIVE,
    ):
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(_LEADING_PUNCTUATION)
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = places.sub("", cleaned).strip(_LEADING_PUNCTUATION)
        cleaned = _TRAILING_FILLER.sub("", cleaned).strip(_LEADING_PUNCTUATION)
    return re.sub(r"\(\s*\)", "", cleaned).strip(_LEADING_PUNCTUATION)


def _is_role_continuation(segment: str) -> bool:
    """Garde un court complément de poste (« Symfony »), pas un slogan ni une entreprise."""
    if len(segment.split()) > _MAX_CONTINUATION_WORDS:
        return False
    return mentions_technology(segment) or _ROLE_WORDS.search(segment) is not None


def _restore_accents(role: str) -> str:
    """« Developpeur » (annonces en capitales sans accents) devient « Développeur »."""

    def restore(match: re.Match[str]) -> str:
        word = match.group(0)
        accented = _UNACCENTED_WORDS[word.casefold()]
        return accented.capitalize() if word[0].isupper() else accented

    pattern = "|".join(_UNACCENTED_WORDS)
    return re.sub(rf"(?<!\w)(?:{pattern})(?!\w)", restore, role, flags=re.IGNORECASE)


def _normalize_case(role: str) -> str:
    """Passe un titre tout en capitales en casse normale, en gardant les sigles (PHP, QA)."""
    letters = [character for character in role if character.isalpha()]
    if len(letters) < 4 or not all(character.isupper() for character in letters):
        return role
    return re.sub(
        r"[^\W_]+",
        lambda match: match.group(0) if len(match.group(0)) <= 3 else match.group(0).capitalize(),
        role,
    )
