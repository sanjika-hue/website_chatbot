import json
import logging

import httpx

from config import LLM_URL, LLM_MODEL, LLM_API_KEY, LLM_CONFIG_PATH

log = logging.getLogger("tastebud")


def get_llm_config() -> tuple[str, str, str]:
    """Return (url, model, api_key) from config file if it exists, else env vars."""
    if LLM_CONFIG_PATH.exists():
        try:
            data = json.loads(LLM_CONFIG_PATH.read_text())
            return (
                data.get("llm_url", LLM_URL),
                data.get("llm_model", LLM_MODEL),
                data.get("llm_api_key", LLM_API_KEY),
            )
        except (json.JSONDecodeError, OSError):
            pass
    return LLM_URL, LLM_MODEL, LLM_API_KEY


def save_llm_config(url: str, model: str, api_key: str = ""):
    """Persist LLM endpoint config to disk."""
    LLM_CONFIG_PATH.write_text(json.dumps({
        "llm_url": url, "llm_model": model, "llm_api_key": api_key,
    }))


def _headers(api_key: str) -> dict:
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def call_llm(system: str, user_msg: str, max_tokens: int = 300, timeout: float = 20.0) -> str | None:
    """Call the LLM and return the raw content string, or None on failure."""
    url, model, api_key = get_llm_config()
    try:
        resp = httpx.post(
            url,
            headers=_headers(api_key),
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.3,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        # Strip thinking tags if present
        if "<think>" in content:
            content = content.split("</think>")[-1].strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return content
    except Exception as e:
        log.warning(f"LLM call failed: {e}")
        return None


def call_llm_with_history(system: str, messages: list, max_tokens: int = 90, timeout: float = 30.0) -> str | None:
    """Call the LLM with full conversation history for multi-turn chat."""
    url, model, api_key = get_llm_config()
    try:
        resp = httpx.post(
            url,
            headers=_headers(api_key),
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}] + messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if "<think>" in content:
            content = content.split("</think>")[-1].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return content
    except Exception as e:
        log.warning(f"LLM call (with history) failed: {e}")
        return None


def test_llm_connection() -> dict:
    """Quick connectivity test — returns status dict."""
    url, model, api_key = get_llm_config()
    try:
        resp = httpx.post(
            url,
            headers=_headers(api_key),
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return {"ok": True, "model": model, "url": url}
    except Exception as e:
        return {"ok": False, "error": str(e), "model": model, "url": url}


def format_session_for_llm(events: list[dict]) -> str:
    """Turn raw session events into a readable summary for the LLM."""
    lines = []
    referrer_shown = False

    for i, ev in enumerate(events, 1):
        parts = [f"{i}. {ev['event']} on {ev['page']}"]

        if not referrer_shown and ev.get("referrer"):
            parts.append(f"(entered site from {ev['referrer']})")
            referrer_shown = True
        if ev.get("device"):
            parts.append(f"[{ev['device']}]")

        data = ev.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = None

        if data:
            if "scrollDepth" in data:
                parts.append(f"scroll:{data['scrollDepth']}%")
            if "timeOnPage" in data:
                parts.append(f"time:{data['timeOnPage']}s")
            if "text" in data:
                parts.append(f'clicked:"{data["text"][:50]}"')

        if ev.get("target"):
            parts.append(f"target:{ev['target']}")

        lines.append(" ".join(parts))

    return "\n".join(lines)
