import json
import logging
import httpx
import config

log = logging.getLogger("tastebud")


def get_llm_config() -> tuple:
    """Return (url, model, api_key) from config file if it exists, else env vars."""
    try:
        with open(config.LLM_CONFIG_PATH) as f:
            data = json.load(f)
        return (
            data.get("llm_url", config.LLM_URL),
            data.get("llm_model", config.LLM_MODEL),
            data.get("llm_api_key", config.LLM_API_KEY),
        )
    except Exception:
        return config.LLM_URL, config.LLM_MODEL, config.LLM_API_KEY


def save_llm_config(url: str, model: str, api_key: str):
    """Persist LLM endpoint config to disk."""
    with open(config.LLM_CONFIG_PATH, "w") as f:
        json.dump({"llm_url": url, "llm_model": model, "llm_api_key": api_key}, f)


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def call_llm(system: str, user_msg: str, max_tokens: int = 200, timeout: float = 30.0) -> str | None:
    """Call the LLM and return the raw content string, or None on failure."""
    url, model, api_key = get_llm_config()
    try:
        r = httpx.post(
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
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        if "<think>" in content and "</think>" in content:
            content = content[content.index("</think>") + len("</think>"):].strip()
        content = content.strip().strip("```").strip()
        return content
    except Exception as e:
        log.warning(f"LLM call failed: {e}")
        return None


def call_llm_with_history(messages: list, max_tokens: int = 200, timeout: float = 30.0) -> str | None:
    """Call the LLM with full conversation history for multi-turn chat."""
    url, model, api_key = get_llm_config()
    try:
        r = httpx.post(
            url,
            headers=_headers(api_key),
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.8,
            },
            timeout=timeout,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        if "<think>" in content and "</think>" in content:
            content = content[content.index("</think>") + len("</think>"):].strip()
        content = content.strip().strip("```").strip()
        return content
    except Exception as e:
        log.warning(f"LLM call (with history) failed: {e}")
        return None


def test_llm_connection() -> dict:
    """Quick connectivity test — returns status dict."""
    url, model, api_key = get_llm_config()
    try:
        r = httpx.post(
            url,
            headers=_headers(api_key),
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 10,
                "temperature": 0.3,
            },
            timeout=10.0,
        )
        r.raise_for_status()
        return {"ok": True, "url": url, "model": model}
    except Exception as e:
        return {"ok": False, "url": url, "model": model, "error": str(e)}


def format_session_for_llm(events: list) -> str:
    """Turn raw session events into a readable summary for the LLM."""
    parts = []
    for e in events:
        event = e.get("event", "")
        page = e.get("page", "")
        referrer = e.get("referrer", "")
        data = e.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}

        line = f"- {event} on {page}"
        if referrer:
            line += f" (entered site from {referrer})"

        scroll = data.get("scrollDepth")
        time_on = data.get("timeOnPage")
        text = data.get("text")
        target = e.get("target")

        if time_on:
            line += f" time:{time_on}"
        if scroll and int(scroll) > 50:
            line += f" scroll:{scroll}"
        if text:
            line += f' clicked:"{text}"'
        elif target:
            line += f" target:{target}"

        parts.append(line)
    return ". ".join(parts)
