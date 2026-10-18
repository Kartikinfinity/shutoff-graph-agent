"""Groq transport for LLM mode (OpenAI-compatible chat completions with tool calling).

This module only talks to the API. The agent decides which tools the model may
call, runs them against FalkorDB, and checks the reply against the results.
The key comes from GROQ_API_KEY; it is never logged or returned.
Docs: https://console.groq.com/docs/tool-use/overview and /docs/rate-limits.
"""

from __future__ import annotations

import os
import time

import httpx

URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = os.getenv("SHUTOFF_GROQ_MODEL", "openai/gpt-oss-20b")
MAX_WAIT_S = 8.0  # wait out a short 429 once; longer limits fall back to the offline planner


class LLMUnavailable(RuntimeError):
    """The provider did not answer usably (rate limit, auth, network, malformed response)."""


def available() -> bool:
    return bool(os.getenv("GROQ_API_KEY"))


def tool_defs(model_tools: list[dict]) -> list[dict]:
    """Agent tool definitions in the OpenAI function format Groq expects."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}} for t in model_tools]


def chat(messages: list[dict], tools: list[dict], timeout: float = 30.0) -> dict:
    """One completion. Returns the assistant message (content and/or tool_calls)."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMUnavailable("GROQ_API_KEY is not set")
    body = {"model": MODEL, "messages": messages, "tools": tools, "tool_choice": "auto",
            "max_completion_tokens": 1024, "reasoning_effort": "low"}
    for attempt in range(2):
        try:
            r = httpx.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Groq unreachable ({type(e).__name__})") from None
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("retry-after") or 1)
            except ValueError:
                wait = MAX_WAIT_S + 1
            if attempt == 0 and wait <= MAX_WAIT_S:
                time.sleep(wait)
                continue
            raise LLMUnavailable(f"Groq rate limit reached (retry after {wait:.0f} s)")
        if r.status_code >= 400:
            try:
                code = (r.json().get("error") or {}).get("code") or ""
            except ValueError:
                code = ""
            if attempt == 0 and code == "tool_use_failed":  # malformed tool call from the model: retry once
                continue
            raise LLMUnavailable(f"Groq API error {r.status_code}{' ' + code if code else ''}")
        try:
            return r.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMUnavailable("Groq returned an unexpected response") from None
    raise LLMUnavailable("Groq did not return a usable response")
