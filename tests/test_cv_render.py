from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from pypdf import PdfReader

import src.services.cv_renderer as cv_renderer
from src.services.cv_renderer import (
    load_base_cv_data,
    render_cv,
    render_cv_html,
    save_base_cv_json,
    tailored_cv_filename,
    validate_base_cv_json,
)

LOREM_IPSUM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua."
)


def _normalized_pdf_text(pdf_path: Path) -> str:
    return " ".join(
        " ".join((page.extract_text() or "").casefold().split())
        for page in PdfReader(pdf_path).pages
    )


def _heading_x_positions(pdf_path: Path) -> dict[str, float]:
    positions: dict[str, float] = {}

    def record_position(text, _cm, text_matrix, _font, _font_size) -> None:
        normalized = "".join(text.casefold().split())
        if normalized in {"profil", "expérienceprofessionnelle"}:
            positions[normalized] = text_matrix[4]

    reader = PdfReader(pdf_path)
    for page in reader.pages:
        page.extract_text(visitor_text=record_position)
    assert abs(positions["profil"] - positions["expérienceprofessionnelle"]) < 20
    return positions


def _append_lorem_ipsum(value: Any) -> Any:
    if isinstance(value, str):
        return f"{value} {LOREM_IPSUM}".strip()
    if isinstance(value, list):
        return [_append_lorem_ipsum(item) for item in value]
    if isinstance(value, dict):
        return {key: _append_lorem_ipsum(item) for key, item in value.items()}
    return value


def test_generates_pdf_from_base_cv_with_lorem_ipsum_in_all_fields(tmp_path: Path) -> None:
    base_data = load_base_cv_data()
    base_pdf_path = tmp_path / "cv-base.pdf"
    render_cv(base_data, base_pdf_path)
    assert PdfReader(base_pdf_path).pages
    _heading_x_positions(base_pdf_path)

    stress_data = _append_lorem_ipsum(deepcopy(base_data))
    output_path = tmp_path / "cv-lorem-test.pdf"

    generated_path = render_cv(stress_data, output_path)

    assert generated_path == output_path
    assert output_path.read_bytes().startswith(b"%PDF-")
    extracted_text = _normalized_pdf_text(output_path)
    assert "alexandre martin" in extracted_text
    assert "apimo" in extracted_text
    assert "lorem ipsum dolor sit amet" in extracted_text


def test_tailored_cv_filename_is_descriptive_and_stable() -> None:
    data = {"name": "Alexandre Martin"}
    filename = tailored_cv_filename(
        data,
        target_role="Développeur PHP / Symfony",
        target_company="Équipe Exemple",
    )

    assert filename == "alexandre-martin-cv-developpeur-php-symfony-equipe-exemple.pdf"
    assert tailored_cv_filename(
        data,
        target_role="Développeur PHP / Symfony",
        target_company="Équipe Exemple",
    ) == filename
    assert "01" not in filename


def test_base_cv_json_can_be_validated_and_saved(tmp_path: Path, monkeypatch) -> None:
    data = load_base_cv_data()
    destination = tmp_path / "cv_base.json"
    monkeypatch.setattr(cv_renderer, "BASE_CV_PATH", destination)
    data["experience"].append(
        {
            "role": "Développeuse de test",
            "company": "Exemple",
            "period": "2026",
            "bullets": ["Ajout vérifié au CV de base."],
        }
    )
    content = json.dumps(data, ensure_ascii=False)

    normalized = validate_base_cv_json(content)
    saved = save_base_cv_json(content)

    assert json.loads(saved)["experience"][-1]["company"] == "Exemple"
    assert json.loads(destination.read_text(encoding="utf-8")) == json.loads(normalized)
    assert not destination.with_suffix(".json.tmp").exists()


def test_render_cv_html_contains_structure() -> None:
    data = load_base_cv_data()
    html = render_cv_html(data)
    assert "<!DOCTYPE html>" in html
    assert f"<title>CV - {data['name']}</title>" in html
    assert "Expérience professionnelle" in html
    assert "TechSolutions" in html
    assert "Compétences" in html
