import os
import requests
from typing import List, Dict, Any

BASE_URL = "https://learn.ontariotechu.ca/api/v1"

def _get_headers(token_env_var: str = "CANVAS_API_TOKEN") -> Dict[str, str]:
    token = os.getenv(token_env_var)
    if not token:
        raise ValueError(f"Environment variable '{token_env_var}' is not set.")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

def fetch_active_courses(token_env_var: str = "CANVAS_API_TOKEN") -> List[Dict[str, Any]]:
    """Fetches all active courses for the current user."""
    url = f"{BASE_URL}/courses"
    params = {"enrollment_state": "active"}
    res = requests.get(url, headers=_get_headers(token_env_var), params=params)
    res.raise_for_status()
    return res.json()

def fetch_canvas_updates(
    days_back: int = 7,
    max_items: int = 50,
    token_env_var: str = "CANVAS_API_TOKEN"
) -> List[Dict[str, Any]]:
    """
    Fetches active courses, then grabs recent announcements and upcoming 
    assignments for each course. Normalizes them into standard items.
    """
    headers = _get_headers(token_env_var)
    courses = fetch_active_courses(token_env_var)
    
    if not courses:
        return []

    context_codes = [f"course_{c['id']}" for c in courses if "id" in c]
    course_map = {c["id"]: c.get("name", f"Course {c['id']}") for c in courses if "id" in c}

    items = []

    # 1. Fetch Announcements across all active courses
    if context_codes:
        announcements_url = f"{BASE_URL}/announcements"
        params = [("context_codes[]", code) for code in context_codes]
        res = requests.get(announcements_url, headers=headers, params=params)
        if res.status_code == 200:
            for ann in res.json():
                course_id = int(ann.get("context_code", "").replace("course_", "0"))
                items.append({
                    "id": f"announcement_{ann['id']}",
                    "type": "Announcement",
                    "course": course_map.get(course_id, "Canvas Course"),
                    "title": ann.get("title", "No Title"),
                    "date": ann.get("posted_at", ""),
                    "content": ann.get("message", "")
                })

    # 2. Fetch Assignments for active courses
    for course_id, course_name in course_map.items():
        assign_url = f"{BASE_URL}/courses/{course_id}/assignments"
        res = requests.get(assign_url, headers=headers, params={"per_page": 20})
        if res.status_code == 200:
            for assign in res.json():
                items.append({
                    "id": f"assignment_{assign['id']}",
                    "type": "Assignment",
                    "course": course_name,
                    "title": assign.get("name", "Untitled Assignment"),
                    "date": assign.get("due_at", ""),
                    "content": assign.get("description", "") or "No details provided."
                })

    return items[:max_items]