"""Normalisation des liens d'annonces : un même lien ne doit pas être enregistré deux fois."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Paramètres de suivi ou de campagne : ils changent d'un partage à l'autre sans changer l'annonce.
TRACKING_PREFIXES = ("utm_", "xtor", "gclid", "fbclid", "mc_", "ref_src", "trk", "tracking")
# Paramètres propres à l'abonné d'une alerte e-mail (jetons, identifiants) : ils peuvent contenir
# son adresse et ne doivent jamais être enregistrés.
SUBSCRIBER_PARAMS = frozenset(
    {"token", "jj_token", "user_id", "email_id", "hcid", "een", "alerteid", "clickid", "puid"}
)
# Sites d'alertes dont l'annonce tient en un seul paramètre : tout le reste est retiré.
SINGLE_PARAM_HOSTS = {"cadremploi.fr": "offreid", "talent.com": "id"}
# Sites dont l'annonce est entièrement identifiée par le chemin : tout le reste est du suivi.
PATH_ONLY_HOSTS = ("adzuna.",)


def _kept_params(host: str, query: str) -> list[tuple[str, str]]:
    params = parse_qsl(query, keep_blank_values=True)
    for suffix, name in SINGLE_PARAM_HOSTS.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return [(key, value) for key, value in params if key.casefold() == name]
    return [
        (key, value)
        for key, value in params
        if not key.casefold().startswith(TRACKING_PREFIXES)
        and key.casefold() not in SUBSCRIBER_PARAMS
    ]


def clean_tracking(url: str) -> str:
    """Retire les paramètres de suivi, les jetons d'abonné et l'ancre d'un lien."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").casefold()
    kept = _kept_params(host, parts.query)
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
        kept = sorted((name.casefold(), value) for name, value in _kept_params(host, parts.query))
        query = urlencode(kept)
    port = f":{parts.port}" if parts.port not in (None, 80, 443) else ""
    return urlunsplit(("https", f"{host}{port}", path, query, ""))
