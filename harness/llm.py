"""OpenAI-compatible chat client with usage accounting and retries (PLAN.md P2.7).

Three things this has to get right, each learned from a measurement:

* **Prompt caching is explicit here.** Every GLM-5.1 endpoint reports
  `supports_implicit_caching: false`, yet pi gets 80% of its input tokens billed as cache
  reads — because it sets cache breakpoints in the request. A static system prefix alone is
  not enough, so this client marks the system block and the instruction with
  `cache_control`. At 1.25M input tokens per trial, that is the single biggest cost lever.
* **Provider routing is pinned.** GLM-5.1 is served at fp4 by one upstream and fp8 by
  twelve, and PLAN.md hard rule 9 requires the same model across configs.
  `provider_routing` from `configs/models.yaml` is sent as OpenRouter's `provider` field.
* **Usage is recorded per call, including the serving provider.** OpenRouter returns a
  real `cost` per response, so cost per task is measured rather than derived from a price
  table, and `provider` lets a later run prove which upstream served it.

Retries: exponential backoff on 429 and 5xx, five attempts, then the caller records
`terminal_reason = api_error`. Authorization headers are never logged.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
BACKOFF_BASE = 2.0
BACKOFF_CAP = 60.0


class LLMError(RuntimeError):
    """A request that could not be completed after retries."""


@dataclass
class Usage:
    input: int = 0
    cached: int = 0
    output: int = 0
    reasoning: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Usage") -> None:
        self.input += other.input
        self.cached += other.cached
        self.output += other.output
        self.reasoning += other.reasoning
        self.cost_usd += other.cost_usd

    def as_dict(self) -> dict:
        return {"input": self.input, "cached": self.cached, "output": self.output}


@dataclass
class Reply:
    message: dict
    usage: Usage
    finish_reason: str | None
    provider: str | None
    latency_s: float
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def tool_calls(self) -> list[dict]:
        return self.message.get("tool_calls") or []

    @property
    def text(self) -> str:
        return self.message.get("content") or ""


def _parse_usage(raw: dict) -> Usage:
    usage = raw.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    return Usage(
        input=usage.get("prompt_tokens", 0) or 0,          # includes cached
        cached=prompt_details.get("cached_tokens", 0) or 0,
        output=usage.get("completion_tokens", 0) or 0,
        reasoning=completion_details.get("reasoning_tokens", 0) or 0,
        cost_usd=usage.get("cost", 0.0) or 0.0,
    )


class LLM:
    def __init__(self, spec: dict, api_key: str, logger=None):
        self.spec = spec
        self.api_key = api_key
        self.logger = logger
        self.total = Usage()
        self.calls = 0
        self.providers: dict[str, int] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/sha-manav/erpharnessrlm",
            "X-Title": "ERP-Harness",
        }

    def _payload(self, messages: list[dict], tools: list[dict] | None) -> dict:
        payload: dict[str, Any] = {
            "model": self.spec["model"],
            "messages": messages,
            "max_tokens": self.spec.get("max_tokens", 4096),
        }
        # pi never sends temperature, so neither do we: the configs must differ by the
        # harness alone, not by sampling settings.
        if self.spec.get("temperature") is not None:
            payload["temperature"] = self.spec["temperature"]
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self.spec.get("provider_routing"):
            payload["provider"] = self.spec["provider_routing"]
        payload["usage"] = {"include": True}
        return payload

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        payload = self._payload(messages, tools)
        body = json.dumps(payload).encode()
        started = time.time()
        last_error = ""

        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                f"{self.spec['base_url'].rstrip('/')}/chat/completions",
                data=body, headers=self._headers(), method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.spec.get("timeout_s", 900)) as response:
                    raw = json.loads(response.read())
                if raw.get("error"):
                    raise LLMError(str(raw["error"])[:300])
                choice = (raw.get("choices") or [{}])[0]
                usage = _parse_usage(raw)
                self.total.add(usage)
                self.calls += 1
                provider = raw.get("provider")
                if provider:
                    self.providers[provider] = self.providers.get(provider, 0) + 1
                return Reply(
                    message=choice.get("message") or {},
                    usage=usage,
                    finish_reason=choice.get("finish_reason"),
                    provider=provider,
                    latency_s=round(time.time() - started, 2),
                    raw=raw,
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:300]
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code not in RETRY_STATUS:
                    raise LLMError(last_error) from None
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt < MAX_ATTEMPTS:
                # Jittered exponential backoff: a whole fleet of trials retrying in step is
                # how a rate limit becomes an outage.
                delay = min(BACKOFF_CAP, BACKOFF_BASE ** attempt) * (0.5 + random.random())
                if self.logger:
                    self.logger.warning(
                        "llm retry %d/%d in %.1fs: %s", attempt, MAX_ATTEMPTS, delay, last_error
                    )
                time.sleep(delay)

        raise LLMError(f"giving up after {MAX_ATTEMPTS} attempts: {last_error}")


def mark_cached(message: dict) -> dict:
    """Tag a message so the provider caches its prefix.

    Caching on these endpoints is explicit, not implicit (NOTES.md ## Models). The system
    block and the instruction are identical for every step of a trajectory, so marking them
    turns ~80% of prompt tokens into cache reads at roughly a fifth of the price.
    """
    content = message.get("content")
    if isinstance(content, str):
        message = dict(message)
        message["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    return message
