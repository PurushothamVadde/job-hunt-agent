"""ATS-safe PDF generation via Jinja2 + WeasyPrint."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Union

from jinja2 import Environment, FileSystemLoader, select_autoescape

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_html(profile: dict[str, Any]) -> str:
    """Render the ATS resume HTML for a canonical profile dict."""
    template = _env.get_template("ats_resume.html")
    return template.render(p=_normalize(profile))


def generate_ats_pdf(profile: dict[str, Any], out_path: Union[str, Path]) -> Path:
    """Render the profile to an ATS-safe PDF at ``out_path``.

    Raises if the WeasyPrint native stack (cairo/pango) is unavailable; callers
    should treat PDF generation as best-effort.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(profile)

    from weasyprint import HTML

    HTML(string=html).write_pdf(str(out_path))
    return out_path


def _normalize(profile: dict[str, Any]) -> dict[str, Any]:
    """Ensure all expected keys exist so the template never errors."""
    contact = profile.get("contact", {}) or {}
    return {
        "contact": {
            "name": contact.get("name", ""),
            "title": contact.get("title", ""),
            "email": contact.get("email", ""),
            "phone": contact.get("phone", ""),
            "location": contact.get("location", ""),
            "linkedin": contact.get("linkedin", ""),
            "website": contact.get("website", ""),
        },
        "summary": profile.get("summary", ""),
        "skills": _normalize_skills(profile.get("skills", []) or []),
        "experience": profile.get("experience", []) or [],
        "education": profile.get("education", []) or [],
        "certifications": profile.get("certifications", []) or [],
        "projects": profile.get("projects", []) or [],
    }


def _normalize_skills(skills: list) -> list[str]:
    """Ensure skills are in grouped 'Category: items' format.

    If the stored profile has flat individual skill names (old format), groups
    them into a single 'Skills' entry so the template renders them inline.
    If already grouped (any entry contains ':'), pass through as-is.
    """
    if not skills:
        return []
    str_skills = [str(s) for s in skills if s]
    # Already grouped — at least one entry has a colon separator
    if any(":" in s for s in str_skills):
        return str_skills
    # Flat list — wrap as a single comma-separated line so it renders cleanly
    return ["Skills: " + ", ".join(str_skills)]
