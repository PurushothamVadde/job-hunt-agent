"""Resume tailoring: JD gap analysis + targeted rewrite of the canonical profile.

The flow:

1. ``gap_analysis(jd_text, profile)`` -> {matched, missing_required, missing_preferred}
2. ``tailor(profile, jd_text)`` -> a new canonical profile JSON re-ordered and
   re-worded to emphasise the matched requirements (never fabricating experience).
"""

from __future__ import annotations

import json
from typing import Any

from agent.llm import complete_json

_GAP_SYSTEM = """You are an ATS gap-analysis engine. Given a job description and a \
candidate's structured resume profile, compare them. Return a JSON object:

{
  "matched": ["requirement the candidate clearly meets", ...],
  "missing_required": ["required skill/qualification absent from resume", ...],
  "missing_preferred": ["preferred/nice-to-have not present", ...],
  "keywords": ["high-signal ATS keywords from the JD", ...]
}

Base every judgement strictly on the resume content. Do not invent skills."""


_TAILOR_SYSTEM = """You tailor a candidate's canonical resume JSON to a specific \
job description. Rules:
- Keep the exact same JSON schema as the input profile.
- Reorder skills and experience bullets to surface JD-relevant content first.
- Rewrite bullets to incorporate JD keywords ONLY where truthfully supported by \
the existing content. Never fabricate employers, titles, dates, or achievements.
- Tighten the summary to target the role.
Return ONLY the tailored JSON profile object."""


async def gap_analysis(jd_text: str, profile: dict[str, Any]) -> dict[str, Any]:
    user = (
        "JOB DESCRIPTION:\n"
        + jd_text[:12000]
        + "\n\nCANDIDATE PROFILE JSON:\n"
        + json.dumps(profile)[:12000]
    )
    result = await complete_json(_GAP_SYSTEM, user)
    # Guarantee the documented shape.
    return {
        "matched": result.get("matched", []),
        "missing_required": result.get("missing_required", []),
        "missing_preferred": result.get("missing_preferred", []),
        "keywords": result.get("keywords", []),
    }


async def tailor(profile: dict[str, Any], jd_text: str) -> dict[str, Any]:
    user = (
        "JOB DESCRIPTION:\n"
        + jd_text[:12000]
        + "\n\nCANDIDATE PROFILE JSON:\n"
        + json.dumps(profile)[:12000]
    )
    tailored = await complete_json(_TAILOR_SYSTEM, user, temperature=0.3)
    # Fall back to the original profile if the model returned something unusable.
    if not tailored or "contact" not in tailored:
        return profile
    return tailored
