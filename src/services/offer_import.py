from __future__ import annotations

import json
import socket
import ssl
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi
from pydantic import AnyHttpUrl, ValidationError

from src.models import JobOfferData

MAX_PAGE_BYTES = 3_000_000
MAX_DESCRIPTION_CHARACTERS = 100_000
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "assistant-candidatures/0.1 (user-initiated offer import)"


class OfferImportError(RuntimeError):
    """An announcement could not safely be retrieved or parsed."""


class _PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        target = urljoin(request.full_url, new_url)
        _validate_public_url(target)
        return super().redirect_request(
            request, response, code, message, headers, target
        )


class _AnnouncementParser(HTMLParser):
    _IGNORED_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
    }
    _BLOCK_TAGS = {
        "address",
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "li",
        "main",
        "p",
        "section",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.h1_parts: list[str] = []
        self.title_parts: list[str] = []
        self.all_parts: list[str] = []
        self.article_parts: list[str] = []
        self.json_ld: list[str] = []
        self._ignored_depth = 0
        self._article_depth = 0
        self._title_depth = 0
        self._h1_depth = 0
        self._json_ld_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.casefold(): value or "" for key, value in attrs}
        if tag in self._IGNORED_TAGS:
            if tag == "script" and "ld+json" in attributes.get("type", "").casefold():
                self._json_ld_depth += 1
            else:
                self._ignored_depth += 1
            return
        if self._ignored_depth or self._json_ld_depth:
            return
        if tag == "meta":
            key = (
                attributes.get("property", "")
                or attributes.get("name", "")
                or attributes.get("itemprop", "")
            ).casefold()
            value = attributes.get("content", "").strip()
            if key and value:
                self.meta[key] = value
        elif tag == "title":
            self._title_depth += 1
        elif tag == "h1":
            self._h1_depth += 1
        elif tag in {"main", "article"}:
            self._article_depth += 1
        if tag in self._BLOCK_TAGS:
            self.all_parts.append("\n")
            if self._article_depth:
                self.article_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._json_ld_depth:
            self._json_ld_depth -= 1
            return
        if tag in self._IGNORED_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if tag == "title":
            self._title_depth = max(0, self._title_depth - 1)
        elif tag == "h1":
            self._h1_depth = max(0, self._h1_depth - 1)
        elif tag in {"main", "article"}:
            self._article_depth = max(0, self._article_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._json_ld_depth:
            self.json_ld.append(data)
            return
        clean = " ".join(data.split())
        if not clean:
            return
        self.all_parts.append(clean)
        if self._article_depth:
            self.article_parts.append(clean)
        if self._title_depth:
            self.title_parts.append(clean)
        if self._h1_depth:
            self.h1_parts.append(clean)


def import_offer_from_url(url: str) -> JobOfferData:
    normalized_url = _normalize_public_url(url)
    host = urlsplit(normalized_url).hostname or ""
    request = Request(
        normalized_url,
        headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": USER_AGENT},
    )
    tls_context = ssl.create_default_context(cafile=certifi.where())
    opener = build_opener(
        _PublicRedirectHandler(),
        HTTPSHandler(context=tls_context),
    )
    try:
        with opener.open(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            _validate_public_url(response.geturl())
            content_type = response.headers.get_content_type().casefold()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                raise OfferImportError(
                    "Le lien ne renvoie pas une page HTML lisible. "
                    "Tu peux toujours saisir l'annonce manuellement."
                )
            payload = response.read(MAX_PAGE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except OfferImportError:
        raise
    except HTTPError as error:
        raise OfferImportError(
            f"Le site a refusé la lecture de l'annonce (HTTP {error.code})."
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise OfferImportError(
            f"Impossible de lire l'annonce depuis ce site : {error}."
        ) from error

    if len(payload) > MAX_PAGE_BYTES:
        raise OfferImportError("La page est trop volumineuse pour être importée.")
    try:
        html = payload.decode(charset, errors="replace")
    except LookupError as error:
        raise OfferImportError("La page utilise un encodage non pris en charge.") from error

    parser = _AnnouncementParser()
    parser.feed(html)
    structured = _job_posting(parser.json_ld)
    title = (
        _as_text(structured.get("title"))
        or parser.meta.get("og:title", "")
        or " ".join(parser.h1_parts)
        or " ".join(parser.title_parts)
    )
    description = (
        _as_text(structured.get("description"))
        or parser.meta.get("og:description", "")
        or parser.meta.get("description", "")
        or _clean_text(parser.article_parts or parser.all_parts)
    )
    description = _clean_text([description])[:MAX_DESCRIPTION_CHARACTERS]
    if not title.strip() or not description:
        raise OfferImportError(
            "La page ne contient pas assez d'informations exploitables pour créer une offre. "
            "Le site masque peut-être son contenu ou nécessite une connexion."
        )
    company = _organization_name(structured.get("hiringOrganization"))
    location = _job_location(structured.get("jobLocation"))
    try:
        return JobOfferData(
            title=title[:250],
            company=company[:200],
            location=location[:200],
            url=normalized_url,
            source=f"Import URL — {host}"[:200],
            description=description,
        )
    except ValidationError as error:
        raise OfferImportError(
            "Les informations extraites ne respectent pas le format d'une offre."
        ) from error


def _normalize_public_url(url: str) -> str:
    try:
        normalized = str(AnyHttpUrl(url.strip()))
    except (ValidationError, AttributeError) as error:
        raise OfferImportError("Saisis une URL HTTP ou HTTPS valide.") from error
    _validate_public_url(normalized)
    return normalized


def _validate_public_url(url: str) -> None:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or host.casefold() == "localhost"
            or host.casefold().endswith((".localhost", ".local"))
        ):
            raise ValueError
        try:
            addresses = {ip_address(host)}
        except ValueError:
            addresses = {
                ip_address(result[4][0])
                for result in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
            }
        if not addresses or any(not address.is_global for address in addresses):
            raise ValueError
    except (OSError, ValueError) as error:
        raise OfferImportError(
            "L'URL doit pointer vers un site public HTTP(S), sans identifiants."
        ) from error


def _job_posting(json_ld_blocks: list[str]) -> dict[str, object]:
    def visit(value: object) -> dict[str, object] | None:
        if isinstance(value, dict):
            type_value = value.get("@type")
            types = type_value if isinstance(type_value, list) else [type_value]
            if any(
                isinstance(item, str) and item.casefold() == "jobposting"
                for item in types
            ):
                return value
            for child in value.values():
                result = visit(child)
                if result is not None:
                    return result
        elif isinstance(value, list):
            for child in value:
                result = visit(child)
                if result is not None:
                    return result
        return None

    for block in json_ld_blocks:
        try:
            result = visit(json.loads(block))
        except (json.JSONDecodeError, RecursionError):
            continue
        if result is not None:
            return result
    return {}


def _as_text(value: object) -> str:
    if isinstance(value, str):
        fragment = _AnnouncementParser()
        fragment.feed(value)
        return _clean_text(fragment.article_parts or fragment.all_parts)
    return ""


def _organization_name(value: object) -> str:
    if isinstance(value, dict):
        return _as_text(value.get("name"))
    return _as_text(value)


def _job_location(value: object) -> str:
    locations = value if isinstance(value, list) else [value]
    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address")
        if isinstance(address, dict):
            parts = [
                address.get(field)
                for field in ("addressLocality", "addressRegion", "addressCountry")
                if isinstance(address.get(field), str)
            ]
            if parts:
                return ", ".join(parts)
    return ""


def _clean_text(parts: list[str]) -> str:
    return " ".join(" ".join(part.split()) for part in parts if part).strip()
