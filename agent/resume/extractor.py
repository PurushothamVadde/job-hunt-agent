"""GPT-4o structured extraction from resume text. Single responsibility: text -> structured data."""

from __future__ import annotations

import json
from typing import Any

from agent.llm import complete_json

_EXTRACTION_SYSTEM = """You are a resume parsing engine. Extract the candidate's \
information from the raw resume text into a strict canonical JSON object with \
exactly these top-level keys:

{
  "contact": {"name": "", "email": "", "phone": "", "location": "",
              "linkedin": "", "website": ""},
  "summary": "",
  "skills": ["Category: skill1, skill2, skill3", "..."],
  "experience": [
    {"company": "", "title": "", "location": "", "start": "", "end": "",
     "bullets": ["..."]}
  ],
  "education": [
    {"institution": "", "degree": "", "field": "", "start": "", "end": "",
     "details": ""}
  ],
  "certifications": ["..."],
  "projects": [{"name": "", "description": "", "bullets": ["..."]}]
}

CRITICAL — skills format: Each entry in "skills" MUST be a grouped category string
like "Languages: Java, Python, SQL" — NOT individual skill names. Use standard
categories: Languages, Frameworks, Cloud & Infrastructure, Data & Analytics,
Databases, Tools & Platforms. Example:
  ["Languages: Java, Python, SQL",
   "Frameworks: Spring Boot, FastAPI, React",
   "Cloud & Infrastructure: AWS, Azure, Docker, Kubernetes",
   "Data & Analytics: Spark, Databricks, Kafka, Airflow",
   "Databases: MySQL, MongoDB, Redis",
   "Tools & Platforms: Git, Jira, IntelliJ"]

Use empty strings / empty arrays where information is missing. Never invent data.
Return ONLY the JSON object."""

_FACTS_SYSTEM = """You extract durable career facts from a resume profile. \
Return a JSON object: {"facts": ["fact 1", "fact 2", ...]}. Each fact must be a \
short, self-contained sentence capturing skills, seniority, domains, notable \
achievements, or stated preferences. Produce 5-15 facts."""


async def extract_profile(raw_text: str) -> dict[str, Any]:
    """Run GPT-4o structured extraction on raw resume text."""
    return await complete_json(_EXTRACTION_SYSTEM, raw_text[:24000])


async def extract_facts(profile: dict[str, Any]) -> list[str]:
    """Extract short, durable career facts from a canonical profile dict."""
    payload = json.dumps(profile)[:16000]
    result = await complete_json(_FACTS_SYSTEM, payload)
    if isinstance(result, dict):
        return result.get("facts", [])
    return []
