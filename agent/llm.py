"""Shared LLM helpers (GPT-4o via the OpenAI SDK and LangChain)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o"


@lru_cache(maxsize=1)
def openai_client():
    """Cached async OpenAI client."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


@lru_cache(maxsize=2)
def chat_model(streaming: bool = False):
    """Cached LangChain ``ChatOpenAI`` instance."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=MODEL,
        streaming=streaming,
        temperature=0.2,
        api_key=os.getenv("OPENAI_API_KEY"),
    )


async def complete_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Run a single GPT-4o call constrained to JSON object output."""
    client = openai_client()
    resp = await client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    content = resp.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


async def complete_text(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
) -> str:
    client = openai_client()
    resp = await client.chat.completions.create(
        model=MODEL,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""
