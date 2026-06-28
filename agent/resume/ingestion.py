"""Backwards-compatible shim. Import directly from the focused modules instead:
  agent.resume.parser   — parse_document
  agent.resume.extractor — extract_profile, extract_facts
  agent.resume.embedder  — chunk_text, embed_resume_chunks, embed_career_facts
  agent.resume.pipeline  — ingest
"""

from agent.resume.embedder import chunk_text  # noqa: F401
from agent.resume.parser import parse_document  # noqa: F401
from agent.resume.pipeline import ingest  # noqa: F401
