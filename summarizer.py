"""Builds and caches the digest, and keeps Gemini calls to a minimum."""

import json
import os
import threading
from datetime import datetime, timedelta

import requests
from google.api_core.exceptions import ResourceExhausted

import canvas_client
import gemini_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
STATE_PATH = os.path.join(BASE_DIR, "digest_state.json")

TIME_FMT = "%Y-%m-%d %H:%M"
BACKOFF_MINUTES = 15  # used for per-minute quota errors
DAILY_RESET_HOUR = 3  # Gemini daily quotas reset at midnight Pacific = 3am Toronto

DEFAULT_STATE = {
    "summary_text": "",
    "processed_ids": [],
    "week_anchor": None,
    "last_checked": None,
    "backoff_until": None,
}

# One lock around everything that can call Gemini, so the scheduler, the
# hotkey and "Force full refresh" can never fire the same request twice.
_lock = threading.RLock()


# ---------- config / state ----------

def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def load_state() -> dict:
    merged = dict(DEFAULT_STATE)
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r") as f:
            merged.update(json.load(f))
    return merged


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _now() -> str:
    return datetime.now().strftime(TIME_FMT)


def _fallback_text(state: dict) -> str:
    return state.get("summary_text") or "Quota limit reached. Please try again later."


# ---------- backoff ----------

def is_in_backoff(state: dict) -> bool:
    backoff_str = state.get("backoff_until")
    if not backoff_str:
        return False
    return datetime.now() < datetime.strptime(backoff_str, TIME_FMT)


def _next_daily_reset() -> datetime:
    now = datetime.now()
    reset = now.replace(hour=DAILY_RESET_HOUR, minute=5, second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return reset


def set_backoff(state: dict, exc: Exception):
    """Per-day quota -> wait for the daily reset; anything else -> short wait."""
    if "PerDay" in str(exc):
        until = _next_daily_reset()
        print(f"[Warning] Daily Gemini quota used up. Pausing until {until:%Y-%m-%d %H:%M}.")
    else:
        until = datetime.now() + timedelta(minutes=BACKOFF_MINUTES)
        print(f"[Warning] Gemini rate limit hit. Pausing for {BACKOFF_MINUTES} minutes.")
    state["backoff_until"] = until.strftime(TIME_FMT)
    save_state(state)


# ---------- helpers ----------

def _fetch_items(config: dict):
    return canvas_client.fetch_canvas_updates(
        days_back=config.get("lookback_days", 7),
        max_items=config.get("max_items", 150),
        token_env_var=config.get("canvas_token_env_var", "CANVAS_API_TOKEN"),
    )


def _generate(config: dict, state: dict, items) -> str | None:
    """Calls Gemini once. Returns None (and sets backoff) on a quota error."""
    try:
        return gemini_client.summarize(
            items,
            model_name=config.get("gemini_model", gemini_client.DEFAULT_MODEL),
            api_key_env_var=config.get("gemini_api_key_env_var", "GEMINI_API_KEY"),
            days=config.get("lookback_days", 7),
        )
    except ResourceExhausted as exc:
        print(f"[Warning] Gemini quota error: {exc}")
        set_backoff(state, exc)
        return None


# ---------- public API ----------

def get_cached_summary():
    state = load_state()
    if not state["summary_text"]:
        return (
            "No runs yet",
            'No summary has been generated yet. Try "Force full refresh" from the tray menu.',
        )
    ts = state.get("last_checked") or state.get("week_anchor") or ""
    return f"Last updated: {ts}", state["summary_text"]


def full_rebuild() -> str:
    with _lock:
        config = load_config()
        state = load_state()

        if is_in_backoff(state):
            print(f"[Info] Skipping full rebuild, paused until {state['backoff_until']}.")
            return _fallback_text(state)

        try:
            items = _fetch_items(config)
        except requests.RequestException as exc:
            print(f"[Warning] Canvas fetch failed: {exc}")
            return _fallback_text(state)

        text = _generate(config, state, items)
        if text is None:
            state["last_checked"] = _now()
            save_state(state)
            return _fallback_text(state)

        now = _now()
        state.update({
            "summary_text": text,
            "processed_ids": [i["id"] for i in items],
            "week_anchor": now,
            "last_checked": now,
            "backoff_until": None,
        })
        save_state(state)
        return text


def incremental_check():
    """Only calls Gemini when Canvas has something new. When it does, the
    digest is rebuilt from the current items rather than merged into the old
    text, so each request stays the same size all week."""
    with _lock:
        config = load_config()
        state = load_state()

        if is_in_backoff(state):
            print(f"[Info] Skipping check, paused until {state['backoff_until']}.")
            return False, state["summary_text"]

        try:
            items = _fetch_items(config)
        except requests.RequestException as exc:
            print(f"[Warning] Canvas fetch failed: {exc}")
            return False, state["summary_text"]

        processed = set(state.get("processed_ids", []))
        new_items = [i for i in items if i["id"] not in processed]

        if not new_items:  # nothing new on Canvas = no Gemini call
            state["last_checked"] = _now()
            save_state(state)
            return False, state["summary_text"]

        print(f"[Info] {len(new_items)} new item(s): {[i['id'] for i in new_items]}")
        text = _generate(config, state, items)
        if text is None:
            return False, state["summary_text"]

        state.update({
            "summary_text": text,
            "processed_ids": sorted(processed | {i["id"] for i in items}),
            "last_checked": _now(),
            "backoff_until": None,
        })
        save_state(state)
        return True, text


def ensure_fresh():
    # If a check is already running (e.g. scheduler + hotkey at once),
    # don't queue a second one - just hand back what we have.
    if not _lock.acquire(blocking=False):
        return False, load_state()["summary_text"]
    try:
        if not load_state()["summary_text"]:
            return True, full_rebuild()
        return incremental_check()
    finally:
        _lock.release()

