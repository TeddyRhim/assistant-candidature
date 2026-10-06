from __future__ import annotations

import importlib
import json
import os
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from src.services.job_titles import extract_job_role

PROJECT_DIR = Path(__file__).resolve().parents[2]
BASE_CV_PATH = PROJECT_DIR / "data" / "cv_base.json"
BASE_CV_EXAMPLE_PATH = PROJECT_DIR / "data" / "cv_base.json.example"
_DLL_DIRECTORY_HANDLES: list[object] = []
REQUIRED_CV_FIELDS = (
    "name",
    "contact",
    "profile",
    "skills",
    "education",
    "experience",
)


def _register_weasyprint_dll_directories() -> list[Path]:
    if sys.platform != "win32":
        return []

    candidates = [
        *os.environ.get("WEASYPRINT_DLL_DIRECTORIES", "").split(os.pathsep),
        *os.environ.get("PATH", "").split(os.pathsep),
        r"C:\msys64\mingw64\bin",
        r"C:\msys64\ucrt64\bin",
        r"C:\Program Files\GTK3-Runtime Win64\bin",
        r"C:\Program Files\GTK3-Runtime\bin",
    ]
    registered: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate:
            continue
        directory = Path(candidate.strip('"'))
        if directory in seen or not (directory / "libgobject-2.0-0.dll").is_file():
            continue
        seen.add(directory)
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(directory)))
        registered.append(directory)
    return registered


@lru_cache(maxsize=1)
def _load_weasyprint_html() -> Any:
    dll_directories = _register_weasyprint_dll_directories()
    try:
        return importlib.import_module("weasyprint").HTML
    except OSError as error:
        if sys.platform != "win32":
            raise
        found = ", ".join(map(str, dll_directories)) or "aucun"
        raise RuntimeError(
            "WeasyPrint ne peut pas charger ses bibliothèques natives Windows. "
            f"Dossiers contenant libgobject détectés et ajoutés : {found}. "
            "Vérifie que GTK3/Pango et leurs DLL sont installés dans le même dossier bin. "
            "Si GTK est ailleurs, définis WEASYPRINT_DLL_DIRECTORIES vers ce dossier bin."
        ) from error


def render_cv(
    data: dict[str, Any],
    output_path: Path,
    template_name: str = "cv.html",
) -> Path:
    """Render the CV template and write the PDF to ``output_path``."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(render_cv_pdf(data, template_name))
    return output_path


def render_cv_pdf(data: dict[str, Any], template_name: str = "cv.html") -> bytes:
    """Render the CV template and return the generated PDF bytes."""
    data = prepare_cv_data(data)
    return render_template_pdf(data, template_name)


def tailored_cv_filename(
    data: dict[str, Any],
    *,
    target_role: str = "",
    target_company: str = "",
) -> str:
    """Build a readable, stable PDF filename from candidate and job details."""

    def slug(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()

    pieces = [
        slug(str(data.get("name", "")))[:40],
        "cv",
        slug(extract_job_role(target_role))[:48],
        slug(target_company)[:36],
    ]
    return "-".join(piece for piece in pieces if piece) + ".pdf"


def render_template_html(data: dict[str, Any], template_name: str) -> str:
    """Render a project-owned Jinja2 HTML template to an HTML string."""
    environment = Environment(
        loader=FileSystemLoader(PROJECT_DIR / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
    )
    environment.filters["md"] = _markdown_bold
    return environment.get_template(template_name).render(**data)


def render_cv_html(data: dict[str, Any], template_name: str = "cv.html") -> str:
    """Prepare CV data and render the CV template as an HTML string."""
    data = prepare_cv_data(data)
    return render_template_html(data, template_name)


def render_template_pdf(data: dict[str, Any], template_name: str) -> bytes:
    """Render a project-owned Jinja2 HTML template to PDF."""
    html_renderer = _load_weasyprint_html()
    html = render_template_html(data, template_name)
    return html_renderer(string=html, base_url=str(PROJECT_DIR)).write_pdf()


def _markdown_bold(text: Any) -> Markup:
    safe_text = str(escape(text))
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe_text))


def prepare_cv_data(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and add optional fields expected by the HTML template."""
    prepared = dict(data)
    if "contact" not in prepared:
        prepared["contact"] = []
        phone = prepared.pop("phone", "")
        email = data.get("email")
        other_contacts = prepared.pop("other_contacts", [])
        if phone:
            prepared["contact"].append({"text": phone})
        if email:
            prepared["contact"].append(
                {"text": email, "url": f"mailto:{email}"}
            )
        prepared["contact"].extend(
            {"text": value} for value in other_contacts if value
        )
    missing = [field for field in REQUIRED_CV_FIELDS if field not in prepared]
    if missing:
        raise ValueError("Champs JSON manquants : " + ", ".join(missing))
    for field in ("name", "profile"):
        if not isinstance(prepared[field], str):
            raise ValueError(f"Le champ JSON « {field} » doit être du texte.")
    if not isinstance(prepared["contact"], list):
        raise ValueError("Le champ JSON « contact » doit être une liste.")
    for index, contact in enumerate(prepared["contact"]):
        if not isinstance(contact, dict) or not isinstance(contact.get("text"), str):
            raise ValueError(
                f"Le contact {index + 1} doit être un objet avec un champ texte."
            )
        contact_url = contact.get("url")
        if contact_url is not None:
            if not isinstance(contact_url, str):
                raise ValueError(f"Le lien du contact {index + 1} doit être du texte.")
            parsed_url = urlsplit(contact_url)
            valid_http_url = parsed_url.scheme in {"http", "https"} and bool(
                parsed_url.netloc
            )
            valid_mailto_url = parsed_url.scheme == "mailto" and bool(parsed_url.path)
            if not (valid_http_url or valid_mailto_url):
                raise ValueError(
                    f"Le lien du contact {index + 1} doit être HTTP, HTTPS ou mailto."
                )
    prepared.pop("target_offer_url", None)
    prepared.pop("title", None)
    for field in ("skills", "education", "experience"):
        if not isinstance(prepared.get(field), list):
            raise ValueError(f"Le champ JSON « {field} » doit être une liste.")
    prepared["education"] = [
        dict(education_item) if isinstance(education_item, dict) else education_item
        for education_item in prepared["education"]
    ]
    if "education" in prepared:
        for education_item in prepared["education"]:
            if (
                isinstance(education_item, dict)
                and "text" not in education_item
                and "school" in education_item
            ):
                education_item["text"] = " | ".join(
                    part
                    for part in (
                        education_item["school"],
                        education_item.get("period", ""),
                        education_item.get("description", ""),
                    )
                    if part
                )
    prepared.setdefault("projects", [])
    for field in ("skills", "education", "experience", "projects"):
        if not isinstance(prepared[field], list):
            raise ValueError(f"Le champ JSON « {field} » doit être une liste.")

    prepared.setdefault("hobbies", "")
    prepared.setdefault("projects_period", "")
    prepared.setdefault("target_company", "")
    prepared.setdefault("target_role", "")
    if isinstance(prepared["target_company"], str):
        prepared["target_company"] = prepared["target_company"].split(" · ", 1)[0]
    required_item_fields = {
        "skills": ("label", "details"),
        "education": ("text",),
        "experience": ("company", "role", "period", "bullets"),
        "projects": ("name", "stack", "bullets"),
    }
    optional_string_fields = {
        "education": ("description",),
        "experience": ("context", "headline"),
    }
    for field, required_fields in required_item_fields.items():
        for index, item in enumerate(prepared[field]):
            if not isinstance(item, dict):
                raise ValueError(f"Élément {index + 1} de « {field} » doit être un objet JSON.")
            missing_item_fields = [
                item_field for item_field in required_fields if item_field not in item
            ]
            if missing_item_fields:
                raise ValueError(
                    f"Élément {index + 1} de « {field} » : champs manquants "
                    + ", ".join(missing_item_fields)
                    + "."
                )
            if "bullets" in required_fields and not isinstance(item["bullets"], list):
                raise ValueError(
                    f"Le champ « bullets » de l'élément {index + 1} de « {field} » "
                    "doit être une liste."
                )
            text_fields = [
                item_field for item_field in required_fields if item_field != "bullets"
            ]
            text_fields.extend(optional_string_fields.get(field, ()))
            for item_field in text_fields:
                if item_field in item and not isinstance(item[item_field], str):
                    raise ValueError(
                        f"Le champ « {item_field} » de l'élément {index + 1} "
                        f"de « {field} » doit être du texte."
                    )
            if "bullets" in item and not all(
                isinstance(bullet, str) for bullet in item["bullets"]
            ):
                raise ValueError(
                    f"Les puces de l'élément {index + 1} de « {field} » doivent être du texte."
                )
    for field in ("target_company", "target_role", "hobbies", "projects_period"):
        if not isinstance(prepared[field], str):
            raise ValueError(f"Le champ JSON « {field} » doit être du texte.")
    return prepared


def load_base_cv_data() -> dict[str, Any]:
    """Load the local editable JSON CV data."""
    if not BASE_CV_PATH.is_file() and BASE_CV_EXAMPLE_PATH.is_file():
        return json.loads(BASE_CV_EXAMPLE_PATH.read_text(encoding="utf-8"))
    return json.loads(BASE_CV_PATH.read_text(encoding="utf-8"))


def validate_base_cv_json(content: str) -> str:
    """Validate editable base CV JSON and return its normalized representation."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Le JSON du CV de base est invalide (ligne {error.lineno}, "
            f"colonne {error.colno}) : {error.msg}."
        ) from error
    if not isinstance(data, dict):
        raise ValueError("Les données du CV de base doivent être un objet JSON.")
    return json.dumps(prepare_cv_data(data), ensure_ascii=False, indent=2)


def save_base_cv_json(content: str) -> str:
    """Validate and atomically replace the local base CV JSON."""
    normalized = validate_base_cv_json(content)
    temporary_path = BASE_CV_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(normalized + "\n", encoding="utf-8")
    temporary_path.replace(BASE_CV_PATH)
    return normalized


def render_base_cv_cli() -> None:
    source_path = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_DIR / "data" / "cv_base.json"
    )
    output_path = (
        Path(sys.argv[2]) if len(sys.argv) > 2 else PROJECT_DIR / "output" / "cv.pdf"
    )
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    cv_data = json.loads(source_path.read_text(encoding="utf-8"))
    print(f"PDF généré : {render_cv(cv_data, output_path)}")
