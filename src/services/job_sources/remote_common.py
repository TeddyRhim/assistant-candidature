from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.models import ProfileData
from src.services.job_sources.base import JobSourceError, SourceListing
from src.services.matching import TECHNOLOGIES

REMOTE_TIMEOUT_SECONDS = 20
REMOTE_PAUSE_SECONDS = 1.5
REMOTE_TERM_LIMIT = 6
FULL_REMOTE_LABEL = "Full remote"

_SEARCH_TERM = re.compile(r"^[\w+#.]+(?: [\w+#.]+)?$")
_KNOWN_TECHNOLOGIES = {
    name.casefold()
    for technology in TECHNOLOGIES
    for name in (technology.label, *technology.aliases)
}
_OPEN_LOCATION = re.compile(
    r"worldwide|anywhere|global|europe|emea|france|(?<!\w)eu(?!\w)|(?<!\w)remote(?!\w)",
    re.IGNORECASE,
)


class RemoteBoardRateLimited(JobSourceError):
    """Le site a répondu 429 : on s'arrête là et on reprend plus tard."""


@dataclass
class RemoteBoardResult:
    listings: list[SourceListing] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rate_limited: bool = False


def fetch_json(url: str, *, source: str, timeout: float = REMOTE_TIMEOUT_SECONDS) -> Any:
    """GET JSON avec les erreurs réseau traduites en erreurs de source lisibles."""
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "assistant-candidatures/0.1"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        if error.code == 429:
            raise RemoteBoardRateLimited(
                f"{source} : limite de requêtes atteinte (HTTP 429)."
            ) from error
        raise JobSourceError(f"{source} a refusé la requête (HTTP {error.code}).") from error
    except (URLError, TimeoutError, ConnectionResetError) as error:
        raise JobSourceError(f"Impossible de joindre {source}. Vérifie la connexion.") from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JobSourceError(f"{source} a renvoyé une réponse JSON invalide.") from error


def remote_search_terms(profile: ProfileData, limit: int = REMOTE_TERM_LIMIT) -> list[str]:
    """Technologies les mieux notées du profil : les sites de télétravail se cherchent par techno.

    Les compétences génériques (« Automatisation », « API REST »…) sont écartées : sur un site
    anglophone elles ramènent surtout du bruit.
    """
    skills = sorted(profile.skills, key=lambda skill: (-skill.level_max, skill.name.casefold()))
    terms: list[str] = []
    for skill in skills:
        name = skill.name.strip()
        if name and len(name) <= 20 and _SEARCH_TERM.match(name) and (
            name.casefold() in _KNOWN_TECHNOLOGIES
        ):
            if name.casefold() not in {term.casefold() for term in terms}:
                terms.append(name)
        if len(terms) >= limit:
            break
    return terms


def is_open_to_france(location: str) -> bool:
    """Vrai pour un lieu vide, mondial, européen ou français ; faux pour un pays précis."""
    cleaned = location.strip()
    return not cleaned or _OPEN_LOCATION.search(cleaned) is not None
