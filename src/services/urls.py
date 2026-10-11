"""Normalisation des liens d'annonces : un même lien ne doit pas être enregistré deux fois."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Paramètres de suivi ou de campagne : ils changent d'un partage à l'autre sans changer l'annonce.
TRACKING_PREFIXES = ("utm_", "xtor", "gclid", "fbclid", "mc_", "ref_src", "trk", "tracking")
# Sites dont l'annonce est entièrement identifiée par le chemin : tout le reste est du suivi.
PATH_ONLY_HOSTS = ("adzuna.",)


def clean_tracking(url: str) -> str:
    """Retire les paramètres de suivi et l'ancre d'un lien, sans autre changement."""
    parts = urlsplit(url.strip())
    kept = [
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not name.casefold().startswith(TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def normalize_url(url: str) -> str:
    """Forme de comparaison d'un lien : même annonce, même résultat.

    http et https, majuscules, « www. », barre finale, ancre, paramètres de suivi et ordre des
    paramètres n'y changent rien.
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    if any(marker in host for marker in PATH_ONLY_HOSTS):
        query = ""
    else:
        kept = sorted(
            (name.casefold(), value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not name.casefold().startswith(TRACKING_PREFIXES)
        )
        query = urlencode(kept)
    port = f":{parts.port}" if parts.port not in (None, 80, 443) else ""
    return urlunsplit(("https", f"{host}{port}", path, query, ""))
