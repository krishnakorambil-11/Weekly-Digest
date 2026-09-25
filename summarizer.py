import json
import os
from datetime import datetime, timedelta
from google.api_core.exceptions import ResourceExhausted

import canvas_client
import gemini_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "digest_state.json")

BACKOFF_MINUTES = 15

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
    backoff_str = state.get("backoff_until")
    if not backoff_str:
        return False
    backoff_time = datetime.strptime(backoff_str, "%Y-%m-%d %H:%M")
    return datetime.now() < backoff_time


def set_backoff(state: dict):
    until = (datetime.now() + timedelta(minutes=BACKOFF_MINUTES)).strftime("%Y-%m-%d %H:%M")
    state["backoff_until"] = until
    save_state(state)


def get_cached_summary():
    state = load_state()
    if not state["summary_text"]:
        return (
            "No runs yet",
            'No summary has been generated yet. Click "Open digest" first.',
        )
    ts = state.get("last_checked") or state.get("week_anchor") or ""
    return f"Last updated: {ts}", state["summary_text"]


def full_rebuild() -> str:
    config = load_config()
    state = load_state()

    items = canvas_client.fetch_canvas_updates(
        days_back=config.get("lookback_days", 7),
        max_items=config.get("max_items", 50),
        token_env_var=config.get("canvas_token_env_var", "CANVAS_API_TOKEN"),
    )

    try:
        text = gemini_client.summarize(
            items,
            model_name=config.get("gemini_model", "gemini-3.6-flash"),
            api_key_env_var=config.get("gemini_api_key_env_var", "GEMINI_API_KEY"),
            days=config.get("lookback_days", 7),
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        save_state(
            {
                "summary_text": text,
                "processed_ids": [i["id"] for i in items],
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
    config = load_config()
    state = load_state()

    if is_in_backoff(state):
        print("[Info] Skipping Gemini check due to active backoff.")
        return False, state["summary_text"]

    all_items = canvas_client.fetch_canvas_updates(
        days_back=config.get("lookback_days", 7),
        max_items=config.get("max_items", 50),
        token_env_var=config.get("canvas_token_env_var", "CANVAS_API_TOKEN"),
    )

    processed = set(state.get("processed_ids", []))
    new_items = [item for item in all_items if item["id"] not in processed]

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 0 new Canvas announcements or assignments = 0 Gemini calls
    if not new_items:
        state["last_checked"] = now
        save_state(state)
        return False, state["summary_text"]

    try:
        merged_text = gemini_client.merge_summary(
            state["summary_text"],
            new_items,
            model_name=config.get("gemini_model", "gemini-3.6-flash"),
            api_key_env_var=config.get("gemini_api_key_env_var", "GEMINI_API_KEY"),
        )

        state["summary_text"] = merged_text
        state["processed_ids"] = list(
            set(state["processed_ids"]) | {i["id"] for i in new_items}
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
    state = load_state()
    if not state["summary_text"]:
        return True, full_rebuild()
    return incremental_check()