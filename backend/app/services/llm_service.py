"""Thin wrapper around the LLM API: retries, JSON parsing, graceful 'unavailable' mode."""
import json
import logging
import re
import time
from functools import lru_cache
from typing import Any

from ..config import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLMService:
    def __init__(self) -> None:
        self._client = None
        if settings.anthropic_api_key:
            import anthropic

            # We do our own retry/backoff below.
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=0)

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        if not self.available:
            raise LLMError("LLM is not configured (set ANTHROPIC_API_KEY).")
        last: Exception | None = None
        for attempt in range(settings.llm_max_retries):
            try:
                resp = self._client.messages.create(
                    model=settings.llm_model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            except Exception as exc:  # network, rate limit, overloaded...
                last = exc
                log.warning("LLM call failed (attempt %s): %s", attempt + 1, exc)
                time.sleep(2**attempt)
        raise LLMError(f"LLM request failed after retries: {last}")

    def complete_json(self, system: str, user: str, max_tokens: int = 3000) -> Any:
        system = system + "\n\nRespond with valid JSON only. No prose, no markdown fences."
        raw = self.complete(system, user, max_tokens)
        return parse_json(raw)


def parse_json(raw: str) -> Any:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    raise LLMError("LLM returned invalid JSON")


@lru_cache
def get_llm() -> LLMService:
    return LLMService()
