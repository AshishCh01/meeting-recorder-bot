"""
Names an Ask AI chat from its first question ("August meeting themes").

It needs only the question, so the service runs it concurrently with the
answer and it adds no wait. Gemini first, then Groq, then the question itself
cut to 50 characters - a title is cosmetic, so this never raises.
"""

import asyncio
import logging
import re
import uuid

from google.genai import types

from app.config import settings
from app.rag.agent_runner import client
from app.rag.chat_fallback_groq import complete_text
from app.services.cost_tracker import gemini_generation_cost, groq_generation_cost, record_usage

logger = logging.getLogger(__name__)

TITLE_MAX_CHARS = 60
FALLBACK_QUESTION_CHARS = 50

_PROMPT = (
    "Write a short title, 3 to 6 words, for a chat that begins with the question below. "
    "Reply with the title only: no quotes, no trailing punctuation, no explanation.\n\n"
    "Question: {question}"
)

_QUOTES = "\"'`“”‘’«»*#_"
_LABEL = re.compile(r"^(title|chat title)\s*:\s*", re.IGNORECASE)


def fallback_title(question: str) -> str:
    """The question itself, whitespace-collapsed and cut to 50 characters."""
    text = " ".join(question.split())
    if len(text) <= FALLBACK_QUESTION_CHARS:
        return text or "New chat"
    return text[: FALLBACK_QUESTION_CHARS - 1].rstrip() + "…"


def clean_title(raw: str | None) -> str | None:
    """
    A model's reply as a usable title, or None if nothing usable is left:
    first non-empty line, no "Title:" label, no quotes or markdown, no
    trailing punctuation, at most 60 characters.
    """
    if not raw:
        return None
    line = next((l for l in raw.splitlines() if l.strip()), "")
    line = _LABEL.sub("", line.strip()).strip(_QUOTES + " ")
    line = " ".join(line.split()).rstrip(".:;,!").strip(_QUOTES + " ")
    if not line:
        return None
    if len(line) > TITLE_MAX_CHARS:
        line = line[: TITLE_MAX_CHARS - 1].rstrip() + "…"
    return line


async def _record(provider: str, model: str, outcome: str, request_id, user_id, prompt_tokens, output_tokens, usd):
    await asyncio.to_thread(
        record_usage,
        operation="ask_ai_title",
        provider=provider,
        model=model,
        outcome=outcome,
        request_id=request_id,
        user_id=user_id,
        input_tokens=prompt_tokens,
        output_tokens=output_tokens,
        usd=usd,
    )


async def generate_title(question: str, *, user_id: str) -> str:
    prompt = _PROMPT.format(question=question.strip()[:1000])
    request_id = uuid.uuid4()

    try:
        response = await client.aio.models.generate_content(
            model=settings.rag_agent_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                # Thinking tokens count against max_output_tokens, so this is
                # far more than a title needs; LOW keeps the thinking short.
                max_output_tokens=256,
                thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = (usage.prompt_token_count or 0) if usage else None
        output_tokens = (usage.candidates_token_count or 0) if usage else None
        title = clean_title(response.text)
        await _record(
            "gemini", settings.rag_agent_model, "ok" if title else "failed", request_id, user_id,
            prompt_tokens, output_tokens, gemini_generation_cost(prompt_tokens or 0, output_tokens or 0),
        )
        if title:
            return title
        logger.warning("[ask_ai] Gemini returned no usable title")
    except Exception as e:
        logger.warning("[ask_ai] title generation failed on Gemini: %s", e)
        await _record("gemini", settings.rag_agent_model, "failed", request_id, user_id, None, None, 0.0)

    if settings.groq_api_key:
        try:
            result = await complete_text([{"role": "user", "content": prompt}])
            title = clean_title(result["text"])
            await _record(
                "groq", settings.groq_chat_model, "fallback" if title else "failed", request_id, user_id,
                result["prompt_tokens"], result["output_tokens"],
                groq_generation_cost(result["prompt_tokens"], result["output_tokens"]),
            )
            if title:
                return title
        except Exception as e:
            logger.warning("[ask_ai] title generation failed on Groq: %s", e)
            await _record("groq", settings.groq_chat_model, "failed", request_id, user_id, None, None, 0.0)

    return fallback_title(question)
