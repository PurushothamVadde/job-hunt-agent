"""Resume tailoring tool.

Gap-analyses a job description against the user's current resume profile, builds
a tailored canonical profile, and (after the HITL ``write_resume`` gate) renders
an ATS-safe PDF to disk.

NOTE: this tool is HITL-gated. The graph calls ``interrupt()`` before the PDF is
written. The ``run`` signature accepts ``approved`` so callers can perform the
analysis phase without writing, then re-invoke with ``approved=True`` to commit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from agent.resume import tailoring
from agent.resume.pdf_generator import generate_ats_pdf
from db import sqlite

DESCRIPTION = (
    "Analyse a job description against the user's resume (matched / missing "
    "skills) and produce a tailored, ATS-safe resume PDF. Requires approval "
    "before writing the file."
)

RESUMES_DIR = Path(os.getenv("RESUMES_DIR", "resumes"))


class ToolSchema(BaseModel):
    jd_text: str = Field(..., description="Full job-description text.")
    company: Optional[str] = Field(None)
    role: Optional[str] = Field(None)
    approved: bool = Field(
        False, description="Set True after HITL approval to write the PDF."
    )


async def run(input: dict[str, Any], user_id: str) -> dict[str, Any]:
    args = ToolSchema(**input)

    profile_row = sqlite.get_current_resume(user_id)
    if not profile_row:
        return {
            "tool": "resume_tailor",
            "error": "onboarding_required",
            "message": "No resume profile found; upload a master resume first.",
        }
    profile = profile_row["data"]

    gaps = await tailoring.gap_analysis(args.jd_text, profile)
    tailored_profile = await tailoring.tailor(profile, args.jd_text)

    result: dict[str, Any] = {
        "tool": "resume_tailor",
        "matched": gaps["matched"],
        "missing_required": gaps["missing_required"],
        "missing_preferred": gaps["missing_preferred"],
        "keywords": gaps["keywords"],
        "tailored_profile": tailored_profile,
        "company": args.company,
        "role": args.role,
    }

    if not args.approved:
        # Analysis only — PDF write deferred to post-HITL re-invocation.
        result["resume_ready"] = False
        return result

    RESUMES_DIR.mkdir(parents=True, exist_ok=True)
    safe = "_".join(filter(None, [user_id, args.company, args.role])).replace(" ", "-")
    pdf_path = RESUMES_DIR / f"{safe or user_id}_tailored.pdf"
    try:
        generate_ats_pdf(tailored_profile, pdf_path)
        result["resume_ready"] = True
        result["path"] = str(pdf_path)
    except Exception as exc:
        result["resume_ready"] = False
        result["error"] = f"pdf_generation_failed: {exc}"
    return result
