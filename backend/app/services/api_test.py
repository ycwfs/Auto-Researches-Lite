"""Lightweight 'does this third-party API actually work' checks for the admin panel.

Each returns (ok, detail). Failures are caught and reported as a short message rather
than raised, so the admin sees a clear pass/fail instead of a 500."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.admin import ModelCatalog

logger = logging.getLogger("far.apitest")


def test_model(db: Session, entry: ModelCatalog) -> tuple[bool, str]:
    """Verify a model-catalog entry's API key/endpoint with the cheapest real call."""
    from app.services import model_catalog

    from app.services.model_select import effective_api_style

    key = model_catalog.key_of(entry)
    if not key:
        return False, "No API key configured for this model."
    style = effective_api_style(entry)  # the wire protocol the real pipeline will use
    try:
        # Probe the endpoint at EACH declared effort level (or once with no effort when
        # none are declared) — so the admin sees per-level pass/fail and success requires
        # every selected level to work.
        return _test_efforts(style, key, entry.base_url, entry.model,
                             list(entry.supported_efforts or []))
    except Exception as exc:  # noqa: BLE001 — surface a clean message
        return False, f"{type(exc).__name__}: {exc}"[:300]


def _test_efforts(
    style: str, key: str, base_url: str, model: str, efforts: list[str], *, prefix: str = ""
) -> tuple[bool, str]:
    """Run the connectivity probe once per declared effort level (or once with no effort
    when the list is empty). Overall OK only when every level passes; the detail is a
    per-level log so the admin sees exactly which levels the endpoint accepts."""
    from app.models.enums import REASONING_LEVELS

    levels = [e for e in efforts if e in REASONING_LEVELS and e != "off"] or [""]
    lines: list[str] = []
    all_ok = True
    for lvl in levels:
        try:
            ok, detail = _test_completion(style, key, base_url, model, effort=lvl)
        except Exception as exc:  # noqa: BLE001 — one level's failure doesn't abort the rest
            ok, detail = False, f"{type(exc).__name__}: {exc}"[:200]
        all_ok = all_ok and ok
        lines.append(f"[{lvl or 'no effort'}] {'✓' if ok else '✗'} {detail}")
    return all_ok, prefix + "\n".join(lines)


# OpenAI reasoning_effort tops out at "high"; xhigh/max map down (mirrors llm.py).
_OPENAI_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


def _test_completion(
    style: str, key: str, base_url: str, model: str, effort: str = ""
) -> tuple[bool, str]:
    # Catalog models are Anthropic/OpenAI-compatible chat endpoints; `style` is the
    # resolved wire protocol ("anthropic" | "openai"). The probes mirror llm.py's
    # _complete_anthropic/_complete_openai but keep the raw response: the test's job is
    # to verify the KEY/ENDPOINT, and a completed round-trip with no plain text (a
    # safety refusal, or a reasoning model spending the whole probe budget on thinking)
    # is an endpoint success, not a failure — some proxy+model combos refuse bare test
    # prompts while working fine on real ones (observed with claude-fable-5 proxies).
    # `effort` sends the reasoning level exactly as the real pipeline does, so the probe
    # verifies the endpoint actually accepts that level (a 400 fails just that level).
    prompt = "What is 2+2? Reply with just the number."
    system = "You are a precise research assistant. Output only what is asked."
    if style == "openai":
        from openai import OpenAI

        client = OpenAI(api_key=key, **({"base_url": base_url} if base_url else {}))
        kwargs: dict = dict(
            model=model or "gpt-4o-mini",
            max_tokens=512,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        if effort:
            kwargs["reasoning_effort"] = _OPENAI_EFFORT.get(effort, effort)
        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        message = getattr(choice, "message", None)
        text = str(getattr(message, "content", "") or "").strip()
        finish = str(getattr(choice, "finish_reason", "") or "")
        reasoned = bool(str(getattr(message, "reasoning_content", "") or "").strip())
        out_tokens = int(getattr(getattr(resp, "usage", None), "completion_tokens", 0) or 0)
    else:
        import anthropic

        client = anthropic.Anthropic(
            api_key=key, **({"base_url": base_url} if base_url else {})
        )
        kwargs = dict(
            model=model or "claude-sonnet-4-6",
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        if effort:
            kwargs["output_config"] = {"effort": effort}
        resp = client.messages.create(**kwargs)
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        finish = str(getattr(resp, "stop_reason", "") or "")
        reasoned = any(getattr(b, "type", "") == "thinking" for b in resp.content)
        out_tokens = int(getattr(getattr(resp, "usage", None), "output_tokens", 0) or 0)
    if text:
        return True, f"OK — model responded: {text[:80]}"
    if finish == "refusal":
        return True, (
            "OK — endpoint & key accepted (the call completed and was billed), but the "
            "model refused the probe prompt (stop_reason=refusal). Some proxies/safety "
            "layers refuse bare test prompts; if project steps come back empty, the "
            "model is refusing plain API calls too."
        )
    if reasoned or out_tokens > 0:
        return True, (
            f"OK — endpoint & key accepted; the model spent the probe on reasoning "
            f"({out_tokens} output tokens) without returning plain text."
        )
    return False, f"Empty response from the model (finish reason: {finish or 'unknown'})."


def test_mineru(db: Session) -> tuple[bool, str]:
    """Check the configured MinerU endpoint is reachable and the key is accepted
    (a lightweight probe — does not run a full PDF conversion)."""
    import httpx

    from app.services import integration_service

    key, url = integration_service.mineru_config(db)
    if not key or not url:
        return False, "MinerU API key and URL are not configured."
    try:
        resp = httpx.post(
            url, headers={"Authorization": f"Bearer {key}"}, json={"url": ""}, timeout=20.0
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not reach MinerU: {type(exc).__name__}: {exc}"[:300]
    if resp.status_code in (401, 403):
        return False, f"Authentication failed (HTTP {resp.status_code}) — check the API key."
    if resp.status_code == 200:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = None
        if isinstance(body, dict) and body.get("error"):
            return False, f"Endpoint returned an error: {str(body.get('error'))[:140]}"
        return True, "OK — endpoint reachable and authenticated."
    if resp.status_code >= 500:
        return False, f"MinerU server error (HTTP {resp.status_code})."
    # Other 4xx: reachable and not an auth rejection, but a successful conversion isn't confirmed.
    return True, f"Reachable (HTTP {resp.status_code}); endpoint responded but a conversion was not confirmed."
