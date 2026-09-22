import json
import os
from datetime import datetime

import gemini_client
import gmail_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "digest_state.json")

DEFAULT_STATE = {
    "summary_text": "",
    "processed_ids": [],
    "week_anchor": None,
    "last_checked": None,
}


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return dict(DEFAULT_STATE)
    with open(STATE_PATH, "r") as f:
        data = json.load(f)
    merged = dict(DEFAULT_STATE)
    merged.update(data)
    return merged


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


def get_cached_summary():
    """Returns (subtitle, text) instantly, with no network calls -
    just whatever's already saved to disk."""
    state = load_state()
    if not state["summary_text"]:
        return (
            "No runs yet",
            'No summary has been generated yet. Click "Open digest" first.',
        )
    ts = state.get("last_checked") or state.get("week_anchor") or ""
    return f"Last updated: {ts}", state["summary_text"]


def full_rebuild() -> str:
    """Full weekly rebuild: fetches the entire lookback window, gets a
    fresh Gemini summary, and resets which emails are considered
    already-processed. Returns the new summary text."""
    config = load_config()

    service = gmail_client.get_service()
    emails = gmail_client.fetch_course_emails(
        service,
        senders=config["course_senders"],
        days_back=config["lookback_days"],
        max_emails=config.get("max_emails", 100),
    )

    text = gemini_client.summarize(
        emails,
        model_name=config.get("gemini_model", "gemini-3.6-flash"),
        api_key_env_var=config.get("gemini_api_key_env_var", "GEMINI_API_KEY"),
        days=config["lookback_days"],
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    save_state(
        {
            "summary_text": text,
            "processed_ids": [e["id"] for e in emails],
            "week_anchor": now,
            "last_checked": now,
        }
    )
    return text


def incremental_check():
    """Fetches only emails not yet folded into the summary. If there
    are none, returns (False, cached_text) with no Gemini call. If
    there are some, merges them in via Gemini and returns
    (True, merged_text)."""
    config = load_config()
    state = load_state()

    service = gmail_client.get_service()
    new_emails = gmail_client.fetch_new_emails(
        service,
        senders=config["course_senders"],
        days_back=config["lookback_days"],
        max_emails=config.get("max_emails", 100),
        processed_ids=set(state["processed_ids"]),
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    if not new_emails:
        state["last_checked"] = now
        save_state(state)
        return False, state["summary_text"]

    merged_text = gemini_client.merge_summary(
        state["summary_text"],
        new_emails,
        model_name=config.get("gemini_model", "gemini-3.6-flash"),
        api_key_env_var=config.get("gemini_api_key_env_var", "GEMINI_API_KEY"),
    )

    state["summary_text"] = merged_text
    state["processed_ids"] = list(
        set(state["processed_ids"]) | {e["id"] for e in new_emails}
    )
    state["last_checked"] = now
    save_state(state)
    return True, merged_text


def ensure_fresh():
    """Used for background polls and on-click checks: does a full
    rebuild if there's no summary yet at all, otherwise an
    incremental check. Returns (changed: bool, text: str)."""
    state = load_state()
    if not state["summary_text"]:
        return True, full_rebuild()
    return incremental_check()