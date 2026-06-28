"""agent.llm — LLM client and provider protocol.

Sub-modules:
  agent.llm.provider  — LLMProvider Protocol (depend on this for DIP)
  agent.llm.client    — OpenAI concrete implementation
"""

from agent.llm.client import (  # noqa: F401
    MODEL,
    chat_model,
    complete_json,
    complete_text,
    openai_client,
)
from agent.llm.provider import LLMProvider  # noqa: F401
