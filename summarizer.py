import json
import os
from datetime import datetime, timedelta
from google.api_core.exceptions import ResourceExhausted

import gemini_client
import gmail_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "digest_state.json")

BACKOFF_MINUTES = 15  # Cooldown period if Gemini hits a rate limit

DEFAULT_STATE = {
    "summary_text": "",
    "processed_ids": [],
    "week_anchor": None,
    "last_checked": None,
    "backoff_until": None,
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
        json.dump(state, f, indent=2)


def is_in_backoff(state: dict) -> bool:
    """Checks if currently in a rate-limit cooldown period."""
    backoff_str = state.get("backoff_until")
    if not backoff_str:
        return False
    
    backoff_time = datetime.strptime(backoff_str, "%Y-%m-%d %H:%M")
    return datetime.now() < backoff_time


def set_backoff(state: dict):
    """Sets backoff_until to current_time + BACKOFF_MINUTES."""
    until = (datetime.now() + timedelta(minutes=BACKOFF_MINUTES)).strftime("%Y-%m-%d %H:%M")
    state["backoff_until"] = until
    save_state(state)


def get_cached_summary():
    """Returns saved summary instantly from local disk - 0 API requests."""
    state = load_state()
    if not state["summary_text"]:
        return (
            "No runs yet",
            'No summary has been generated yet. Click "Open digest" first.',
        )
    ts = state.get("last_checked") or state.get("week_anchor") or ""
    return f"Last updated: {ts}", state["summary_text"]


def full_rebuild() -> str:
    if is_in_backoff(state):
        return state.get("summary_text") or "Quota limit reached. Please try again later."
    
    """Initial baseline build: fetches emails and generates initial summary."""
    config = load_config()
    state = load_state()

    service = gmail_client.get_service()
    emails = gmail_client.fetch_course_emails(
        service,
        senders=config["course_senders"],
        days_back=config["lookback_days"],
        max_emails=config.get("max_emails", 100),
    )

    try:
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
                "backoff_until": None,
            }
        )
        return text

    except ResourceExhausted:
        print("[Warning] Hit Gemini quota during full rebuild. Entering backoff.")
        set_backoff(state)
        return state.get("summary_text") or "Quota limit reached. Please try again later."


def incremental_check():
    """Checks Gmail and only calls Gemini if brand new emails exist."""
    config = load_config()
    state = load_state()

    # Skip API calls if in active rate-limit backoff
    if is_in_backoff(state):
        print("[Info] Skipping Gemini check due to active backoff.")
        return False, state["summary_text"]

    service = gmail_client.get_service()
    new_emails = gmail_client.fetch_new_emails(
        service,
        senders=config["course_senders"],
        days_back=config["lookback_days"],
        max_emails=config.get("max_emails", 100),
        processed_ids=set(state["processed_ids"]),
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 0 new emails = 0 Gemini calls
    if not new_emails:
        state["last_checked"] = now
        save_state(state)
        return False, state["summary_text"]

    try:
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
        state["backoff_until"] = None
        save_state(state)
        return True, merged_text

    except ResourceExhausted:
        print("[Warning] Hit Gemini quota during incremental check. Entering backoff.")
        set_backoff(state)
        return False, state["summary_text"]


def ensure_fresh():
    """Entry point for app checks. Uses local cache first, or incremental update."""
    state = load_state()
    if not state["summary_text"]:
        return True, full_rebuild()
    return incremental_check()