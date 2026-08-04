"""Minimal OpenRouter chat client. Uses free router when OPENROUTER_API_KEY is set."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "openrouter/free")
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def available() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def chat(
    prompt: str,
    *,
    model: str | None = None,
    system: str = "You are a concise assistant for an outreach agent. Reply with only the requested text.",
) -> dict[str, Any]:
    """
    Returns {ok, text, model, error}.
    Falls back to a deterministic stub when no API key is configured (CI-safe).
    """
    model = model or DEFAULT_MODEL
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        stub = f"[stub:{model}] {prompt.strip()[:160]}"
        return {"ok": True, "text": stub, "model": f"stub/{model}", "error": ""}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/rockybalboan19/SemanticRun",
            "X-Title": "SemanticRun drift-recovery bench",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
        used = body.get("model") or model
        return {"ok": True, "text": text.strip(), "model": used, "error": ""}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "text": "", "model": model, "error": f"HTTP {exc.code}: {detail}"}
    except Exception as exc:  # noqa: BLE001 — bench harness
        return {"ok": False, "text": "", "model": model, "error": str(exc)}
