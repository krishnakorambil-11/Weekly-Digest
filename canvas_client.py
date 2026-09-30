"""Canvas client: fetches recent announcements and upcoming assignments."""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import requests

BASE_URL = "https://learn.ontariotechu.ca/api/v1"
TIMEOUT = 30  # seconds per request


def _clean_html(raw_html: str) -> str:
    """Strips basic HTML tags to reduce token count."""
    if not raw_html:
        return "No details provided."
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    return " ".join(clean.split())


def _get_headers(token_env_var: str = "CANVAS_API_TOKEN") -> Dict[str, str]:
    token = os.getenv(token_env_var)
    if not token:
        raise ValueError(f"Environment variable '{token_env_var}' is not set.")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _get_all(url: str, headers: Dict[str, str], params=None) -> List[Dict[str, Any]]:
    """GETs a Canvas list endpoint and follows pagination links to the end."""
    results = []
    while url:
        res = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        res.raise_for_status()
        results.extend(res.json())
        url = res.links.get("next", {}).get("url")
        params = None  # the "next" URL already carries the query string
    return results


def fetch_active_courses(token_env_var: str = "CANVAS_API_TOKEN") -> List[Dict[str, Any]]:
    """Fetches all active courses for the current user."""
    return _get_all(
        f"{BASE_URL}/courses",
        _get_headers(token_env_var),
        params={"enrollment_state": "active", "per_page": 50},
    )


def fetch_canvas_updates(
    days_back: int = 7,
    max_items: int = 150,
    token_env_var: str = "CANVAS_API_TOKEN",
) -> List[Dict[str, Any]]:
    """
    Returns announcements from the last `days_back` days plus assignments
    that are still upcoming (past-due ones are skipped), with HTML stripped.
    """
    headers = _get_headers(token_env_var)
    courses = fetch_active_courses(token_env_var)
    course_map = {c["id"]: c.get("name", f"Course {c['id']}") for c in courses if "id" in c}
    if not course_map:
        return []

    items = []

    # 1. Announcements from the lookback window
    start_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    params = [("context_codes[]", f"course_{cid}") for cid in course_map]
    params += [("start_date", start_date), ("per_page", "50")]
    for ann in _get_all(f"{BASE_URL}/announcements", headers, params):
        raw_code = ann.get("context_code", "")
        course_id = int(raw_code.split("_")[1]) if raw_code.startswith("course_") else 0
        items.append({
            "id": f"announcement_{ann['id']}",
            "type": "Announcement",
            "course": course_map.get(course_id, "Canvas Course"),
            "title": ann.get("title", "No Title"),
            "date": ann.get("posted_at", ""),
            "content": _clean_html(ann.get("message", "")),
        })

    # 2. Upcoming assignments only - "bucket=upcoming" drops anything past due,
    #    so old assignments can't keep resurfacing as "new" items.
    for course_id, course_name in course_map.items():
        try:
            assignments = _get_all(
                f"{BASE_URL}/courses/{course_id}/assignments",
                headers,
                params={"bucket": "upcoming", "order_by": "due_at", "per_page": 50},
            )
        except requests.HTTPError as exc:
            print(f"[Warning] Skipping assignments for {course_name}: {exc}")
            continue
        for assign in assignments:
            items.append({
                "id": f"assignment_{assign['id']}",
                "type": "Assignment",
                "course": course_name,
                "title": assign.get("name", "Untitled Assignment"),
                "date": assign.get("due_at", ""),
                "content": _clean_html(assign.get("description", "")),
            })

    if len(items) > max_items:
        # Truncating makes items slide in and out of the list between checks,
        # which looks like "new" content and costs a Gemini call. Raise
        # max_items in config.json if you see this.
        print(f"[Warning] {len(items)} items found, cutting to max_items={max_items}.")
        items = items[:max_items]

    return items

