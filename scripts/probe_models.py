#!/usr/bin/env python3
"""P0.4 — prove a 2-turn tool-call round trip works and record what `usage` reports.

Runs the smallest honest exercise of the path our loop will take: system + user, a tool
schema, an assistant tool call, a tool result, a final assistant message. Prints the raw
`usage` object from both turns plus the rate-limit headers, so `configs/models.yaml` and
NOTES.md ## Models are filled from measurement rather than from provider docs.

    python3 scripts/probe_models.py                 # both models from configs/models.yaml
    python3 scripts/probe_models.py --model big
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "python",
            "description": "Execute Python in a persistent kernel and return stdout/stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source to execute."}
                },
                "required": ["code"],
            },
        },
    }
]

SYSTEM = "You are an ERP operations agent. Use the python tool to compute; never guess arithmetic."
USER = "Using the python tool, compute 6 * 7 and then tell me the result in one short sentence."

RATE_LIMIT_HEADERS = (
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "retry-after",
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_models() -> dict:
    import yaml  # deferred: only needed when configs/models.yaml exists

    return yaml.safe_load((REPO_ROOT / "configs/models.yaml").read_text())


def post(base_url: str, api_key: str, payload: dict) -> tuple[dict, dict, float]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter attribution headers; harmless elsewhere.
            "HTTP-Referer": "https://github.com/sha-manav/erpharnessrlm",
            "X-Title": "ERP-Harness",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read())
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() in RATE_LIMIT_HEADERS
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise SystemExit(f"HTTP {exc.code} from {base_url}: {detail}") from exc
    return body, headers, time.time() - started


def probe(name: str, spec: dict) -> dict:
    api_key = os.environ.get(spec["api_key_env"], "")
    if not api_key:
        raise SystemExit(f"{spec['api_key_env']} is not set")

    base_payload = {
        "model": spec["model"],
        "tools": TOOLS,
        "max_tokens": spec.get("max_tokens", 4096),
    }
    if spec.get("temperature") is not None:
        base_payload["temperature"] = spec["temperature"]

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": USER},
    ]

    first, headers1, latency1 = post(
        spec["base_url"], api_key, {**base_payload, "messages": messages}
    )
    choice = first["choices"][0]["message"]
    tool_calls = choice.get("tool_calls") or []
    print(f"\n=== {name}: {spec['model']} ===")
    print(f"turn 1  latency {latency1:.1f}s  finish_reason={first['choices'][0].get('finish_reason')}")
    print("turn 1 usage:", json.dumps(first.get("usage"), indent=1))
    print("turn 1 tool_calls:", json.dumps(tool_calls)[:400] or "(none)")
    if headers1:
        print("rate-limit headers:", json.dumps(headers1))

    if not tool_calls:
        print("!! model did not call the tool — tool-calling format needs investigation")
        return {"tool_call": False, "usage_turn1": first.get("usage")}

    call = tool_calls[0]
    messages.append(choice)
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call["id"],
            "content": "42\n",
        }
    )

    second, _, latency2 = post(
        spec["base_url"], api_key, {**base_payload, "messages": messages}
    )
    final = second["choices"][0]["message"]
    print(f"turn 2  latency {latency2:.1f}s  finish_reason={second['choices'][0].get('finish_reason')}")
    print("turn 2 usage:", json.dumps(second.get("usage"), indent=1))
    print("turn 2 content:", (final.get("content") or "").strip()[:300])

    return {
        "tool_call": True,
        "tool_name": call.get("function", {}).get("name"),
        "tool_args": call.get("function", {}).get("arguments"),
        "usage_turn1": first.get("usage"),
        "usage_turn2": second.get("usage"),
        "latency_s": [round(latency1, 2), round(latency2, 2)],
        "id_format": call.get("id"),
        "provider": second.get("provider"),
    }


def main() -> int:
    load_env(REPO_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["big", "small"], help="probe just one")
    args = parser.parse_args()

    models = load_models()
    names = [args.model] if args.model else [n for n in ("big", "small") if n in models]

    results = {name: probe(name, models[name]) for name in names}
    print("\n=== summary ===")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
