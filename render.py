"""Command-line entry point for generating a CV PDF.

Usage:
    uv run python render.py
    uv run python render.py data/cv_custom.json output/cv_custom.pdf
"""

from src.services.cv_renderer import (
    load_base_cv_data,
    render_base_cv_cli,
    render_cv,
    render_cv_pdf,
)

__all__ = ["load_base_cv_data", "render_cv", "render_cv_pdf"]


if __name__ == "__main__":
    render_base_cv_cli()
